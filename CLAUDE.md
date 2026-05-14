# Datasheetminer

A UI and API that sorts and filters industrial product specs from a database, plus an autonomous agent that scrapes datasheets online. Stupid simple on purpose.

## Entry point

Everything goes through `./Quickstart <command>`. It's a bash shim that delegates to `cli/quickstart.py`. Available commands:

    ./Quickstart dev              Local dev servers (default)
    ./Quickstart test             Unit tests only (fast feedback during dev)
    ./Quickstart verify           Pre-push gate: lint + tests + build (alias: ci).
                                  Mirrors .github/workflows/ci.yml exactly. Run this
                                  before pushing — green here means CI will be green.
                                  --only python|backend|frontend  Run one stage
    ./Quickstart staging [URL]    Staging contract tests
    ./Quickstart deploy [--stage] Deploy to AWS via CDK
    ./Quickstart smoke [URL]      Post-deploy smoke tests
    ./Quickstart process          Process S3 upload queue
    ./Quickstart admin <sub>      Blacklist, data movement, purge
    ./Quickstart bench            Benchmark the ingress pipeline
    ./Quickstart schemagen PDF... --type NAME
                                  Propose a new Pydantic product model from one
                                  or more datasheets. Multi-source is preferred:
                                  pass 3-5 vendors' PDFs so the LLM generalizes
                                  instead of tuning to one catalog's quirks.
                                  Writes <type>.py + <type>.md (reasoning doc
                                  with source citations).
    ./Quickstart price-enrich     Backfill MSRP on existing products
    ./Quickstart ingest-report    Group ingest-log quality-fails by manufacturer
                                  for vendor outreach. --email-template emits
                                  a ready-to-send email body per manufacturer.
    ./Quickstart gen-types        Regenerate app/frontend/src/types/generated.ts
                                  from the Pydantic models under specodex/models/.
                                  Single source of truth for the Python ↔ TypeScript
                                  contract — see "Type generation" below.

All CLI modules live in `cli/`. Quickstart is the single entry point — don't run `python -m cli.foo` in docs or scripts unless there's a reason.

## Type generation (Pydantic → TypeScript)

The TypeScript types in the frontend are **generated** from the Pydantic
models, not hand-written. `./Quickstart gen-types` runs `pydantic2ts`
against every concrete `BaseModel` under `specodex/models/*.py` and writes
`app/frontend/src/types/generated.ts`. The script is `scripts/gen_types.py`;
it shells out to `npx json-schema-to-typescript`, so Node 18+ must be on PATH
(it already is for `./Quickstart dev`).

**When to run it.** After any edit to:
- `specodex/models/*.py` (new field, renamed field, new model)
- `specodex/models/common.py` (`ProductType` literal, unit families,
  `ValueUnit` / `MinMaxUnit` shape)

CI re-runs `./Quickstart gen-types` and fails the build if
`app/frontend/src/types/generated.ts` drifts from source — i.e., the
generated file is committed and treated as part of the diff, not as a
build artifact. The check lives in the `test-codegen` job in
`.github/workflows/ci.yml` and gates `deploy-staging`.

**Don't hand-edit generated files.** The banner comment at the top of
`generated.ts` says so. If the generated TS is wrong, fix the Pydantic
model and re-generate; never patch the TS.

**Background and roadmap.** This is Phase 0 of the Python-backend
migration plan in `todo/PYTHON_BACKEND.md`, which retires the hand-synced
TypeScript layer in `app/backend/`. Phase 0a (re-route the existing TS
imports through `generated.ts`) and Phase 0b (collapse the search Zod
enum onto the generated types) follow.

## Pipeline architecture

PDF → **page finder** (text heuristic, free) → **LLM extraction** (Gemini 2.5 Flash) → **Pydantic validation** → **quality gate** → **DynamoDB write**

- `specodex/page_finder.py` — text keyword heuristic (`find_spec_pages_by_text`) identifies spec-table pages without an API call. Falls back to Gemini Flash for image-based classification.
- `specodex/llm.py` — Gemini extraction (sole LLM path for product extraction).
- `specodex/models/llm_schema.py` — `to_gemini_schema` builds the uppercase OpenAPI-subset schema Gemini accepts via `response_schema`.
- `specodex/utils.py` — `parse_gemini_response` maps raw LLM JSON through `common.py` BeforeValidators into structured `ValueUnit` / `MinMaxUnit` instances (`{value, unit}` / `{min, max, unit}` dicts on serialisation — same shape Gemini emits, DynamoDB stores, and the frontend consumes).
- `specodex/schemagen/` — proposes new Pydantic models from a PDF (also Gemini, via `response_schema=ProposedModel`).
- `specodex/pricing/extract.py` — price extraction cascade; LLM last-resort uses Gemini 2.5 Flash.
- `specodex/quality.py` — scores completeness, rejects below threshold.
- `specodex/models/` — Pydantic models per product type: `drive.py`, `motor.py`, `gearhead.py`, `robot_arm.py`, `electric_cylinder.py`, `contactor.py`. New types get auto-discovered via `specodex/config.py:_discover_schema_models` — drop a file here and `SCHEMA_CHOICES[product_type]` is populated at import time.

