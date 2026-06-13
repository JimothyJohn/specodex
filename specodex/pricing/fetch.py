"""Polite HTTP fetcher for price scraping.

- robots.txt check per domain (cached)
- per-domain rate limit (1 req/s default, token bucket)
- response cache in ``outputs/price_cache/`` keyed by sha256(normalized_url)
- httpx as primary transport, Playwright escalation only when static HTML
  contains no price-looking token. The escalation signal is intentionally
  cheap: if ``$`` or the word ``price`` doesn't appear in the static HTML
  we fall back to JS rendering.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "Specodex/1.0 (+https://www.specodex.com; contact: nick@advin.io)"
CACHE_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class FetchResult:
    url: str
    html: str
    from_cache: bool
    used_playwright: bool


class _TokenBucket:
    """Per-domain rate limit. Refills 1 token / interval_s."""

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s = interval_s
        self._last: Dict[str, float] = {}

    def wait(self, domain: str) -> None:
        now = time.monotonic()
        last = self._last.get(domain, 0.0)
        delta = now - last
        if delta < self.interval_s:
            time.sleep(self.interval_s - delta)
        self._last[domain] = time.monotonic()


class PriceFetcher:
    """Fetch web pages politely with caching and optional JS rendering."""

    def __init__(
        self,
        cache_dir: Path,
        rate_limit_s: float = 1.0,
        timeout_s: float = 15.0,
        allow_playwright: bool = True,
        max_consecutive_429: int = 8,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._bucket = _TokenBucket(rate_limit_s)
        self._robots: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
            follow_redirects=True,
            http2=False,  # HTTP/2 adds complexity without win here
        )
        self._allow_playwright = allow_playwright
        self._pw = None  # lazy
        # Per-domain circuit breaker. Once a host has 429'd this many
        # times in a row it has hard-blocked us — every further request
        # is doomed, so stop asking for the rest of this fetcher's life.
        # Without this, a flagged store (shop1.us.mitsubishielectric.com,
        # observed 2026-06-12) spends ~90s per product cycling retries
        # and records a fake miss — a 14-hour run produced 0 hits and
        # 1,816 429s before it was killed by hand.
        self._max_consecutive_429 = max_consecutive_429
        self._consecutive_429: Dict[str, int] = {}
        self._blocked_domains: set[str] = set()

    # ── caching ────────────────────────────────────────────────────

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _cache_read(self, url: str) -> Optional[FetchResult]:
        path = self._cache_path(url)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return FetchResult(
                url=data["url"],
                html=data["html"],
                from_cache=True,
                used_playwright=data.get("used_playwright", False),
            )
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def _cache_write(self, result: FetchResult) -> None:
        path = self._cache_path(result.url)
        try:
            path.write_text(
                json.dumps(
                    {
                        "url": result.url,
                        "html": result.html,
                        "used_playwright": result.used_playwright,
                    }
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Cache write failed for %s: %s", result.url, e)

    # ── robots ─────────────────────────────────────────────────────

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._robots.get(root)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{root}/robots.txt")
            # Fetch robots.txt through our httpx client so Cloudflare/WAFs
            # don't 403 Python's default urllib UA — when they do, stdlib's
            # robotparser silently flips to `disallow_all = True`, which
            # would block every fetch.
            try:
                resp = self._client.get(f"{root}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    # Matches stdlib default but makes the choice explicit
                    # in logs rather than masked behind a silent 403 retry.
                    rp.disallow_all = True
                else:
                    # 404 / 5xx / anything else → assume open
                    rp.allow_all = True
            except Exception as e:
                logger.info(
                    "robots.txt unreadable for %s: %s — assuming allowed", root, e
                )
                rp.allow_all = True
            self._robots[root] = rp
        try:
            return rp.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    # ── primary fetch ──────────────────────────────────────────────

    def fetch(self, url: str) -> Optional[FetchResult]:
        # SSRF defense — URL comes from search-result candidates, which
        # are user-influenced (manufacturer/part-number queries flow into
        # Serper, then back here as URLs to fetch). See specodex.url_safety.
        from specodex.url_safety import validate_url

        validate_url(url)

        cached = self._cache_read(url)
        if cached is not None:
            return cached

        if not self._allowed(url):
            logger.info("robots.txt disallows %s", url)
            return None

        domain = urlparse(url).netloc

        # Circuit breaker: this host has hard-blocked us — skip without
        # touching the network (and without burning the rate-limit wait).
        if domain in self._blocked_domains:
            return None

        self._bucket.wait(domain)

        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            logger.info("httpx error on %s: %s", url, e)
            return None

        # 429 handling: up to 3 retries honoring Retry-After (capped at
        # 30s each). A single retry was not enough in practice — the
        # Mitsubishi store keeps 429ing for a while once it has flagged
        # the client, and each premature give-up records a fake miss
        # (observed live 2026-06-12: 54 429s → 93 "misses", 3 hits).
        attempts = 0
        while resp.status_code == 429 and attempts < 3:
            attempts += 1
            retry_after = min(int(resp.headers.get("Retry-After", "5") or "5"), 30)
            logger.info(
                "429 from %s — sleeping %ds (retry %d/3)",
                domain,
                retry_after,
                attempts,
            )
            time.sleep(retry_after)
            try:
                resp = self._client.get(url)
            except httpx.HTTPError:
                return None

        if resp.status_code == 429:
            # Still throttled after all retries. Count it toward the
            # per-domain breaker; trip it once the host has done this
            # max_consecutive_429 times in a row.
            n = self._consecutive_429.get(domain, 0) + 1
            self._consecutive_429[domain] = n
            if n >= self._max_consecutive_429:
                self._blocked_domains.add(domain)
                logger.warning(
                    "%s 429'd %d times in a row — circuit-breaking this "
                    "domain for the rest of the run (it has hard-blocked us)",
                    domain,
                    n,
                )
            logger.info("HTTP 429 on %s", url)
            return None

        if resp.status_code >= 400:
            logger.info("HTTP %d on %s", resp.status_code, url)
            return None

        # A non-429 response means the host is talking to us again —
        # reset its consecutive-429 streak.
        self._consecutive_429.pop(domain, None)

        # Redirect-to-root/parent guard. Kyklo-backed stores (Mitsubishi,
        # IEC Supply, Lakewood, etc.) send unknown part numbers to the
        # site root; Bodine-style stores 302 unknown product slugs to the
        # parent category (/products/n4603/ → /products/, observed live
        # 2026-06-12). Both still 200 and serve unrelated products whose
        # prices the body-text fallback would happily extract. Treat
        # "requested a deep path, landed on / or on a parent prefix of
        # the requested path" as a miss.
        requested_path = urlparse(url).path
        final_path = urlparse(str(resp.url)).path
        if requested_path and requested_path not in ("/", ""):
            landed_on_root = final_path in ("/", "", "/index.html")
            landed_on_parent = (
                final_path != requested_path
                and final_path.endswith("/")
                and requested_path.startswith(final_path)
            )
            if landed_on_root or landed_on_parent:
                logger.info(
                    "redirected to %s (not carried): %s → %s",
                    "root" if landed_on_root else "parent",
                    url,
                    resp.url,
                )
                return None

        html = resp.text
        if self._needs_js(html) and self._allow_playwright:
            rendered = self._render_with_playwright(url)
            if rendered is not None:
                result = FetchResult(
                    url=str(resp.url),
                    html=rendered,
                    from_cache=False,
                    used_playwright=True,
                )
                self._cache_write(result)
                return result

        result = FetchResult(
            url=str(resp.url), html=html, from_cache=False, used_playwright=False
        )
        self._cache_write(result)
        return result

    @staticmethod
    def _needs_js(html: str) -> bool:
        # Price-looking token missing → likely rendered by JS.
        if not html or len(html) < 500:
            return True
        needle = html.lower()
        if "$" in html:
            return False
        if "price" in needle or 'itemprop="price"' in needle:
            return False
        if "application/ld+json" in needle:
            return False
        return True

    def _render_with_playwright(self, url: str) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            logger.info("Playwright not installed; skipping JS render for %s", url)
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(user_agent=USER_AGENT)
                    page = ctx.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)  # settle JS
                    html = page.content()
                finally:
                    browser.close()
            return html
        except Exception as e:
            logger.info("Playwright render failed for %s: %s", url, e)
            return None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PriceFetcher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
