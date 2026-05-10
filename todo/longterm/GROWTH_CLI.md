# Growth CLI: `./Quickstart growth` — awareness first, measurement later

Status: 📐 **design-only as of 2026-05-09.** No code lands until the
scope below is signed off.

## Why this doc exists

Nick asked: *"Let's develop a CLI to incorporate common marketing CLIs
like Google Cloud, Meta, etc. so we maximize our engagement footprint
and begin to build a feedback loop of engagement, improvement,
awareness, and iteration."*

After one pass through `todo/MARKETING.md`, two things
re-shaped the scope:

1. **`MARKETING.md` puts paid ads in the "Don't bother" column** — *"The
   CPM for 'industrial engineer' is brutal and the audience tunes out.
   Re-evaluate at $10k MRR."* Meta is not even named. So a Google Ads /
   Meta Marketing / LinkedIn Ads orchestrator is out — directly contrary
   to the active marketing thesis.
2. **There is no footprint yet to measure.** Nick (2026-05-09): *"I
   don't expect any actual hits yet because I'm not reaching out, we
   need to focus solely on awareness before we build a footprint, and
   after we have a footprint we can start seeing what's working."*
   Wiring Search Console / GitHub traffic / CloudFront analytics on
   week zero produces a digest of zeros. The order is: build a
   footprint first → then measure what's working.

So the CLI breaks into two halves on different clocks:

- **Awareness half (now).** A small set of helpers that make the
  *outbound* push from MARKETING.md cheaper and less error-prone —
  *without* automating the actual posting. Peer signal collapses if
  posts read as automated, so the human stays in the loop.
- **Measurement half (deferred).** The Search Console + GitHub +
  CloudFront + Stripe digest from the prior version of this doc.
  Worth nothing until there's traffic; revisit once MARKETING.md
  phase 1 (Show HN + r/PLC + r/AskEngineers + awesome-* PRs) has
  shipped and produced its first week of signal.

## What the CLI does (awareness half — phase 1)

One subcommand, read-only against the local repo + prod deployment +
the GitHub board. Outputs to stdout. Does not post anywhere.

### `./Quickstart growth preflight`

The pre-flight gate before pressing post on a high-traffic channel
(Show HN, r/PLC, etc.). Composes existing commands and the board:

- `./Quickstart smoke https://datasheets.advin.io` (or `specodex.com`
  once the rebrand cutover ships) — every canonical endpoint must
  200 with the expected shape.
- `./Quickstart bench` — offline quality run; flag any
  precision/recall regression vs `outputs/benchmarks/latest.json`.
- `gh project item-list 1 --owner JimothyJohn` — flag any P0 card
  in `In progress` / `In review` / `Backlog` status.
- Git state — flag any uncommitted changes on master, or master not
  matching origin.

Output is one of:

    GROWTH PREFLIGHT — READY
    ✓ smoke: 5/5 endpoints healthy
    ✓ bench: precision 0.91 (Δ +0.01), recall 0.84 (Δ -0.00)
    ✓ board: no P0 cards open
    ✓ git:   master clean, in sync with origin

or:

    GROWTH PREFLIGHT — HOLD
    ✗ smoke: /api/v1/search?type=motor returned 400
    ✓ bench: precision 0.91, recall 0.84
    ✗ board: P0 card open — "Stripe webhook signature broken"
    ✓ git:   master clean

Why this is worth building: MARKETING.md is explicit that *"a
high-profile post on HN/Reddit with a broken comparison is worse than
no post"*. The cost of one broken Show HN is a 90-day cooldown before
resubmission. One command that gates against that is worth the build.

### Out of scope (phase 1)

- **No automated posting.** No HN submit API, no Reddit submit, no
  LinkedIn API call. Peer signal evaporates instantly when posts
  read as bot-driven; MARKETING.md is unambiguous about this.
- **No paid-ads APIs.** Not Google Ads, not Meta Marketing, not
  LinkedIn Ads. Per MARKETING.md "Don't bother" until $10k MRR.
- **No checklist tracker.** A `todo/MARKETING.md` checkbox or a
  Project board card is cheaper than a state-file CLI. Skip until
  the manual approach actually creaks.
- **No draft templating.** Nick writes his own posts. The maintainer
  voice is part of the trust signal; CLI-templated post drafts are
  not.