Single provider: everything uses `GEMINI_API_KEY`. Model id is pinned in `specodex/config.py` (`MODEL = "gemini-2.5-flash"`).

**Rule: never feed a raw multi-hundred-page PDF to the LLM.** Always route through `page_finder` first — either `find_spec_pages_by_text` (free) or `find_spec_pages_scored` (density-ranked, capped). The scraper's bundled path tops out around 30 pages before Gemini truncates the JSON mid-string; `scraper.process_datasheet` auto-switches to per-page extraction when `pages <= MAX_PER_PAGE_CALLS`, so pass an explicit `pages=` list when ingesting big catalogs.

## Adding a new product type

The Python side auto-discovers. MODELGEN Phases 0b (shipped 2026-05-03)
and 0a-ii (shipped 2026-05-07) collapsed the backend allowlist + Zod
enum and the frontend `models.ts` mirror onto the codegen output —
all four of those hand-edits are gone. The frontend's `models.ts` is
now a thin re-export shim from `generated.ts`.

1. `specodex/models/<type>.py` — Pydantic model inheriting `ProductBase`, with `product_type: Literal["<type>"] = "<type>"`.
2. `specodex/models/common.py` — add `"<type>"` to the `ProductType` literal.
3. `./Quickstart gen-types` — regenerates `app/frontend/src/types/generated.ts` and the backend `generated_constants.ts` twin. The new type auto-flows into `VALID_PRODUCT_TYPES` (backend), the search Zod enum, the frontend `Product` union, and the frontend `ProductType` literal — no hand-edits needed.
4. `app/backend/src/types/models.ts` — still hand-typed, **for now**. Add a `<Type>` interface + include it in the `Product` and `ProductType` unions. Goes away with the Express deletion in `todo/PYTHON_BACKEND.md` Phase 3 (see `todo/MODELGEN.md` "Don't migrate `app/backend/src/types/models.ts`" — wasted work to migrate before deletion).
5. **Refresh the schema-compat snapshot.** `tests/unit/test_schema_compat.py` keeps a frozen JSON fixture per `ProductType` under `tests/unit/fixtures/schema_snapshots/`. After steps 1–2 land, regenerate with `uv run python -c 'from tests.unit.test_schema_compat import refresh_snapshots; refresh_snapshots()'` and commit the new file — without this the `TestSnapshotsExist` drift gate fails on the first run. Refresh is also required when intentionally renaming/dropping a model field (the test exists to surface that exact decision).

> **Heads up:** Phase 0b retired the old steps 3 (`VALID_PRODUCT_TYPES`)
> and the search Zod enum; Phase 0a-ii retired the frontend `models.ts`
> hand-edit. The only remaining hand-edit is the Express-backend mirror
> (step 4 above), and that retires when Express does in
> PYTHON_BACKEND.md Phase 3.

Step 1 can be scaffolded with `./Quickstart schemagen <pdf>... --type <name>`, which runs the standard `page_finder → Gemini → ProposedModel` pipeline and writes the model file plus the `common.py` patch. **Pass 3-5 vendors' datasheets** (ABB, Schneider, Siemens, Allen-Bradley, etc.) so the LLM generalizes across vendors instead of tuning the schema to one catalog's quirks — a single-source proposal will happily hardcode vendor-specific voltage columns or frame codes. The CLI also writes a companion `<type>.md` doc citing the sources and explaining non-obvious design decisions; treat that `.md` as the schema's reviewable ADR, not scratchwork.

### Smoke-testing a new type end-to-end

After touching the five files above, run this loop locally before pushing. Skipping any step is how types silently 400 in prod.

