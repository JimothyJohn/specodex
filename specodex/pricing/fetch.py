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
        cached = self._cache_read(url)
        if cached is not None:
            return cached

        if not self._allowed(url):
            logger.info("robots.txt disallows %s", url)
            return None

        domain = urlparse(url).netloc
        self._bucket.wait(domain)

        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            logger.info("httpx error on %s: %s", url, e)
            return None

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.info(
                "429 from %s — sleeping %ds then retrying once", domain, retry_after
            )
            time.sleep(retry_after)
            try:
                resp = self._client.get(url)
            except httpx.HTTPError:
                return None

        if resp.status_code >= 400:
            logger.info("HTTP %d on %s", resp.status_code, url)
            return None

        # Redirect-to-root guard. Kyklo-backed stores (Mitsubishi, IEC
        # Supply, Lakewood, etc.) send unknown part numbers to the site
        # root, which still 200s but serves homepage JSON-LD for an
        # unrelated product. Treat "requested a deep path, landed on /"
        # as a miss so downstream extractors never see that HTML.
        requested_path = urlparse(url).path
        final_path = urlparse(str(resp.url)).path
        if (
            requested_path
            and requested_path not in ("/", "")
            and final_path in ("/", "", "/index.html")
        ):
            logger.info("redirected to root (not carried): %s → %s", url, resp.url)
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
