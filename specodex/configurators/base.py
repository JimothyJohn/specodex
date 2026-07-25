"""Drive a vendor's JS configurator JSON API from a real browser session.

Many industrial vendors ship an online "configurator" — a single-page app
(Angular/React/Vue) that sizes and selects products over a structured JSON
API. That API is a higher-fidelity, LLM-free data source than a PDF catalog:
values arrive already typed with units, and the vendor's own sizing engine
resolves requirements → part number.

The catch is that the API's write side (POST/PUT) usually sits behind a WAF
that silently drops non-browser requests — a ``curl`` POST hangs with no
response, while the SPA's own ``fetch()`` goes straight through. This client
bridges that gap: it loads the SPA once under headless Chromium (so the origin
issues its session cookie and the edge sees a genuine browser), then issues
API calls with **in-page ``fetch()``** — indistinguishable from the SPA's own
traffic.

``ConfiguratorClient`` is vendor-agnostic. A per-vendor adapter (see
``stober.py``) knows the endpoint map, the shop/locale segments, and the
``filterId → Pydantic field`` mapping; it drives this client.

Discipline reused from the vendor-facts registry (todo/COMMERCIAL.md Phase 4):
every fetched URL is SSRF-checked (``specodex.url_safety.validate_url``),
robots.txt is respected, and calls are rate-limited.
"""

from __future__ import annotations

import json as _json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from specodex.url_safety import UnsafeURLError, validate_url

logger: logging.Logger = logging.getLogger(__name__)

_DEFAULT_UA: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class ConfiguratorError(RuntimeError):
    """Raised when the configurator session or an API call fails fatally."""


class RobotsDisallowedError(ConfiguratorError):
    """Raised when robots.txt forbids fetching the configurator API."""


@dataclass(frozen=True)
class ApiResponse:
    """Result of a single in-page API call."""

    status: int
    json: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ConfiguratorClient:
    """Headless-browser session that issues in-page API calls to a configurator.

    Use as a context manager::

        with ConfiguratorClient("https://configurator.stober.com") as c:
            c.open("https://configurator.stober.com/en-US/category-selection?shop=SDI")
            resp = c.api("GET", "en-US/ProductSelection/SDI")

    Args:
        origin: Scheme + host of the configurator (e.g.
            ``https://configurator.stober.com``). API paths passed to
            :meth:`api` are resolved relative to ``origin + api_base``.
        api_base: Path prefix the SPA mounts its API under. Default ``/api/``.
        user_agent: Browser UA string for the session.
        nav_timeout_ms: Playwright navigation timeout.
        settle_ms: Fixed wait after navigation for the SPA to bootstrap its
            session (cookie + any handshake) before the first API call.
        min_interval_s: Minimum wall-clock spacing between API calls
            (politeness rate limit).
        respect_robots: When True, refuse to call the API if robots.txt
            disallows ``api_base`` for our UA.
    """

    def __init__(
        self,
        origin: str,
        *,
        api_base: str = "/api/",
        user_agent: str = _DEFAULT_UA,
        nav_timeout_ms: int = 60_000,
        settle_ms: int = 3_000,
        min_interval_s: float = 1.0,
        respect_robots: bool = True,
    ) -> None:
        validate_url(origin)  # SSRF: origin is the fetch target host
        self.origin = origin.rstrip("/")
        self.api_base = "/" + api_base.strip("/") + "/"
        self.user_agent = user_agent
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms
        self.min_interval_s = min_interval_s
        self.respect_robots = respect_robots

        self._pw = None
        self._browser = None
        self._page = None
        self._last_call: float = 0.0
        self._robots_checked: bool = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> "ConfiguratorClient":
        # Imported lazily so unit tests that inject a fake client (and never
        # touch a real browser) don't require the Playwright browser binary.
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        context = self._browser.new_context(user_agent=self.user_agent)
        self._page = context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._browser = self._pw = self._page = None

    # -- session bootstrap -------------------------------------------------

    def open(self, bootstrap_url: str) -> None:
        """Load the SPA so the origin sets its session and the WAF sees a browser.

        Must be called once before :meth:`api`. Raises
        :class:`RobotsDisallowedError` if robots.txt forbids the API path,
        and :class:`ConfiguratorError` if the page cannot be loaded.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        validate_url(bootstrap_url)
        self._check_robots()
        if self._page is None:
            raise ConfiguratorError("client used outside its context manager")

        logger.info("Bootstrapping configurator session: %s", bootstrap_url)
        try:
            self._page.goto(
                bootstrap_url,
                wait_until="domcontentloaded",
                timeout=self.nav_timeout_ms,
            )
        except PlaywrightTimeout as e:
            raise ConfiguratorError(f"navigation timed out: {bootstrap_url}") from e
        # Give the SPA time to establish its session (cookie / handshake).
        self._page.wait_for_timeout(self.settle_ms)

    def _check_robots(self) -> None:
        if not self.respect_robots or self._robots_checked:
            return
        self._robots_checked = True
        robots_url = f"{self.origin}/robots.txt"
        rp = RobotFileParser()
        try:
            validate_url(robots_url)
            req = urllib.request.Request(
                robots_url, headers={"User-Agent": self.user_agent}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (https-only, SSRF-checked)
                rp.parse(resp.read().decode("utf-8", "replace").splitlines())
        except UnsafeURLError:
            raise
        except Exception as e:
            # No robots.txt (or unreachable) → treat as "allow all", the
            # RFC-9309 default. Log it; don't block harvesting on a 404.
            logger.info("robots.txt unavailable (%s): assuming allow-all", e)
            return
        if not rp.can_fetch(self.user_agent, self.origin + self.api_base):
            raise RobotsDisallowedError(
                f"robots.txt disallows {self.api_base} for {self.user_agent!r}"
            )

    # -- API calls ---------------------------------------------------------

    def api(self, method: str, path: str, body: Optional[Any] = None) -> ApiResponse:
        """Issue one API call via in-page ``fetch`` and return the parsed result.

        Args:
            method: HTTP method (``GET``, ``POST``, ...).
            path: Path relative to ``origin + api_base`` (no leading slash),
                e.g. ``en-US/ProductSelection/SDI``.
            body: JSON-serialisable request body, or None for no body.

        Returns:
            :class:`ApiResponse` with the HTTP status and parsed JSON (or the
            raw text, truncated, when the response is not JSON).
        """
        if self._page is None:
            raise ConfiguratorError("client used outside its context manager")

        # Politeness rate limit.
        wait = self.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            self._page.wait_for_timeout(int(wait * 1000))

        url = urljoin(self.origin + self.api_base, path.lstrip("/"))
        # Defence in depth: an adapter could pass an absolute path; make sure
        # in-page fetch never leaves the configurator's own origin.
        if urlparse(url).netloc != urlparse(self.origin).netloc:
            raise ConfiguratorError(f"refusing cross-origin API call: {url}")

        result = self._page.evaluate(
            """async ({ method, url, body }) => {
                const res = await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: body === null ? undefined : JSON.stringify(body),
                    credentials: 'include',
                });
                const text = await res.text();
                let json;
                try { json = JSON.parse(text); } catch { json = text.slice(0, 500); }
                return { status: res.status, json };
            }""",
            {"method": method.upper(), "url": url, "body": body},
        )
        self._last_call = time.monotonic()
        logger.info("%s %s -> %s", method.upper(), path, result.get("status"))
        return ApiResponse(status=int(result["status"]), json=result["json"])


def dumps(obj: Any) -> str:
    """Compact JSON for CLI/debug output (stable key order)."""
    return _json.dumps(obj, indent=2, sort_keys=True)