1. **Pre-push gate.** `./Quickstart verify` runs the same lint + tests + build that CI runs (Python ruff + pytest, backend lint + jest + tsc, frontend lint + vitest + tsc + vite). Green here means CI will be green; red here is your problem to fix before pushing. A missing `common.py` patch fails the Python pytest stage; a forgotten `gen-types` run fails the `test-codegen` drift gate; a missing backend or frontend interface fails the TypeScript build stage.
2. **Seed at least one record.** Drop a PDF in `tests/benchmark/datasheets/`, add a fixture entry, and run `./Quickstart bench --live --update-cache --filter <slug>` — the extraction path writes nothing to DynamoDB but validates the model end-to-end. To actually populate dev DynamoDB, point `./Quickstart process` at a local S3 upload (see "Processing the upload queue" in `cli/processor.py`).
3. **Start dev servers** with `./Quickstart dev` (backend: `localhost:3001`, frontend Vite: `localhost:3000`).
4. **Verify API surface:**

        curl -s localhost:3001/api/products/categories | jq '.data[].type'       # new type listed
        curl -s "localhost:3001/api/v1/search?type=<new>" | jq '.success'         # returns true (not 400)

   If `categories` omits the type or `search` 400s, `./Quickstart gen-types` wasn't run (step 3) — `VALID_PRODUCT_TYPES` and the search Zod enum both derive from the generated artifact.
5. **UI check.** Load `http://localhost:3000`, select the new type in the sidebar dropdown, confirm filter chips and table columns render. Missing frontend `ProductType` entry manifests as "type is not assignable" at compile time OR as the type silently filtered out by `deriveAttributesFromRecords`.

## Frontend UI conventions

The filter chips and the results-table columns both derive their attribute list from a merge of **static per-type metadata** (rich display names, tuned units) and **records-derived attributes** (caught at runtime from the actual DynamoDB rows). See `app/frontend/src/types/filters.ts:deriveAttributesFromRecords` + `mergeAttributesByKey`. Adding a new product type no longer requires editing `filters.ts` — the table will auto-populate from whatever fields the records carry, with auto-generated display names from the snake_case keys. Curated `getXxxAttributes()` lists are an override, not a requirement. User preferences (hidden columns, row density, column cap, sort direction) persist in `localStorage`.

### No native browser/OS chrome — every action stays inside the app

Specodex is meant to feel like a designed app, not a browser tab with our colors painted on it. **Every user action must have an app-native visualization.** When you reach for a built-in browser primitive, stop and use (or build) the app-native equivalent.

**Banned by default** (use the app-native primitive instead):

| Native primitive | Use this instead |
|---|---|
| `title=` attribute (OS tooltip) | `app/frontend/src/components/ui/Tooltip.tsx` |
| `window.confirm()` / `confirm()` | `useConfirm()` hook + `<ConfirmDialog>` |
| `window.alert()` / `alert()` | `useToast()` for non-blocking; `<ConfirmDialog>` if an explicit ack is required |
| Silent `console.error` in user-triggered flows | `useToast().error(...)` paired with the console log |
| `<form>` without `noValidate` (UA validation bubbles) | `noValidate` + JS validation + inline error in `<FormField>` |
| `<input type="checkbox">` without `appearance: none` | Styled checkbox (see filter sidebar pattern) |
| `<select>` (OS dropdown) | `Dropdown.tsx` (single) or `MultiSelectFilterPopover.tsx` (multi) |
| `<input type="file">` (OS file picker) | Custom dropzone — none exist today; build before adding upload |
| `<input type="date|color|range|datetime-local">` (OS pickers) | Custom picker — none exist today |
| `<dialog>` (UA backdrop) | Custom modal pattern (see `ProductDetailModal.tsx` etc.) |
| `<details>` / `<summary>` (UA disclosure triangle) | Custom collapse component |
| `target="_blank"` bare anchor | `<ExternalLink>` (themed icon + Tooltip + `rel="noopener noreferrer"`) |
| `overflow: auto/scroll` without custom `::-webkit-scrollbar` | `.scrollable` utility class (themed scrollbar) |
| `window.print()` | Custom print stylesheet — none exists; build before adding print |
| `window.open()` (popup with OS chrome) | In-app modal/route |
| `<progress>` element (UA chrome) | Custom progress bar |

**The rule.** When adding a new feature, if the obvious implementation reaches for one of the rows above, treat it as a signal that you're about to ship native chrome. Either use the app-native primitive listed, or — if no primitive exists yet — build one and add a row to the table above before shipping the feature, rather than introducing the native fallback.

**Drift gates.** `./Quickstart verify` greps for the forbidden patterns (`title=`, `window.confirm`, `alert`, `<form>` without `noValidate`, bare `target="_blank"`, raw `overflow: auto`). CI mirrors verify, so a regression PR is red before review. If the lint hits a false positive (e.g. an `<svg><title>`), allowlist the specific case rather than disabling the rule.

**Exceptions worth knowing.** Browser autofill on login forms (`:-webkit-autofill`) is intentionally left alone — password managers depend on it, and theming the yellow background hasn't been worth the complexity. The native context menu is also left in place for non-interactive content (text selection, "Inspect"). If you suppress either, document why.

