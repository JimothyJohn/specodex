---
paths:
  - "specodex/**"
  - "cli/**"
  - "data/vendors/**"
  - "scripts/gen_vendor_data.py"
---

# Pipeline, enrichment CLIs, and the MCP server

The one-line rules in `CLAUDE.md` always apply. This loads when you touch the Python library or CLI.

## Architecture

PDF → **page finder** (text heuristic, free) → **LLM extraction** (Gemini 2.5 Flash) → **Pydantic validation** → **quality gate** → **DynamoDB write**

- `specodex/page_finder.py` — `find_spec_pages_by_text` identifies spec-table pages by keyword heuristic without an API call (`SPEC_KEYWORDS` has 20 groups covering electronics, mechanics, mechatronics, switching devices, linear actuation, rotary/gearing, robotics, sensors, environmental, certifications). `find_spec_pages_scored` is the density-ranked, capped variant. Falls back to Gemini Flash for image-based classification. `find_drawing_pages_by_text` / `_score_drawing_page` / `find_drawing_pages_scored` are the dimensional-drawing twins used by `mounting-extract`.
- `specodex/llm.py` — Gemini extraction, the sole LLM path for product extraction.
- `specodex/models/llm_schema.py` — `to_gemini_schema` builds the uppercase OpenAPI-subset schema Gemini accepts via `response_schema`.
- `specodex/utils.py` — `parse_gemini_response` maps raw LLM JSON through `common.py` BeforeValidators into structured `ValueUnit` / `MinMaxUnit` instances (`{value, unit}` / `{min, max, unit}` dicts on serialisation — the same shape Gemini emits, DynamoDB stores, and the frontend consumes).
- `specodex/schemagen/` — proposes new Pydantic models from PDFs (also Gemini, via `response_schema=ProposedModel`); `renderer.py` emits the Python source and `meta_schema.py` gates identifiers so the LLM never writes executable Python.
- `specodex/pricing/extract.py` — price extraction cascade; the LLM last resort is Gemini 2.5 Flash.
- `specodex/quality.py` — scores completeness, rejects below threshold. `scraper.process_datasheet` writes `passed_models` (the quality-filtered list) via `batch_create`; the filter is the source of truth.
- `specodex/models/` — Pydantic models per product type: `drive.py`, `motor.py`, `gearhead.py`, `robot_arm.py`, `electric_cylinder.py`, `linear_actuator.py`, `contactor.py`, `inductive_sensor.py`. New types are auto-discovered by `specodex/config.py:_discover_schema_models` — drop a file here and `SCHEMA_CHOICES[product_type]` is populated at import time.

Single provider: everything uses `GEMINI_API_KEY`; the model id is pinned in `specodex/config.py` (`MODEL = "gemini-2.5-flash"`).

**Never feed a raw multi-hundred-page PDF to the LLM.** Route through `page_finder` first — `find_spec_pages_by_text` (free) or `find_spec_pages_scored` (density-ranked, capped). The scraper's bundled path tops out around 30 pages before Gemini truncates the JSON mid-string; `scraper.process_datasheet` auto-switches to per-page extraction when `pages <= MAX_PER_PAGE_CALLS` (env-overridable, default 30), so pass an explicit `pages=` list when ingesting big catalogs.

## Enrichment and data-movement commands

All run through `./Quickstart <cmd>`; `./Quickstart <cmd> --help` for flags. Dry-run is the default everywhere; `--apply` writes on dev/staging only and refuses prod.

