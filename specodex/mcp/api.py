"""HTTP client for the public Specodex catalog API.

Everything here is a GET (plus one POST for the compatibility check)
against endpoints that serve the browsable site without a key. Inputs
are validated before they become URL parameters so a tool argument can
never smuggle a path segment, a CR/LF, or a second query parameter.

Two production quirks this client must know about:

* CloudFront serves the SPA's index.html with HTTP 200 for any path the
  API answers 404 on (single-page-app fallback). A "product not found"
  therefore arrives as text/html, not JSON. We detect non-JSON bodies
  and raise a not-found error instead of handing HTML to the model.
* Production (master) can lag dev/staging; an endpoint that exists on
  staging may not exist on www yet. That surfaces as the same HTML
  fallback and gets the same actionable error.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://www.specodex.com"
ENV_BASE_URL = "SPECODEX_API_URL"

# Tool-argument shapes. product_id is a deterministic UUID5
# (specodex/ids.py); product types are lowercase snake_case literals
# from specodex/models/common.py:ProductType. Keep these strict — they
# become URL path/query pieces.
PRODUCT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
PRODUCT_TYPE_RE = re.compile(r"^[a-z][a-z_]{1,40}$")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
WHERE_RE = re.compile(
    r"^(?P<field>[a-z][a-z0-9_.]*)(?P<op>>=|<=|!=|>|<|=)(?P<value>[^\r\n]{1,200})$"
)
SORT_RE = re.compile(r"^(?P<field>[a-z][a-z0-9_.]*)(:(?P<dir>asc|desc))?$")
MAX_TEXT = 200
SEARCH_LIMIT_MAX = 100
LIST_LIMIT_MAX = 2000

# Keys the API returns that are storage/ingest plumbing, not specs.
# Stripped from compact responses so a 20-row search doesn't spend the
# model's context on partition keys and comparable-id lists.
_NOISE_KEYS = frozenset({"PK", "SK", "GSI1PK", "GSI1SK", "pages"})


class SpecodexApiError(Exception):
    """A request the API rejected, or one it could not serve.

    ``status`` is the HTTP status (0 for transport failures); ``details``
    carries the API's own validation messages when it sent any, so the
    model sees "type: Invalid option: expected one of ..." rather than a
    bare 400.
    """

    def __init__(
        self, message: str, *, status: int = 0, details: list[str] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.details = details or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.details:
            return f"{base} ({'; '.join(self.details)})"
        return base


def _text(value: str, name: str) -> str:
    """Bound and sanitise a free-text argument (query, manufacturer)."""
    if not isinstance(value, str):
        raise SpecodexApiError(f"{name} must be a string")
    cleaned = value.replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        raise SpecodexApiError(f"{name} must not be empty")
    if len(cleaned) > MAX_TEXT:
        raise SpecodexApiError(f"{name} is longer than {MAX_TEXT} characters")
    return cleaned


def validate_product_id(product_id: str) -> str:
    if not isinstance(product_id, str) or not PRODUCT_ID_RE.match(product_id):
        raise SpecodexApiError(
            "product_id must be the UUID returned by a search or list tool "
            "(e.g. 4c960ae8-e5b3-555d-8808-b06f9d2e9357)"
        )
    return product_id


def validate_product_type(product_type: str) -> str:
    if not isinstance(product_type, str) or not PRODUCT_TYPE_RE.match(product_type):
        raise SpecodexApiError(
            "product_type must be a lowercase snake_case type such as 'motor', "
            "'drive' or 'gearhead'; call specodex_list_product_types to see them"
        )
    return product_type


def validate_where(clauses: list[str] | None) -> list[str]:
    out: list[str] = []
    for clause in clauses or []:
        if not isinstance(clause, str) or not WHERE_RE.match(clause.strip()):
            raise SpecodexApiError(
                f"where clause {clause!r} is not 'field<op>value' with op one of "
                ">=, <=, !=, >, <, = (e.g. 'rated_power>=1000', 'manufacturer=abb')"
            )
        out.append(clause.strip())
    return out


def validate_sort(keys: list[str] | None) -> list[str]:
    out: list[str] = []
    for key in keys or []:
        if not isinstance(key, str) or not SORT_RE.match(key.strip()):
            raise SpecodexApiError(
                f"sort key {key!r} is not 'field' or 'field:asc'/'field:desc' "
                "(e.g. 'rated_power:desc')"
            )
        out.append(key.strip())
    return out


def validate_limit(limit: int | None, *, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > maximum
    ):
        raise SpecodexApiError(f"limit must be an integer between 1 and {maximum}")
    return limit


def compact_product(product: dict[str, Any]) -> dict[str, Any]:
    """Drop storage plumbing and the comparable-id lists behind price estimates."""
    out = {k: v for k, v in product.items() if k not in _NOISE_KEYS}
    price = out.get("price_estimate")
    if isinstance(price, dict) and "sources" in price:
        out["price_estimate"] = {k: v for k, v in price.items() if k != "sources"}
    return out


class SpecodexApi:
    """Async client. One instance per server process; safe to share."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = (
            base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        ).rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"accept": "application/json", "user-agent": "specodex-mcp/0.1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── transport ───────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        try:
            resp = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise SpecodexApiError(
                f"could not reach {self.base_url}: {exc.__class__.__name__}"
            ) from exc

        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            # CloudFront's SPA fallback: HTML 200 for anything the API
            # 404'd, and for routes this stage doesn't serve at all.
            raise SpecodexApiError(
                f"{method} {path} is not served as JSON by {self.base_url} — either the "
                "id does not exist for that product_type, or this endpoint is not "
                "deployed at this base URL yet (try SPECODEX_API_URL=<staging url>)",
                status=resp.status_code,
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise SpecodexApiError(
                f"{method} {path} returned malformed JSON", status=resp.status_code
            ) from exc

        if resp.status_code >= 400 or (
            isinstance(body, dict) and body.get("success") is False
        ):
            message = body.get("error") if isinstance(body, dict) else None
            details = body.get("details") if isinstance(body, dict) else None
            raise SpecodexApiError(
                message or f"{method} {path} failed with HTTP {resp.status_code}",
                status=resp.status_code,
                details=[str(d) for d in details]
                if isinstance(details, list)
                else None,
            )
        return body

    # ── catalog ─────────────────────────────────────────────────

    async def product_types(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/products/categories"))["data"]

    async def summary(self) -> dict[str, Any]:
        return (await self._request("GET", "/api/products/summary"))["data"]

    async def manufacturers(self) -> list[str]:
        return (await self._request("GET", "/api/products/manufacturers"))["data"]

    async def datasheets(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/datasheets"))["data"]

    async def search(
        self,
        *,
        query: str | None = None,
        product_type: str | None = None,
        manufacturer: str | None = None,
        where: list[str] | None = None,
        sort: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": validate_limit(limit, default=20, maximum=SEARCH_LIMIT_MAX)
        }
        if query is not None:
            params["q"] = _text(query, "query")
        if product_type is not None:
            params["type"] = validate_product_type(product_type)
        if manufacturer is not None:
            params["manufacturer"] = _text(manufacturer, "manufacturer")
        if where:
            params["where"] = validate_where(where)
        if sort:
            params["sort"] = validate_sort(sort)
        body = await self._request("GET", "/api/v1/search", params=params)
        return {"count": body.get("count", len(body["data"])), "products": body["data"]}

    async def list_products(
        self, *, product_type: str, limit: int | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "type": validate_product_type(product_type),
            "limit": validate_limit(limit, default=100, maximum=LIST_LIMIT_MAX),
        }
        if cursor:
            if not isinstance(cursor, str) or not re.fullmatch(
                r"[A-Za-z0-9_=-]{1,4096}", cursor
            ):
                raise SpecodexApiError(
                    "cursor must be the opaque value returned by a previous page"
                )
            params["cursor"] = cursor
        body = await self._request("GET", "/api/products", params=params)
        return {
            "products": body["data"],
            "cursor": body.get("cursor"),
            "count": len(body["data"]),
        }

    async def get_product(
        self, *, product_id: str, product_type: str
    ) -> dict[str, Any]:
        pid = validate_product_id(product_id)
        params = {"type": validate_product_type(product_type)}
        return (await self._request("GET", f"/api/products/{pid}", params=params))[
            "data"
        ]

    # ── relations ───────────────────────────────────────────────

    async def actuators(
        self,
        *,
        min_stroke_mm: float | None = None,
        min_peak_force_n: float | None = None,
        min_peak_velocity_mm_s: float | None = None,
        min_duty_cycle: float | None = None,
        orientation: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        for name, value in (
            ("min_stroke_mm", min_stroke_mm),
            ("min_peak_force_n", min_peak_force_n),
            ("min_peak_velocity_mm_s", min_peak_velocity_mm_s),
            ("min_duty_cycle", min_duty_cycle),
        ):
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value != value
                or value < 0
                or value == float("inf")
            ):
                raise SpecodexApiError(f"{name} must be a finite number >= 0")
            params[name] = value
        if min_duty_cycle is not None and min_duty_cycle > 1:
            raise SpecodexApiError("min_duty_cycle is a fraction between 0 and 1")
        if orientation is not None:
            if orientation not in ("horizontal", "vertical"):
                raise SpecodexApiError("orientation must be 'horizontal' or 'vertical'")
            params["orientation"] = orientation
        return (
            await self._request("GET", "/api/v1/relations/actuators", params=params)
        )["data"]

    async def motors_for_actuator(
        self, *, product_id: str, product_type: str
    ) -> list[dict[str, Any]]:
        if product_type not in ("linear_actuator", "electric_cylinder"):
            raise SpecodexApiError(
                "product_type must be 'linear_actuator' or 'electric_cylinder'"
            )
        params = {"id": validate_product_id(product_id), "type": product_type}
        return (
            await self._request(
                "GET", "/api/v1/relations/motors-for-actuator", params=params
            )
        )["data"]

    async def drives_for_motor(self, *, product_id: str) -> list[dict[str, Any]]:
        params = {"id": validate_product_id(product_id)}
        return (
            await self._request(
                "GET", "/api/v1/relations/drives-for-motor", params=params
            )
        )["data"]

    async def gearheads_for_motor(self, *, product_id: str) -> list[dict[str, Any]]:
        params = {"id": validate_product_id(product_id)}
        return (
            await self._request(
                "GET", "/api/v1/relations/gearheads-for-motor", params=params
            )
        )["data"]

    async def compat_adjacent(self, *, product_type: str) -> list[str]:
        params = {"type": validate_product_type(product_type)}
        return (await self._request("GET", "/api/v1/compat/adjacent", params=params))[
            "data"
        ]

    async def compat_check(
        self, *, a_id: str, a_type: str, b_id: str, b_type: str
    ) -> dict[str, Any]:
        body = {
            "a": {
                "id": validate_product_id(a_id),
                "type": validate_product_type(a_type),
            },
            "b": {
                "id": validate_product_id(b_id),
                "type": validate_product_type(b_type),
            },
        }
        return (await self._request("POST", "/api/v1/compat/check", json=body))["data"]
