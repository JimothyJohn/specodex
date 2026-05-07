# Specodex — project history

A reading of the git log from `0653a29 Pinging` (2025-06-28) through
`6ac30b0` (2026-05-02). Where the commits are terse, the `todo/*.md`
docs and CLAUDE.md provided the colour. Dates are commit dates.

---

## I. Vibe era — *Pinging → Finding mfgs* (Jun 2025 – Dec 2025)

The first nine months are 21 commits with one- or two-word messages:
`Pinging`, `API passthrough`, `Fully tested`, `Biggun`, `Pulling pages`,
`Cleanup`, `Simplified`, `DB framework`, `Schema refined / extended`,
`Refactored for NoSQL`, `Frontend vibed`, `Frontend okay`, `More types`,
`Switching`, `Finding mfgs`. The arc is plain enough:

1. **Ping a Gemini API end-to-end.**
2. **Pull pages out of PDFs.**
3. **Stand up a database** — first in some other shape, then "refactored
   for NoSQL" (the single-table DynamoDB design that's still in place).
4. **Schema iteration.** Two extension passes, then a refinement.
5. **Vibe a frontend.** A React UI gets stood up over a single weekend
   in October 2025 (`Frontend vibed` → `Frontend okay`).
6. **More types, switching, finding mfgs.** First evidence of more than
   one product type and the manufacturer normalization problem that
   would later spawn the blacklist.

This is exploratory code — no tests, no CI, no deploy story. The
working title in the early commit messages and code is
`datasheetminer`; **Specodex** doesn't appear until April 2026.

---

## II. Professionalization — *the March 2026 sprint* (Mar 22 – Mar 29)

A quiet three-month gap, then 16 substantive commits in eight days. The
project goes from prototype to production-shaped:

- **`27447f9` Comprehensive test suite across all layers** — first
  proper tests, organized into the `unit/integration/staging/post_deploy`
  split that's still the layout today.
- **`60d0fac` Docker production build and deployment tooling.**
- **`09500c8` AWS deployment, Rust rewrite, CLI, quality scoring,
  mandatory manufacturer.** The squashed message hides three large
  decisions: AWS as the deploy target, an early Rust experiment, and
  *manufacturer is a required field* — the thing the Dec 2025
  "Finding mfgs" commits were trying to enforce by hand.
- **`87aabd0` PDF upload pipeline** — presigned S3 + frontend form
  + CLI queue processor. The shape that's still in `cli/processor.py`.
- **`584d4fc` Rust Stripe payment Lambda for metered token billing.**
  The first piece of Rust to ship. It still lives at `stripe/` as a
  standalone crate; metered billing was never wired to the UI but the
  Lambda is the precedent the April 2026 Rust port leans on.
- **`ceaafee` HTML scraping, gearhead/robot_arm schemas, page finder.**
  The first non-motor product types.
- **`317aafd` Remove `rust/` directory — moved to dedicated 'rust'
  branch.** The Rust experiment is parked. (One year later, on the same
  branch, it would come back as the planned full port — see §VIII.)
- **`db92198` Agent-facing query CLI (dsm).** First sign that LLM
  agents are intended consumers of this data, not just humans.
- **`8a09f44` Intake triage pipeline, quality scoring, batch processing.**
- **`a587673` / `972900d` CloudFront stale deploys + prod table
  mismatch.** The first prod escapade — cache headers, explicit
  invalidation, and a prod table pointing at the wrong DynamoDB.
- **`e2c91c2` CI/CD pipeline, mobile UI, datasheet links.**
- **`20c40cf` Public API with OpenAPI spec and recommendation chat.**
  A "recommendation" chat surface — short-lived, reverted six days
  later (see §III).
- **`5d4fa2a` SSM secrets, CDK infrastructure stacks, CI secret
  provisioning.** CDK lands. The infrastructure shape that the April
  Rust port plans to migrate off (toward raw CloudFormation + SAM,
  per global CLAUDE.md preference) starts here.

By the end of the sprint the project has: Python core, React frontend,
Express backend, CDK infrastructure, AWS Lambda + API Gateway + S3 +
CloudFront + DynamoDB, a Rust Stripe sidecar, CI on GitHub Actions,
and tests across every layer. The next two months are about cleaning
up choices made in this week.

---