- `process` — drain the S3 upload queue (`cli/processor.py`; its module docstring explains the queue layout). This is how a local S3 upload populates dev DynamoDB.
- `admin <sub>` — `blacklist list|add|remove`, `diff`, `promote`, `demote`, `purge`, `audit-units` (`cli/admin.py` docstring). `promote --source dev --target prod` is the only path to prod data and is Nick's.
- `price-enrich` — backfill MSRP on existing products (web/Serper/LLM tiers, each capped by a flag).
- `price-infer` — deterministic price estimates from DB comparables (price-comps-v1: same-family log-log ladder, KNN fallback). `--lead-times` mode instead applies published vendor lead-time statements (registry `lead_time_statement` facts) as `lead_time_estimate` — citations carried verbatim, confidence `"medium"`, no invented lead times ever. See `todo/COMMERCIAL.md` Phase 2.
- `price-book PB` — bulk MSRP backfill from a public price book (XLSX or PDF, path or URL). Dry-run first: `--manufacturer <substr> --dry-run` prints the join table. See `todo/PRICING.md` Phase 1.
- `mounting-extract` — extract motor mounting-flange dimensions (bolt circle diameter, pilot/register diameter, shaft length, shaft modification, mounting standard) from dimensional-drawing pages — a different page set than the cached spec-table pages — and backfill onto existing Motor rows. Enrich-only: skips rows that already carry `mounting_flange`. `--max-pages` caps the drawing pages (clamped at 0; a negative cap once expanded the set). Local skill: `.claude/skills/mounting-dimension-extract` (gitignored, local-only).
- `ingest-report` — group ingest-log quality-fails by manufacturer for vendor outreach; `--email-template` emits a ready-to-send body per manufacturer.
- `vendor-facts validate|verify` — citation-required vendor-facts registry (`data/vendors/*.json`, loader `specodex/vendors/registry.py`). `validate` loads every profile and exits 1 on any fact without a valid citation; `verify` re-fetches each cited URL (SSRF-checked, robots-respecting) and re-checks the verbatim excerpt — the replicability audit from `todo/COMMERCIAL.md` Phase 4. After editing `data/vendors/`, rerun `scripts/gen_vendor_data.py` and commit the regenerated `app/frontend/src/data/vendors.json`.
- `configurator` — harvest a vendor's JS configurator through its JSON API from a headless browser (`todo/CONFIGURATOR_HARVEST.md`); live subcommands need network and Playwright Chromium.
- `availability-enrich`, `audit-dedupes`, `units-triage`, `godmode`, `growth preflight`, `build-backend-py` — see the `cli/quickstart.py` docstring.

## MCP server

`specodex/mcp/` exposes the public catalog API as MCP tools so an LLM client (Claude Code, Claude Desktop, anything speaking MCP) can query the same data the web UI shows. It is a thin, read-only HTTP client: no DynamoDB credentials, no Gemini key — anything that can reach `https://www.specodex.com` can run it.

    uv run specodex-mcp                                   # stdio, against prod
    SPECODEX_API_URL=https://d1acboh655kvrt.cloudfront.net uv run specodex-mcp   # staging
    claude mcp add specodex -- uv run --directory /Users/nick/github/specodex specodex-mcp

Tools (all `specodex_*`, all read-only): `list_product_types`, `summary`, `list_manufacturers`, `search_products` (text + `where` clauses like `rated_power>=1000` + `sort` like `rated_power:desc`, limit ≤ 100), `list_products` (cursor-paged, ≤ 2000/page), `get_product`, `find_actuators`, `motors_for_actuator`, `drives_for_motor`, `gearheads_for_motor`, `compatible_types`, `check_compatibility`, `list_datasheets`. Search/list results are "compact" by default (storage keys and price-estimate comparable lists stripped); `compact: false` returns raw records.

Things the client knows that a naive wrapper would not:

- **CloudFront masks API 404s as HTML 200** (SPA fallback). A missing product, or an endpoint that prod (`master`) does not serve yet because it is behind `dev`, arrives as `text/html`. The client turns any non-JSON body into an actionable not-found error instead of handing HTML to the model.
- **Anticipated failures must be `ToolError`.** mcp 2.x forwards only `ToolError` messages to the client; any other exception reaches the model as a bare "Error executing tool X" and the reason goes to a server-side traceback. `server.py:_anticipated` does the mapping.
- **Every argument is validated before it becomes URL** (UUID product ids, snake_case types, `field<op>value` where clauses, `field[:dir]` sort keys, bounded limits, opaque cursors). Property tests in `tests/unit/test_mcp_api_property.py` pin that; the protocol-level contract (real MCP client + a local HTTP server serving responses captured from prod) is `tests/unit/test_mcp_server.py`; `tests/integration/test_mcp_live.py` (`-m live`) hits prod for real.
- **The SDK is `mcp` 2.x** (`MCPServer` from `mcp.server.mcpserver`, snake_case result attributes such as `is_error` / `structured_content`). The 1.x `FastMCP` import path does not exist; don't paste 1.x examples.
