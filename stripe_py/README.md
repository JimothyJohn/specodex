# DatasheetMiner Billing Lambda (Python)

Behaviour-parity replacement for the Rust Lambda in `../stripe/`. Same
five-endpoint contract, same `datasheetminer-users` DynamoDB table,
same hard test-mode guard.

**Status:** Phase 1 of `todo/PYTHON_STRIPE.md`. Code only. Not deployed.

## Why Python

Stripe webhooks are async; nobody waits on them. Checkout is a
redirect; cold-start latency is invisible. The Rust Lambda's
~50ms cold-start advantage buys nothing here, and carrying the `cargo`
toolchain for 500 lines of code is the textbook polyglot tax. See
`todo/REFACTOR.md` §4.1, §5.5.

## Layout

    stripe_py/
    ├── pyproject.toml          # standalone uv project (NOT a workspace member)
    ├── .env.example
    ├── src/billing/
    │   ├── handler.py          # Lambda entrypoint
    │   ├── router.py           # method/path dispatch (5 routes)
    │   ├── config.py           # env loader + test-mode guard
    │   ├── models.py           # Pydantic request/response shapes
    │   ├── db.py               # boto3 DynamoDB wrapper (UsersDb)
    │   ├── checkout.py
    │   ├── webhook.py
    │   └── usage.py
    └── tests/                  # pytest + moto, no live AWS or Stripe required

## Endpoints

Identical to `../stripe/README.md`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/checkout` | Create Stripe Checkout session → returns URL |
| `POST` | `/webhook` | Stripe webhook receiver |
| `POST` | `/usage` | Report token usage (called by your backend) |
| `GET`  | `/status/{user_id}` | Check subscription status |
| `GET`  | `/health` | Health check |

DynamoDB schema is unchanged from the Rust impl — same table name, same
attributes. Phase 2 cutover is purely the SSM URL flip.

## Local development

    cd stripe_py
    uv sync
    uv run pytest                       # all tests use moto + mocked Stripe
    uv run ruff check
    uv run ruff format --check

No live AWS or Stripe credentials needed for tests. The hard test-mode
guard in `config.py` would refuse to load `sk_live_...` even if you
tried.

## Deploying (manual, Phase 1 → dev)

The billing Lambda lives outside CDK by design — same shape as the
Rust deploy. CDK ownership is a deferred Phase 3 §3.2 follow-up.

    cd stripe_py
    rm -rf build && mkdir build
    uv pip install --target build/ stripe pydantic python-dotenv boto3
    cp -r src/billing build/
    (cd build && zip -r ../function.zip .)

    aws lambda create-function \
      --function-name datasheetminer-payments-py \
      --runtime python3.12 \
      --handler billing.handler.lambda_handler \
      --zip-file fileb://function.zip \
      --role <execution-role-arn> \
      --environment 'Variables={STRIPE_SECRET_KEY=sk_test_...,STRIPE_WEBHOOK_SECRET=whsec_...,STRIPE_PRICE_ID=price_...,USERS_TABLE_NAME=datasheetminer-users,FRONTEND_URL=https://www.specodex.com}' \
      --region us-east-1

    aws lambda create-function-url-config \
      --function-name datasheetminer-payments-py \
      --auth-type NONE

The execution role needs `dynamodb:GetItem`, `PutItem`, `UpdateItem`,
`Scan` on `arn:aws:dynamodb:*:*:table/datasheetminer-users`. The Rust
Lambda's existing role is reusable verbatim — no IAM change needed.

## End-to-end smoke (manual, $0.50 test charge)

    # 1. Create checkout session
    curl -X POST <function-url>/checkout \
      -H 'Content-Type: application/json' \
      -d '{"user_id": "smoke-1", "email": "you@advin.io"}'

    # 2. Visit the returned checkout_url, pay with Stripe test card
    #    4242 4242 4242 4242 / any future date / any CVC

    # 3. Stripe sends checkout.session.completed → /webhook
    #    (use `stripe listen --forward-to <function-url>/webhook`
    #    locally, or configure the prod webhook in dashboard.stripe.com)

    # 4. Verify activation
    curl <function-url>/status/smoke-1
    # → {"user_id":"smoke-1","subscription_status":"active",...}

    # 5. Report usage
    curl -X POST <function-url>/usage \
      -H 'Content-Type: application/json' \
      -d '{"user_id":"smoke-1","input_tokens":500,"output_tokens":500}'

## Cutover (Phase 2 — PROD, gated on Nick's approval)

    aws ssm put-parameter \
      --name /datasheetminer/prod/stripe-lambda-url \
      --value <python-function-url> \
      --overwrite --type String

The Rust Lambda stays live but unused. Rollback = re-run with the old
URL. See `todo/PYTHON_STRIPE.md` §2 for the full soak/rollback playbook.

## Differences from the Rust impl

- **Webhook signature** uses `stripe.Webhook.construct_event(...)`
  instead of hand-rolled HMAC-SHA256 (see `webhook.py`). The Stripe
  SDK handles timestamp tolerance and signature encoding edge cases;
  the Rust port reimplemented this from scratch (`webhook.rs:88`).
- **Models** are Pydantic, not serde structs. JSON-on-the-wire shape
  is byte-identical.
- **DynamoDB scan** for `get_user_by_customer_id` is unchanged — same
  TODO to add a GSI when row count > ~5k.