## III. Stabilization & a CSV detour — *early April 2026* (Apr 5)

Four commits, three of them course corrections.

- **`171d4ec` Add project CLAUDE.md.** The codebase's instruction
  manual lands. From here on, every future agent reads the same brief.
- **`410711e` Switch LLM extraction to CSV with unit-in-header schema.**
  A failed-to-stick experiment: CSV is dense and Gemini handles it, but
  it loses the `{value, unit}` structure that the rest of the pipeline
  wants. By mid-April this is reverted in favor of structured JSON
  with Gemini's `response_schema`. The lesson — **the compact form
  is a Python-only artifact; everything else wants the dict** — is
  re-learned, harder, in §VII (UNITS).
- **`71d74bd` Remove recommendation feature, add web scraper.** The
  "recommendation chat" from `20c40cf` is pulled out. The product is
  a *spec database*, not a chat surface. The autonomous web scraper
  that replaces it is what `specodex/scraper.py` does today.
- **`edfca84` Add output_*.json, cdk-outputs.json, package-lock.json
  to .gitignore.** Cleanup.

---

## IV. Schema generalization — *mid-April 2026* (Apr 16 – Apr 17)

Seventeen commits in two days. The frontend gets its current shape
and the project learns how to add new product types without manual
work.

**Backend / pipeline:**

- **`69c9dce` Per-page extraction with merge for datasheet
  deep-linking.** The catalog-too-big-for-Gemini problem. Per-page
  extraction lets ingestion link a spec back to the page it came from
  and avoids the "Gemini truncates the JSON mid-string" failure mode
  that bundled extraction hit at ~30 pages.
- **`75f8d12` schemagen CLI, Contactor model, broaden page_finder
  keywords.** The first version of `./Quickstart schemagen` — propose
  a new Pydantic model from a PDF. `Contactor` is the proof-of-concept
  type. The keyword broadening (motor-centric → 18 groups covering
  switching devices, linear actuation, sensors, certifications) takes
  the Mitsubishi contactor catalog from 4/410 spec pages found to
  77/410.
- **`c7d35c5` Gemini-only LLM, admin panel, pricing pipeline.** A
  multi-provider experiment ends; Gemini wins on price/structure.
  Admin panel (the AdminPanel.tsx behind `?admin=1`) ships.
- **`b48d9ac` Expert-curate default-visible columns per product type;
  add model .md docs.** Each Pydantic model gets a companion `.md`
  ADR explaining design choices and citing source datasheets. This
  becomes the standard.
- **`d3cd816` schemagen multi-source input + companion .md output.**
  The schemagen lesson: pass 3-5 vendors' PDFs. Single-source
  proposals hardcode one catalog's quirks.

**Frontend — the seven-step table evolution:**

| Commit | Step |
|---|---|
| `942de24` | 1: derive product-table columns from records |
| `f6878d5` | 2: horizontal scroll on the product table |
| `8ee6589` | 3: single monospace font family |
| `21f5ca5` | 4: compact/comfy row density toggle |
| `d8b324e` | 5: per-column hide with restore |
| `a66b843` | 6: column count cap |
| `e1b2e50` | 7: column sort direction toggle |

Plus the deeper change in `db8bc77`: **filter attributes are derived
from records at runtime**, merged with optional curated overrides.
After this, adding a new product type stops requiring Python-side
*and* TypeScript-side manual list edits — the table just populates
from whatever fields the records carry.

---

## V. Productionization — *late April 2026* (Apr 21 – Apr 25)

The "ship it, then keep shipping" phase.

- **`b4fefd0` Prod-migration: HTTP API v2, Lambda bundling fix,
  stage-aware CI.** Express moves from API Gateway REST API v1 to
  HTTP API v2. The Lambda bundling fix is the first hint of the
  npm-install non-determinism that would bite again on Apr 27
  (`12829d8`).
- **`b4396e0` Delete /api/datasheets/:id/scrape and the whole backend
  scrape path.** Scraping moves out of the request path entirely —
  it's an offline pipeline now.
- **`9e3c70f` resurrect-orphan-table.sh.** A script to re-adopt
  DynamoDB tables that get orphaned by CDK stack rebuilds. Born from
  pain.
