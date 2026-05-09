# BOARD_FEEDBACK — easy/obvious subset

> Action subset pulled from [longterm/BOARD.md](longterm/BOARD.md). Only
> items that are **low effort** (≤ a day each), **high agreement** across
> the simulated board, and **don't require a customer / partner /
> external decision** to start. The strategic items (beachhead choice,
> engineer-direct vs. B2B2C, hiring) live in BOARD.md and stay there
> until you're ready.

## What's already done (don't redo)

A quick scan of the repo before writing this list — these came up in the
board doc but are partly or fully shipped already:

- **User-facing brand is already Specodex.** `Welcome.tsx` and
  `app/frontend/index.html` don't say "Datasheetminer" anywhere; only
  the README still leans on the dual identity. So the brand-split fix
  is a README cleanup, not a UI overhaul.
- **STYLE infrastructure is shipped** — Phases 1+2+3+4+5+6+7.1 closed
  this session. The board's "engineering taste signal" comment lands
  on top of work that's already paid for.
- **SEO Phase 0 metadata is shipped** (per `todo/longterm/SEO.md`).
  Structural lifts (per-product pages, dynamic sitemap, prerender)
  remain — but they're a separate workstream, not this list.

---

## Action items

Ordered roughly by leverage / least-friction first. Pick three for the
next two weeks; don't try to do all of them at once.

### 1. Resolve the brand split in the README and GitHub org

**What:** rename "Datasheetminer" → "Specodex" everywhere a *user* sees
it. Concretely: `README.md` headline + first paragraph + badges, GitHub
repo description, GitHub social-preview image, the LinkedIn bio /
Twitter / any external profile. Keep "Datasheetminer" alive only as the
internal-pipeline name in `specodex/`, `cli/`, and architecture docs —
never on a customer-facing surface.

**Why:** the dual identity costs every first-time visitor 10 seconds of
"are these one thing or two?" The user-facing UI already converged to
Specodex; the README is the lone holdout. Free credibility once
unified.

**Effort:** ~1 hour. One PR, one re-publish of any external bios.

**Dependencies:** none.

---

### 2. Write `PUBLIC.md` (continuity plan)

**What:** a single short markdown file at the repo root, publicly
visible. Sections:

- **If I'm out of contact for 60 days:** name a single trusted contact,
  describe how someone could keep the deploy chain alive (which secrets
  to rotate, which CloudWatch alarms to disable, how to put the site
  in read-only mode).
- **What's archived where:** S3 bucket of source PDFs, DynamoDB export
  cadence, where the Stripe customer list lives.
