# PYTHON_STRIPE — Rust → Python migration for the billing Lambda

> **Status:** 🚧 Phase 1 layout scaffolded — `stripe_py/` exists with all 7
> modules and 8 test files. Phase 1 deploy + Phase 2 SSM cutover + Phase 3
> Rust deletion still pending. Independent of `todo/PYTHON_BACKEND.md`
> Phases 0–3 (the Express → FastAPI cutover); can ship at any time.
>
> **Date drafted:** 2026-05-02.
> **Owner:** Nick.

---

## 0. Why this exists

The `stripe/` Rust Lambda is **the only Rust in the repo**. It is ~500
LoC across 7 files, handles 5 endpoints in test-mode metered billing,
and was originally justified as "fast cold starts" — a property that
buys nothing for Stripe webhooks (async, nobody waits) or Checkout
sessions (the user is being redirected to Stripe). The cost of carrying
it is the entire `cargo` toolchain in CI, a separate deploy path
(`cargo lambda deploy` — not in CDK), and a third mental context for
every developer.

Replacing it with a Python Lambda:

- Reuses the existing `boto3` and `python-dotenv` already in
  `pyproject.toml`.
- Uses the official `stripe` Python SDK (which exposes
  `Webhook.construct_event` — no need to reimplement the HMAC-SHA256
  signature check by hand, as the Rust impl does).
- Drops `cargo`, `clippy`, `rust-toolchain`, the ARM64 cross-compile
  dance, and the `stripe/Cargo.toml` dep tree.
- Brings the runtime count from 3 (Python + TS + Rust) to 2.
- Closes the polyglot-tax loop the original audit opened.

