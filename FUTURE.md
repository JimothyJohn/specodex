# Specodex — what's next

It is 4pm on a Wednesday. A mechatronics engineer needs a 750 W servo
motor with 200 V input, ≤4 × 10⁻⁵ kg·m² rotor inertia, and a drive
that mates with both. Today they will open seven vendor PDFs in seven
browser tabs, fill out two "request a quote" forms, and abandon the
fifth tab when the spec table is buried behind a JavaScript carousel.
Specodex turns that afternoon into a single search box: side-by-side
specs across ABB / Yaskawa / Mitsubishi / Schneider / Oriental / Omron,
deep links straight to the original datasheet, and a build tray that
tells the engineer whether the drive they picked actually mates with
the motor they picked.

The substrate is shipped — seven product types, structured units
end-to-end, single-table DynamoDB, page-finder front-running every
Gemini call, motion-system builder live, auth + projects live, prod
chain green on `www.specodex.com`. The next two quarters consolidate
three runtimes into one (Python everywhere), light up the SEO surface
so an engineer searching `MR-J5-40G datasheet` lands on Specodex
instead of a distributor's lead form, and earn the audience
engineer-to-engineer.

> *A product selection frontend that only an engineer could love.*

---

## Why now

- **Substrate is clean.** UNITS shipped (`ValueUnit` / `MinMaxUnit`
  end-to-end, 273 dev + 10 prod rows backfilled). REBRAND Stage 4
  cutover ✅ — `www.specodex.com` is live, `datasheets.advin.io` is
  NXDOMAIN'd. Operator queue: empty.
- **Catalog has shape.** Seven product types — motor, drive, gearhead,
  robot_arm, contactor, electric_cylinder, linear_actuator — each with
  a Pydantic model + a companion `.md` ADR citing source datasheets.
  Page-finder's 18 `SPEC_KEYWORDS` groups gate every Gemini call;
  77/410 spec pages land on a Mitsubishi contactor catalog without a
  single API call.
- **Builder mode works.** Pairwise compatibility (drive↔motor↔gearhead)
  shipped with build tray, slot-aware filter, Copy BOM, "looks
  complete" badge, ChainReviewModal. The catalog is a graph now, not
  a flat search.
- **Test surface is real.** 51 Python test files + 27 frontend test
  files (373 tests) gate every push; CI runs the same
  `./Quickstart verify` developers run locally; nightly
  `./Quickstart bench` regression-gates the LLM pipeline.
- **The product is open.** ~11.6k lines of Python + ~24.8k lines of
  TypeScript on GitHub. The repo is the marketing asset.

## What we've already proved

For the long version, see [HISTORY.md](HISTORY.md). The traction line:

- **Page-finder is free and accurate.** Text heuristic alone finds
  77/410 spec pages on the Mitsubishi contactor catalog and 83/616
  on `j5.pdf` — bundled extraction's truncate-mid-string failure
  mode at >30 pages is gone since per-page deep-linking landed.
- **Schemagen turns 3–5 vendor PDFs into a Pydantic model + ADR**
  (`./Quickstart schemagen`). Multi-source input is enforced so the
  LLM generalizes instead of hardcoding one catalog's quirks.
- **Read path is fast.** 1242 motors round-trip through the live dev
  DynamoDB in 1.20s; 2106 rows across all types in 1.70s.
- **Pydantic → TypeScript codegen ✅.** `./Quickstart gen-types`
  regenerates `generated.ts` from every `BaseModel` under
  `specodex/models/`; the `test-codegen` CI job fails on drift.
- **Auth is on master.** Phases 1–4 (Cognito user pool, requireAuth
  middleware, AuthContext + modal + account menu, Stripe + admin
  gating via Cognito group) plus Phase 5b (WAF) and Phase 5d (CSP +
  HSTS via CloudFront `ResponseHeadersPolicy`).
- **Projects ships.** Per-user product collections on the existing
  single-table layout (`PK=USER#sub`, `SK=PROJECT#id`). No new
  DynamoDB resource; identity comes from `req.user.sub`, never the URL.
- **Frontend testing catches the spillover-bug class.** L1 stale-modal
  regression and L6 `isBuild` array bug were caught and fixed inside
  the FRONTEND_TESTING plan itself.
- **Prod chain green** end-to-end (Test → Deploy Staging → Smoke Staging
  → Deploy Prod → Smoke Prod) since 2026-04-29.

## The next six months