- **`b0d104f` MANUAL_UPDATES.md — agent-unreachable followups from
  prod cut.** The first acknowledgment that some things genuinely
  need a human (DNS, ACM cert approvals, secret rotations). Pattern
  repeats throughout April.
- **`af964b7` scraper: fix batch_create bypassing the quality
  filter.** Classic. The DB write was passing `parsed_models` (raw)
  instead of `valid_models` (quality-filtered). Low-quality products
  were getting written anyway. The "Successfully pushed 99 items,
  -75 items failed" log line was the tell — negative failure count
  meant the filter had been bypassed.
- **`9696f96` Models: align shared field names + unify `ip_rating` +
  typed quantity aliases.** Cross-type field harmonization, mostly
  motivated by the upcoming integration layer.
- **`06e101f` Integration layer: product-to-product compatibility +
  drop dead CSV path.** `specodex/integration/{ports,adapters,compat}.py`
  lands. Still server-side only at this point.
- **`94faac5` linear_actuator product type + inspect tooling for
  failed datasheets.**
- **`925273a` Ingest log: per-attempt records + ingest-report for
  vendor outreach.** Quality-fail rows get grouped by manufacturer
  for outreach. `--email-template` emits a ready-to-send body.
- **`47236a7` Bench: enforce wall-clock budgets per fixture.**
  Benchmarking gets teeth.
- **`04bbf39` Edge-case hardening: intake guards, page finder,
  scraper, API client.** Sweep through the non-happy paths.

---

## VI. The motion-system builder — *2026-04-26* (`a54c169`)

INTEGRATION phases A+B ship in a single commit. The frontend gets a
**motion-system builder** — drive → motor → gearhead — exposing
pairwise compatibility through a build tray.

Two design decisions worth recording:

- **Scope is rotary only.** Linear actuators are explicitly out until
  the rotary path is shipped and used.
- **Compat policy is *fits-partial* — `fail` is downgraded to
  `partial`.** Cross-product schemas aren't normalized yet (fieldbus
  protocol strings, encoder names), so a strict gate would produce
  false negatives the user has no way to override. Once shared enums
  exist, a flag flips to re-enable strict mode.

This is the first feature that treats the catalog as a *graph* rather
than a flat search.

---

## VII. The Specodex sprint — *2026-04-26 → 2026-04-29*

Four overlapping initiatives in four days.

### REBRAND (Apr 26 – Apr 27)

`datasheetminer` → **Specodex**. Domain `specodex.com` registered via
Route 53 on Apr 26. Staged rollout:

- **Stages 1+2 — Welcome page + app chrome** (`f2e77cf`, `0a83e34`).
  Landed at `/welcome` first so bookmarks to `datasheets.advin.io/`
  kept resolving.
- **Stages 3a+3b — Python package + Node workspace renames**
  (`ebcc595`).
- **Stage 3c — CDK rename** (`825423b`): Lambda/API names, exports,
  tag, descriptions. No deploy in this commit.
- **Stage 3d — GitHub repo rename** (`7095324`). Triggered the first
  CICD escapade (see below).
- **Stage 3e — documentation + copy sweep** (`457c03b`).

The naming rationale (from `todo/REBRAND.md`): *Spec + odex (codex /
index)*. Mech-engineer friendly, pairs with the army-green / mil-spec
aesthetic. Tagline: *A product selection frontend that only an
engineer could love.* Surface palette anchored on TM-style field
manuals (manila paper, OD green chrome, khaki amber accents — stencil
orange got pulled because OD + orange read "Christmas").

### CICD (Apr 26 – Apr 28)

Phases P0 → P3 over three days, then a postmortem.

- **`9f054a4` AWS auth via OIDC.** Drops static keys.
- **`7fa5f13` P0 — unblock CI red gate** (admin module + frontend race).
- **`2371798` P1 — `./Quickstart verify` is the single source of
  truth.** CI runs the same script the developer runs locally.
- **`bb5913d` P2 — unblock deploys after the repo rename.** OIDC
  trust policy hardcoded `repo:JimothyJohn/datasheetminer:*` patterns;
  rename to `JimothyJohn/specodex:*` was the unblock.
- **`5274945` P2.1 — URL-encode `where=` in staging smoke.** The
  smoke test was generating malformed query strings.
- **`6bdadbc` P2 — concurrency, dep caching, prod SSM bug.**
- **`9a236d2` P2 — `./Quickstart cdk-outputs` replaces inline YAML
  heredocs.** Consolidates the "read this output from CFN" logic.
