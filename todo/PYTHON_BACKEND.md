# PYTHON_BACKEND — TypeScript → Python migration for `app/backend/`

> **Status:** 🚧 in progress. Phase 0 (codegen) shipped. **Phase 1 is
> code-complete** as of the 2026-05-14/15 sprint — the entire FastAPI
> backend (`app/backend_py/`) is ported and tested: auth middleware,
> all 11 Express route files, the CDK `/api/v2` wiring, and the
> frontend `VITE_API_VERSION` switch. What's left is **operator-driven
> deploy + cutover** (Phases 1.3-deploy, 2, 3) — none of it is code.
>
> **Date drafted:** 2026-05-02. **Phase 1 code-complete:** 2026-05-15.
> **Owner:** Nick. The remaining work (deploy v2, flip the flag, delete
> Express) needs a single hand on the rudder.
>
> **What shipped (2026-05-14/15 sprint):**
> - 1.1 Layout + scaffold — PR #205
> - 1.2 Auth middleware (Cognito JWT, readonly guard, admin gate) — PR #206
> - Products CRUD + aggregations — PR #207
> - Datasheets + search + compat + relations routes — PR #208
> - Projects + auth (Cognito proxy) + upload routes — PR #209
> - Subscription (Stripe proxy) + admin routes — PR #210
> - 1.3 CDK `ApiPyHandler` Lambda + `/api/v2/*` route — **DRAFT** PR #212
>   (infra; conditionally synthesised — inert until `dist/` is built)
> - 1.4 `VITE_API_VERSION` switch + `/api/v2` prefix-strip middleware — PR #213
> - 1.5 backend_py tests wired into `./Quickstart verify` — PR #214
> - Phase 4 — `stripe_py/` tests wired into verify — PR #215
>
> **What's left (operator-driven, no code):**
> 1. Review + merge the CDK draft (#212).
> 2. `./Quickstart build-backend-py` → `cdk deploy` → v2 Lambda live at `/api/v2/*`.
> 3. Phase 2 cutover — set `VITE_API_VERSION=v2` in `app/.env.<stage>`, redeploy
>    frontend. Full cutover, no canary (per §7).
> 4. Phase 3 — once v2 is confirmed healthy, delete `app/backend/`,
>    drop the Express Lambda from CDK, drop the `backend` CI job.
> 5. Phase 4 deploy — CDK `RustFunction` → `PythonFunction` swap for
>    the billing Lambda; the `stripe_py/` code is already ported.

---

## 0. Why this exists

A code audit (in git history at `todo/REFACTOR.md`) flagged three
structural taxes on the current architecture:

1. **Polyglot stack without polyglot justification.** Python pipeline + Node
   API + Rust billing. Three runtimes, three lint configs, three deploy
   paths. Only the pipeline (Python, ML libs) and the frontend (React) are
   load-bearing — the Node backend is **a hand-typed mirror of the Pydantic
   layer**.
2. **Six places to update for a new product type.** Pydantic model → TS
   interface → TS union → Zod enum → backend allowlist → frontend union.
   CLAUDE.md has a runbook for it; the right answer is to make the runbook
   unnecessary.
3. **No type-safe contract between Python and TypeScript.** A field
   renamed in Python without updating TS ships and nobody notices until a
   user reports a missing column.

This doc is the actionable plan to retire the Express backend. It is **not**
a license to start ripping out code — Phase 0 is the only piece you can ship
without an ADR. Phases 1-4 each get their own scoped doc when the team
decides to run them.

**Out of scope (don't bundle into this work):**
- Rewriting Pydantic models. They're correct.
- Rewriting the page-finder, scraper, or quality gate.
- Migrating off DynamoDB or single-table design.
- Frontend rewrites (React stays).
- The CDK infrastructure layer stays in TypeScript — too much
  AWS-specific TS context to be worth porting.

---

## 1. End-state shape

```
specodex/models/*.py  ── pydantic2ts ──►  app/frontend/src/types/generated.ts
        │                                          ▲
        │                                          │  imported by React
        ▼                                          │
   FastAPI app  ─── handlers consume Pydantic ─────┘
   (app/backend_py/)        return Pydantic
        │
        └──► Mangum / AWS Lambda Web Adapter ──► same API Gateway as today
                                                          │
                                                          ▼
                                                    DynamoDB (unchanged)
```

**Single source of truth: `specodex/models/`.** Everything else is generated
or consumes generated artifacts.

**What stays:**
- `specodex/` Python pipeline.
- `app/frontend/` React SPA (with `types/generated.ts` replacing the
  hand-typed `types/models.ts`).
- `app/infrastructure/` CDK stacks (TS — single exception to the
  Python-everywhere goal).
- DynamoDB schema, S3 upload bucket, Cognito user pool.

**What goes:**
- `app/backend/` (the entire Express + serverless-http app).
- `app/backend/src/types/{models,schemas}.ts` (replaced by generated).
- The Zod re-validation layer in `routes/search.ts` (FastAPI does it from
  Pydantic directly).
- Eventually: the Rust Stripe Lambda, replaced by a Python Lambda. Tracked
  in [§9 Phase 4](#phase-4--drop-the-rust-billing-lambda).

---

## 2. Phases at a glance

| Phase | Scope | Effort | Risk | Reversible? | Gate |
|---|---|---|---|---|---|
| **0**  | `pydantic2ts` codegen wired through `./Quickstart gen-types` ✅ shipped 2026-05-02 | 1-2 days | low | yes (delete generated.ts) | runs in CI |
| **0a** | Drift check + commit `generated.ts` ✅ shipped 2026-05-02; consumer rewire still pending | 0.5 day shipped + ~1 day rewire | low | yes | `test-codegen` job green |
| **0b** | Drop the Zod enum in `routes/search.ts` in favour of `VALID_PRODUCT_TYPES` derived from generated types | 0.5 day | low | yes | search contract tests still pass |
| **1**  | FastAPI service stood up at `/api/v2/...` in parallel | 1-2 weeks | medium | yes (delete v2 stack) | feature-flag in frontend; <2% error delta vs v1 |
| **2**  | Frontend cuts over to v2 by default; v1 in fallback for one release | 2-3 days | medium | yes (re-flag) | one full release cycle of v2 stable |
| **3**  | Express backend deleted from repo + CDK | 1 day | low (after Phase 2) | git revert | smoke tests green on v2 only |
| **4**  | Rust Stripe Lambda → Python Lambda | 1-2 days | low | yes | webhook signatures verified; a $0.50 test charge round-trips |
| **5**  | `cli/` migration scripts cleaned up ✅ shipped 2026-04-30 (commit `c322393` deleted 13 finished one-shot scripts; one archive remains under `scripts/migrations/`) | 0.5 day | zero | trivial | grace period + delete |

**You can stop after any phase and the system is still shippable.** This is
the most important property — there is no "you've started so you must
finish" cliff between Phase 0 and Phase 4.

---

## Phase 0 — codegen (split out into [MODELGEN.md](MODELGEN.md))

The `pydantic2ts` codegen + drift gate + consumer rewire moved into its
own doc on 2026-05-02 because it ships independently and is worth
tracking on its own.

**State at split:**
- ✅ `./Quickstart gen-types` → `app/frontend/src/types/generated.ts`
- ✅ `test-codegen` CI job blocking deploy on drift
- ⏳ Frontend `models.ts` → re-export shim (5 documented shape mismatches)
- ⏳ Backend Zod enum + allowlist collapse
- ⏳ "Adding a new product type" runbook collapse to 2 files + codegen

**See [MODELGEN.md](MODELGEN.md) for the operational details.** Phases
1+ below assume Phase 0 has landed (the FastAPI service consumes the
same Pydantic models, so the codegen gate keeps the contract honest as
the backend changes underneath it).

---

## Phase 1 — FastAPI service in parallel

**Goal:** stand up `app/backend_py/` as a FastAPI app that reuses
`specodex.models` directly. Deploy it to a separate Lambda + API Gateway
path (`/api/v2/...`). The frontend continues to use v1; v2 is dark.

### 1.1 Layout

```
app/
├── backend/          # Express, slated for Phase 3 deletion
└── backend_py/
    ├── pyproject.toml          # FastAPI + Mangum, scoped to this dir
    ├── src/
    │   ├── main.py             # FastAPI app, Mangum handler
    │   ├── routes/
    │   │   ├── products.py
    │   │   ├── datasheets.py
    │   │   ├── search.py
    │   │   ├── upload.py
    │   │   ├── projects.py
    │   │   ├── auth.py
    │   │   └── admin.py
    │   ├── middleware/
    │   │   ├── readonly.py
    │   │   ├── adminonly.py
    │   │   └── auth.py
    │   ├── db/
    │   │   └── dynamodb.py     # imports specodex.db.dynamo
    │   └── services/
    │       ├── search.py
    │       └── stripe.py
    └── tests/
```

**Reuse over rewrite.** `db/dynamodb.py` re-exports
`specodex.db.dynamo.DynamoDBService`. The FastAPI `/products` handler is
4 lines:

```python
@router.get("/products", response_model=list[Product])
async def list_products(type: ProductType | None = None):
    return DynamoDBService().list_products(product_type=type)
```

### 1.2 Auth

`aws-jwt-verify`'s Python equivalent is `python-jose` + the Cognito JWKS
endpoint. The middleware mirrors `app/backend/src/middleware/auth.ts`
exactly — same JWT extraction, same group check.

The PHASE5_RECOVERY work (`todo/PHASE5_RECOVERY.md`) lands first. **Do
not start Phase 1 until Phase 5a/c/d/e/f are confirmed on `origin/master`.**
Building a parallel backend on top of unmerged auth is the most expensive
mistake we could make here.

### 1.3 Deployment

CDK `api-stack.ts` adds a second Lambda function (`ApiPyFunction`) and a
second API Gateway stage (or a path-prefixed route on the existing API
Gateway, behind `/api/v2/`). Both backends share the DynamoDB table, the
S3 upload bucket, and the Cognito user pool. **No data migration needed.**

Lambda runtime: Python 3.12 (matches `pyproject.toml`). Handler:
`mangum.Mangum(app)`. Cold start: ~800ms (vs Express ~200ms). Mitigation:
SnapStart (works for Python now) or provisioned concurrency on the v2
path once we cut over.

### 1.4 Frontend feature flag

`app/frontend/src/api/client.ts` reads `VITE_API_VERSION` (default `v1`).
A localStorage override (`?api=v2`) flips one user to v2 for soak
testing. Once v2 is stable, the default flips.

### 1.5 Test parity

- **Contract tests** (`tests/staging/`) get parameterised over v1 and v2.
  Same fixtures, both endpoints, both must pass.
- **Smoke tests** (`tests/post_deploy/`) run against both stacks.
- **Benchmark suite** is unaffected — it doesn't touch the API.

### 1.6 Exit criteria for Phase 1

- [ ] `app/backend_py/` deploys to dev + staging via CDK.
- [ ] All `tests/staging/` and `tests/post_deploy/` pass against v2.
- [ ] CloudWatch shows v2 error rate within 2% of v1 over 24h.
- [ ] At least one engineer (Nick) has used `?api=v2` for a full session.

---

## Phase 2 — Frontend cutover

**Goal:** flip `VITE_API_VERSION` default to `v2`. v1 remains live as a
fallback for one release cycle.

### 2.1 Steps

1. Bump `VITE_API_VERSION=v2` in `app/.env.dev`, then `app/.env.prod`.
2. Deploy frontend with the new default.
3. Soak for one week. Watch CloudWatch error metrics, watch user reports.
4. If any regression, set `VITE_API_VERSION=v1` and redeploy frontend
   (no backend change required). This is the kill-switch.

### 2.2 Exit criteria

- [ ] Production traffic on v2 for 7 days with error rate ≤ v1's baseline.
- [ ] Smoke suite green on v2.
- [ ] Zero open incidents tied to v2.

---

## Phase 3 — Decommission Express

**Goal:** delete `app/backend/`. Reclaim 26k lines of TS.

### 3.1 Steps

1. Delete the v1 Lambda + API Gateway stage in CDK. Keep DynamoDB, S3,
   Cognito.
2. Delete `app/backend/` and the npm workspace entry pointing at it.
3. Delete `app/backend/src/types/{models,schemas}.ts` (already a
   re-export shim from Phase 0a).
4. Update `./Quickstart verify` to drop the `backend` stage.
5. Update `.github/workflows/ci.yml` to drop the `backend` job.
6. Remove every `app/backend` reference from `CLAUDE.md`.
7. Drop the `uuid` ignore from `.github/dependabot.yml`. Added because
   uuid v14 is ESM-only and the Express jest setup (ts-jest CJS, no
   `transformIgnorePatterns`) couldn't load it; once Express is gone
   the constraint expires. See PR #147 (closed 2026-05-12) for the
   diagnosis.

### 3.2 Exit criteria

- [ ] `app/backend/` does not exist.
- [ ] `./Quickstart verify` runs `python` and `frontend` stages only.
- [ ] CI is faster (one fewer job).
- [ ] CDK stacks list shows no Express Lambda.

### 3.3 Don't forget

- Compat routes (`app/backend/src/routes/compat.ts`) — these are a
  smell flagged in the original audit. Phase 3 is the natural moment
  to ask "do we still have any consumer of the compat shape?" If yes,
  port it to FastAPI. If no, delete it.

---

## Phase 4 — Drop the Rust billing Lambda

**Goal:** rewrite `stripe/` (Rust) as a Python Lambda. ~100 lines, reuses
existing `boto3` and shares the DynamoDB client. Removes the third
runtime entirely.

### 4.1 Why this is post-cutover

The Rust Lambda is functionally orthogonal to the API migration. We do
it last because:
- Stripe webhook downtime tolerance is **zero** — best to touch this
  when nothing else is changing.
- The benefit is purely cleanup; users see nothing.

### 4.2 Steps

1. New `stripe_py/` directory. FastAPI `POST /webhook` handler with
   signature verification using the `stripe` Python SDK.
2. CDK stack swaps `RustFunction` for `PythonFunction`.
3. Deploy to dev. Run a $0.50 test charge end-to-end (Stripe test mode).
4. Deploy to prod during a low-traffic window.
5. Delete `stripe/` (Rust source).

### 4.3 Exit criteria

- [ ] No `cargo`, `clippy`, or `rust-toolchain` references in CI.
- [ ] `stripe_py/` handles all webhook events the Rust Lambda did.
- [ ] One real charge round-trips through prod.

---

## Phase 5 — Migration archive cleanup ✅ shipped 2026-04-30

**Goal:** move one-time migration scripts out of `cli/` into
`scripts/migrations/<date>-<name>.py`. Pure hygiene; not blocking
anything.

**What landed:** commit `c322393` deleted 13 finished one-shot scripts
outright (`cli/migrate_electric_cylinders.py`, `cli/migrate_units_to_dict.py`,
`cli/batch_servo_*.py`, `cli/ingest_tolomatic.py`, `cli/units_triage.py`,
plus dead refs in FUTURE.md and Quickstart). The earlier
`scripts/migrations/2026-04-26-batch_process.py` is the lone archived
script kept for provenance. Net effect matches Phase 5's intent — the
`cli/` directory now only contains active recurring tools.

Effort: 0.5 day. Risk: zero. Run when the queue is empty.

---

## 3. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Generated TS doesn't compile against existing frontend code | medium | medium | Phase 0 ships generation only; Phase 0a does the cutover gradually with `verify` as the gate |
| FastAPI cold-start hurts UX | medium | low | SnapStart / provisioned concurrency on v2 path |
| Auth middleware behaviour drifts from Express version | medium | high | Port test-by-test from `app/backend/tests/auth.middleware.test.ts`; phase 5 recovery lands first |
| Pydantic computed_field doesn't emit cleanly to TS | low | low | Fallback: drop `@computed_field` for `PK`/`SK` and compute on read in the API |
| Hidden coupling: a TS service consumes a non-standard shape | low | medium | Phase 1 runs in parallel; v2 doesn't replace v1 until contract tests are 100% green on both |
| Stripe webhook downtime during Phase 4 cutover | low | high | Deploy during low-traffic window; have a Rust-Lambda rollback ready in CDK |
| Polyglot dev experience worsens before Phase 3 | high | low | Acceptable — Phase 1-2 deliberately runs both backends; pain is bounded |

---

## 4. Decisions deferred (not blocking Phase 0)

These are real questions a senior would ask. None of them gate the codegen
work. Each becomes its own scoped doc when it matters.

1. **OpenAPI client codegen for the frontend.** Once FastAPI is up, do we
   point `openapi-typescript` at `/openapi.json` and generate the client
   too? Probably yes. Tracked in Phase 1, decided then.
2. **Rate limiting layer.** Express has none today; FastAPI gets one
   built in via `slowapi`. Default to "match current behaviour" (i.e.
   none) and add it post-cutover.
3. **Async DynamoDB client.** `aioboto3` exists. The pipeline is sync.
   Decision: stay sync in Phase 1; revisit if request latency becomes a
   complaint.
4. **Where does Pydantic `Mangum` Lambda Web Adapter live in the source
   tree?** `app/backend_py/src/main.py` is the strawman. Could also be
   `specodex/api/` if we want the API to ship with the library. Defer
   until Phase 1 starts.
5. **Migrating the agent / processor / scraper into the same FastAPI
   app.** Tempting, but they're cron-driven, not request-driven. Keep
   them as separate Lambdas (current shape). No microservices unless
   required.

---

## 5. Triggers — when to surface this doc

If your current task matches any of these, raise this plan before going
further:

- Touching `app/backend/src/**`. Anything beyond a bug fix should ask
  "does this make Phase 3 harder?"
- Adding a new product type. The runbook (`CLAUDE.md` "Adding a new
  product type") still says 6 files; once Phase 0a/b lands it'll be 2.
  Until then, do the 6, but keep the codegen migration in mind.
- Adding a new endpoint. If it'll just be re-implemented in FastAPI
  next month, weigh the cost.
- Touching `app/backend/src/types/{models,schemas}.ts`. **Stop.** Edit
  the Pydantic model and re-run `./Quickstart gen-types` instead. The
  hand-edit will get blown away by Phase 0a.
- Touching `stripe/` (Rust). Phase 4 is queued.

---

## 6. What this plan deliberately does not do

- **Pick a definite calendar date for Phase 1.** That's a scheduling
  decision, not a planning decision.
- **Assume FastAPI is the right framework.** Litestar, Robyn, and
  vanilla `aws-lambda-powertools` are all candidates. FastAPI is the
  strawman because (a) Pydantic-native, (b) auto OpenAPI, (c) widest
  community. Re-evaluate at Phase 1 kickoff.
- **Mandate a Cognito → Clerk swap.** Clerk is a faster-to-ship
  alternative the audit flagged, but PHASE5_RECOVERY.md lands Cognito
  first. Don't conflate the two.
- **Re-shape DynamoDB.** Adding GSIs is its own plan; doing it
  concurrently with a backend rewrite is exactly the "two refactors at
  once" anti-pattern.
- **Promise that this collapses the polyglot stack to one language.**
  CDK stays in TS. Frontend stays in TS (React). After Phase 4 the
  runtime count is 2 (Python app-layer + TS UI/IaC), not 1.

---

## 7. Open questions for Nick — answered 2026-05-14

All four questions answered. Decisions captured below so future
sessions don't re-litigate them. **Phase 1 is now unblocked end-to-end.**

1. **Calendar.** No calendar gate. Phase 1 can land whenever the
   code is ready — not waiting on SEO / MARKETING / GODMODE.
2. **Stretch on this code path.** Parallel-deploy strategy is
   acceptable. Don't learn FastAPI on the critical path; let v2
   stand up alongside v1, soak, then flip.
3. **Risk appetite for v2 traffic.** **Full cutover.** No 10% canary
   week — there are no users to protect from a bad v2. Kill-switch
   via `VITE_API_VERSION=v1` redeploy is the safety net. This
   simplifies Phase 2 considerably; remove the canary-traffic
   plumbing from any Phase 2 PR draft.
4. **Express deprecation date.** No hard date. "When v2 is stable"
   is the trigger — could be tomorrow, could be a year, the only
   bar is exit criteria green and Phase 3 cleanup landed.

**Implication for the scaffold (Phase 1.1, shipped via PR opened
2026-05-14):** the FastAPI app stands up at `app/backend_py/` with
its `/api/v2/...` namespacing reserved for the CDK wiring step.
Routes already carry the Express-compatible `/api/products/...`
prefix internally; the operator's CDK PR maps the Lambda's path
to `/api/v2/*`.

---

## 8. References

- `todo/PHASE5_RECOVERY.md` — auth must land before Phase 1.
- `CLAUDE.md` — "Adding a new product type" runbook (target of Phase
  0b's collapse).
- `pydantic-to-typescript` — https://github.com/phillipdupuis/pydantic-to-typescript
- `Mangum` — https://mangum.fastapiexpert.com/
- `FastAPI` — https://fastapi.tiangolo.com/

The original code audit lives in git history at `todo/REFACTOR.md`
(deleted 2026-05-03 once its findings were operationalised here, in
`MODELGEN.md`, and in `PYTHON_STRIPE.md`).
