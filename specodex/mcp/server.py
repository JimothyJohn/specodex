"""Specodex MCP server: read-only catalog tools over the public API.

Every tool is a thin wrapper around one endpoint the web UI already
calls, keyed on the same product_type / product_id vocabulary, so an
agent can move between search, detail, and compatibility calls without
translating identifiers. All tools are read-only, idempotent, and
network-bound (open-world).

Run: ``uv run specodex-mcp`` (stdio). Base URL: ``SPECODEX_API_URL``
(default https://www.specodex.com).
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from specodex.mcp.api import SpecodexApi, SpecodexApiError, compact_product

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])

SERVER_NAME = "specodex_mcp"

INSTRUCTIONS = """Specodex is a catalog of industrial motion-control products
(motors, drives, gearheads, electric cylinders, linear actuators, robot
arms, contactors) with specs extracted from manufacturer datasheets.

Typical flow: specodex_list_product_types → specodex_search_products
(filter with where clauses like 'rated_power>=1000', sort with
'rated_power:desc') → specodex_get_product for the full record →
specodex_drives_for_motor / specodex_gearheads_for_motor /
specodex_check_compatibility to build a kit. Spec values are
{value, unit} or {min, max, unit} objects; units are as published.
Search results are a trimmed projection; the full record from
specodex_get_product carries datasheet_url so claims can be cited."""

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def _anticipated(fn: _F) -> _F:
    """Re-raise SpecodexApiError as the SDK's ToolError.

    MCPServer forwards a ToolError's message to the client verbatim and
    logs it at INFO; any other exception is treated as a crash — the
    model sees only "Error executing tool <name>" and the real reason
    (bad product_id, unknown type, the API's own validation detail)
    lands in a server-side traceback nobody reads. Every failure this
    package raises is anticipated and actionable, so it goes through
    here.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except SpecodexApiError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def build_server(api: SpecodexApi | None = None) -> MCPServer:
    """Construct the server. ``api`` is injectable for tests."""
    client = api or SpecodexApi()
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.tool(
        name="specodex_list_product_types",
        title="List product types",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def list_product_types() -> dict[str, Any]:
        """List the product types in the catalog with the row count and display name of each.

        Call this first: the `type` strings it returns are the values every
        other tool's `product_type` argument accepts.
        """
        return {"product_types": await client.product_types()}

    @server.tool(
        name="specodex_summary", title="Catalog summary", annotations=_READ_ONLY
    )
    @_anticipated
    async def summary() -> dict[str, Any]:
        """Total row count and per-type counts for the whole catalog."""
        return await client.summary()

    @server.tool(
        name="specodex_list_manufacturers",
        title="List manufacturers",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def list_manufacturers() -> dict[str, Any]:
        """All manufacturer names present in the catalog, exactly as stored.

        Use a returned name (case-insensitive substring match) as the
        `manufacturer` argument of specodex_search_products.
        """
        names = await client.manufacturers()
        return {"count": len(names), "manufacturers": names}

    @server.tool(
        name="specodex_search_products", title="Search products", annotations=_READ_ONLY
    )
    @_anticipated
    async def search_products(
        query: Annotated[
            str | None,
            Field(
                description="Free-text match on part number, product name, manufacturer and family.",
                max_length=200,
            ),
        ] = None,
        product_type: Annotated[
            str | None,
            Field(
                description="Restrict to one type, e.g. 'motor', 'drive', 'gearhead'. Omit to search every type."
            ),
        ] = None,
        manufacturer: Annotated[
            str | None,
            Field(
                description="Case-insensitive substring match on manufacturer, e.g. 'siemens'.",
                max_length=200,
            ),
        ] = None,
        where: Annotated[
            list[str] | None,
            Field(
                description="Spec filters, one per clause: 'field<op>value' with op in >=, <=, !=, >, <, =. Numeric fields compare numerically ('rated_power>=1000', 'rated_speed<3000'); strings match as substrings ('frame_size=nema 23'). Nested fields use dots ('rated_torque.value>5').",
                max_length=10,
            ),
        ] = None,
        sort: Annotated[
            list[str] | None,
            Field(
                description="Sort keys in priority order: 'field' or 'field:asc' / 'field:desc', e.g. ['rated_power:desc']. Nulls sort last.",
                max_length=5,
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum products to return (1-100).", ge=1, le=100)
        ] = 20,
        compact: Annotated[
            bool,
            Field(
                description="Strip storage keys and price-estimate comparable lists from each product (default). Set false for the raw records."
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Search the catalog by text, spec filters and sort order.

        Returns up to `limit` products as a trimmed projection: product_id,
        product_type, manufacturer, product_name, part_number and the
        headline specs as {value, unit} / {min, max, unit} objects. Use
        product_id + product_type with specodex_get_product for the full
        record (including datasheet_url) and with the relations tools.
        """
        result = await client.search(
            query=query,
            product_type=product_type,
            manufacturer=manufacturer,
            where=where,
            sort=sort,
            limit=limit,
        )
        products = (
            [compact_product(p) for p in result["products"]]
            if compact
            else result["products"]
        )
        return {
            "count": result["count"],
            "returned": len(products),
            "products": products,
        }

    @server.tool(
        name="specodex_list_products",
        title="List products (paged)",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def list_products(
        product_type: Annotated[
            str, Field(description="Product type to page through, e.g. 'gearhead'.")
        ],
        limit: Annotated[
            int, Field(description="Page size (1-2000).", ge=1, le=2000)
        ] = 100,
        cursor: Annotated[
            str | None,
            Field(
                description="Opaque `cursor` from the previous page; omit for the first page."
            ),
        ] = None,
        compact: Annotated[
            bool,
            Field(
                description="Strip storage keys and price-estimate comparable lists (default)."
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Page through every product of one type, unfiltered, in storage order.

        For bulk export or when a search would need more than 100 rows.
        `cursor` is null on the last page.
        """
        result = await client.list_products(
            product_type=product_type, limit=limit, cursor=cursor
        )
        products = (
            [compact_product(p) for p in result["products"]]
            if compact
            else result["products"]
        )
        return {
            "returned": len(products),
            "cursor": result["cursor"],
            "products": products,
        }

    @server.tool(
        name="specodex_get_product", title="Get product", annotations=_READ_ONLY
    )
    @_anticipated
    async def get_product(
        product_id: Annotated[
            str, Field(description="UUID from a search or list result.")
        ],
        product_type: Annotated[
            str,
            Field(
                description="The product's type, e.g. 'motor' (the storage key needs both)."
            ),
        ],
    ) -> dict[str, Any]:
        """Fetch one product's full record, untrimmed, including price_estimate provenance."""
        return await client.get_product(
            product_id=product_id, product_type=product_type
        )

    @server.tool(
        name="specodex_find_actuators",
        title="Find actuators by requirement",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def find_actuators(
        min_stroke_mm: Annotated[
            float | None, Field(description="Minimum stroke in mm.", ge=0)
        ] = None,
        min_peak_force_n: Annotated[
            float | None, Field(description="Minimum peak force in N.", ge=0)
        ] = None,
        min_peak_velocity_mm_s: Annotated[
            float | None, Field(description="Minimum peak velocity in mm/s.", ge=0)
        ] = None,
        min_duty_cycle: Annotated[
            float | None,
            Field(description="Minimum duty cycle as a fraction 0-1.", ge=0, le=1),
        ] = None,
        orientation: Annotated[
            str | None, Field(description="'horizontal' or 'vertical' mounting.")
        ] = None,
    ) -> dict[str, Any]:
        """Linear actuators and electric cylinders meeting motion requirements (stroke, force, velocity, duty, orientation)."""
        rows = await client.actuators(
            min_stroke_mm=min_stroke_mm,
            min_peak_force_n=min_peak_force_n,
            min_peak_velocity_mm_s=min_peak_velocity_mm_s,
            min_duty_cycle=min_duty_cycle,
            orientation=orientation,
        )
        return {"count": len(rows), "actuators": [compact_product(r) for r in rows]}

    @server.tool(
        name="specodex_motors_for_actuator",
        title="Motors for an actuator",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def motors_for_actuator(
        product_id: Annotated[str, Field(description="Actuator UUID.")],
        product_type: Annotated[
            str, Field(description="'linear_actuator' or 'electric_cylinder'.")
        ],
    ) -> dict[str, Any]:
        """Motors whose shaft, torque and speed suit the given actuator or cylinder."""
        rows = await client.motors_for_actuator(
            product_id=product_id, product_type=product_type
        )
        return {"count": len(rows), "motors": [compact_product(r) for r in rows]}

    @server.tool(
        name="specodex_drives_for_motor",
        title="Drives for a motor",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def drives_for_motor(
        product_id: Annotated[str, Field(description="Motor UUID.")],
    ) -> dict[str, Any]:
        """Drives compatible with the given motor (voltage, current, encoder protocol)."""
        rows = await client.drives_for_motor(product_id=product_id)
        return {"count": len(rows), "drives": [compact_product(r) for r in rows]}

    @server.tool(
        name="specodex_gearheads_for_motor",
        title="Gearheads for a motor",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def gearheads_for_motor(
        product_id: Annotated[str, Field(description="Motor UUID.")],
    ) -> dict[str, Any]:
        """Gearheads whose input flange, torque and speed ratings accept the given motor."""
        rows = await client.gearheads_for_motor(product_id=product_id)
        return {"count": len(rows), "gearheads": [compact_product(r) for r in rows]}

    @server.tool(
        name="specodex_compatible_types",
        title="Adjacent product types",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def compatible_types(
        product_type: Annotated[
            str, Field(description="A product type, e.g. 'motor'.")
        ],
    ) -> dict[str, Any]:
        """Which product types can be compatibility-checked against the given one (e.g. motor → drive, gearhead)."""
        return {
            "product_type": product_type,
            "adjacent": await client.compat_adjacent(product_type=product_type),
        }

    @server.tool(
        name="specodex_check_compatibility",
        title="Check compatibility",
        annotations=_READ_ONLY,
    )
    @_anticipated
    async def check_compatibility(
        a_id: Annotated[str, Field(description="First product UUID.")],
        a_type: Annotated[str, Field(description="First product's type.")],
        b_id: Annotated[str, Field(description="Second product UUID.")],
        b_type: Annotated[str, Field(description="Second product's type.")],
    ) -> dict[str, Any]:
        """Field-by-field compatibility report for a supported pair (drive↔motor, motor↔gearhead).

        Status is 'ok' or 'partial'; each check names the field, the
        demand vs. supply values, and why it did or did not line up.
        """
        return await client.compat_check(
            a_id=a_id, a_type=a_type, b_id=b_id, b_type=b_type
        )

    @server.tool(
        name="specodex_list_datasheets", title="List datasheets", annotations=_READ_ONLY
    )
    @_anticipated
    async def list_datasheets() -> dict[str, Any]:
        """Datasheet upload records visible to the public API (often empty)."""
        rows = await client.datasheets()
        return {"count": len(rows), "datasheets": rows}

    return server


def main() -> None:
    """Console entry point: serve over stdio. Logs go to stderr only."""
    print(f"specodex-mcp: serving {SpecodexApi().base_url} over stdio", file=sys.stderr)
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