### Per-component CSS files

When adding a new UI primitive under `app/frontend/src/components/ui/`, ship its CSS in a sibling `.css` file imported from the component, **not** appended to the bottom of `App.css`. The mega-file pattern was the source of recurring merge-conflict pain on every parallel feature PR — see the conflict trail on PRs #74 and #76 (May 2026) for the smoking gun. Existing primitives (`Tooltip`, `ExternalLink`, `ConfirmDialog`, `FeedbackModal`, `Toast`) all follow this pattern; add a row alongside them.

Convention:

- `components/ui/Foo.tsx` does `import './Foo.css';` near the top.
- `components/ui/Foo.css` holds the selectors that target Foo's own DOM.
- Cross-component selectors (the typography reset, layout grid, color tokens, theme switches) stay in `App.css` — that's its remaining job.
- Test `Foo.test.tsx` doesn't need to import the CSS; vitest doesn't render styles.

`App.css` should keep shrinking as more primitives extract. When a PR adds a new selector that's clearly Foo-specific, push it into `Foo.css` even if you didn't author Foo originally — the whole point is keeping App.css's bottom edge from being the universal merge target.

## Benchmarking

`./Quickstart bench` measures the ingress pipeline against control datasheets with known ground truth.

### What it measures

| Metric | How |
|--------|-----|
| **Redundancy** | Pages skipped by text heuristic / total pages. Lower = more sent to LLM than necessary. |
| **Speed** | Wall-clock ms for page-finding + LLM extraction per fixture. |
| **Cost** | Input/output tokens × configurable $/1M token pricing. |
| **Quality** | Per-field precision/recall vs ground truth (5% tolerance on numerics, unit-aware). |

### Fixture layout

    tests/benchmark/
    ├── fixtures.json                  # manifest: slug → PDF, product_type, context, expected file
    ├── datasheets/                    # control PDFs (the test inputs)
    │   ├── j5.pdf                     # 110 MB, 616 pages — full Mitsubishi MR-J5 catalog
    │   ├── j5-filtered.pdf            # 2 MB, 15 pages — pre-filtered spec pages
    │   ├── nidec-d-series-frameless.pdf
    │   ├── omron-g-series-servo-motors.pdf
    │   └── orientalmotor-nx-series.pdf
    ├── expected/                       # ground-truth JSON (one array of product dicts per fixture)
    │   ├── j5.json                     # from outputs/drives/ — 20 drive variants
    │   ├── nidec-d-series-frameless.json  # from DynamoDB — richest motor record
    │   ├── omron-g-series-servo-motors.json
    │   └── orientalmotor-nx-series.json   # empty placeholder, needs population
    └── cache/                          # cached LLM responses (--update-cache writes here)

### Usage

    ./Quickstart bench                          # offline: page-finding + quality diff against cached responses
    ./Quickstart bench --live                   # live: calls Gemini, costs real money
    ./Quickstart bench --live --update-cache    # live + save responses for future offline runs
    ./Quickstart bench --filter j5-filtered     # single fixture
    ./Quickstart bench -o results.json          # custom output path

Results write to `outputs/benchmarks/<timestamp>.json` and `outputs/benchmarks/latest.json`.

### Adding a new fixture

1. Drop the PDF in `tests/benchmark/datasheets/`.
2. Add an entry to `tests/benchmark/fixtures.json` with slug, pdf filename, product_type, manufacturer, and product_name.
3. Create `tests/benchmark/expected/<slug>.json` with ground-truth product array (or `[]` as placeholder).
4. Run `./Quickstart bench --live --update-cache --filter <slug>` to populate the cache.

### Known issues (from first dry run, 2025-04-15)

- **[FIXED 2026-04-16]** Page finder keywords were motor-centric. `SPEC_KEYWORDS` in `specodex/page_finder.py` now has 18 groups covering electronics, mechanics, mechatronics (switching devices, linear actuation, rotary/gearing, robotics, sensors, environmental, certifications). Mitsubishi contactor catalog: 4/410 → 77/410 spec pages after the broadening.
- **Nidec: 1/14 spec pages found** — added `cogging torque`/`thermal resistance` keywords didn't move the needle; this PDF may use non-English phrasings page_finder can't match. Revisit when someone cares about frameless coverage specifically.
- **[FIXED via structured ValueUnit/MinMaxUnit migration]** `ambient_temp` validation bug: the field is now typed `Temperature = _typed_value_unit(TEMPERATURE)` on Contactor (and was retired from Drive); the per-family BeforeValidator drops wrong-family inputs to `None` instead of raising, so a `{"unit": "V"}` dict no longer kills the row. The structured-types regression suite (`tests/unit/test_models_common.py::TestTypedAliases`) pins this behaviour.
- **Omron: 80% precision / 42% recall**: 13 variants extracted but missing over half the fields on matched ground-truth record.
- **[FIXED]** `scraper.py:batch_create(parsed_models)` bug: the DB write at `specodex/scraper.py:959` now passes `passed_models` (the quality-filtered list), not `parsed_models`. The "negative failure count" log signature no longer surfaces; the filter is the source of truth.