- **`0b525ca` P3 — assert stack status post-deploy.** Half a deploy
  used to look like a green deploy; no longer.

Two latent infra bugs surfaced in the same week:

- **`c3a89fb`** — `process.env.HOSTED_ZONE_NAME ?? "advin.io"` kept
  `""` because `??` only triggers on `null`/`undefined`. Bash-set vars
  are strings. **Fix:** `??` → `||`. **Lesson lifted into
  `~/.claude/CLAUDE.md`.**
- **`25e0c74`** — `HOSTED_ZONE_ID` secret pointed at
  `Z02805013L9EPXCI8U7ZD` (zone `bigcanyonboys.com.`, an unrelated
  personal domain in the same AWS account) instead of `advin.io.`.
  Manual prod deploys had been masking it because the operator's local
  shell exported the right value; OIDC + CI was the first place to
  read the bad secret. The followup is to delete the secret entirely
  and use `HostedZone.fromLookup({ domainName })`.

The full chain — Test → Deploy Staging → Smoke Staging → Deploy Prod →
Smoke Prod — went green for the first time since 2026-03-30 on
2026-04-29.

### UNITS — drop the `"value;unit"` compact string (Apr 28)

The trigger was a Parker BE motor surfacing `"5.5e-5;kg·cm²"`
(literal semicolon) in the Product Detail UI. Root cause: a six-step
round-trip where Gemini emitted the right shape, a `BeforeValidator`
collapsed it to a compact string, an `AfterValidator` normalized
units, model dump returned the joined string, and a `_parse_compact_units`
regex was supposed to re-split before the DynamoDB write — but the
regex `^(-?[\d.]+)(?:-(-?[\d.]+))?;(.*)$` didn't match `5.5e-5`
(the `e` and the second `-` failed). The string was stored as-is.
The TS-side `parseCompactUnits` had the same regex limitation. UI
fell through to `String(value)`.

The fix (`a8f6162` + `aac7050` + `a3e9ca5`):

- Phases 1–4: structured `ValueUnit` / `MinMaxUnit` BaseModels carrying
  `{value, unit}` end-to-end. The compact-string layer is deleted.
- Phases 5–6: `cli/migrate_units_to_dict.py` rescues `~`/`,`/`≤`/`≥`
  quirks and runs across dev (273 rows fixed) + prod (10 rows fixed).
- **Deliberately deferred:** `±X;unit` (semantically ambiguous between
  scalar tolerance and bilateral range) and `;null` / `;unknown`
  literal sentinels (bad LLM emissions, not encoding artefacts).

This is now the *substrate* the rest of the backlog depends on — the
chronological ordering in `todo/README.md` calls UNITS the linchpin.

### RUST — the second port attempt (Apr 28)

The `rust/` directory removed in March 2026 (`317aafd`) comes back, on
its own branch, as a planned full port. The driver from `todo/RUST.md`:
the repo currently runs Python (~23.4k LOC), TypeScript (~13k LOC), and
a small Rust crate (Stripe), which is the maximally awkward shape.
Going Rust collapses three toolchains into one and aligns with the
"one language per project" preference in global CLAUDE.md.

What landed in two days:

- **Phase 0 — risk-burn spikes** (`8ecb53f`). Both green:
  - Gemini structured-output spike: 84 motor variants from
    omron-g-series-servo-motors.pdf in 12.5s, deserialized cleanly.
  - PDF parity spike: 7/7 benchmark fixtures match Python exactly,
    including the j5.pdf 616-page monster (83 spec pages each side)
    and the Mitsubishi 410-page catalog (77 each side).
  - **Caveat:** the spike used Poppler shell-out for text extraction.
    The first attempt with `pdf-extract` (lopdf-based) emitted zero
    form-feeds and crashed on encrypted PDFs. Production engine
    choice (`pdfium-render` vs `mupdf-rs`) is a separable Phase 1
    decision.
- **Phase 1 — `specodex-core` + `specodex-db`** (`17f5da4` →
  `b795efe`). All seven product models, units, quality scoring,
  blacklist, admin ops (diff/promote/demote). 170/170 tests pass.
  Live smoke against dev DynamoDB: 1242 motors round-trip in 1.20s,
  2106 rows across all types in 1.70s.
