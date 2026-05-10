"""Browser-based page fetching and HTML cleaning for product webpages.

Uses Playwright to render JS-heavy e-commerce pages that block simple
HTTP clients, then strips non-content tags to reduce LLM token usage.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


logger: logging.Logger = logging.getLogger(__name__)

# Tags whose entire subtree should be removed (content is noise for spec extraction).
_STRIP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "nav", "footer", "header", "svg", "iframe", "noscript"}
)

# Max characters of cleaned HTML to send to the LLM.
_MAX_HTML_CHARS: int = 50_000

# Playwright navigation timeout (ms).
_NAV_TIMEOUT: int = int(os.environ.get("WEB_SCRAPER_TIMEOUT", "30000"))


@dataclass
class PageMetadata:
    """Lightweight metadata extracted from the page head."""

    title: str = ""
    canonical_url: str = ""
    description: str = ""
    breadcrumbs: list[str] = field(default_factory=list)


@dataclass
class PageContent:
    """Everything extracted from a single product page."""

    url: str
    html: str
    structured_data: list[dict[str, Any]] = field(default_factory=list)
    metadata: PageMetadata = field(default_factory=PageMetadata)


# ---------------------------------------------------------------------------
# HTML cleaning (stdlib only — no BeautifulSoup)
# ---------------------------------------------------------------------------


class _TagStripper(HTMLParser):
    """HTMLParser subclass that drops unwanted tags and their children."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pieces: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _STRIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _STRIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        return " ".join(self._pieces)


def clean_html(raw_html: str, max_chars: int = _MAX_HTML_CHARS) -> str:
    """Strip non-content tags and collapse whitespace.

    Args:
        raw_html: Full page HTML string.
        max_chars: Truncation limit for the cleaned output.

    Returns:
        Cleaned text content, truncated to *max_chars*.
    """
    stripper = _TagStripper()
    stripper.feed(raw_html)
    text = stripper.get_text()
    # Collapse runs of whitespace into single spaces.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        logger.info("Truncating cleaned HTML from %d to %d chars", len(text), max_chars)
        text = text[:max_chars]
    return text


# ---------------------------------------------------------------------------
# Structured data extraction
# ---------------------------------------------------------------------------


def _extract_json_ld(raw_html: str) -> list[dict[str, Any]]:
    """Pull all JSON-LD blocks from the page source."""
    results: list[dict[str, Any]] = []
    # Regex is simpler than parsing the DOM for this narrow task.
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw_html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict):
                results.append(data)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed JSON-LD block")
    return results


def _extract_meta(raw_html: str) -> PageMetadata:
    """Extract <title>, canonical URL, description, and Open Graph tags."""
    meta = PageMetadata()

    # <title>
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.DOTALL | re.IGNORECASE)
    if m:
        meta.title = re.sub(r"\s+", " ", m.group(1)).strip()

    # <link rel="canonical">
    m = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        raw_html,
        re.IGNORECASE,
    )
    if m:
        meta.canonical_url = m.group(1)

    # <meta name="description">
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']',
        raw_html,
        re.IGNORECASE,
    )
    if m:
        meta.description = m.group(1)

    # Breadcrumbs: look for BreadcrumbList in JSON-LD first, fall back to
    # common HTML patterns (aria-label="breadcrumb", class*="breadcrumb").
    for ld in _extract_json_ld(raw_html):
        if ld.get("@type") == "BreadcrumbList":
            items = ld.get("itemListElement", [])
            meta.breadcrumbs = [
                item.get("name", item.get("item", {}).get("name", ""))
                for item in sorted(items, key=lambda x: x.get("position", 0))
            ]
            break

    return meta


# ---------------------------------------------------------------------------
# Playwright page fetch
# ---------------------------------------------------------------------------


def fetch_page(url: str) -> PageContent:
    """Render a product page in a headless browser and extract content.

    Args:
        url: Full URL of the product page.

    Returns:
        PageContent with cleaned HTML, structured data, and metadata.

    Raises:
        RuntimeError: If the page cannot be loaded.
        UnsafeURLError: If the URL targets an internal/metadata host
            (RFC1918, link-local, loopback, etc.) or uses a non-HTTPS
            scheme. See :mod:`specodex.url_safety`.
    """
    # SSRF defense — URL is user-controlled.
    from specodex.url_safety import validate_url

    validate_url(url)

    logger.info("Fetching page with Playwright: %s", url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT)
        except PlaywrightTimeout:
            logger.warning("Network-idle timeout; proceeding with partial load")
        except Exception as e:
            browser.close()
            raise RuntimeError(f"Failed to load {url}: {e}") from e

        raw_html: str = page.content()
        browser.close()

    logger.info("Retrieved %d characters of raw HTML", len(raw_html))

    structured_data = _extract_json_ld(raw_html)
    if structured_data:
        logger.info("Found %d JSON-LD block(s)", len(structured_data))

    metadata = _extract_meta(raw_html)
    cleaned = clean_html(raw_html)

    return PageContent(
        url=url,
        html=cleaned,
        structured_data=structured_data,
        metadata=metadata,
    )