Status legend: ✅ done · 🚧 in progress · ⏸ deferred · 🔴 urgent · 📐 planned

Order matches `todo/README.md`'s suggested chronological dependency
chain. Active board: [Specodex Orchestration](https://github.com/users/JimothyJohn/projects/1).

| # | Doc | Status | Effort | One-line |
|---|---|---|---|---|
| 1 | [PHASE5_RECOVERY](todo/PHASE5_RECOVERY.md) | 🔴 blocking | 🟡 medium | Cherry-pick stranded 5a / 5c / 5e / 5f auth-hardening (~1.1k LOC) onto master. |
| 2 | [MODELGEN](todo/MODELGEN.md) | 🚧 toolchain ✅; consumer rewire pending | 🟢 small | Collapse hand-typed `models.ts` + Zod enum onto `generated.ts`. |
| 3 | [SEO](todo/SEO.md) | 🚧 Phase 0 metadata ✅ | 🟡 medium | Build-time prerender, dynamic per-product sitemap, `Product` JSON-LD, Lighthouse CI gate. |
| 4 | [MARKETING](todo/MARKETING.md) | 📐 planned | 🟡 medium | Show HN, r/PLC, `awesome-*` PRs, blog, trade press. Gated on SEO Phase 1. |
| 5 | [DEDUPE](todo/DEDUPE.md) | 🚧 Phase 1 audit script ✅ | 🟡 medium | Phase 2 auto-merge safe cases; Phase 3 human-review queue. |
| 6 | [PYTHON_BACKEND](todo/PYTHON_BACKEND.md) | 🚧 Phase 0 codegen ✅ | 🔴 multi-week | Phase 1+: Express → FastAPI parallel-deploy. Gated on PHASE5_RECOVERY. |
| 7 | [PYTHON_STRIPE](todo/PYTHON_STRIPE.md) | 🚧 layout scaffolded | 🟢 small | Drop the Rust billing Lambda for ~100 lines of Python. |
| 8 | [API](todo/API.md) | 📐 paid programmatic surface | 🟡 medium | Stripe-metered curl-able API. Gated on Phase 5a SES + 5b WAF (5b ✅). |
| 9 | [STYLE](todo/STYLE.md) | 📐 7-phase plan | 🟡 medium | Eliminate native browser/OS chrome (36 `title=`, 2 `confirm`, 1 `alert`, 17 unstyled scrollbars). |
| 10 | GODMODE (active on the [orchestration board](https://github.com/users/JimothyJohn/projects/1); plan doc retired) | 📐 deferred | 🔴 large | One-page admin dashboard. Lands last on stable substrate. |

CICD itself is healthy (chain green; full runbook in the `/cicd`
skill at `.claude/skills/cicd/SKILL.md`) and has dropped off the
queue. Apex `specodex.com` DNS is the only outstanding follow-up.

---

## 1. PHASE5_RECOVERY — get the stranded auth-hardening onto master

`gh pr list` reports PRs #3 (5a SES), #5 (5c refresh-token revocation),
#7 (5e audit logging), and #8 (5f WAF CloudWatch alarms) as MERGED. The
SHAs aren't on `origin/master`. They merged into the stacked
`feat-auth-phase1` PR branch, which had itself already merged earlier,
so the Phase 5 commits never reached master. Net: ~1.1k LOC of
auth-hardening — the SES sender that prevents bouncing Cognito welcome
emails on a $50/mo invoice, the refresh-token revocation that closes
the localStorage-token tradeoff Phase 3 acknowledged, the audit log
that gives us a paper trail, and the alarms that let us sleep — sits
off-master.

**Recovery is one stacked cherry-pick PR.** Per-commit footprint is
3–5 files / 200–400 lines; expected conflicts are limited to
`app/backend/src/routes/auth.ts` (touched by 5c + 5e). Don't tear down
the four `specodex-{ses,revoke,audit,alarms}` worktrees until the
recovery PR lands — they hold the only local copies of some commits.

Phase 5d (CSP + HSTS via CloudFront) is on master as `3ef96a7` /
merge `c63df04`. Phase 5b (WAF rate-limit + AWS managed common rules)
is on master as `116b4cb`. The `specodex-csp` worktree can be torn
down at any time.

**Why it blocks PYTHON_BACKEND:** the FastAPI cutover would mirror
the wrong Cognito surface area if 5c (refresh-token revocation) and
5e (audit logging) aren't already in shape on the Express side.

## 2. MODELGEN — finish what `c397ec5` started

The toolchain shipped: `./Quickstart gen-types` regenerates
`app/frontend/src/types/generated.ts` from every `BaseModel` under
`specodex/models/`, and the `test-codegen` CI job gates `deploy-staging`
on the committed file matching source. What's left is consumer
rewire, two pieces:

- **Phase 0a-ii.** `app/frontend/src/types/models.ts` becomes a thin
  re-export shim from `generated.ts`. Today it's a hand-typed mirror
  with a banner comment that says so.
- **Phase 0b.** The Zod enum in `app/backend/src/routes/search.ts` and
  the `VALID_PRODUCT_TYPES` allowlist in
  `app/backend/src/config/productTypes.ts` derive from the same
  generated artifact. The "Adding a new product type" runbook
  collapses from six files to three (Pydantic model + `common.py`
  patch + `gen-types` run).

Small, isolated, captures the value of the Phase 0 toolchain that's
already shipped.

## 3. SEO — make Specodex the answer when an engineer searches a part number

Phase 0 (metadata foundation) shipped 2026-04-28: `robots.txt`, static
homepage `sitemap.xml`, OG/Twitter cards, JSON-LD `WebSite` +
`Organization`, canonical URL. The product *is* the SEO asset; every
product row in DynamoDB is a long-tail landing page waiting to be
rendered.

**Phase 1 — technical foundation (must ship before any marketing push):**

- **1a. SPA crawlability** via build-time prerender. At `vite build`,
  hit `/api/products`, generate one static `.html` per product with
  the right `<title>`, `<meta>`, JSON-LD baked in. SPA shell as
  fallback for unknown routes.
- **1b. Dynamic per-product sitemap.** New `cli/sitemap.py` scans
  DynamoDB, emits one `<url>` per product at `/products/{type}/{slug}`.
  Switch to `sitemap-index.xml` past 50k URLs.
- **1d. Per-product `<title>`, `<meta>`, `Product` JSON-LD.** Use
  UN/CEFACT unit codes (`KWT`, `MTR`, `HUR`, `NEW`) where they exist;
  Schema.org `unitCode` expects UN/CEFACT, not SI strings.
- **1e. Canonical URLs** — every product → exactly one canonical
  `/products/{type}/{slug}`.
- **1f. Lighthouse CI in `verify`.** Gate at LCP < 2.5s, INP < 200ms,
  CLS < 0.1, SEO score > 95.

Phase 2 (category / manufacturer / comparison index pages, OG image
generator, engineering blog) and Phase 3 (keyword strategy by intent
tier) are concurrent / downstream. Open follow-ups: generate
`og-default.png`, flip the apex-canonical line from `www.specodex.com`
to apex when Stage 4 finishes.

**Risk to watch:** `noindex` on staging leaking to prod = entire site
disappears from Google. Add an explicit assertion in
`./Quickstart smoke` that prod HTML has no `X-Robots-Tag: noindex`
and no `<meta name="robots" content="noindex">`.

## 4. MARKETING — engineer-to-engineer distribution

No paid spend, no ads, no agency. Lean on the niche signal of the
field-manual aesthetic and the open-source repo as proof of seriousness.

**Audience, sharply:** mechatronics design engineers, system
integrators / OEMs, robotics startup engineers, sourcing engineers,
university capstone teams, consulting firms. Unifying trait: *they all
know what `rotor_inertia=4.5e-5 kg·m²` means and resent UIs that hide
it behind "request a quote".*

**Anti-positioning** — re-read before any sponsorship conversation:
not a marketplace (we don't sell, no referral fees, no shadow-ranking).
Not a CAD/PDM/PLM tool. **Not vendor-affiliated** — search ordering
must be deterministic and stable across vendors. *Neutrality is the
product.*

**Channels by ROI tier:**

- **Tier 1.** Show HN (one-shot, after `specodex.com` apex resolves
  and SEO Phase 1 is green). r/PLC + r/AskEngineers + r/Mechatronics
  + r/robotics + r/AutomationEng (~600k combined, one thread per sub
  spaced over 2-3 weeks). Eng-Tips and ControlBooth (older, smaller,
  extremely high-trust — *soft-introduce by answering questions with
  Specodex links before any standalone announcement*). `awesome-*` PRs
  (`awesome-industrial`, `awesome-robotics`, `awesome-mechatronics`).
- **Tier 2.** Engineering blog at `docs/blog/` (paired with SEO Phase
  2d), YouTube collabs (Tim Wilborne, Tim Hyland, RealPars — pitch
  5-min "live search demo" segments), LinkedIn long-form posts every
  10–14 days.
- **Tier 3.** Trade press (*Design World*, *Control Engineering*,
  *Machine Design*) — short pitch + one image, no press release.
  Conference attend (no booth pre-revenue). CSIA cold outbound.
- **Don't bother.** Google/LinkedIn paid ads, generic SaaS review
  sites, influencer marketing.

**Conversion ladder.** (A) Bulk / API tier (Stripe metered, paid via
the Python billing Lambda from PYTHON_STRIPE). (B) Sponsored ingestion
— *only with neutrality preserved, hold off until user base makes it
matter to them.* (C) Custom-type ingestion as a service (paid by
integrators).

## 5. DEDUPE — cross-vendor historical-duplicate cleanup

The `compute_product_id` family-aware fix (2026-04-26) prevents
**future** prefix-drift duplicates. Phase 1 audit script
(`cli/audit_dedupes.py`) shipped 2026-04-29 — read-only, scans every
product-type partition in dev DynamoDB, groups by family-aware
normalized core, emits JSON of every group with ≥2 rows + side-by-side
diff classified as `identical` / `complementary` / `conflicting`.

**Next:**

- **Phase 2 — auto-merge safe cases.** `--apply --safe-only` writes
  the merged row under the canonical (family-aware) UUID, deletes
  orphans. Most-populated part-number form wins (`MPP-1152C` over
  `1152C`); `pages` becomes a union; `datasheet_url` keeps the
  most-populated row's URL.
- **Phase 3 — human review queue.**
  `outputs/dedupe_review_<ts>.md` has one section per `conflicting`
  group with a 3-column field table + direct PDF links. Reviewer
  fills in picks; `--apply --from-review` merges with chosen values.

**Edge cases worth respecting:** `MPP` vs `MPJ` are different motors
despite sharing a normalized core — only strip when the *exact*
`product_family` token is the prefix, never a sibling family.
Datasheet URL drift is normal; group on
`(manufacturer, normalized_part)` and ignore the URL.

Estimated: half a day of code + ~1 hour of human review on dev.
Promote to staging/prod via existing `./Quickstart admin promote`.
No prod writes from this CLI ever.

## 6. PYTHON_BACKEND — Express → FastAPI

Phase 0 (codegen) is the substrate; see §2. Phases 1–3 are the
multi-week part. The motivating audit lived briefly at
`todo/REFACTOR.md` on the parked `2660f92` WIP and named three
structural taxes:

1. **Polyglot stack without polyglot justification.** Python pipeline
   + Node API + Rust billing → three runtimes, three lint configs,
   three deploy paths. Only the pipeline (Python, ML libs) and the
   frontend (React) are load-bearing.
2. **Six places to update for a new product type** — Pydantic model
   → TS interface → TS union → Zod enum → backend allowlist →
   frontend union. The runbook is the smell.
3. **No type-safe contract between Python and TypeScript.** Phase 0
   already addressed this (`generated.ts` + drift gate); Phases 1+
   make the backend itself the same language as the pipeline.

**Gated on PHASE5_RECOVERY landing first** so the FastAPI auth
surface mirrors the right Cognito shape.

## 7. PYTHON_STRIPE — drop the Rust billing Lambda for ~100 lines of Python

The `stripe/` Rust Lambda is the only Rust in the repo: ~500 LoC
across 7 files, 5 endpoints, originally justified as "fast cold
starts" — a property that buys nothing for Stripe webhooks (async,
nobody waits) or Checkout sessions (the user is being redirected to
Stripe). The cost of carrying it: the entire `cargo` toolchain in CI,
a separate deploy path (`cargo lambda deploy`, not in CDK), and a
third mental context.

**Phase 1 layout scaffolded** (`6ac30b0`): `stripe_py/` exists with
all 7 modules + 8 test files. Replacement reuses the existing `boto3`
+ `python-dotenv` already in `pyproject.toml`, plus the official
`stripe` Python SDK — `Webhook.construct_event` replaces the
hand-rolled HMAC-SHA256 signature check.

**Pending:** Phase 1 deploy + Phase 2 SSM cutover + Phase 3 Rust
deletion. Independent of PYTHON_BACKEND; can ship at any time.

## 8. API — paid programmatic surface

Engineering teams want curl, scripts, CI integrations — a different
auth shape, a different rate-limit shape, a different billing shape
than the SPA's "logged-in user with a JWT in localStorage" path. Long
API keys with embedded JWT-shaped claims signed by a separate KMS key,
per-API-key rate limits at the WAF layer, Stripe metered usage on the
read side.

**Dependencies:**

- Cognito identity, JWT middleware, admin gating — ✅ shipped (auth
  Phases 1–4).
- SES verified-identity sender for welcome / receipt emails —
  ⏳ stranded on `feat-auth-phase5a-ses` (PHASE5_RECOVERY).
- WAF rate-limit layer — ✅ shipped (auth Phase 5b on master).
- Either the existing `stripe/` (Rust) or the upcoming `stripe_py/`
  Lambda actually deployed to production. Today the Rust Lambda is
  unwired to the production stack.

## 9. STYLE — eliminate native browser/OS chrome

The frontend is already 80% of the way there: focus rings styled,
modals custom, single-select dropdowns custom, no native `<dialog>`,
`<details>`, `<select>`, file pickers, drag-drop, or `window.open`.
What remains is the long tail of native chrome that still shows up
daily.

**Inventory (snapshot 2026-05-02):**

| Surface | Count | Owner phase |
|---|---|---|
| `title=` native tooltips | 36 | Phase 1 (Tooltip) |
| `window.confirm()` | 2 | Phase 2 (ConfirmDialog) |
| `alert()` | 1 | Phase 3 (Toast) |
| `<form>` without `noValidate` | 9 | Phase 4 (FormField) |
| Unstyled scrollbars (`overflow: auto/scroll`) | 17 | Phase 5 (`.scrollable` utility) |
| Bare `target="_blank"` | 3 | Phase 6 (ExternalLink) |
| Silent `console.error` in user flows | ~25 | Phase 3 (paired toast) |

After each phase the surface it owns is *closed* — no new code is
allowed to introduce native chrome in that category, and a
`./Quickstart verify` lint rule enforces it. Phases 1, 5, 6 are pure-
additive and can ship in any spare slot; Phases 2–4 touch shared
state, so single-stream them.

## 10. GODMODE — one-page admin dashboard

Single URL — `/godmode` in the React app, gated by `adminOnly` — that
answers "what the hell is going on with this project right now?"
without context-switching across AWS Console, GitHub, CloudWatch,
terminal, and three Quickstart commands.

**Six panels.** AI usage (Gemini token spend, Claude transcripts).
Pipeline health (recent ingest attempts, top failing manufacturers,
p50/p95 wall-clock). Database health (products by type, last-24h
writes, "unhealthy" rows). Repo activity (commits last 7/30 d, LOC by
language, churn, test pass rate). Deploy state (per-stage stack
version, `/health`, last 10 CloudWatch errors). Backlog state (`todo/`
count by status, urgency).

**Architecture: A + B, split by data locality.** Deployed (React +
Express endpoints) covers cloud data. Local (`./Quickstart godmode`
writes `outputs/godmode/latest.html`) covers Claude transcripts, git,
LOC, last test run, backlog state — things a Lambda can't see. Both
render with the same panel CSS so they feel like one tool.

**Lands last** so panels read finalized substrates. ~1 day for MVP.

---

## What winning looks like

Concrete end-state markers — what "shipped" means at the project level:

- **Specodex is the URL.** `specodex.com` apex serves the app
  directly (Stage 4d/e complete). `datasheets.advin.io` redirected
  for ≥6 months, then decommissioned.
- **One language, one toolchain.** Express → FastAPI cutover'd. Rust
  billing Lambda replaced by Python. CI runs a single Python lint +
  test stage instead of three. `./Quickstart verify` < 5 min.
- **Two files, not six.** Adding a new product type is `<type>.py` +
  `<type>.md` + a one-line patch to `common.py`'s `ProductType`
  literal. Everything else generates.
- **Engineers find Specodex via Google.** SEO Phase 1 shipped; an
  engineer searching `MR-J5-40G datasheet` lands on a per-product
  page with structured `Product` JSON-LD. Lighthouse CI gates SEO ≥ 95.
- **Stripe MRR > $0.** First paid bulk-API engineer cleared through
  the metered billing Lambda. The conversion ladder works at all.
- **The dashboard tells the story.** GODMODE answers "what's going
  on" without context-switching; oncall doesn't need three terminal
  windows.
- **The `todo/` queue is empty or epic-sized.** Every shipped doc
  deleted; remaining docs are multi-quarter Phase 2 epics (e.g.
  spec-first sizing wizard) named explicitly so architecture
  doesn't preclude them.

---

## Late Night queue

Curated tasks safe to run autonomously overnight on dev. Each meets
four criteria: bounded, dev-only writes, recoverable, morning-checkable.

**Tier 1 — read-only or local-only (zero cost):**

| Task | Command | Output |
|---|---|---|
| Bench (offline) | `./Quickstart bench --quality-only` | `outputs/benchmarks/<ts>.json` — diff vs `latest.json` |
| Ingest-report | `./Quickstart ingest-report --email-template` | `outputs/ingest_report_*.md` |
| UNITS review triage | `./Quickstart units-triage outputs/units_migration_review_dev_*.md` | pattern groups + suggested action per group |
| Integration test sweep | `./Quickstart verify --integration` | exit code; stale tests surface as failures |
| DEDUPE Phase 1 audit | `./Quickstart audit-dedupes --stage dev` | `outputs/dedupe_audit_dev_<ts>.json` + `dedupe_review_dev_<ts>.md` |

**Tier 2 — small Gemini cost, dev DB writes only:**

| Task | Cost | Output |
|---|---|---|
| Schemagen on stockpiled PDFs | ~$0.10–0.50/PDF | `<type>.py` + `<type>.md` ADR per cluster |
| Price-enrich (dev) | scraping + occasional Gemini | row counts before/after; spot-check 5–10 in UI |

**Tier 3 — bounded but expensive (run weekly):**

| Task | Cost | Output |
|---|---|---|
| Bench (live) | ~$1–5/run | precision/recall delta + cache delta |
| Process upload queue (dev) | unbounded — only with known queue size | products via `/api/v1/search` |

**Morning checklist** — `tail -100 .logs/*.log`, diff bench, hit dev
`/health` + `/api/products/categories` + `/api/v1/search`, read
schemagen ADRs if any, UI walkthrough, then promote staging → prod
via `./Quickstart admin promote`.

**Not Late Night material:** anything in `app/infrastructure/` (CDK)
or `.github/workflows/`; any prod write or `--stage prod` promotion;
SEO structural lifts (per-product page rendering, dynamic sitemap)
— needs build + manual crawl check.

---

## Cross-cutting themes

**The substrate ordering matters more than urgency.** UNITS was the
linchpin everything else compiles against. PHASE5_RECOVERY now
plays that role for PYTHON_BACKEND — the FastAPI parallel-deploy on a
Cognito surface that's still moving will mirror the wrong shape.
DEDUPE only makes sense on post-UNITS uniform data. STYLE phases
unblock as primitives, not as a cliff. GODMODE panels read finalized
substrates.

**Class-of-bug eliminations are queued, not just specific fixes.**
`HOSTED_ZONE_ID` secret → `fromLookup`. `??` vs `||` lifted into
global CLAUDE.md. The DEDUPE forward fix prevents new prefix-drift;
the audit cleans the historical mess. Same pattern for UNITS — fix
the parser **and** delete the compact-string layer so the next
exotic value can't regress. Stacked-PR `gh pr list` lying about
MERGED → memory entry to verify with
`git log origin/master --first-parent` before trusting status.

**The polyglot retreat is a stuck shift.** Python pipeline + Node API
+ Rust billing → Python everywhere. The Rust port queued in HISTORY
§VII as the only multi-week initiative is parked; Phase 0 codegen
(`pydantic2ts` → `generated.ts`) delivers the single-source-of-truth
benefit the Rust port was supposed to deliver, without leaving Python.

**Operator queue stays empty.** Followups in CICD, REBRAND, UNITS,
PHASE5_RECOVERY are explicitly partitioned into "operator action
required" vs. "autonomous followups." When the operator queue refills,
it's named (secret rotation, environment approval, IAM policy review)
rather than a vague "ask the human."

**Neutrality is non-negotiable.** Search ordering, vendor visibility,
sponsorship policy — every load-bearing surface defends the rule that
Specodex doesn't favor any manufacturer. Re-read MARKETING anti-
positioning before any sponsorship conversation.

**The next multi-week item is PYTHON_BACKEND.** Everything else is
half-day to a few days. PYTHON_BACKEND is the one that needs a single
hand on the rudder, sequenced behind PHASE5_RECOVERY so the FastAPI
auth surface ports a stable shape.