- **Phase 3 — `specodex-api`** (`80328a9` → `2a4d222`). Drop-in
  compatible with the Express service for every route the frontend
  calls. Same response envelope, same spec-filter language, same
  summary projection. 28 contract tests cover the validation surface.
  `readonly_guard` and `admin_only` middleware via `route_layer` so
  unmatched paths still 404.
- **Phase 5 — IaC scaffolding** (`0b7c135`). `rust/infra/` standalone
  SAM template. Lambda (`provided.al2023`, arm64) + HTTP API + CORS,
  validates with `sam validate --lint`. Cutover is via CloudFront
  origin swap — the Express stack stays put until the Rust API has
  baked in staging.

Phase 4 (frontend → Leptos/WASM) is explicitly deferred — the
recommendation in `todo/RUST.md` is to skip it. WASM bundles are
*bigger* than the current Vite build for a UI this size, and the
user-visible benefit is zero.

---

## VIII. After the cutover — auth, Projects, and the consolidation pivot — *2026-04-30 → 2026-05-03*

The chain was green; attention shifted off the deploy path and onto the
work it had been blocking. Three things landed in parallel — auth ships
on master, the integration / frontend-testing / dedupe / units backlog
finishes in a single flurry, and the project quietly walks back the
Rust port in favor of consolidating onto Python. The week ends with the
operator queue empty for the first time and a fresh planning load
oriented around one toolchain instead of three.

**The substrate-cleanup queue closes.** INTEGRATION ships its
end-of-chain affordances — `40118ec` (Copy BOM + "looks complete" badge)
and `0a704ab` (ChainReviewModal) — and the doc is deleted. FRONTEND_TESTING
lands all eight phases in a single day (`11faa7c` persistence keys,
`04422a6` AppContext setter contract, `541f908` ProductList type-switch
reset bundle + L1 stale-modal fix, `08e4435` header toggles, `4b39ef3`
FilterChip × unit system, `ae01e17` BuildTray + ErrorBoundary + smoke
render); the suite is now 23 files / 373 tests, and the doc is deleted.
DEDUPE Phase 1's audit script lands on its own branch. By the end of
Apr 30, four todo files (REBRAND, UNITS, INTEGRATION, FRONTEND_TESTING)
have been deleted because their scope shipped; CICD.md is deleted on
the parked WIP because the runbook moved into the `/cicd` skill.

**Auth ships on master — mostly.** Phases 1–4 land in three days:
`0a8298d` + `4d7004e` (Cognito user pool scaffolding + AuthStack wired
into `bin/app.ts`), `6a494d6` (backend middleware + Cognito proxy
routes), `f45ae0b` (frontend AuthContext, modal, account menu),
`c94fb5e` + `c3ed0d9` (Stripe + admin gating via Cognito group, drop
the `APP_MODE` arm). Phase 5b lands as `116b4cb` (WAF rate-limit + AWS
managed common rules at the edge). Phase 5d lands as `3ef96a7` /
merge `c63df04` (CSP + HSTS + frame-ancestors via a CloudFront
`ResponseHeadersPolicy` — load-bearing `script-src 'self'`,
acknowledged `style-src 'unsafe-inline'` for React inline styles,
`X-XSS-Protection` deliberately omitted as deprecated). **But Phases
5a / 5c / 5e / 5f never reach master** — `gh pr list` reports them
MERGED, but they merged into a stacked parent PR (`feat-auth-phase1`)
that had itself already merged, so the SHAs are stranded on the local
worktrees `specodex-{ses,revoke,audit,alarms}`. Net: ~1.1k LOC of
auth-hardening (SES sender, refresh-token revocation, audit logging,
WAF CloudWatch alarms) sits off-master pending recovery.
`todo/PHASE5_RECOVERY.md` is the cleanup plan.

**The polyglot retreat.** The Rust port queued in §VII as the only
multi-week initiative gets walked back. Mid-week, an architecture
audit on the parked `2660f92` WIP (`todo/REFACTOR.md`, 608 lines)
calls out the project's "polyglot stack without polyglot
justification" — Python pipeline + Node API + Rust billing, three
toolchains for what is effectively one codebase, with the Node
backend being a hand-typed mirror of the Pydantic layer. The
remediation pivots away from Rust, not toward it. Three replacement
plans land:

