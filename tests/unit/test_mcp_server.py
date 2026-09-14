"""Specodex MCP server — protocol-level contract tests.

Real MCP protocol (in-memory transport + ClientSession from the SDK),
real HTTP (a stdlib http.server thread serving responses captured from
production on 2026-09-13 under tests/unit/fixtures/mcp/). Nothing is
mocked: the client goes through httpx, URL encoding, content-type
sniffing and JSON parsing exactly as it will against www.specodex.com.
"""

from __future__ import annotations

import json
import threading

import anyio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from specodex.mcp.api import SpecodexApi, SpecodexApiError
from specodex.mcp.server import build_server

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
MOTOR_ID = "4c960ae8-e5b3-555d-8808-b06f9d2e9357"
DRIVE_ID = "001411e2-e535-5975-937c-29afbc125a2e"
MISSING_ID = "00000000-0000-0000-0000-000000000000"


class _Handler(BaseHTTPRequestHandler):
    """Route → fixture file. Records every request for assertions."""

    requests: list[tuple[str, str, dict[str, list[str]], bytes]] = []

    def log_message(self, *_: object) -> None:  # keep pytest output clean
        pass

    def _send(self, name: str, status: int = 200) -> None:
        path = FIXTURES / name
        body = path.read_bytes()
        self.send_response(status)
        self.send_header(
            "content-type",
            "text/html; charset=utf-8"
            if path.suffix == ".html"
            else "application/json",
        )
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        _Handler.requests.append((method, url.path, qs, body))
        p = url.path
        if p == "/api/products/categories":
            return self._send("categories.json")
        if p == "/api/products/summary":
            return self._send("summary.json")
        if p == "/api/products/manufacturers":
            return self._send("manufacturers.json")
        if p == "/api/datasheets":
            return self._send("datasheets.json")
        if p == "/api/v1/search":
            if qs.get("type") == ["nope"]:
                return self._send("search_400.json", 400)
            return self._send("search_motor.json")
        if p == "/api/products":
            return self._send("list_gearhead.json")
        if p == f"/api/products/{MOTOR_ID}":
            if "type" not in qs:
                return self._send("search_400.json", 400)
            return self._send("product_motor.json")
        if p == f"/api/products/{MISSING_ID}":
            # CloudFront SPA fallback: HTML with 200, exactly as prod does.
            return self._send("product_404.html", 200)
        if p == "/api/v1/relations/actuators":
            return self._send("actuators.json")
        if p == "/api/v1/relations/drives-for-motor":
            return self._send("drives_for_motor.json")
        if p == "/api/v1/relations/gearheads-for-motor":
            return self._send("gearheads_for_motor.json")
        if p == "/api/v1/compat/adjacent":
            return self._send("compat_adjacent.json")
        if p == "/api/v1/compat/check" and method == "POST":
            return self._send("compat_check.json")
        return self._send("product_404.html", 200)

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")


@pytest.fixture(scope="module")
def fixture_api_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


@pytest.fixture
def requests_log():
    _Handler.requests.clear()
    return _Handler.requests


