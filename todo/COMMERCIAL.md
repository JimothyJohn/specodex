# COMMERCIAL — sourced, confidence-scored commercial data (price, lead time, vendor facts) as first-class citizens

> 🚧 in progress — designed 2026-07-21. Phase 1 (provenance schema) and
> Phase 2 (inference engine) land first; Phase 3 (UI overhaul) and
> Phase 4 (vendor-facts registry) follow. Extends PRICING.md — that doc
> owns *acquisition* of listed prices; this doc owns *confidence,
> inference, lead time, vendor facts, and presentation*.

## The problem

Users pick industrial components on commercial criteria at least as
much as technical ones: what does it cost, when can I get it, who
stands behind it. Today the catalog is technical-first: `msrp` covers
~4% of rows, `lead_time`/`warranty` are never populated, availability
is a hidden per-seller snapshot, and nothing tells the user how
trustworthy a figure is or where it came from.

## Ground rules (non-negotiable)

1. **Facts only, opinions never.** A figure enters the system only if
   it is an explicit, published fact: a listed price, a printed
   warranty term, a published on-time-delivery percentage, a stated
   support-hours window. No review scores, no sentiment, no "known
   for good service." If a vendor doesn't publish it, we don't have it.
2. **Every figure carries its receipts.** Each commercial value stores
   ≥1 citation: URL + retrieval timestamp + verbatim excerpt. A reader
   must be able to open the source and find the excerpt. Inferred
   values cite the comparable product IDs + algorithm version instead
   — equally replicable given a DB snapshot.
3. **Confidence is explicit and honest.** Every displayed commercial
   figure has a confidence tier. An estimate never masquerades as a
   listed price.
4. **Replicable.** Given the same sources (or same DB snapshot +
   algorithm version), a re-run produces the same figure. Inference is
   deterministic: sorted comparables, versioned algorithm, no RNG.

## The generic acquisition ladder (any commercial figure)

Ordered by confidence; a figure records which rung produced it:

| Rung | Source class | Examples | Confidence |
|---|---|---|---|
| 1 | OEM listed | manufacturer web store, printed price book | `listed` |
| 2 | Distributor listed | authorized distributor product page (JSON-LD/microdata) | `listed` |
| 3 | Aggregator / marketplace | shopping-tier median across sellers, surplus/aggregator pages | `high` |
| 4 | Trade press / articles | published "starts at $X", press-release pricing | `medium` |
| 5 | Family interpolation | same manufacturer + family price ladder (DB) | `inferred` (medium score) |
| 6 | Cross-vendor comparables | same type + closest specs across manufacturers (DB) | `inferred` (low score) |

Rungs 1–4 are the existing PRICING.md machinery (extract cascade,
price books, shopping tier) — already built, plus articles as a new
`SourceKind`. Rungs 5–6 are the new inference engine (Phase 2). The
same ladder applies to lead time: OEM published lead times (rung 1),
distributor stock status (rung 2, via availability-enrich),
family/vendor inference (rungs 5–6).

## Phase 1 — provenance + confidence schema (`specodex/models/commercial.py`)

New shared models, additive-only on `ProductBase`:

```python
SourceKind = Literal["oem", "distributor", "aggregator", "price_book",
                     "shopping", "article", "vendor_doc", "db_inference"]
Confidence = Literal["listed", "high", "medium", "low", "inferred"]

class SourceCitation(BaseModel):
    url: Optional[str]           # None only for kind="db_inference"
    kind: SourceKind
    retrieved_at: str            # ISO 8601
    excerpt: Optional[str]       # verbatim quote backing the figure (≤280 chars)
    comparable_ids: Optional[List[str]]  # db_inference: sorted product_ids used
    method_version: Optional[str]        # db_inference: e.g. "price-comps-v1"

class SourcedFigure(BaseModel):
    value: ValueUnit             # {value: 1234.0, unit: "USD"} / {value: 6, unit: "weeks"}
    confidence: Confidence
    observed_range: Optional[MinMaxUnit]  # dispersion across sources/comparables
    sources: List[SourceCitation]         # min_length=1 — no orphan figures
```

`ProductBase` gains `price_estimate: Optional[SourcedFigure]` and
`lead_time_estimate: Optional[SourcedFigure]`. Existing fields keep
their meaning: `msrp` (+`msrp_source_url`/`msrp_fetched_at`) stays the
*listed* price; `availability` (+provenance pair) stays the per-seller
snapshot. The UI resolution order is: listed figure if present, else
estimate with its confidence badge. Never merge the two.

Invariant tests: a `SourcedFigure` cannot exist without ≥1 citation; a
`db_inference` citation must carry `comparable_ids` + `method_version`;
a non-inference citation must carry `url`. Schema-compat snapshots
refresh (additive fields), gen-types reruns.

## Phase 2 — inference engine (`specodex/pricing/inference.py`)

"Scan the database and price from similar results" — deterministic and
explainable, no black box:

- **Comparable selection.** Same `product_type`. Prefer same
  manufacturer + same `product_family` (rung 5); fall back to
  cross-manufacturer same-type (rung 6). Spec distance in log space
  over the type's dominant sizing specs (motor: rated_power,
  rated_torque; drive: rated_power, rated_current; gearhead:
  max_continuous_torque, gear_ratio; actuator: max_force, stroke;
  robot_arm: payload, reach). Missing-spec rows are excluded from the
  comparable pool, never guessed.
- **Estimation.** Within a same-family ladder with ≥3 priced points:
  log-log interpolation on the dominant spec (industrial pricing is
  power-law in size). Otherwise: distance-weighted median of the k≤8
  nearest priced comparables. `observed_range` = p25–p75 of the pool.
- **Confidence scoring.** Family ladder interpolation → `inferred`
  with medium score; cross-vendor median → `inferred` low. Pool size
  and dispersion gate output: <3 comparables or p75/p25 > 4× → no
  estimate at all (an absent figure beats a junk figure).
- **Determinism / replicability.** Comparables sorted by product_id;
  algorithm version string in every citation; re-running against the
  same DB snapshot reproduces the estimate bit-for-bit. Property test
  pins this.
- **Lead time inference.** `stocked` derives from availability
  (in_stock/limited at any observed seller ⇒ "stocked — ships in
  days", citation = the availability observation). Non-stocked rows
  get a lead-time estimate only from published vendor/family lead
  times (vendor registry, Phase 4) — we do NOT statistically invent
  factory lead times; there is no honest public per-part signal
  (see availability field note in product.py).

CLI: `./Quickstart price-infer [--stage dev] [--type X] [--dry-run]`
(dry-run default, dev-only writes, prints the comparable table per
estimate so an operator can eyeball the reasoning).

## Phase 3 — commercial-first UI

- **Commercial band** (already pinned far-left: Price, Lead Time):
  price cell shows listed price, or estimate rendered distinctly
  (`~$1,240` + confidence chip); lead-time cell shows `STOCKED` badge
  or estimate/`—`. Warranty column joins the band when populated.
- **Confidence chip + source popover.** Every commercial cell gets a
  small tier chip (`listed`/`high`/`med`/`low`/`est`). Click → popover
  listing each citation: publisher, date, excerpt, external link; for
  inferred figures: "estimated from N comparables" with links to those
  product rows. App-native popover (Tooltip/Popover primitives — no
  `title=`, per the no-native-chrome table).
- **Commercial ⇄ Technical balance.** Segmented control
  (Commercial / Balanced / Technical) that reorders column groups and
  default sort; persists in localStorage like density/units. Filter
  chips work on commercial fields (price range incl. estimates toggle,
  stocked-only, min warranty).
- **Vendor drawer.** Click a manufacturer → drawer with the vendor's
  registry facts (Phase 4): warranty terms, support channels + hours,
  certifications, published OTD%, factory locations — every line with
  its citation. No facts → drawer says so, plainly.

## Phase 4 — vendor-facts registry (citation-required)

`data/vendors/<slug>.json`, one file per manufacturer. Every field is
a `{fact, sources[]}` pair; the loader **rejects any fact without a
citation**. Facts limited to the explicit-and-published:

- standard warranty terms (e.g. "3-year warranty on servo products")
- support channels: phone, email, hours, regions
- certifications: ISO 9001/14001 etc. (cert body + number when shown)
- published on-time-delivery % (annual/quality/investor reports only)
- factory / support locations
- published standard lead-time statements ("motors ship in 2 weeks")

Tooling: `./Quickstart vendor-facts validate` (schema + citation
completeness) and `vendor-facts verify` (re-fetch each URL, confirm
the verbatim excerpt still appears; failures flag the fact stale —
this is the replicability audit). Registry rows land in DynamoDB as
`VENDOR#<slug>` and serve via `/api/v1/vendors`. Seeding is
research-driven and incremental; an empty registry renders honestly
as "no published facts on file."

## What this deliberately does NOT do

- No review-site scores, star ratings, Reddit/forum sentiment, or
  "reputation" synthesis — subjective, non-replicable, excluded.
- No per-part factory lead-time guesses beyond the stocked signal and
  published vendor statements.
- No currency conversion (USD only, matching the price band logic).
- No paid data sources without an explicit decision.

## Phasing / PRs

| # | Scope | Status |
|---|---|---|
| 1 | `commercial.py` schema + ProductBase fields + gen-types + snapshots | 🚧 |
| 2 | Inference engine + `price-infer` CLI + property tests | 🚧 |
| 3 | UI: confidence chips, source popover, stocked badge, balance control, vendor drawer shell | ⚪ after 1 |
| 4 | Vendor registry: schema, validate/verify CLI, seed 3–5 vendors with real citations | ⚪ independent |
| 5 | Article `SourceKind` wiring in the extract cascade; estimates backfill sweep on dev | ⚪ after 1+2 |

## Triggers

| Trigger | Surface |
|---|---|
| `specodex/models/commercial.py`, `SourcedFigure`, confidence chips, vendor drawer, `data/vendors/` | this doc |
| price acquisition (crawler, price books, shopping tier) | [PRICING.md](PRICING.md) |