## Property testing — adversarial by default

The `hypothesis` library is in `[dependency-groups].dev` (`pyproject.toml`). Every parser, coercer, and validator that eats LLM-emitted or user-controlled bytes should have a `tests/unit/test_<surface>_property.py` companion to the example-based test file. The sprint of 2026-05-10 added property coverage across the coercion layer and caught three real bugs the example-based tests had missed:

1. `_coerce_ip_rating` returned `list`/`tuple`/`float` inputs unchanged instead of `None` (docstring contract violated); bool inputs `True`/`False` slipped through `isinstance(v, int)` and became IP ratings 1/0 — fixed in PR #112.
2. `_coerce_protocol_list` left empty strings in the protocol list, killing the whole `Drive` row via Pydantic's `Literal[EncoderProtocol]` validator — fixed in PR #116.
3. `Gearhead.coerce_string_fields` leaked Python dict-repr (`"{'value': 0, 'unit': 'mm'}"`, `"{}"`) for falsy values because of an `if v` check that didn't distinguish 0 / empty string / empty dict from None — fixed in PR #118.

Each one was a bug where the docstring said one thing and the code did another. The property test pinned the documented contract, and the code revealed itself.

### Convention

- **File name.** `tests/unit/test_<module>_property.py` — sibling to `test_<module>.py`. Sit them next to each other; the example-based test pins the happy path, the property test pins the contract.
- **Strategy structure.** Build one or two `st.composite` / `st.one_of` strategies for "adversarial input to this surface": include None, bool, int, NaN/inf floats, empty strings, unicode-laced strings, recursive dicts/lists, and a sampled list of real-world attack vectors when the function is security-relevant (see `test_url_safety_property.py` for the URL-attack list).
- **Contract template.** Every property test asserts at least: (1) the function never raises an exception type outside its documented set, and (2) the return value has the documented shape. For functions that mutate or return Pydantic instances, also assert the instance fields are well-formed (e.g. `device in EncoderDevice` enum values).
- **Example count.** Default to `max_examples=200` for routine surfaces, `300` for security-relevant ones (`validate_url`, the BeforeValidators). `HealthCheck.too_slow` is fine to suppress when the surface is slow per-call (e.g. PyMuPDF `fitz.open`).
- **Logging.** Add an autouse fixture to silence the per-row validation-error logging when running 200+ examples — without it, a single property run produces 200 stack traces in the output (see `test_parse_gemini_property.py` for the `_silence_validation_logs` fixture).
- **Slow-path patches.** For tests that exercise tenacity retries or `lru_cache`-backed paths, override the slow-path attribute (`generate_content.retry.wait = wait_none()`) and clear the cache (`_client_for.cache_clear()`) in a module autouse fixture — see `test_resilience.py::_no_retry_waits`. The 2s vs 22s bimodal CI timing that bit us in May 2026 was exactly this missing override.
- **Bug fix → regression test FIRST.** When hypothesis catches a real bug, add an explicit example-based regression case to the sibling `test_<module>.py` file (e.g. `TestCoerceProtocolListEdgeCases`) **in addition to** the property test. The property test catches the contract violation; the explicit case pins the specific shape so it can't regress even if the hypothesis strategy drifts. Both stay forever.
- **False positives.** When a property test fails on an input that's actually a legitimate edge case (e.g. user-supplied `'{'` as a frame_size value triggering a "starts with `{`" assertion designed to catch dict-repr leaks), tighten the assertion to match the bug's exact signature (`startswith("{'")` or `startswith('{"')`) — don't widen the input filter, which would hide future bug shapes.

### What to test (current coverage map, 2026-05-14)

| Surface | Property test | Sibling example test |
|---|---|---|
| `parse_gemini_response` (LLM JSON parser) | `test_parse_gemini_property.py` | inline in `test_utils.py` |
| `common.py` BeforeValidators | `test_common_validators_property.py` | `test_models_common.py` |
| `find_spec_pages_by_text` (PDF intake) | `test_page_finder_property.py` | `test_page_finder*.py` |
| `coerce_protocol_string`, `_coerce_protocol_list`, `EncoderFeedback._coerce_legacy_freetext` | `test_encoder_coercers_property.py` | `test_encoder.py` |
| `Gearhead.coerce_string_fields` | `test_gearhead_coerce_property.py` | (via `test_models_common.py`) |
| `validate_url` (SSRF defense) | `test_url_safety_property.py` | `test_url_safety.py` |
| `merge_per_page_products` | `test_merge_property.py` | `test_merge.py` |
| `double_tap.verifier` (`_encoder_is_ambiguous`, `verify`) | `test_double_tap_verifier_property.py` | `test_double_tap.py` |
| `cli/processor.py:parse_datasheet_id_from_key` (upload-queue S3 key parsing) | `test_processor_property.py` | `test_processor.py` |
| `specodex/integration/compat.py` field-compat helpers (`_scalar`, `_range`, `_check_*`, `_roll_up`) | `test_compat_property.py` | `test_integration.py` |
| `specodex/spec_rules.py:validate_product` magnitude rules + identity check + duplicate pair | `test_spec_rules_property.py` | `test_spec_rules.py` |
| `specodex/quality.py:score_product` + `filter_products` partition | `test_quality_property.py` | `test_quality.py` + `test_quality_boundary.py` |

The 2026-05-14 sprint closed out the four "untested adversarial surfaces" from the 2026-05-10 callout (`cli/processor.py`, `compat.py`, `spec_rules.py`, `quality.py`) via PRs #149, #185, #202, #203. None of the four runs surfaced new bugs — every Hypothesis search confirmed the contract the example tests had already pinned. The boring-good outcome.

When the next round of property-test gaps comes up, add them to the list above; don't leave the section empty for long.

## Post-deploy verification

After `./Quickstart deploy --stage <stage>` returns, confirm the stack is actually live before closing the loop. `./Quickstart smoke <URL>` runs the full `tests/post_deploy/` suite; the ad-hoc checks below are what to reach for when a single endpoint is misbehaving.

**URLs per stage.** Prod uses the configured custom domain; staging/dev come from the Frontend stack's `CloudFrontUrl` output:

    # staging / dev
    aws cloudformation describe-stacks \
      --stack-name DatasheetMiner-<Stage>-Frontend \
      --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontUrl`].OutputValue' --output text

    # prod
    https://www.specodex.com

**Canonical endpoints** — each of these must 200 with the shape noted:

| Endpoint                          | Expected (HTTP 200)                                                        |
|-----------------------------------|----------------------------------------------------------------------------|
| `/health`                         | `{"status": "healthy", "timestamp": "...", "environment": "production", "mode": "public"}` |
| `/api/products/categories`        | `{"success": true, "data": [{type, count, display_name}, ...]}`            |
| `/api/products/summary`           | `{"success": true, "data": {"total": N, ...}}`                              |
| `/api/products`                   | `{"success": true, "data": [...]}` (array, possibly empty)                  |
| `/api/v1/search?type=<valid>`     | `{"success": true, ...}` (400 if type not in the zod enum)                  |

**One-shot smoke:**

    ./Quickstart smoke https://www.specodex.com          # prod
    ./Quickstart smoke "$(aws cloudformation describe-stacks \
      --stack-name DatasheetMiner-Staging-Frontend \
      --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontUrl`].OutputValue' \
      --output text)"                                       # staging

