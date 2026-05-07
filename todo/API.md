# Programmatic API access (paid)

Status: 📐 not started. Depends on:

- Cognito identity, JWT middleware, admin gating — ✅ shipped (auth Phases 1–4).
- SES verified-identity sender for welcome / receipt emails —
  ⏳ stranded on `feat-auth-phase5a-ses` (see [PHASE5_RECOVERY.md](PHASE5_RECOVERY.md)).
  Bouncing the welcome on a $50/mo invoice is bad.
- WAF rate-limit layer — ✅ shipped (auth Phase 5b on master). The
  data plane has a floor before any external-facing reader can hit
  it.
- The existing `stripe/` (Rust) or `stripe_py/` (Python port — see
  [PYTHON_STRIPE.md](PYTHON_STRIPE.md)) Lambda actually deployed and
  live. Today the Rust Lambda is unwired to the production stack.

This doc plans the **paid programmatic access tier** as a separate
product surface from the SPA's "logged-in user with a JWT in
localStorage" path. Engineering teams want curl, scripts, CI
integrations; that's a different auth shape, a different rate-limit
shape, and the right place to apply per-call billing.

## Why this is a separate doc

The SPA-user story (auth Phases 1–4 on master) is "interactive user
in a browser": register, log in, manage account, get admin powers.
Forcing API access to fit that model would mean issuing JWTs to
scripts (which expire every hour and require a refresh-token dance)
or running curl through OAuth (overkill).

Programmatic access wants:

- Long-lived credentials (don't refresh every hour).
- A header you can `curl -H ...` without a session manager.
- Per-key attribution and rate limits.
- Per-call billing — engineers want to see "how much did my CI run
  cost" without dividing by token counts.

Different auth shape → different doc.

## The thesis

**Reuse what's there:**

- **Cognito users for identity.** One person = one Cognito sub. API
  keys belong to a Cognito user; revoking a user revokes their keys.
  Don't fork into a separate "API user" model.
- **The `stripe/` Lambda for billing.** It already implements the
  hard half: per-user Stripe Customer, metered Price, usage records,
  subscription gate, DynamoDB users table. The integration story is
  "API keys map to user_ids; usage flows through the existing
  `/usage` endpoint unchanged."

**Add minimum viable surface:**

- API key issuance (one Cognito user → many keys).
- Per-key auth middleware on the API.
- Per-key usage attribution (so a leaked key gets revoked without
  killing the user's other keys).
- Self-service management UI in the SPA's account menu.

The boring parts (Stripe Customer setup, metered Price, webhooks for
subscription state, invoice generation) **are already done in
`stripe/`.** Don't re-invent them.

## What `stripe/` already does

Source: `stripe/README.md` and `stripe/src/`. Quick inventory:

| Capability | Endpoint | Status |
|------------|----------|--------|
| Create Stripe Customer + Subscription | `POST /checkout` | ✅ implemented |
| Receive subscription state changes | `POST /webhook` | ✅ implemented |
| Record per-call usage | `POST /usage` | ✅ implemented (token-based today) |
| Check subscription active | `GET /status/{user_id}` | ✅ implemented |
| Test mode enforced via `sk_test_` prefix check | n/a | ✅ implemented |

DynamoDB schema (`datasheetminer-users`):

```
user_id (PK)            String   App's user identifier (= Cognito sub)
stripe_customer_id      String
subscription_id         String
subscription_status     String   active | past_due | canceled | none
created_at              String   ISO 8601
```

**Today the wire format is `user_id` (a generic string).** That's
the integration point: Cognito sub fits the slot.

**One open Q on `stripe/`:** the metered Price today is per-token.
For a data API, billing per call (per record returned, per search
query) probably makes more sense than per token — engineers
intuitively price calls, not tokens. This is a Stripe Dashboard
config change (new Price ID), not a code change; the `/usage`
endpoint shape stays the same. Decision deferred until pricing is
actually being set.

## Phase A — API key model

DynamoDB layout. Lean toward **adding to `stripe/`'s users table**
since it's already keyed on `user_id` and tied to the billing
model. Don't fork a third table.

Schema additions:

```
PK = USER#<cognito_sub>
SK = APIKEY#<key_id>            <- ULID is fine; sortable + unique

Attributes:
  hashed_key         String      argon2id of the full secret
  prefix             String      sk_live_<8-char>          (for display)
  name               String      user-supplied label
  created_at         String      ISO 8601
  last_used_at       String      ISO 8601, async-updated
  revoked_at         String      ISO 8601 if revoked
  monthly_cap_cents  Number?     optional per-key spend cap
```

**Key format:** `sk_live_<22 chars base62>`. Stripe-style prefix so
GitHub secret scanning catches leaks (Stripe is on the partner list;
we'd register `sk_live_` for our own scanner if we ever scale to
that, but the format also makes our own internal grep tooling easy).

**Show the full key once on creation.** Store only the argon2id hash.
Lose-it-once-it's-gone is the standard for API keys; nobody expects
to retrieve the raw value.

## Phase B — Auth middleware

Header convention: `Authorization: Bearer sk_live_...`. **Same header
as JWT auth.** The middleware sniffs the prefix to decide which path:

```typescript
// app/backend/src/middleware/auth.ts (extend existing)
export async function requireAuth(req, res, next) {
  const token = extractBearer(req);
  if (!token) return res.status(401).json({...});

  if (token.startsWith('sk_live_')) {
    // API key path
    const user = await verifyApiKey(token);
    if (!user) return res.status(401).json({...});
    req.user = user;        // same shape as the JWT path
    return next();
  }

  // JWT path (existing)
  const user = await verifyToken(token);
  ...
}
```

`verifyApiKey` flow:

1. Hash the incoming key with argon2id.
2. Lookup by hashed_key (GSI on the users table — `hashed_key` →
   `user_id`).
3. If not found or `revoked_at` set → 401.
4. Async: update `last_used_at`. Fire-and-forget; do not block the
   request.
5. Resolve to `{ sub, email, groups }` — same `AuthedUser` shape as
   the JWT path so downstream middleware (subscription gate,
   adminOnly) doesn't need to know which auth path was taken.
6. Call `stripe/`'s `GET /status/{user_id}`; if not active → 402
   Payment Required.

After the endpoint completes, fire `POST /usage` with the call's
token count or record-count, depending on what the metered Price ends
up tracking.

**Performance:** argon2id is intentionally slow (~50ms typical). Cache
the hashed_key → user_id lookup in-memory for ~30s with a small LRU.
On a Lambda warm container this drops typical key-auth overhead to
<1ms after first hit.

## Phase C — Rate limiting

Per-key sliding window. Two layers:

**Edge (WAFv2 rate-based rule, keyed on Authorization header).**
Picks up bursts before the Lambda even spawns. Cheap; piggybacks on
the auth Phase 5b WAF stack already on master.

**Application-side (Lambda + DynamoDB token bucket).** Fine-grained,
per-key. Keeps the abuse case where one user's leaked key can't
saturate everyone's rate limit pool. Default 60 req/min per key,
configurable per key (paying users can negotiate higher limits).

Don't use Redis — DynamoDB conditional updates handle the bucket math
fine and we already have it. ~30 lines.

## Phase D — Self-service UI

In the SPA's `AccountMenu`, add a new "API keys" item alongside
"Logout":

- **List:** show prefix only (full key never re-displayed), name,
  created/last-used, revoke button.
- **Create:** modal asking for a name, then showing the full key
  exactly once with a "copy to clipboard" button and a clear "save
  this now, you won't see it again" warning.
- **Revoke:** confirm-and-set `revoked_at`. No soft-delete; revoked
  keys stay in the DB for audit.
- **Optional v2:** per-key spend cap input (writes
  `monthly_cap_cents`); auto-revoke when exceeded (a webhook from
  `stripe/` checks invoice progress against the cap).

## Phase E — Documentation + onboarding

`/api/docs` already exists in the backend (see project CLAUDE.md).
Add an "API key auth" section that mirrors the existing docs but
with API key examples:

```bash
curl -H "Authorization: Bearer sk_live_..." \
     https://api.specodex.com/api/v1/search?type=motor
```

Add a "Getting Started" page in the SPA accessible to logged-in
paying users — pre-fills a curl with their key (the one-time-show
modal), shows pricing, links to docs.

## Phase F — Pricing decision

**The biggest open question** and the one that doesn't depend on any
of the above shipping. Decide before pricing the product publicly:

| Model | Shape | Pros | Cons |
|-------|-------|------|------|
| Per-call | $0.01/call | Engineers price intuitively; low friction | Underprices high-data calls (search returning 1000 records) |
| Per-record | $0.001/record returned | Aligns with value delivered | Hard to predict for the buyer |
| Per-token | $0.001/1K tokens (current `stripe/` shape) | Aligns with backend cost | Buyers don't know what tokens are |
| Tiered subscription | $99/mo for 100K calls, $499/mo for 1M | Predictable buyer cost | Misaligned with usage; over/under |
| Free tier + paid | 1K calls/mo free, $0.01/call after | Conversion play | Free tier gets scraped by bots → security cost |

Recommendation pending real data: **per-record returned**, with a
free tier hard-capped at 1K records/month per Cognito user. Aligns
with what users actually take from the API; the data is the product.
Free tier creates a lead funnel for the SPA without giving away the
catalog wholesale.

## Risks

- **Key leaks.** Engineers paste keys into Slack / GitHub / Notion.
  Mitigations:
  - GitHub secret scanning partnership (long-tail, low effort once
    registered).
  - Internal grep-the-leaks job: scrape recent public commits in
    user-supplied GitHub usernames for the prefix. Optional.
  - Auto-revocation on detection.
- **Cost runaway for the user.** Stripe metered means a runaway
  script bills the user real money. Mitigations:
  - Per-key `monthly_cap_cents` (Phase D v2).
  - Email at 50% / 80% / 100% of cap.
  - Hard cap on first key for new users — $20/mo until they
    explicitly raise it.
- **Free tier as an attack surface.** If we add a free tier, bots
  will burn through 1K-call quotas to extract data without paying.
  Counter: enforce per-Cognito-sub quotas (one user, one quota); rely
  on Phase 5b registration captcha to keep account-creation cheap
  for legit users but expensive for bots.
- **Pricing migration.** If we launch with per-token (current
  `stripe/` shape) and migrate to per-record later, existing
  customers' invoices change shape mid-relationship. Better to make
  the pricing decision before launch than to change it after.
- **`stripe/` test-mode lock.** The Lambda refuses to start without
  `sk_test_`. Switching to live keys is a one-line config check
  inversion + a real Stripe live key, but make sure the test/live
  separation is correct end-to-end before flipping.
- **Identity drift.** If we ever migrate off Cognito (not optimizing
  for this today), the user_id in `stripe/`'s table needs a migration
  path. Cognito sub is opaque — keep it that way; don't bake
  assumptions about its format into the API code.

## What this unblocks

- **Public API as a paid product line** — the catalog has B2B value
  beyond the SPA UI; programmatic access opens that revenue.
- **CI integrations** — Bazel rules, npm packages, GitHub Actions
  reading product data into build-time catalogs.
- **Webhook subscriptions** — "notify me when a new motor matching
  these specs is added." Same auth shape; built on top of the same
  Stripe billing pipeline (per-event pricing).
- **Partner integrations** — design tools, BOM generators, CAD
  plugins. All want a long-lived key, not an OAuth dance.

## Triggers

Read this doc before:

- Adding any non-SPA caller to `/api/*` routes (CLI, Postman
  collection, partner integration). Don't bypass-auth them — figure
  out the API key story first.
- Pricing or marketing the API as a product line.
- Touching `stripe/` to make sure changes don't conflict with the
  per-call billing direction sketched above.
- Adding a `usage` reporter to a new endpoint — keep the wire format
  consistent with `stripe/`'s `/usage` endpoint.
- Designing the rate-limit story (the auth Phase 5b WAF on master
  handles edge bursts; the per-key fine-grained limit lives here,
  not there).