**Out of scope (don't bundle into this work):**

- Moving billing into the existing API Lambda. The billing surface
  stays a separate Lambda with its own Function URL — the SSM-driven
  URL handoff (see §1.3) is the cleanest cutover mechanism we have.
- Switching off test mode. The new Lambda must keep the same hard
  guard (refuse to start without `sk_test_...`).
- Adding new endpoints, new webhook event types, or new billing
  models. Behaviour parity first; new features in their own scoped
  doc.
- CDK ownership of the billing Lambda. Today it's deployed
  out-of-band via `cargo lambda deploy`; the Python Lambda will keep
  the same out-of-band shape for Phase 1, with CDK adoption optional
  in Phase 3.

---

## 1. End-state shape

```
app/backend/services/stripe.ts ── HTTP ──►  Python billing Lambda
        ▲                                      │
        │                                      ├── stripe (Python SDK)
        │                                      │
        │                                      └── boto3 ──► DynamoDB
        │                                                    (datasheetminer-users)
        │
        └── reads `${ssmPrefix}/stripe-lambda-url` (unchanged)
```

**Single source of truth for the URL: SSM.** The Express backend already
reads `${ssmPrefix}/stripe-lambda-url` (see
`app/backend/src/config/index.ts`) and calls into whatever Lambda lives
behind it. Cutover = update the SSM parameter; rollback = update it
back.

**What stays:**

- `datasheetminer-users` DynamoDB table (same schema, same name, same
  region).
- `${ssmPrefix}/stripe-lambda-url` SSM parameter (just points at a
  new Function URL).
- The 5-endpoint contract: `POST /checkout`, `POST /webhook`,
  `POST /usage`, `GET /status/{user_id}`, `GET /health`.
- `app/backend/src/services/stripe.ts` (the Express-side fetch
  client) — it speaks JSON; it doesn't care what runtime is on the
  other end.
- The test-mode hard guard.

**What goes:**

- `stripe/` (the entire Rust crate — `Cargo.toml`, `Cargo.lock`,
  `src/{main,config,checkout,db,models,usage,webhook}.rs`, `.env.example`).
- Any `cargo` / `cargo-lambda` references in CI.
- Manual HMAC-SHA256 signature verification (Rust `webhook.rs:88-117`)
  — replaced by `stripe.Webhook.construct_event(...)` from the
  official SDK.

---

## 2. Phases at a glance

| Phase | Scope | Effort | Risk | Reversible? | Gate |
|---|---|---|---|---|---|
| **1.1 layout** | `stripe_py/` directory + 7 modules + 8 test files matching §1.1 | shipped | — | — | ✅ scaffold landed in `6ac30b0` |
| **1.x deploy** | Test-suite green, Function URL provisioned on dev, `$0.50` test charge round-trips | 0.5–1 day | low | yes (Function URL is throwaway) | `tests/integration/test_billing_py.py` green; round-trip works on dev |
| **2** | Cutover: update prod SSM `stripe-lambda-url` to the Python Function URL; soak 7 days; Rust Lambda stays alive but unused | 0.5 day + soak | medium | yes (one SSM `put-parameter` reverts) | zero billing-related errors in CloudWatch over the soak; one real webhook (subscription update or invoice event) processed cleanly |
| **3** | Delete `stripe/` (Rust crate). Optional: fold `stripe_py/` into CDK as a managed `PythonFunction`. | 0.5 day | low | git revert | `./Quickstart verify` green; CDK synth clean |

**Phase 1 ships independently and pays for itself even if Phase 2
never happens** — at worst we land a tested Python Lambda no one is
using, and the choice to cut over is a future SSM update.

---

## Phase 1 — Stand up `stripe_py/` in parallel

**Goal:** byte-compatible drop-in replacement for the Rust Lambda,
deployed as a separate Lambda with its own Function URL. The Express
backend keeps calling the Rust Lambda; the Python Lambda is dark.

### 1.1 Layout — ✅ shipped (commit `6ac30b0`)

```
stripe_py/
├── pyproject.toml              # scoped to this dir; uv workspace member
├── README.md                   # mirrors stripe/README.md
├── .env.example
├── src/
│   └── billing/
│       ├── __init__.py
│       ├── handler.py          # Lambda entrypoint: lambda_handler(event, context)
│       ├── router.py           # path/method dispatch (5 routes)
│       ├── config.py           # env loader + test-mode guard
│       ├── models.py           # Pydantic request/response shapes
│       ├── db.py               # boto3 DynamoDB wrapper (UsersDb)
│       ├── checkout.py         # create_checkout_session
│       ├── webhook.py          # construct_event + dispatch by event type
│       └── usage.py            # report_usage
└── tests/
    ├── conftest.py             # moto fixtures for DynamoDB, fake Stripe client
    ├── test_checkout.py
    ├── test_webhook.py
    ├── test_usage.py
    ├── test_status.py
    └── test_handler.py         # integration via the Lambda event shape
```

**Why a separate `pyproject.toml` (not just a `cli/billing/` module
under the root project):** the billing Lambda has its own dependency
footprint (`stripe`, `aws-lambda-powertools`) that has no business in
the pipeline's deps. Keeping it isolated means the Lambda zip is small
and the pipeline's deps don't bleed into the billing surface. Wire it
as a uv workspace member from the root `pyproject.toml` so
`./Quickstart verify` picks it up.

### 1.2 Framework choice — plain handler, not FastAPI

The Rust Lambda is a 5-route dispatch with no shared middleware (no
auth — this Lambda is internal to our infra, called by the Express
backend, not by users). FastAPI + Mangum buys nothing here and adds
~600ms to cold start.

**Strawman:** plain `lambda_handler(event, context)` with a 30-line
router function that pattern-matches on `(method, path)`. This is
what `stripe/src/main.rs:59-81` does in Rust — the Python equivalent
is ~50 lines.

If we ever want OpenAPI auto-docs or middleware here, switch to
`aws-lambda-powertools` (already a sensible default for AWS-native
Python Lambdas). FastAPI is overkill for 5 internal routes.

### 1.3 Endpoint behaviour parity

| Endpoint | Rust source | Python destination | Notes |
|---|---|---|---|
| `POST /checkout` | `checkout.rs:12` | `checkout.py:create_checkout_session` | Same Stripe SDK calls (`Customer.create`, `checkout.Session.create`). Same DynamoDB writes. |
| `POST /webhook` | `webhook.rs:12` | `webhook.py:handle_webhook` | **Use `stripe.Webhook.construct_event(payload, sig_header, secret)`** — drops the manual HMAC code in `webhook.rs:88-117`. Same event handlers (`checkout.session.completed`, `customer.subscription.updated|deleted`, `invoice.payment_failed`). |
| `POST /usage` | `usage.rs:8` | `usage.py:report_usage` | Same: lookup user → require active subscription → fetch subscription items → `POST /v1/subscription_items/{id}/usage_records`. **Note:** stripe-python ≥10 dropped the `SubscriptionItem.create_usage_record` wrapper, so the Python impl uses `StripeClient.raw_request("post", ...)` to hit the legacy metered endpoint Stripe still serves (and that the existing Dashboard product is configured against). Migration to the modern Meter Events API would require reconfiguring the Stripe Dashboard product (legacy metered prices → Billing Meters); deliberately out of scope for this port (see §6). |
| `GET /status/{user_id}` | `main.rs:130` | `handler.py` (inline; trivial) | Same: get_user, return `{user_id, subscription_status, stripe_customer_id}` or `none` if missing. |
| `GET /health` | `main.rs:70` | `handler.py` (inline) | Returns `{"status": "ok", "mode": "test"}`. |

**Status enum parity** (`stripe/src/models.rs:15`): `active`,
`past_due`, `canceled` (also accept `cancelled`), `incomplete`,
`none`. Python uses a `StrEnum` with the same values. DynamoDB
serialisation stays string-typed; no migration needed because the
shape is byte-identical.

**Test-mode guard parity** (`stripe/src/config.rs:28`): refuse to
start if `STRIPE_SECRET_KEY` doesn't start with `sk_test_`. In Python:
raise `RuntimeError` at module import time inside `config.py`. The
Lambda fails to initialise and CloudWatch shows the error — same UX
as the Rust panic.

### 1.4 Webhook signature verification — use the SDK

Rust currently rolls its own HMAC-SHA256 verification (`webhook.rs:88`)
because `async-stripe` doesn't ship a verifier helper. The Python
SDK does ship one:

```python
import stripe
event = stripe.Webhook.construct_event(
    payload=raw_body,
    sig_header=request_headers["stripe-signature"],
    secret=config.stripe_webhook_secret,
)
# event is stripe.Event with .type, .data.object, etc.
```

This is the canonical path Stripe documents. **Do not port the manual
HMAC code.** The SDK handles timestamp tolerance, signature
encoding edge cases, and version skew correctly. Less code, fewer
foot-guns.

### 1.5 DynamoDB — reuse `specodex/db/dynamo.py` patterns

The `datasheetminer-users` table is **separate** from the products
table, so we can't share the existing `DynamoDBService`. But we copy
its idioms (boto3 client memoised at module scope, attribute-value
helpers, `moto` integration tests). 4 methods on the new `UsersDb`
class, mirroring `stripe/src/db.rs`:

- `get_user(user_id) -> UserRecord | None`
- `get_user_by_customer_id(customer_id) -> UserRecord | None`
  — currently a full table scan in Rust (`db.rs:32`); keep it as a
  scan for now (table is small) but **add a TODO** to introduce a
  GSI on `stripe_customer_id` when row count justifies it.
- `put_user(record) -> None`
- `update_subscription_status(user_id, sub_id, status) -> None`

### 1.6 Models — Pydantic, not dataclasses

Reuses our existing Pydantic dependency. The 5 request/response shapes
in `stripe/src/models.rs:38-72` become Pydantic models in
`stripe_py/src/billing/models.py`. The router validates inbound
JSON via `Model.model_validate_json(...)` (returns 400 on
`ValidationError`) and serialises responses with `model_dump_json()`.

This is the only place the project gets uniformity for free: every
service shape, end-to-end, is a Pydantic model.

### 1.7 Deployment

**Phase 1 keeps the out-of-band shape.** Ship via SAM CLI or `aws
lambda` directly:

```bash
cd stripe_py
uv pip install --target ./build -r requirements.txt   # or use uv-managed venv + zip
zip -r function.zip src/ build/
aws lambda create-function \
  --function-name datasheetminer-payments-py \
  --runtime python3.12 \
  --handler billing.handler.lambda_handler \
  --zip-file fileb://function.zip \
  --role <existing-role-arn> \
  --environment "Variables={STRIPE_SECRET_KEY=sk_test_...,STRIPE_WEBHOOK_SECRET=whsec_...,STRIPE_PRICE_ID=price_...,USERS_TABLE_NAME=datasheetminer-users,FRONTEND_URL=https://datasheets.advin.io}"

aws lambda create-function-url-config \
  --function-name datasheetminer-payments-py \
  --auth-type NONE
```

Or — and probably better for the longer-term — wrap this in a
`./Quickstart deploy-billing-py` subcommand that does the same dance
once. Cargo-lambda is gone; we don't need an exotic build tool.

**Note:** Lambda Python 3.12 ships boto3 in the runtime layer. The
deploy zip only needs to bundle `stripe`, `pydantic`, and
`python-dotenv` (~5 MB). No layers required.

### 1.8 Local testing

```bash
# Stripe CLI for webhook signature replay
stripe listen --forward-to http://localhost:9000/webhook

# AWS SAM local emulator for the Lambda runtime
cd stripe_py && sam local start-api  # if we add template.yaml
# OR just run the handler directly with a mock event
uv run python -c "from billing.handler import lambda_handler; print(lambda_handler({...}, None))"
```

Test fixtures use `moto` for DynamoDB (already in `[dependency-groups].dev`)
and a fake Stripe client that records calls (using `pytest-mock` —
also already a dep).

### 1.9 Exit criteria for Phase 1

- [ ] `stripe_py/` exists with the 7 modules above.
- [ ] `uv run pytest stripe_py/tests/` is green.
- [ ] `./Quickstart verify` runs the new tests as part of the Python
      stage.
- [ ] The Lambda is deployed to **dev** with its own Function URL.
- [ ] A `$0.50` Stripe test-mode charge round-trips:
      `/checkout` → Stripe Checkout → `/webhook (checkout.session.completed)`
      → user marked active in DynamoDB → `/usage` records 1000 tokens
      → Stripe dashboard shows the usage record.
- [ ] Rust Lambda still runs prod traffic. No SSM change yet.

---

## Phase 2 — Cutover via SSM URL flip

**Goal:** switch prod traffic from the Rust Function URL to the Python
Function URL with a single `aws ssm put-parameter` call.

### 2.1 Pre-flight

- [ ] Phase 1 exit criteria all green.
- [ ] Python Lambda also deployed to **prod** (same script, prod env
      vars, prod role). It is live but unreached because no one calls
      its URL yet.
- [ ] Pull the current SSM URL value and stash it for rollback:

      ```bash
      aws ssm get-parameter --name /datasheetminer/prod/stripe-lambda-url \
        --query 'Parameter.Value' --output text
      # Save this output as the rollback target.
      ```

### 2.2 Cutover

```bash
# Replace <python-function-url> with the URL output from Phase 1's
# create-function-url-config.
aws ssm put-parameter \
  --name /datasheetminer/prod/stripe-lambda-url \
  --value <python-function-url> \
  --overwrite \
  --type String
```

The Express backend reads SSM at boot. **This means existing warm
Lambdas keep using the Rust URL until they cycle.** Force a cycle by
either deploying a no-op change to the API Lambda or by waiting for
natural cycling (~hours under low traffic). Plan for a 1-hour overlap
window where both backends could be hit.

The overlap is safe — both Lambdas read/write the same DynamoDB rows
and same Stripe customer/subscription records. The only risk is **one
webhook event processed twice** if Stripe retries during the window,
which is idempotent because:
- `checkout.session.completed`: setting subscription_id and status to
  `active` twice is a no-op.
- `customer.subscription.updated|deleted`: setting status to whatever
  Stripe just told us, twice, is a no-op.
- `invoice.payment_failed`: only logs; no state change.

### 2.3 Soak

7 days of CloudWatch monitoring on the Python Lambda:

- Error rate ≤ baseline (Rust Lambda's 7-day error rate before
  cutover).
- p50 / p99 latency within 2× of Rust (cold starts will be worse;
  steady-state should be fine).
- At least one real webhook of each type (subscription update, invoice
  event) processed cleanly.

If anything looks wrong, revert in one command:

```bash
aws ssm put-parameter \
  --name /datasheetminer/prod/stripe-lambda-url \
  --value <rust-function-url-from-step-2.1> \
  --overwrite --type String
```

### 2.4 Exit criteria for Phase 2

- [ ] Prod SSM URL points at the Python Lambda for 7 consecutive days.
- [ ] Zero billing-related incidents during the soak.
- [ ] Stripe dashboard shows usage records and webhook deliveries
      processed in the soak window.

---

## Phase 3 — Delete Rust + optional CDK adoption

**Goal:** reclaim the diff. Optional follow-up: bring the Python
Lambda into CDK so its deploy stops being out-of-band.

### 3.1 Delete the Rust crate

```bash
git rm -r stripe/
```

Touch points to clean up afterwards:

- `.github/workflows/ci.yml` — drop any `cargo` / `clippy` /
  `cargo-lambda` steps if they exist.
- Root `README.md` / `CLAUDE.md` — search for `Rust`, `cargo`, the
  `stripe/` directory; rewrite the references.

### 3.2 Optional: fold `stripe_py/` into CDK

Today the billing Lambda is deployed via a shell script. CDK ownership
buys: stack-level rollback, infra change-tracking, environment-pinned
config. It costs: a `PythonFunction` construct in
`app/infrastructure/lib/api-stack.ts` and dep changes in CDK's own
`package.json` (the `aws-cdk/aws-lambda-python-alpha` module). For a
single 5-endpoint Lambda this is a real win, but it's not required for
the migration to be "done." Defer if the queue is full.

If we do it: the Function URL gets created by CDK, and the SSM
parameter becomes a CDK output reference instead of a hand-edited
value. The cutover (Phase 2) is then just a CDK deploy with the new
URL.

### 3.3 Exit criteria for Phase 3

- [ ] `stripe/` directory is gone.
- [ ] No `cargo` references anywhere in `.github/`, `Quickstart`,
      `cli/`, or root configs.
- [ ] `./Quickstart verify` green.
- [ ] (Optional) CDK synth shows the Python billing Lambda and its
      Function URL as managed resources.

---

## 3. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Stripe webhook downtime during cutover overlap | low | high | Both Lambdas are idempotent on the events we handle (see §2.2). Cutover is reversible via SSM. |
| Python cold start ≥ 2s vs Rust ~50ms | medium | low | Webhooks are async; Checkout is a redirect; nobody is staring at a spinner. If `/status` or `/usage` latency complaints arise, enable provisioned concurrency on the billing Lambda (~$5/mo). |
| `stripe.Webhook.construct_event` throws on a malformed signature where Rust would have silently rejected | low | low | The exception path is the same: return 400. Test parity in `tests/test_webhook.py` with malformed signatures. |
| `boto3` scan in `get_user_by_customer_id` becomes slow as the users table grows | low | medium | Same risk exists today in Rust. Add a GSI on `stripe_customer_id` when row count > 5k. Tracked as a TODO in `db.py`. |
| Missing env var in Python Lambda silently defaults instead of erroring | low | medium | Mirror `config.rs`: every required var raises in `config.py` at module import; missing vars surface as a CloudWatch init error, not a runtime 500. |
| Test-mode guard regression — accidentally accepting `sk_live_` | low | very high | Hard guard in `config.py` *plus* a unit test (`test_config.py::test_refuses_live_keys`). |
| The Python deploy zip exceeds Lambda's 50 MB direct-upload limit once `stripe`+`pydantic` deps are bundled | low | low | Empirically `stripe`+`pydantic`+`python-dotenv` is ~5 MB. If it ever balloons, switch to a Lambda Layer or the container-image runtime. |

---

## 4. Decisions deferred (not blocking Phase 1)

These don't gate the migration. Each becomes its own scoped doc when
it matters.

1. **CDK ownership of the billing Lambda.** Phase 3 §3.2 — defer if
   the queue is full.
2. **GSI on `stripe_customer_id`.** Today it's a scan in both Rust and
   Python. Worth doing eventually; not now.
3. **Live-mode flip.** Today the Lambda hard-rejects `sk_live_`. The
   migration to live billing is a separate exercise that touches
   pricing, terms-of-service, and PCI scope review. Out of scope here.
4. **Move billing into the FastAPI service** (the Phase-1 destination
   in `todo/PYTHON_BACKEND.md`). Tempting once the FastAPI service
   exists — same Pydantic models, same Lambda runtime. But billing's
   security posture (Stripe webhook signatures, isolated DynamoDB
   table, fail-open semantics) argues for keeping it isolated. Defer
   the decision until both services are running.
5. **Async vs sync `stripe` calls.** The Python SDK is sync. Lambda
   is async-friendly via `asyncio.to_thread(...)`, but for a 5-route
   dispatch with no concurrent work per request, sync is fine.
   Revisit only if we ever batch operations.

---

## 5. Triggers — when to surface this doc

If your current task matches any of these, raise this plan before
going further:

- Touching `stripe/` (Rust source). Phase 1 is queued; new features
  belong in Python, not Rust.
- Adding a new webhook event type. Implement it once, in Python; do
  not double-implement in Rust.
- Adding a new endpoint to the billing surface. Same — Python only.
- Modifying `app/backend/src/services/stripe.ts` (the fetch client).
  Verify the change is runtime-agnostic (it should be — the fetch
  client doesn't care what's behind the URL). If you're tempted to
  add a feature flag for "Rust vs Python," don't — the SSM URL flip
  is the feature flag.
- Touching `${ssmPrefix}/stripe-lambda-url` in any deploy script.
  Phase 2 owns this parameter; coordinate cutovers.

---

## 6. What this plan deliberately does not do

- **Rewrite Rust as a learning exercise.** This is a port. The Rust
  shape is good; the Python shape is identical with a smaller dep
  tree.
- **Add new functionality.** Behaviour parity first. New billing
  features go in their own scoped doc, after cutover.
- **Promise zero downtime.** The SSM cutover has a 1-hour overlap
  window where both Lambdas may receive traffic. The events are
  idempotent (§2.2), so the practical downtime is zero — but the
  *guarantee* of zero would require API Gateway alias routing, which
  is overkill for an internal Lambda.
- **Replace the SSM URL handoff with API Gateway.** The current
  Function-URL-via-SSM shape is the simplest possible thing that
  works and is what makes cutover a one-line revert.
- **Bring the billing Lambda's dependencies into the root
  `pyproject.toml`.** Keeping `stripe` out of the pipeline's deps
  (and the pipeline's CI matrix) is a real isolation win.

---

## 7. Open questions for Nick

These need answers before Phase 2 cutover. **Phase 1 is unblocked
regardless.**

1. **Cutover timing.** Stripe webhooks are async, so the cutover is
   safe at any hour. Is there a preferred window (e.g., post-7pm PT
   on a Tuesday) for the SSM flip and the post-flip soak?
2. **Provisioned concurrency budget.** If we end up needing ~$5/mo
   for billing-Lambda warm starts, is that approved up-front, or
   does it need its own decision?
3. **CDK adoption (Phase 3 §3.2).** Want it bundled into Phase 3, or
   broken out as a separate small task?
4. **`./Quickstart deploy-billing-py` shape.** The current Rust
   deploy is a hand-run `cargo lambda deploy`. Worth investing 30
   minutes in a Quickstart subcommand for the Python deploy, or
   leave it as a documented shell snippet in `stripe_py/README.md`?

---

## 8. References

- `stripe/` — the Rust source being replaced (`Cargo.toml` + 7
  modules + README).
- `app/backend/src/services/stripe.ts` — the Express-side client
  whose URL points at the Lambda.
- `app/backend/src/middleware/subscription.ts` — the gate that
  consumes `/status/{user_id}`.
- `app/backend/src/routes/subscription.ts` — exposes
  `/api/subscription/{checkout,status}` on the Express side.
- `app/backend/src/config/index.ts` — reads
  `${ssmPrefix}/stripe-lambda-url` from SSM.
- `todo/PYTHON_BACKEND.md` Phase 4 — the seed sketch this doc
  expands. The original code audit lives in git history at
  `todo/REFACTOR.md`.
- `stripe` Python SDK — https://github.com/stripe/stripe-python
- `stripe.Webhook.construct_event` — https://docs.stripe.com/webhooks#verify-official-libraries
- `aws-lambda-powertools` (Python) — https://docs.powertools.aws.dev/lambda/python/