**Common failure fingerprints:**

- `/health` times out but `/api/products` 200s → CloudFront behavior for `/health` is wrong (it should route to API Gateway, same as `/api/*`). Check `frontend-stack.ts` behaviors.
- `/health` returns `"mode": "admin"` → Lambda is running with the local dev env. `APP_MODE=public` is hardcoded in `api-stack.ts`; verify that code shipped.
- `/api/products/categories` returns `data: []` → DynamoDB table is empty OR the Lambda is pointed at the wrong table. Check the `DYNAMODB_TABLE_NAME` env in the Lambda config.
- `/api/v1/search?type=<new>` returns 400 → new product type wasn't added to the zod enum in `routes/search.ts` (see "Adding a new product type" above).

## Key directories

    cli/                    CLI modules (bench, intake, processor, admin, batch_*, agent, triage)
    specodex/              Core library (LLM, scraper, models, DB, page_finder, quality, units)
    specodex/models/        Pydantic product models + schema builders
    specodex/db/            DynamoDB client
    app/                    Node.js frontend + backend (Express API, React UI)
    tests/                  Python tests (unit/, integration/, staging/, post_deploy/, benchmark/)
    outputs/                Extraction outputs and benchmark results

## Shipping a change

Two long-lived branches drive deploys:

- **`dev`** — push here to auto-deploy the staging stack (CloudFront
  staging URL). No prod deploy. Use for visually verifying a change
  on a real deployed environment before promoting.