- **No `facts` subcommand.** A "live numbers for embedding in a post"
  dump was scoped here briefly (2026-05-09), then dropped: the
  numbers are one curl + one `outputs/benchmarks/latest.json` read
  away, and Nick will be writing the post by hand anyway. Skip until
  someone is actually drafting a post and finding the manual lookup
  annoying.

## What the CLI does (measurement half — deferred)

Picked up only after MARKETING.md phase 1 produces its first week of
signal. The full surface from the prior version of this doc, in
order of dependency:

| Phase | Surface | Pulls | Auth |
|---|---|---|---|
| **2** | GitHub repo traffic | stars Δ, clones, top referrers, top paths | `gh auth token` (already on dev box) |
| **3** | Google Search Console | impressions, clicks, top queries, new top-10s | OAuth installed-app refresh token in `.env` |
| **4** | CloudFront access logs | sessions/week, top paths, 4xx ratio, cache hits | existing AWS creds; verify log bucket exists first |
| **5** | HN / Reddit mentions | new threads referencing `specodex.com`, `datasheets.advin.io`, `specodex` | Algolia HN (no auth); Reddit JSON (no auth) |
| **6** | Stripe (when paid surface ships) | new subs, MRR, churn | restricted read-only key |
| **7** | Threshold flags | new top-10 GSC query, 4xx spike, new referrer never seen before | local logic only |

Output: `outputs/growth/<yyyy-mm-dd>.md` weekly digest plus a
terminal summary, mirroring the bench output pattern. Each phase
ships standalone — kill the project between phases if the value
isn't there.

## Phasing

| Phase | Scope | Size | Gate to next |
|---|---|---|---|
| **1 — Preflight.** Compose `smoke` + `bench` + board + git into one gate. | XS | One real run produces a meaningful HOLD on a known-broken state. |
| **— wait —** | First week of MARKETING.md phase 1 traffic must land before phase 2 starts. The gate is "did we actually post anywhere?", not a date. |
| **2 — GitHub traffic.** Stars, clones, referrers. | XS | One real digest readable end-to-end. |
| **3 — Search Console.** | S | OAuth refresh token wired without a credential leak. |
| **4 — CloudFront logs.** | M | Numbers reconcile within 5% of a manual spot-check. |
| **5 — HN + Reddit mentions.** | XS | Surfaces a known-good past mention. |
| **6 — Stripe.** | XS | Stripe MRR > $0 in test mode. |
| **7 — Threshold flags.** | S | At least one flag fires on real data. |

## Implementation sketch (phase 1)

- **Module:** `cli/growth.py`, dispatched via
  `./Quickstart growth <subcommand>`.
- **Subcommands:** `preflight`. Future: `traffic`, `search`,
  `cloudfront`, `mentions`, `stripe`, `digest`.
- **Sources:** one source = one module under
  `specodex/growth/<source>.py` with a single `fetch(window) -> dict`
  function. The CLI composes them.
- **Cache.** Each fetch writes raw JSON to
  `outputs/growth/.cache/<source>/<yyyy-mm-dd>.json`; subsequent
  runs read cache when present. Mirrors the bench cache pattern.
- **Secrets.** All new env vars added to `.env.example` with one-line
  comments. No credential file beyond `.env`.
- **Tests.** `tests/unit/test_growth_*.py` per module, fixtured
  against a recorded JSON response. No live calls in CI.

## Open questions (for phases 2+)

- **GSC property URL.** Both `specodex.com` and `datasheets.advin.io`
  verified as Search Console properties? Phase 3 ships with whichever
  is verified.
- **CloudFront log bucket.** Confirm the bucket name + read perms
  per stage before scoping phase 4. If logs aren't enabled, that's
  a CDK change in `app/infrastructure/` — out of this CLI's scope.

## Triggers

| Trigger (files / topics in your current task) | Surface |
|---|---|
| User mentions "growth CLI", "engagement footprint", "marketing CLI", "feedback loop on traffic", "preflight", "Show HN check", "facts dump for a post" | this doc |
| User asks to wire Search Console, GitHub traffic, CloudFront logs, HN/Reddit mentions, or Stripe metrics into a report | this doc |
| `cli/growth.py` or `specodex/growth/` created or modified | this doc |
| Any conversation about *paid* spend on Google / Meta / LinkedIn | this doc + MARKETING.md "Don't bother" |
| Before any high-traffic post (Show HN, r/PLC, awesome-* PR) | run `./Quickstart growth preflight` (once phase 1a ships) |
