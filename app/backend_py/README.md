# `app/backend_py` — FastAPI backend (Phase 1.1 vertical slice)

This directory is the **code-only Phase 1.1 vertical slice** of the
Express → FastAPI migration planned in
[`todo/PYTHON_BACKEND.md`](../../todo/PYTHON_BACKEND.md). It proves
the FastAPI + Mangum + `specodex.db.dynamo` chain works end-to-end
against a moto-mocked DynamoDB table, without committing the
operator-driven CDK + deploy + cutover work.

## What's done

- **Package layout** matching `todo/PYTHON_BACKEND.md` §1.1 — `src/`,
  `src/routes/`, `src/db/`, `src/middleware/` (empty for now),
  `tests/`. The plan's three other directories (`services/`,
  `middleware/auth.py`, `middleware/readonly.py`) are stubbed
  out as empty packages so the structure is reviewable.
- **`pyproject.toml`** — FastAPI ≥ 0.115, Mangum ≥ 0.17, boto3 ≥
  1.43, Pydantic ≥ 2.13 (matches the parent monorepo's pin). Dev
  deps cover pytest + moto + httpx (via FastAPI's `TestClient`).
- **`src/main.py`** — FastAPI factory + `Mangum(app, lifespan="off")`
  Lambda handler. Mirrors the route-mounting shape in
  `app/backend/src/index.ts`.
- **`src/config.py`** — env-var loader (`APP_MODE`, `NODE_ENV`,
  `DYNAMODB_TABLE_NAME`, `AWS_REGION`, `CORS_ORIGINS`). Defaults
  match the Express service.
- **`src/db/dynamodb.py`** — `BackendDB` wrapper around
  `specodex.db.dynamo.DynamoDBClient`. Adds `get_categories()` and
  `list_by_type()` aggregations that the pipeline DAL doesn't carry
  but the Express backend exposed. **Composition over inheritance**
  — the pipeline owns the base DAL's contract.
- **`src/routes/health.py`** — `GET /health` returning the same
  `{status, timestamp, environment, mode}` shape as Express, so the
  post-deploy smoke suite in `tests/post_deploy/` passes against
  either stack.
- **`src/routes/products.py`** — `GET /api/products` (with optional
  `type=` and `limit=` query params) and `GET /api/products/categories`.
  Returns the Express `{success, data, count?}` envelope.
- **`tests/`** — moto-mocked DynamoDB fixture, contract tests pinning
  the response envelope, per-type filtering, the unknown-type
  empty-array case, and the limit behaviour.

## Local dev

The parent monorepo's `uv` lockfile doesn't include FastAPI yet,
deliberately — Phase 1's deploy work is what decides whether
FastAPI lives in the parent lock or stays scoped to this sub-package.
For now, install in a sub-venv:

```bash
cd app/backend_py
uv venv
uv pip install -e ../..             # specodex package, editable
uv pip install -e .
uv pip install -e ".[dev]"          # or `uv sync --all-extras`
```

Then run the dev server:

```bash
uv run uvicorn app.backend_py.src.main:app --reload --port 3001
```

Or the tests:

```bash
uv run pytest
```

## What's deliberately NOT done in this PR

Every item below is **operator-driven** — needs a Nick session, not
an autonomous sprint. Each line names the plan section it maps to.

- **`todo/PYTHON_BACKEND.md` §1.2 — Auth middleware port.** The
  Cognito JWKS verification, the readonly guard, and the admin-only
  guard all need a port-test-by-test against
  `app/backend/src/middleware/`. The plan calls this out as the
  step where parallel deployment goes wrong most easily.
- **§1.3 — CDK deploy wiring.** `app/infrastructure/lib/api-stack.ts`
  needs a new `ApiPyFunction` Lambda + `/api/v2/` route or stage.
  Skip-list per CLAUDE.md "Edits to `app/infrastructure/**`".
- **§1.4 — Frontend feature flag.** `VITE_API_VERSION=v1|v2` in
  `app/.env*` and `app/frontend/src/api/client.ts` switching between
  v1 / v2 base URLs.
- **§1.5 — CI parameterisation.** `tests/staging/` and
  `tests/post_deploy/` parameterised over both stacks; `./Quickstart
  verify` learns to run this stage.
- **The remaining Express routes** — `datasheets`, `search`,
  `upload`, `projects`, `auth`, `admin`, `subscription`, `relations`,
  `compat`, plus the per-product CRUD (`GET /api/products/:id`,
  `POST /api/products`, `PUT/DELETE`). The vertical slice picks
  `list` + `categories` + `health` deliberately — those three are
  the canonical-endpoint trio the smoke suite checks first.

The plan's exit criteria for Phase 1 — v2 deployed to dev + staging,
all `tests/staging/` and `tests/post_deploy/` green against v2,
24h CloudWatch error rate within 2% of v1, one engineer
soak-tested via `?api=v2` — sit downstream of all four items
above. Phase 2 (frontend cutover with kill-switch) and Phase 3
(Express deletion) sit downstream of those.

## Why ship this slice instead of waiting

Three reasons:

1. **The shape of the code informs the deploy plan.** The CDK
   changes in §1.3 want to know what the Lambda handler signature
   looks like and how the package gets bundled. With this slice
   committed, the CDK PR can wire `app.backend_py.src.main.handler`
   directly.
2. **The auth port in §1.2 wants a target.** Porting middleware
   into an empty FastAPI app is harder than porting it into a
   working one — you don't know if your auth middleware
   integrates correctly until the rest of the stack is there.
3. **The contract tests pinned the Express envelope.** Future
   route ports have a regression suite to match, not just a TypeScript
   file to read.

## File map

```
app/backend_py/
├── README.md             this file
├── pyproject.toml        FastAPI + Mangum, scoped to this dir
├── src/
│   ├── __init__.py
│   ├── main.py           FastAPI factory + Mangum handler
│   ├── config.py         env-var settings
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── health.py     GET /health
│   │   └── products.py   GET /api/products, /categories
│   ├── db/
│   │   ├── __init__.py
│   │   └── dynamodb.py   BackendDB wrapper around specodex.db.dynamo
│   └── middleware/
│       └── __init__.py   (stubs for §1.2 — auth / readonly / admin)
└── tests/
    ├── __init__.py
    ├── conftest.py       moto DynamoDB fixture
    ├── test_health.py    health-endpoint contract
    └── test_products.py  /api/products + /categories contract
```