- **`master`** — push here (or merge `dev` → `master`) to run the
  full chain: tests → staging deploy → smoke → **prod approval gate
  (`environment: production`, JimothyJohn required reviewer)** →
  prod deploy → smoke.

Default flow for non-routine work in an interactive session:

1. Branch off `dev` (or `master` for hotfix-shaped work).
2. Commit + push the feature branch.
3. Open a **non-draft** PR. Title says what changed; body says why.
   - Targeting `dev`: when CI passes, merge → dev auto-deploys to
     staging → eyeball the staging CloudFront URL → open a follow-up
     PR `dev` → `master` to ship to prod.
   - Targeting `master` directly (small/obvious changes, hotfixes):
     when CI passes, merge → staging deploys → prod waits on Nick's
     environment-approval click.
4. Watch CI. When all required checks pass and there are no
   conflicts, **merge it** — don't wait for Nick to click approve
   on the *PR* (the prod-environment click is a separate gate that
   stays).

Nick approves every clean PR; the PR click is theater. The PR exists
for CI gating and as a paper trail, not as a review gate. The
production-environment click is the real gate.

**Exception — hard-to-reverse blast radius.** Keep the PR draft and
hand back when the diff touches `app/infrastructure/**` (CDK),
`.github/workflows/**` (CI/CD), IAM/secrets/KMS policy, Stripe
live-mode, prod deploy, or shared infra mutations. For these, the
review-wait gate still matters.

(Background remote agents — if any are still wired — run unattended
and keep the draft-PR-then-Nick-merges flow. That's a different mode
than interactive work.)

### Per-PR HTML documentation

The `docs/requests/<n>.html` per-PR pages are reserved for **major
functional changes** — user-visible features, schema migrations,
architecture shifts, security-meaningful fixes. Dependabot bumps,
lint fixes, lockfile regens, doc syncs, formatting passes, or other
chore PRs **do not** get a per-PR page. The signal-to-noise on the
public archive was the reason for the rule change; if a PR doesn't
move the product forward, it doesn't deserve a public page.

When you do write one, match the manila / engineering-paper style of
`docs/index.html`: same palette (`--paper #E8E2C9`, `--od #3A2C1C`,
`--stencil #A88A1C`), same fonts (Oswald headlines, IBM Plex Mono
body), square-bordered cells, no rounded corners or shadows. Set
`body[data-issue]="PR-<n>"` for the watermark. Pull source from
`gh pr view <n> --json title,body,headRefName,mergedAt,files` —
never paraphrase a PR you haven't read.

The existing `docs/requests/` archive remains as historical record
but is no longer linked from the `docs/index.html` top band. If you
write a new per-PR page, update `docs/requests/index.html`
(newest-first) so the archive page itself stays internally
navigable.

---

## Backlog & `todo/`

Work is queued in `todo/`. One file per area
(`todo/<AREA>.md`); `todo/README.md` is the dependency map, the
chronological order, and the source of status. `docs/roadmap.html`
is a generated kanban of the same data (run `uv run python
scripts/gen_roadmap.py` after editing any `todo/*.md`).

There is **no GitHub Project board**. The "Specodex Orchestration"
board was deleted 2026-05-13 — it was duplicating `todo/` and
nobody was actually using it. If a remote agent or skill still
references `https://github.com/users/JimothyJohn/projects/1` or
`gh project … --owner JimothyJohn 1`, that reference is stale and
should be removed.

### What makes a good `todo/` entry

A well-formed todo doc is a small, reviewable chunk of work, named
by its area (`HARDENING.md`, `SEO.md`). It includes:

- **First line:** `# <AREA> — <one-line scope>`. The H1 becomes
  the card title in `docs/roadmap.html`.
- **Status blockquote** right after the H1, naming the current
  state (`📐 planned`, `🚧 in progress`, `🔴 needs sign-off`,
  `🎨 in flight`). The status emoji is what the roadmap renderer
  reads.
- **Phased plan** below, broken into independently shippable PRs.
- **Trigger conditions** if the doc has file-level triggers — add
  a row to `todo/README.md`'s "Trigger conditions" table so the
  doc auto-surfaces when matching files are touched.

When deferring something to "after MVP soak" or "no active plan,"
move the file under `todo/longterm/` — that bucket is inert until
explicitly named (see auto-memory `feedback_todo_longterm.md`).

### How work gets done

1. Pick a row from `todo/README.md`'s churn plan (the table at the
   bottom of the file) or scan the kanban at `docs/roadmap.html`.
2. Branch off master as `auto/<area>-<short-slug>-<yyyymmdd>` (or
   any descriptive name for human-driven work).
3. Make the smallest correct change. Run `./Quickstart verify` or
   the relevant subset before committing.