- **License/data terms:** restate MIT for code, declare the data terms
  (probably "facts are not copyrighted; presentations of marketing
  copy are not reproduced; takedown contact is X").

Pair it with the practical step: hand a sealed envelope (deploy keys +
emergency runbook) to one trusted person. They never open it unless
something's actually wrong.

**Why:** Diego, Priya, and Ron all flagged "will you be here in 24
months?" independently. A public continuity plan + sealed envelope
costs almost nothing and removes the cheapest possible objection from
prospects, partners, and future hires. It also forces *you* to think
through the failure modes, which makes you less brittle.

**Effort:** 1–2 hours for the file; another hour to organise the keys
and find a trusted contact.

**Dependencies:** picking the trusted contact (most likely already
obvious — spouse, sibling, or one peer engineer).

---

### 3. Ship a public data / takedown policy

**What:** a small page (or section in `PUBLIC.md`, or a footer link
to a `/policy/data` page) documenting:

- Source policy: every spec is extracted from a publicly-published
  manufacturer PDF, linked on the product row.
- Reproduction policy: structured numeric specs are reproduced as
  facts (not copyrightable). Verbatim manufacturer marketing prose is
  not reproduced.
- Takedown contact: one email address, response SLA (e.g. 5 business
  days).
- Opt-in process for manufacturers who'd like to provide enhanced
  listings (better photos, official datasheet links, whatever).

**Why:** cheap insurance. The first hostile vendor letter will arrive
some time after meaningful traffic. A documented public stance makes
that letter much easier to handle, and signals seriousness to the
opt-in vendors you'll want as references.

**Effort:** 2–4 hours including legal-adjacent careful wording.

**Dependencies:** none.

---

### 4. Email five manufacturers introducing the project

**What:** pick five manufacturers whose products you've already indexed
(motors / drives / gearheads). Send a short, plain email from you
personally — not a marketing template — to the product manager or
marketing contact you can find:

> Subject: Indexing your <X> series in Specodex
>
> Hi — I'm building Specodex, a cross-vendor industrial spec database
> aimed at mechatronics engineers. Your <X> series is already indexed
> from your public datasheets. Two things I'd value your input on: (a)
> are there fields you'd like surfaced or de-emphasised, (b) is there
> a contact at your end I should keep informed when we make
> presentation choices about your product line? Happy to take any
> takedown request immediately, but my hope is we'd be useful to your
> customer base. — Nick

**Why:** the ask is genuine ("we'd love your input") and the optional
opt-in pitch is implicit. Two written "yes, this is fine" responses
are worth more than ten user testimonials when a hostile lawyer (or a
serious investor) eventually shows up.

**Effort:** 2 hours to write + send. Inbound responses trickle in over
weeks.

**Dependencies:** the data/takedown policy page (item 3) should be live
first so the email can link to it.

---

### 5. Pick the first paid tier and price

**What:** make the decision, don't ship it yet.

- **Tier name:** "Pro" or similar.
- **Price:** $9 or $19 / mo. Pick one.
- **Three features behind the gate:** my suggestions, optimising for
  what's already half-built —
  1. **Saved Projects** with sharing (the Projects feature already
     exists; gate the "save more than 3" or "share with teammates"
     part).
  2. **BOM export** in CSV + JSON (the export likely exists for the
     build tray; gate the un-watermarked / un-truncated version).
  3. **Datasheet-update alerts** — opt in to a row, get an email when
     the source PDF changes hash. (New work but small.)
- **Free tier:** unlimited browsing, filtering, manual record export
  one-at-a-time. Specodex stays free-to-read forever.

**Why:** a paid tier is the only signal that tells you whether the
audience is real or only enthusiastic. Skipping straight to enterprise
is a common bootstrap death — Priya's $5K/year gate is 18 months out;
the $9/mo individual gate is the test you can run *now*.

**Effort:** the decision is 30 minutes. Stripe scaffolding is already
in `stripe/` per `todo/longterm/PYTHON_STRIPE.md`. Putting the page up
and wiring the gate is a 1–2 day shippable PR after the decision.

**Dependencies:** none for the decision.

---

### 6. Start the customer-conversation log

**What:** create `~/notes/specodex-conversations.md` (private to you,
not in the repo). For every call / DM / email exchange with a real
named potential user:

- Date, name, role/company.
- What they're solving (in their words, not yours).
- Whether they'd pay, at what price, for what.
- One-line summary of what surprised you.

Schedule **5 30-minute calls per week** for the next four weeks. Use
your warm network first (LinkedIn search "controls engineer" /
"motion control engineer" within 2nd-degree connections), then
cold-email second. The script can literally be: "I'm building a
cross-vendor motion-control spec compare tool, and I'm trying to
understand what the workflow actually looks like for people doing
your job. Could I ask you 20 questions for 30 minutes?"

**Why:** this is the highest-leverage activity for a bootstrapped
solo founder, and the one most reliably skipped because shipping
feels productive and calling feels uncomfortable. The board doc names
this out loud; the log is the accountability mechanism.

**Effort:** ~3 hours/week (call time + scheduling + notes), every week.
Replaces about a third of your shipping velocity, on purpose.

**Dependencies:** none. Just start.

---

### 7. Track the customer-conversation / coding-hours ratio

**What:** at the end of each week, add a line to a personal log:

```
2026-05-09  ship: 28h   talk: 4h   ratio: 0.14   notes: …
```

Aim for **ratio ≥ 0.3** by week 8. If you're below 0.15 for three
weeks in a row, that's the founder-trap signal — clean engineering
dopamine substituting for customer fear. Course-correct deliberately.

**Why:** without the metric, the ratio drifts toward 100% coding.
With the metric, the trend is visible weekly. Single most reliable
predictor of bootstrap-survival in Ron's experience.

**Effort:** 60 seconds per week.

**Dependencies:** none.

---

### 8. Add data-provenance affordances to product rows

**What:** every numeric value on a product row should be one click
away from showing:

- The source datasheet URL (likely already there).
- The page in the PDF where it came from (probably captured in
  `pages` per `process_datasheet`; surface it in the UI).
- The date the value was extracted.
- Optionally: the LLM model + prompt version (Gemini 2.5 Flash + a
  schema hash).

If any of those four fields aren't currently captured in the
DynamoDB row, that's a separate upstream change — but at minimum the
fields that *are* captured should be visible without opening DevTools.

**Why:** Maya, Diego, and Hank all named data trust as the gating
trust issue. "Last verified, source page 47 of <X>.pdf, extracted
2026-04-22 by Gemini 2.5 Flash schema v1.3" is the single
highest-credibility affordance in industrial spec data.

**Effort:** if all fields are already captured: a 2–4 hour UI change.
If `extracted_at` and `source_page` aren't on the model: ~1 day
including a `gen-types` and a backfill of existing rows.

**Dependencies:** check what's already on the Pydantic models first.
A quick `grep` says `extracted_at` / `source_page` / `model_version`
aren't present today, so this is a model addition + UI surface, not
just a UI surface.

---

### 9. Decisions to make — and write down — this week

Not actions; *resolutions* to write into a one-page notes file so they
stop being open questions:

| Question | Why decide now |
|---|---|
| Which one product type is the beachhead? (motor / drive / gearhead / electric cylinder / robot arm) | Catalog depth + SEO compounding both depend on the choice. |
| Engineer-direct or B2B2C data layer (BOARD.md Decision A)? | Each rejects the other; deferring costs option value monthly. |
| What you will *not* do this quarter (the Priya bucket — SSO, SOC 2, audit log, SLA pages). | "Not now" written down beats "not now" in your head — stops the recurring guilt drag. |

**Effort:** 30 minutes of honest thought + 30 minutes of writing.

**Dependencies:** ideally informed by item 6 (customer conversations)
once it has 2–3 weeks of data. If you've already got a strong gut
read, write it now and revisit in 60 days.

---

## Deferred (intentionally not in this list)

These came up in BOARD.md but aren't on this checklist for a reason:

- **Catalog depth growth to 5K SKUs in one type** — high-leverage but
  weeks of work, not a "knock out tomorrow" item.
- **Biweekly advisor call with an industry vet** — high value (Ron's
  point 6), but depends on identifying and asking a specific person,
  which has a different rhythm than a checklist.
- **Public OG image generator, per-product SSR, dynamic sitemap** —
  belongs in the SEO workstream, already documented in
  `todo/longterm/SEO.md`.
- **API as a shippable product surface** — Diego's $99–199/mo ask is
  real money but architectural work; deserves its own design pass.
- **Compatibility-graph investment** — the real moat per Diego and
  Ron; multi-month R&D rather than a checklist item.
- **SOC 2 / SSO / SCIM / audit log** — explicitly Priya-tier; defer
  18+ months until a paying customer asks.

---

## Honest assessment of this list

Of items 1–9: **items 1, 2, 6, 7 are pure-upside and zero-risk** —
do those this week. **Items 3, 4 are insurance with a small ongoing
cost** — do them this month. **Item 5 is a decision, not a build**
— make it this week and let it sit until you've got customer signal.
**Item 8 is the highest-impact UI work on the list and possibly the
hardest** — it might pull in a model change. **Item 9 is the
homework that makes the rest of these meaningful.**

Total checklist scope, executed deliberately: ~2 weeks of evening
work plus the ongoing customer-conversation discipline. None of it
unblocks itself; pick three and start.
