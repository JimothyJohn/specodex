---
paths:
  - "specodex/models/**"
  - "scripts/gen_types.py"
  - "app/frontend/src/types/**"
  - "app/backend/src/types/**"
  - "tests/unit/test_schema_compat.py"
  - "tests/unit/test_gen_types_normalize.py"
  - "tests/unit/fixtures/schema_snapshots/**"
---

# Product types and Pydantic → TypeScript codegen

The one-line rules in `CLAUDE.md` always apply. This loads when you touch a product model or the generated TypeScript.

## Type generation

The TypeScript types in the frontend are **generated** from the Pydantic models, not hand-written. `./Quickstart gen-types` runs `pydantic2ts` against every module listed in `scripts/gen_types.py:_MODEL_MODULES` and writes `app/frontend/src/types/generated.ts` plus the backend twin `app/backend/src/types/generated_constants.ts`. `pydantic2ts` shells out to `npx json-schema-to-typescript`, so Node 18+ must be on PATH (it already is for `./Quickstart dev`).

Run it after any edit to `specodex/models/*.py` (new field, renamed field, new model) or `specodex/models/common.py` (`ProductType` literal, unit families, `ValueUnit` / `MinMaxUnit` shape).

CI's `test-codegen` job (`.github/workflows/ci.yml`) re-runs `./Quickstart gen-types` and fails if the committed `generated.ts` drifts from source; `deploy-staging` needs it. The generated file is committed and reviewed as part of the diff, not treated as a build artifact.

**Don't hand-edit generated files.** The banner at the top of `generated.ts` says so. If the generated TS is wrong, fix the Pydantic model and regenerate.

**Wrap-only formatting drift is canonicalised away.** Local `json-schema-to-typescript` version skew wraps long union types differently from CI's regeneration; `scripts/gen_types.py:_unwrap_multiline_unions` joins every prettier continuation shape (leading-`|` members, `)` / `)[]` closers, break-after-colon) back onto its opening line, so the committed artifact is independent of prettier's wrap decisions. `tests/unit/test_gen_types_normalize.py` pins the committed `generated.ts` as a fixed point of the normalizer. If `test-codegen` fails with a whitespace-looking diff, that is a new continuation shape the normalizer doesn't join yet — extend the normalizer with a regression test pinning the shape; don't hand-edit the artifact.

This is Phase 0 of the Python-backend migration in `todo/PYTHON_BACKEND.md`, which retires the hand-synced TypeScript layer in `app/backend/`.

## Adding a new product type

The Python side auto-discovers. The backend allowlist, the search Zod enum, and the frontend `models.ts` all derive from the codegen output (`models.ts` is a thin re-export shim over `generated.ts`); the Express backend's `models.ts` is the one remaining hand-edit.

1. `specodex/models/<type>.py` — Pydantic model inheriting `ProductBase`, with `product_type: Literal["<type>"] = "<type>"`.
2. `specodex/models/common.py` — add `"<type>"` to the `ProductType` literal.
3. `scripts/gen_types.py:_MODEL_MODULES` — add `specodex.models.<type>` (codegen does not auto-discover; without it the type lands in `PRODUCT_TYPES` but gets no TS interface), then `./Quickstart gen-types`. The new type flows into `VALID_PRODUCT_TYPES` (backend), the search Zod enum, the frontend `Product` union, and the frontend `ProductType` literal — no hand-edits.
4. `app/backend/src/types/models.ts` — still hand-typed. Add a `<Type>` interface and include it in the `Product` and `ProductType` unions. This goes away with the Express deletion in `todo/PYTHON_BACKEND.md` Phase 3; don't migrate it to codegen before then (the retired `todo/MODELGEN.md` made that call — `git log --diff-filter=D --follow -- todo/MODELGEN.md` recovers it).
5. **Refresh the schema-compat snapshot.** `tests/unit/test_schema_compat.py` keeps a frozen JSON fixture per `ProductType` under `tests/unit/fixtures/schema_snapshots/`. After steps 1–2, regenerate with `uv run python -c 'from tests.unit.test_schema_compat import refresh_snapshots; refresh_snapshots()'` and commit the new file — otherwise the `TestSnapshotsExist` drift gate fails on the first run. Refresh is also required when intentionally renaming or dropping a model field (the test exists to surface exactly that decision).

Step 1 can be scaffolded with `./Quickstart schemagen <pdf>... --type <name>`, which runs `page_finder → Gemini → ProposedModel` and writes the model file plus the `common.py` patch. **Pass 3–5 vendors' datasheets** (ABB, Schneider, Siemens, Allen-Bradley, …) so the LLM generalizes across vendors instead of tuning the schema to one catalog — a single-source proposal will hardcode vendor-specific voltage columns or frame codes. The CLI also writes a companion `<type>.md` citing the sources and explaining non-obvious design decisions; treat that `.md` as the schema's reviewable ADR, not scratchwork.

## Smoke-testing a new type end-to-end

Run this loop locally before pushing; skipping a step is how types silently 400 in prod.

1. **Pre-push gate.** `./Quickstart verify` runs what CI runs (Python ruff + pytest, backend lint + jest + tsc, frontend lint + vitest + tsc + vite, deploy build). A missing `common.py` patch fails the Python stage; a forgotten `gen-types` run fails the `test-codegen` drift gate; a missing backend or frontend interface fails the TypeScript build stage.
2. **Seed at least one record.** Drop a PDF in `tests/benchmark/datasheets/` (gitignored), add a fixture entry, and run `./Quickstart bench --live --update-cache --filter <slug>` — writes nothing to DynamoDB but validates the model end-to-end. To populate dev DynamoDB, point `./Quickstart process` at a local S3 upload (`cli/processor.py` docstring).
3. **Start dev servers** with `./Quickstart dev` (backend `localhost:3001`, frontend Vite `localhost:3000`).
4. **Verify the API surface:**

        curl -s localhost:3001/api/products/categories | jq '.data[].type'       # new type listed
        curl -s "localhost:3001/api/v1/search?type=<new>" | jq '.success'         # true, not 400

   If `categories` omits the type or `search` 400s, `./Quickstart gen-types` wasn't run (step 3) — `VALID_PRODUCT_TYPES` and the search Zod enum both derive from the generated artifact.
5. **UI check.** Load `http://localhost:3000`, select the new type in the sidebar dropdown, confirm filter chips and table columns render. A missing frontend `ProductType` entry shows up as "type is not assignable" at compile time or as the type silently filtered out by `deriveAttributesFromRecords`.
