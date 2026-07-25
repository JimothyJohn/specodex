# CONFIGURATOR_HARVEST — pull specs + part numbers from vendors' online configurators

> 📐 planned — spike proven against Stober (2026-07-25). No code merged yet.

## Why

Today every product row comes from a **PDF catalog** through
`page_finder → Gemini → Pydantic → quality gate`. That path is lossy
(LLM extraction error, only whatever the datasheet tabulated) and
expensive (Gemini tokens per page).

Many industrial vendors also ship an **online JS configurator** — a
sizing/selection tool that is a thin SPA over a structured JSON API.
That API is a *higher-fidelity, LLM-free* data source:

- Values arrive already **typed with units** (`{value, unit}` shaped,
  the same shape our `ValueUnit`/`MinMaxUnit` models store).
- The vendor's own **sizing engine** resolves requirements → part
  number and the exact **order code** — no synthesis, no guessing.
- The full **valid-configuration space** is enumerable, not just the
  variants a catalog happened to print.

Two uses:

1. **New products without catalogs** — ingest a vendor we have no
   PDF for by walking its configurator.
2. **Cross-reference** — join configurator data to catalog-extracted
   DynamoDB rows by part number/family and flag disagreements. The
   configurator is generally ground-truth (it's the vendor's own
   engine), so a mismatch is a signal our PDF extraction drifted.

This is **per-vendor adapter work, not a universal scraper** — see
"Why it stays per-vendor" below. It does *not* replace the LLM
pipeline; it's a second, exact source for the subset of vendors that
expose a configurator.

## Proven feasibility (Stober spike, 2026-07-25)

`configurator.stober.com` is an Angular SPA over a same-origin REST
API at `/api/{locale}/…` (`API_BASE_URL = origin + "/api/"`, `shop`
is a path segment, e.g. `SDI`). Findings:

- **Anonymous GET works** directly (curl): `GET /api/en-US/ProductSelection/SDI`
  returns the product-type list as JSON.
- **POST is dropped at the edge for non-browser clients** (WAF). The
  fix: drive **headless Chromium (Playwright)**, load the SPA to get a
  real session, then call the API with **in-page `fetch()`** — the
  request is indistinguishable from the SPA's own traffic and passes.
- The navigation tree and requirement schema are fully readable:

      GET  /api/{loc}/ProductSelection/{shop}                          -> product types
      POST /api/{loc}/ProductSelection/{type}/{shop}                   -> product groups + prefilters
      POST /api/{loc}/ProductSelection/{type}/{group}/{shop}           -> select group -> configurationId + filters
      POST /api/{loc}/ProductSelection/{type}/{group}/Series/{shop}    -> series[] each with bestMatchProduct slot
      POST .../Series/{seriesId}/Products/{shop}   {filterValues}      -> concrete products
      POST /api/{loc}/Configuration/Set            {characteristics}   -> apply requirements
      GET  /api/{loc}/Configuration/Summary                            -> configurationStrip == the order code

- **Requirements schema** (servo gearboxes) came back typed with units
  and ranges — e.g. `ig` gear ratio [1.97, 1762.58], `Macc`
  acceleration torque [16.6, 43000] Nm, `Fr` radial force [0, 80000] N,
  `Phi` backlash [1, 25] arcmin, `n1zb` input speed [2600, 8000] rpm,
  plus enumerated dropdowns (gearbox design, shaft, motor adapter).
- **Reverse lookup is a first-class server feature** (`bestMatchBy` /
  `bestMatchProduct`): POST `{filterValues:[{name, value1}]}` and the
  API returns the closest product per series. Filter state accumulates
  in the session `configurationId`.

Spike artifacts (scratchpad, not committed): `stober_probe.sh`,
`stober_loop.mjs`, `stober_bestmatch.mjs`, endpoint map.

## Design

### Where it lives

