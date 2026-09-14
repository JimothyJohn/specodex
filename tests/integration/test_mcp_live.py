"""Live contract test: the MCP server against the real public API.

Deselected in CI (`-m "not live"`); run by hand when the API changes:
    uv run pytest tests/integration/test_mcp_live.py -m live -v
Pins the response shapes the offline fixtures under
tests/unit/fixtures/mcp/ were captured from, so drift shows up here
first.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from specodex.mcp.api import SpecodexApi
from specodex.mcp.server import build_server

pytestmark = [pytest.mark.live, pytest.mark.integration]


def test_search_then_detail_against_production():
    anyio.run(_search_then_detail)


async def _search_then_detail():
    api = SpecodexApi()  # SPECODEX_API_URL or https://www.specodex.com
    try:
        async with InMemoryTransport(build_server(api)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                types = await s.call_tool("specodex_list_product_types", {})
                assert types.is_error is False
                names = {
                    row["type"] for row in types.structured_content["product_types"]
                }
                assert "motor" in names

                found = await s.call_tool(
                    "specodex_search_products",
                    {
                        "product_type": "motor",
                        "where": ["rated_power>=1000"],
                        "sort": ["rated_power:desc"],
                        "limit": 2,
                    },
                )
                assert found.is_error is False
                products = found.structured_content["products"]
                assert products and all(p["product_type"] == "motor" for p in products)
                assert (
                    products[0]["rated_power"]["value"]
                    >= products[-1]["rated_power"]["value"]
                    >= 1000
                )

                one = await s.call_tool(
                    "specodex_get_product",
                    {"product_id": products[0]["product_id"], "product_type": "motor"},
                )
                assert one.is_error is False
                assert (
                    one.structured_content["part_number"] == products[0]["part_number"]
                )

                missing = await s.call_tool(
                    "specodex_get_product",
                    {
                        "product_id": "00000000-0000-0000-0000-000000000000",
                        "product_type": "motor",
                    },
                )
                assert (
                    missing.is_error is True and "<html" not in missing.content[0].text
                )
    finally:
        await api.aclose()