- **`todo/MODELGEN.md`** — `pydantic2ts` regenerates
  `app/frontend/src/types/generated.ts` from every `BaseModel` under
  `specodex/models/`, with a CI drift gate (`test-codegen` job) that
  fails the build if the committed file isn't up to date. Ships first
  as `c397ec5` — the single source of truth for the Python ↔ TS type
  contract that the Rust port was supposed to deliver, delivered
  without leaving Python.
- **`todo/PYTHON_BACKEND.md`** — Express → FastAPI parallel-deploy.
  Phase 0 (codegen) is the above; Phases 1–3 are gated on
  PHASE5_RECOVERY landing first so the FastAPI auth surface mirrors
  the right Cognito shape.
- **`todo/PYTHON_STRIPE.md`** — drop the `stripe/` Rust Lambda for
  ~100 lines of Python (~500 LoC → ~100, reuses the existing `boto3`
  + `stripe` SDK, `Webhook.construct_event` replaces the hand-rolled
  HMAC-SHA256). Layout scaffolded under `stripe_py/` in `6ac30b0`.

The Rust port is parked. `todo/RUST.md` and `todo/RUST_ONE.md` are
retired; `cargo` stops being a third toolchain in CI's near future.

**Projects ships.** First user-data feature on the catalog: `d4de0bd`
adds `/projects` (list + detail), an `AddToProjectMenu` popover on the
product detail modal, and a `ProjectsContext` synced through
`useLayoutEffect` (a sibling-provider effect-order race surfaced as
"Missing bearer token" on first popover open after a hard reload).
Single-table layout: `PK=USER#sub`, `SK=PROJECT#id`, on the existing
products-`<stage>` table — no new DynamoDB resource. Identity comes
from `req.user.sub` only; the URL never carries the owner. `/projects`
is exempted from `readonlyGuard` since per-user data isn't part of the
catalog the public-mode write block protects.

**Smaller cleanups, all on Apr 30:**

- `fcabc36` absorbs the standalone `webscraper/` package into
  `specodex/` as a sibling to the PDF scraper — drops a redundant
  package boundary that imported six modules from `specodex.*` and
  had no external consumers.
- `189a715` factors the shared LLM call+parse helper into
  `specodex/extract.py`. PDF and web scrapers now route through one
  `call_llm_and_parse`; the divergent post-fetch behavior (chunking,
  ID strategy, ingest-log telemetry) stays scraper-specific.
- `c322393` drops 13 finished one-shot scripts and dead refs.
  `cli/batch_process.py` is archived under
  `scripts/migrations/2026-04-26-batch_process.py`.
- `b407520` lands `todo/STYLE.md` — a 7-phase plan to eliminate native
  browser/OS chrome (36 `title=` tooltips, 2 `window.confirm`, 1
  `alert`, 9 forms with UA validation bubbles, 17 unstyled scrollbars,
  3 bare `target="_blank"`, ~25 silent `console.error` paths) plus a
  "No native browser/OS chrome" subsection in `CLAUDE.md` that
  codifies the rule and the verify-time enforcement.
- `8c5139d` adds Dependabot for weekly dep updates. `9f71731`
  SHA-pins every `github/codeql-action` step to v3.35.2. `d81a5b8`
  fixes the apex-domain `hostedZoneName` fallback (3+ parts strips
  the leftmost label; 2 parts uses the domain itself — `specodex.com`
  → `specodex.com`, not `"com"`). `0693efe` adds a `--quality-only`
  flag to bench so cache→expected diffs run portably without the
  241MB of bench PDFs that aren't in the repo.

