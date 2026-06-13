# PAYGATE — per-query Stripe charging for the API

> 🔴 needs sign-off — code complete, test-mode, **dormant** until a Stripe
> query meter/price is configured. Draft PR; activation steps are Nick's.

Charge programmatic API consumers per query, metered through the
existing Stripe subscription. The browsable public website is
untouched — it sends no API key and stays free.

## Decisions (chosen 2026-06-13)

- **Billing model:** metered subscription. Each billable query reports
  +1 usage to a Stripe **query meter** via the billing Lambda's
  `/usage/query`, invoiced at period end. Reuses the existing
  metered-subscription plumbing.
- **Gate:** API keys. A request carrying `X-API-Key` is verified →
  must have an active subscription → served → metered. No header →
  the existing free, anonymous, read-only public path (the UI).
- **Billable endpoints:** `/api/v1/search` and `/api/v1/relations/*`
  only. Product/datasheet listing stays free.

## What shipped in this PR (test-mode, dormant)

Feature-flagged by `STRIPE_QUERY_PRICE_ID` on the billing Lambda. While
unset: checkout adds no query line item, `/usage/query` records
nothing, and the paygate still serves keyed requests but meters zero.

**Billing Lambda (`stripe_py/`)**
- `config.stripe_query_price_id` (optional env `STRIPE_QUERY_PRICE_ID`)
  + `per_query_billing_enabled`.
- `POST /apikey` — mint a key for an existing user (hash stored, plaintext
  returned once). `POST /apikey/verify` — resolve key → owner + live
  subscription status. `POST /usage/query` — report N queries to the
  query-priced subscription item.
- API-key records live in the `datasheetminer-users` table under a
  synthetic `apikey#<sha256>` PK — **no new table or GSI.**
- Checkout adds the query price as a second metered line item when the
  flag is on.

**Backends (Express v1 live + FastAPI v2 mirror, at parity)**
- `apiKeyPaygate` middleware on search + relations: no key → free;
  unknown key → 401; valid + no active sub → **402**; valid + active →
  served, then +1 metered **after a <400 response** (failed queries
  aren't billed). Billing outage → **fail open** to free (never 500s
  the read API).
- `POST /api/apikeys` — authed (Cognito JWT) mint route; identity from
  the token, never the body. Exempted from the public-mode readonly guard.
- Stripe clients gain `createApiKey` / `verifyApiKey` / `reportQueryUsage`.

Tests: billing Lambda +30 (apikeys, query_usage, router); Express +11;
FastAPI v2 +9. All adversarial paths covered (key forgery, hash-as-key,
IDOR, deleted-owner, no-sub 402, failed-query-not-metered, outage
fail-open).

## Activation — Nick's steps (not done here)

1. **Stripe dashboard (test mode):** create a metered **Meter** for
   queries and a recurring metered **Price** on the existing product.
   Note the price id (`price_...`).
2. **Billing Lambda env:** add `STRIPE_QUERY_PRICE_ID=price_...` to the
   deploy `--environment` block (see `stripe_py/README.md`). No CDK
   change — the billing Lambda is deployed outside CDK by design.
3. **Re-checkout:** existing subscribers predate the query line item;
   they need a new checkout (or a Stripe-side subscription-item add) to
   gain the query meter. New subscribers get both items automatically.
4. **Mint a key:** `POST /api/apikeys` with a Cognito token → returns
   `sk_query_...` once.
5. **Verify metering** in the Stripe test dashboard, then plan the
   live-mode flip as a **separate** exercise (the test-mode hard guard
   in `config.py` blocks live keys until deliberately lifted).

## Follow-ups (not blocking)

- **Direct DynamoDB read for verify/meter.** The paygate does two
  billing-Lambda hops per metered query (verify + usage). Fine for
  launch; cache verify results or read the users table directly if
  search latency matters.
- **Per-key revocation + listing.** Today a key lives until the table
  row is deleted by hand. Add `DELETE /api/apikeys/:id` + a list view
  when self-serve key management is needed.
- **Rate-limit the free anonymous path** if programmatic users route
  around the paygate by simply not sending a key (the paygate charges
  key holders; it doesn't *force* keys). Product call, not a bug.
- **Pre-existing bug (separate):** the Express `reportUsage` sends
  `{user_id, tokens}` but the Lambda's `UsageRequest` wants
  `{input_tokens, output_tokens}` — token metering silently no-ops
  today. Out of scope here; flag for a token-metering fix.