Stack is Python, so use **playwright-python** (`uv add --group dev
playwright`) rather than a Node harness — keeps everything in one
language and reuses our Pydantic models directly.

    specodex/configurators/
        base.py          # ConfiguratorClient: browser session, in-page fetch, retry, rate-limit, robots check
        stober.py        # first adapter
        <vendor>.py      # one module per vendor
    cli/configurator.py  # ./Quickstart configurator <vendor> ... (recon | walk | harvest | crossref)

Reuse existing discipline: the **vendor-facts registry**
(`specodex/vendors/registry.py`) already does SSRF checks + robots
respect + citation capture — lift that into the client so every
fetched value carries a source URL + timestamp.

### Two directions

- **Forward (requirements → part number):** POST filter values /
  characteristics, read back `bestMatchProduct` + `configurationStrip`
  (order code). This is the sizing use case and the `/actuators`-page
  cross-check.
- **Inverse (part number → specs):** enumerate the valid-configuration
  space (walk series → products with empty filters), or decode an
  existing order code via the config `Load`/`Search` endpoints. This
  is the bulk-ingest use case.

### Normalize → Pydantic

The configurator parameter schema (filterId + unit + value) maps
straight onto `ValueUnit`/`MinMaxUnit` — **no LLM**. Each adapter owns
a `param → model field` mapping table (the one bespoke bit). Output is
a standard product model instance, so it flows into the existing DB
write / bench `expected/` fixtures unchanged. Add a `source`
provenance field (`configurator:<vendor>` vs `catalog`) so
cross-reference can tell them apart.

### Cross-reference

Join configurator rows to DynamoDB rows by `product_id` / family.
Feed disagreements through the existing `spec_rules.py` +
`quality.py` + triage loop. A configurator/catalog mismatch on a
numeric field beyond tolerance → triage item ("PDF extraction likely
wrong, configurator says X").

## Phases (independently shippable PRs)

- **P0 — `ConfiguratorClient` base + Stober recon adapter.** Playwright
  session bootstrap, in-page fetch, robots/rate-limit, endpoint-map
  dump. `./Quickstart configurator stober recon`. Ships the spike as
  real code + a drift test that re-derives the endpoint map (fails when
  Stober redeploys and the routes move).
- **P1 — Stober forward path.** Requirements → `bestMatchProduct` +
  order code. Property test on the filter-payload builder (adversarial
  values: out-of-range, NaN, unit mismatch). Cross-check against one
  `/actuators` synthesized part number.
- **P2 — Stober inverse path + normalize.** Walk the full servo-gearbox
  space → Pydantic `Gearhead` instances with `source=configurator:stober`.
  Dry-run DB diff against existing Stober rows; no writes outside dev.
- **P3 — Cross-reference report.** `./Quickstart configurator crossref
  <vendor>` emits the configurator-vs-catalog disagreement table into
  the triage/ingest-report flow.
- **P4 — Second vendor.** Prove the base client generalizes (pick a
  different framework — a React/GraphQL configurator — to stress the
  abstraction). Only then is the `base.py` API trustworthy.

## Why it stays per-vendor (the honest limit)

- Each configurator is a different SPA (framework, routes, payload
  shapes, auth). No universal scraper — the value is a **shared
  harness** (`base.py`) + a **thin adapter** per vendor (~endpoint map
  + param→field table).
- Bot/WAF protection means the browser-session path is mandatory;
  some deeper endpoints (quote/pricing) sit behind vendor auth (Stober
  carries MSAL/Azure AD) and are out of scope.
- Configurators change without notice → every adapter needs a drift
  test, same as vendor-facts `verify`.
- **ToS/robots:** respect robots.txt, rate-limit hard, cache
  aggressively, identify the client. Reuse the vendor-facts
  replicability discipline. Treat any vendor that disallows it as
  out of scope.

## Trigger conditions (add to todo/README.md)

Files: `specodex/configurators/**`, `cli/configurator.py`; user asks
about "configurator harvest", "scrape a configurator", "reverse a part
number from a vendor tool", "cross-reference catalog vs configurator".