4. Push and open a PR. Default is non-draft when CI passes and the
   diff doesn't touch hard-to-reverse surfaces (see
   "Shipping a change" above for the draft-PR exceptions).
5. After merge, update the status line in `todo/<AREA>.md` (or
   delete the doc if its scope is fully shipped); regenerate
   `docs/roadmap.html`.

**Hard rules for autonomous / unattended work** (skip if any apply):

- Anything blocked per `todo/README.md`'s dependency map.
- Edits to `app/infrastructure/**` (CDK), `.github/workflows/**`
  (CI/CD), or anything that triggers an AWS-mutating command
  (`./Quickstart deploy`, `./Quickstart admin promote`, `--stage
  prod`).
- DynamoDB writes outside dev (and even on dev, prefer dry-run).
- Stripe live-mode keys, real charges, or webhook secret rotation.
- Cherry-picking across worktrees or rewriting shared history.

If a row is borderline, skip it. The cost of skipping is a missed
day; the cost of an unauthorized infra change is much higher.

When `todo/<AREA>.md` ships its full scope (or its action items
move into another doc), delete the file (`git rm todo/<AREA>.md`);
the design rationale stays recoverable via `git log --diff-filter=D
--follow -- todo/<AREA>.md`. See the deletion log in
`todo/README.md`'s "Recently shipped" header for the pattern.

## Environment

- `.env` at repo root — `GEMINI_API_KEY`, `DYNAMODB_TABLE_NAME`, `AWS_REGION`
- `app/.env` — frontend/backend config
- Stage-specific: `app/.env.dev`, `app/.env.prod`

### AWS auth paths (two of them — don't mix them up)

This account has two GitHub-Actions auth principals with overlapping but **non-identical** permissions:

- **OIDC role `gh-deploy-datasheetminer`** — what `.github/workflows/ci.yml` actually uses (`role-to-assume:` at lines 266, 398). Inline policy is `CdkDeploy`. This is the one CI deploys with.
- **IAM user `datasheetminer-github`** — static-creds principal with managed policy `datasheetminer-cicd` attached. Not referenced by any current workflow, but its policy is broader and easy to mistake for "the deploy policy."

When adding a deploy permission, attach it to the **role's `CdkDeploy` inline policy** (or as a managed policy attached to the role). Adding it only to `datasheetminer-cicd` does nothing for CI — that's a foot-gun we hit on 2026-04-29 with the `Route53Lookup` perm for `HostedZone.fromLookup`. Verify with:

    aws iam get-role-policy --role-name gh-deploy-datasheetminer --policy-name CdkDeploy \
      --query 'PolicyDocument.Statement[?Sid==`<your-sid>`]'

### Apex (2-part) domains and `HOSTED_ZONE_NAME`

`app/infrastructure/lib/config.ts` infers the hosted-zone name from `DOMAIN_NAME` when `HOSTED_ZONE_NAME` is unset. The fallback handles the 2-part apex case: 3+ parts strips the leftmost label (e.g. `app.example.com` → `example.com`), 2 parts uses the domain itself (e.g. `specodex.com` → `specodex.com` — the parent would be `com`, which `fromLookup` can't resolve). Setting `HOSTED_ZONE_NAME` explicitly is no longer required for apex deploys, but still works as an override when the zone name differs from the inferred parent.

History: prior to the 2026-04-30 fix the fallback always stripped the leftmost label, so an apex `DOMAIN_NAME` produced `"com"` and CDK `fromLookup` failed synth with `Found zones: [] for dns:com`. Bit us during the 2026-04-29 cutover.

### Pushing from a Claude session

SSH keys aren't loaded in the session, so `git push` over `git@github.com:...` fails. Use one of:

- **HTTPS via `gh` credential helper:** `git -c credential.helper='!gh auth git-credential' push https://github.com/JimothyJohn/specodex.git <branch>`
- **User pushes manually** from their own terminal (always works)

Caveat: `gh`'s default token usually has `repo` but **not `workflow` scope**, so any push that touches `.github/workflows/*` will be rejected by GitHub even after auth succeeds. Either run `gh auth refresh -s workflow` first (interactive, needs `! gh auth refresh -s workflow` in the prompt), or have the user push from their terminal.

### Continuing a prior session's plan

If a session resumes from a previous one's task list, **re-verify each "completed" step before trusting it.** Prior sessions can show "Apply IAM perm: completed" when the apply call actually never finished, or "branch X merged" when only the merge command was queued. The cheapest way to avoid an hour of confusion is to spend 30 seconds confirming state with the underlying tool (`aws iam get-role-policy`, `git log master..<branch>`, etc.) before continuing.