**Operating-model shift.** `todo/README.md` becomes a "what's in the
working tree right now" snapshot rather than a stable plan; active
work moves onto a [GitHub Projects v2 board](https://github.com/users/JimothyJohn/projects/1)
(user `JimothyJohn`, project #1). Initial card load on May 2:
PHASE5_RECOVERY (P0), MODELGEN, SEO, MARKETING, DEDUPE, GODMODE,
PYTHON_BACKEND, STYLE.

---

## Themes that recur across the log

**Direction shifts that stuck:**
- Pydantic + DynamoDB + Gemini-only LLM (Apr 17). After this the
  pipeline shape is stable.
- Per-page extraction with deep-linking (Apr 16). Killed the bundled
  >30-page failure mode.
- Filter attributes derived from records at runtime (Apr 17). New
  product types stop needing manual list edits to render.
- Structured `ValueUnit` / `MinMaxUnit` end-to-end (Apr 28). The
  compact string was a Python-only artifact masquerading as a wire
  format.
- Pydantic-driven codegen via `pydantic2ts` (Apr 30). Single source
  of truth for the Python ↔ TS contract; CI drift gate
  (`test-codegen` job) fails the build if `generated.ts` falls out
  of sync.

**Direction shifts that were reverted:**
- CSV-with-unit-in-header LLM extraction (Apr 5 → Apr 17). Lost the
  structure the rest of the pipeline wanted.
- Recommendation chat surface (Mar 29 → Apr 5). The product is a
  spec database.
- `rust/` directory in tree (Mar 2026 → moved to its own branch,
  Apr 2026 → reintroduced as a planned full port → parked again
  Apr 30, 2026 in favor of Python consolidation: `todo/REFACTOR.md`
  flags the polyglot tax, `todo/PYTHON_BACKEND.md` and
  `todo/PYTHON_STRIPE.md` replace it).

**Repeating escapades:**
- Lambda bundling determinism (`b4fefd0`, `12829d8`). Resolved by
  `npm ci` + lockfile copy.
- CloudFront stale deploys (`972900d`, `a587673`). Cache headers +
  explicit invalidation.
- CI/CD `??` vs `||` (Apr 27) and `HOSTED_ZONE_ID` zone mismatch
  (Apr 28). Both class-of-bug eliminations are queued — `||`
  pattern lifted into global CLAUDE.md, `fromLookup` lined up to
  delete the secret.
- Quality filter bypass in the scraper (`af964b7`). The kind of bug
  that only shows up in a log line that doesn't quite parse.
- Stacked PRs and `gh pr list` (Apr 29–30). Phases 5a/5c/5e/5f
  reported MERGED but never reached master because they merged into
  a stacked parent PR that had itself already merged. Lesson lifted
  to memory: verify with `git log origin/master --first-parent` or
  `gh pr view <n> --json baseRefName` before trusting "MERGED."

**Choices that have held:**
- Single-table DynamoDB (`PK=PRODUCT#{type}`, `SK=PRODUCT#{id}`).
  Predates the first proper test suite; survived the NoSQL refactor,
  the Rust port spec, and three product-type expansions.
- `./Quickstart` as the single entry point. Bash shim → Python
  dispatcher → subprocess to underlying tools. CI runs the same
  script the developer runs locally.
- One Pydantic model per product type, each with a companion `.md`
  ADR citing source datasheets. The `.md` is reviewable — *why* the
  schema looks the way it does, not just what it is.
- Quality scoring + intake triage at the gate, not after. Bad rows
  go to `outputs/ingest_log/` for vendor outreach, not into the
  catalog.
- 18 SPEC_KEYWORDS groups in `page_finder`. A free, deterministic
  filter that keeps Gemini off pages that don't have specs.

**Open work** (post-Apr-30, see `FUTURE.md` for the pitch-shaped view):
1. PHASE5_RECOVERY — cherry-pick stranded 5a/5c/5e/5f auth-hardening
   onto master (~1.1k LOC).
2. MODELGEN — collapse hand-typed `models.ts` + Zod enum onto
   `generated.ts`.
3. SEO — Phase 1 structural lifts (build-time prerender, per-product
   pages, dynamic sitemap, Lighthouse CI gate).
4. MARKETING — Show HN + r/PLC + `awesome-*` PRs, gated on SEO
   Phase 1.
5. DEDUPE Phase 2+3 — auto-merge safe cases, human-review queue.
6. PYTHON_BACKEND Phase 1+ — Express → FastAPI parallel-deploy.
7. PYTHON_STRIPE — drop the Rust billing Lambda for ~100 lines of
   Python.
8. API — paid programmatic surface (Stripe-metered, gated on Phase
   5a SES + 5b WAF).
9. STYLE — eliminate native browser/OS chrome (Tooltip, ConfirmDialog,
   Toast, FormField, scrollbars, ExternalLink).
10. GODMODE — one-page admin dashboard. Lands last on stable
    substrate.