async def _acall(base_url: str, name: str, args: dict):
    api = SpecodexApi(base_url)
    try:
        async with InMemoryTransport(build_server(api)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool(name, args)
    finally:
        await api.aclose()


def _call(base_url: str, name: str, args: dict):
    """Sync entry: one real MCP session per call, run on anyio's asyncio backend."""
    return anyio.run(_acall, base_url, name, args)


def test_lists_every_tool_as_read_only_with_schemas(fixture_api_url):
    async def _list():
        api = SpecodexApi(fixture_api_url)
        try:
            async with InMemoryTransport(build_server(api)) as (r, w):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    assert init.server_info.name == "specodex_mcp"
                    assert "specodex_search_products" in (init.instructions or "")
                    return (await s.list_tools()).tools
        finally:
            await api.aclose()

    tools = anyio.run(_list)
    names = {t.name for t in tools}
    assert names == {
        "specodex_list_product_types",
        "specodex_summary",
        "specodex_list_manufacturers",
        "specodex_search_products",
        "specodex_list_products",
        "specodex_get_product",
        "specodex_find_actuators",
        "specodex_motors_for_actuator",
        "specodex_drives_for_motor",
        "specodex_gearheads_for_motor",
        "specodex_compatible_types",
        "specodex_check_compatibility",
        "specodex_list_datasheets",
    }
    for t in tools:
        assert t.name.startswith("specodex_")
        assert t.description, t.name
        assert (
            t.annotations
            and t.annotations.read_only_hint is True
            and t.annotations.destructive_hint is False
        )
        assert t.output_schema, f"{t.name} has no structured output schema"
    search = next(t for t in tools if t.name == "specodex_search_products")
    assert set(search.input_schema["properties"]) == {
        "query",
        "product_type",
        "manufacturer",
        "where",
        "sort",
        "limit",
        "compact",
    }
    assert search.input_schema["properties"]["limit"]["maximum"] == 100


def test_search_encodes_filters_and_strips_noise(fixture_api_url, requests_log):
    res = _call(
        fixture_api_url,
        "specodex_search_products",
        {
            "product_type": "motor",
            "where": ["rated_power>=1000", "manufacturer=weg"],
            "sort": ["rated_power:desc"],
            "limit": 3,
        },
    )
    assert res.is_error is False
    method, path, qs, _ = requests_log[-1]
    assert (method, path) == ("GET", "/api/v1/search")
    assert qs["type"] == ["motor"] and qs["limit"] == ["3"]
    assert qs["where"] == [
        "rated_power>=1000",
        "manufacturer=weg",
    ]  # repeated params, not comma-joined
    assert qs["sort"] == ["rated_power:desc"]
    data = res.structured_content
    assert data["count"] == data["returned"] == len(data["products"]) == 3
    first = data["products"][0]
    assert first["product_id"] == MOTOR_ID and first["product_type"] == "motor"
    assert "PK" not in first and "pages" not in first
    expected = json.loads((FIXTURES / "search_motor.json").read_text())["data"][0]
    assert (
        first["rated_power"] == expected["rated_power"]
        and first["rated_power"]["unit"] == "W"
    )
    # Search returns a trimmed projection (no datasheet_url); the full
    # record, with the citation URL, comes from specodex_get_product.
    assert "datasheet_url" not in first


def test_search_compact_false_returns_raw_records(fixture_api_url):
    res = _call(
        fixture_api_url,
        "specodex_search_products",
        {"product_type": "motor", "compact": False},
    )
    raw = json.loads((FIXTURES / "search_motor.json").read_text())["data"][0]
    assert res.structured_content["products"][0] == raw


def test_api_validation_detail_reaches_the_model(fixture_api_url):
    res = _call(fixture_api_url, "specodex_search_products", {"product_type": "nope"})
    assert res.is_error is True
    text = res.content[0].text
    assert "Invalid query parameters" in text and "expected one of" in text


def test_get_product_sends_type_and_returns_full_record(fixture_api_url, requests_log):
    res = _call(
        fixture_api_url,
        "specodex_get_product",
        {"product_id": MOTOR_ID, "product_type": "motor"},
    )
    assert res.is_error is False
    _, path, qs, _ = requests_log[-1]
    assert path == f"/api/products/{MOTOR_ID}" and qs["type"] == ["motor"]
    assert res.structured_content["part_number"]
    assert res.structured_content["rated_speed"] == {"value": 3584, "unit": "rpm"}


def test_html_fallback_is_reported_as_not_found_not_leaked(fixture_api_url):
    res = _call(
        fixture_api_url,
        "specodex_get_product",
        {"product_id": MISSING_ID, "product_type": "motor"},
    )
    assert res.is_error is True
    text = res.content[0].text
    assert "not served as JSON" in text and "does not exist" in text
    assert "<!DOCTYPE" not in text and "<html" not in text


@pytest.mark.parametrize(
    ("name", "args", "needle"),
    [
        (
            "specodex_get_product",
            {"product_id": "../admin", "product_type": "motor"},
            "product_id must be the UUID",
        ),
        (
            "specodex_get_product",
            {"product_id": MOTOR_ID, "product_type": "Motor; DROP"},
            "product_type must be",
        ),
        ("specodex_search_products", {"where": ["rated_power >> 5"]}, "where clause"),
        (
            "specodex_search_products",
            {"where": ["rated_power>=1\nq=x"]},
            "where clause",
        ),
        ("specodex_search_products", {"sort": ["rated_power:sideways"]}, "sort key"),
        (
            "specodex_motors_for_actuator",
            {"product_id": MOTOR_ID, "product_type": "motor"},
            "linear_actuator",
        ),
        ("specodex_find_actuators", {"orientation": "diagonal"}, "orientation must be"),
        (
            "specodex_list_products",
            {"product_type": "gearhead", "cursor": "not base64url!"},
            "cursor must be",
        ),
    ],
)
def test_hostile_arguments_are_rejected_before_any_request(
    fixture_api_url, requests_log, name, args, needle
):
    res = _call(fixture_api_url, name, args)
    assert res.is_error is True
    assert needle in res.content[0].text
    assert requests_log == [], "validation must fail before the HTTP request is made"


def test_schema_bounds_are_enforced_by_the_protocol(fixture_api_url, requests_log):
    res = _call(fixture_api_url, "specodex_search_products", {"limit": 101})
    assert res.is_error is True
    assert requests_log == []


def test_relations_and_compat_round_trip(fixture_api_url, requests_log):
    res = _call(
        fixture_api_url,
        "specodex_find_actuators",
        {"min_stroke_mm": 100, "orientation": "vertical"},
    )
    assert (
        res.is_error is False
        and res.structured_content["count"]
        == len(res.structured_content["actuators"])
        > 0
    )
    _, _, qs, _ = requests_log[-1]
    assert qs == {"min_stroke_mm": ["100.0"], "orientation": ["vertical"]} or qs == {
        "min_stroke_mm": ["100"],
        "orientation": ["vertical"],
    }

    res = _call(
        fixture_api_url,
        "specodex_check_compatibility",
        {"a_id": MOTOR_ID, "a_type": "motor", "b_id": DRIVE_ID, "b_type": "drive"},
    )
    assert res.is_error is False
    method, path, _, body = requests_log[-1]
    assert (method, path) == ("POST", "/api/v1/compat/check")
    assert json.loads(body) == {
        "a": {"id": MOTOR_ID, "type": "motor"},
        "b": {"id": DRIVE_ID, "type": "drive"},
    }
    assert res.structured_content["status"] in ("ok", "partial")
    assert res.structured_content["results"][0]["checks"][0]["field"]

    res = _call(fixture_api_url, "specodex_compatible_types", {"product_type": "motor"})
    assert res.structured_content == {
        "product_type": "motor",
        "adjacent": ["drive", "gearhead"],
    }

    res = _call(fixture_api_url, "specodex_drives_for_motor", {"product_id": MOTOR_ID})
    assert res.is_error is False and res.structured_content == {
        "count": 0,
        "drives": [],
    }


def test_catalog_lookups(fixture_api_url):
    res = _call(fixture_api_url, "specodex_list_product_types", {})
    types = {row["type"]: row for row in res.structured_content["product_types"]}
    assert {"motor", "drive", "gearhead"} <= set(types)
    assert types["drive"]["display_name"] == "Drives" and types["drive"]["count"] > 0

    res = _call(fixture_api_url, "specodex_summary", {})
    assert res.structured_content["total"] == 35595

    res = _call(fixture_api_url, "specodex_list_manufacturers", {})
    assert "ABB" in res.structured_content["manufacturers"]
    assert res.structured_content["count"] == len(
        res.structured_content["manufacturers"]
    )

    res = _call(
        fixture_api_url,
        "specodex_list_products",
        {"product_type": "gearhead", "limit": 2},
    )
    assert (
        res.structured_content["returned"] == 2 and "cursor" in res.structured_content
    )


def test_unreachable_api_is_an_actionable_error():
    async def _go():
        api = SpecodexApi("http://127.0.0.1:9")  # discard port: connection refused fast
        try:
            with pytest.raises(SpecodexApiError, match="could not reach"):
                await api.summary()
        finally:
            await api.aclose()

    anyio.run(_go)
