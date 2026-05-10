# CATAGORIES — supercategory / subcategory model + part-number configurator

> **Filename note.** Filename intentionally matches the request spelling
> ("CATAGORIES.md") so the original ask is grep-able. Body text uses
> the standard spelling ("categories") throughout.

**Status (post-2026-05-10 sprint):** Phase 0 + Phase 1 ✅ shipped via
PRs #85 (recovered design docs) and #87 (MotorMountPattern bridge
fields). Phase 2+ (additional supercategories beyond Linear Motion —
e.g. Rotary Motion, Sensing, Fluid Power) not yet scoped. The
`/actuators` MVP page lives at `app/frontend/src/components/ActuatorPage.tsx`
and is the working baseline; BUILD.md will generalise it into the
requirements-first `/build` page.

This doc covers two coupled designs that arrived together with the
"new actuator MVP" ask:

1. A **supercategory / subcategory** layer over the flat `product_type`
   literal — needed because Linear Motion already has two sibling types
   (`electric_cylinder`, `linear_actuator`) and a third (`belt-driven
   rodless`, `linear motor`, etc.) is plausible. The current sidebar
   dropdown treats each as a peer of `motor` / `drive` / `gearhead`,
   which buries the fact that they all answer the same selection
   question ("how do I move this load this far?").
2. A **procedural part-number configurator** layer — most actuator
   vendors (Lintech, Parker, Toyo, IAI, SMC) sell a *configurable
   family* whose individual SKUs are synthesised from travel + lead +
   motor mount + accessories. Catalog ingest can capture the family
   shape but never the explosion of trailing modifiers, and trying to
   would balloon the DB with junk rows that all differ by a wiper kit.
   The right surface is generative: store the *template* and let the UI
   build a candidate part number on demand.

Both pieces are **MVP-quality** at first ship — enough to make the
Actuator page real, not enough to be the final shape. Real consumer
feedback on the actuator MVP should drive any restructuring.

## Status

**Phase 0 + 1 shipping on `feat-actuators-mvp-20260508` (2026-05-08):**

- Supercategory map (`Linear Motion`, `Rotary Motion`, `Drives & Control`,
  `Switching`, `Robotics`) in `app/frontend/src/types/categories.ts`.
- `/actuators` MVP page with subtype tabs (Linear Actuator / Electric
  Cylinder), filter chips auto-derived from records.
- Procedural part-number configurator with **bidirectional sync**:
  - **Synthesise** part numbers from segment choices.
  - **Parse** existing record part numbers back into segment choices
    (click a row → configurator pre-fills).
  - **Round-trip pinned** by 8 unit tests across 6 templates.
  - **Performance derivation**: each template knows how to map its
    choices to lead pitch (mm/rev), theoretical max speed at 3000 RPM,
    and a suggested motor frame size.
- **Cross-device relations**: when a configuration suggests a motor
  frame, the page lazy-loads motor records and surfaces 3-5 best-effort
  candidates filtered by word-boundary frame match.
- Six templates ship: Tolomatic TRS / BCS / ERD (cover all 12+14+27 = 53
  records of those families in dev DB), Lintech 200 Series (real catalog
  encoding `200<frame><travel>-WC<accessory>`, validated against 28
  variants extracted by the schema fit-check), Toyo Y-series, Parker HD
  (documentation-only since Akamai blocks Parker fetches).
- Schema fit-check across Lintech 200 / Lintech 150 / Toyo extracted 34
  variants total with **zero "extras"** — the existing `linear_actuator`
  Pydantic schema covers Lintech and Toyo as cleanly as Tolomatic.
  Findings appended to `specodex/models/linear_actuator.md`.

**Not yet shipped:**

- Pydantic `configurator` field on `LinearActuator` (templates still
  live in frontend fixtures — see "Where the templates come from"
  below for the migration trigger).
- Per-supercategory landing pages beyond `/actuators` (the supercategory
  map is in place but only `/actuators` ships).
- Sidebar dropdown grouping by supercategory (mentioned as a "cheap
  lift" but deferred until needed).
- Compatibility validation between actuators and motors (the current
  surface is "frame-name suggestion", not a full strict check à la
  `compat.ts`).

## Why now

The user asked for a "rodless screw-driven" classification and an
MVP Actuator page. The Pydantic schema for that exact shape already
exists (`linear_actuator.py` with `type: rodless_screw`,
`actuation_mechanism: ball_screw|lead_screw`) — so the bottleneck
isn't schema, it's the **navigational surface** ("what is an
Actuator?", "how do I find one for my load?"). That surface is
the supercategory layer + a configurator that lets users describe
what they want and get a candidate part number back, even when the
DB doesn't have that exact SKU because the family encodes 10⁴+
permutations in 30 catalog rows.

The doc deliberately scopes small. Aim is to make the next person
who adds a Linear Motion sibling (e.g. voice-coil) or adds a new
supercategory (e.g. "Sensors": photoelectric, inductive, vision)
understand the contract without re-reading every PR.

---

## Part 1 — Supercategory / subcategory model

### Current state (flat)

The frontend sidebar dropdown lists every registered product type as
a peer:

    [Contactor] [Drive] [Electric Cylinder] [Gearhead]
    [Linear Actuator] [Motor] [Robot Arm]

Two of those (Electric Cylinder, Linear Actuator) answer the same
selection question. A user looking for "linear motion" has to know
that "Electric Cylinder" is rod-style and "Linear Actuator" is
rodless before they'll click the right one. That's a leak of internal
schema decomposition into the user-facing nav.

### Target shape

A **two-level taxonomy** layered on top of (not replacing) the flat
`product_type` literal:

    Supercategory       Subcategories (= product_type values)
    ──────────────────────────────────────────────────────────
    Linear Motion       linear_actuator, electric_cylinder
    Rotary Motion       motor, gearhead
    Drives & Control    drive
    Switching           contactor
    Robotics            robot_arm

The supercategory is **derived metadata**, not a stored field on
records. `product_type` remains the only discriminator the DB and
LLM see; the supercategory is computed from a small static map.

### Data shape

A single source of truth, generated alongside `generated.ts`:

```ts
// app/frontend/src/types/categories.ts (hand-written for now;
// promote to gen-types output later if this table grows past ~20
// product types).

export type Supercategory =
  | 'linear_motion'
  | 'rotary_motion'
  | 'drives_control'
  | 'switching'
  | 'robotics';

export interface SupercategorySpec {
  id: Supercategory;
  display_name: string;
  description: string;     // one-sentence "what is this?" for the page header
  subcategories: ProductType[];
  // Optional: the "selection question" copy a user actually has in
  // their head. Drives the page subhead and the dashboard tile copy.
  selection_question: string;
}

export const SUPERCATEGORIES: Record<Supercategory, SupercategorySpec> = {
  linear_motion: {
    id: 'linear_motion',
    display_name: 'Linear Motion',
    description: 'Devices that translate a payload along a single axis.',
    selection_question: 'How do I move this load this far?',
    subcategories: ['linear_actuator', 'electric_cylinder'],
  },
  // ... other supercategories
};
```

The map is **append-only** in MVP: deletes/renames need a code
search of every consumer. Stored separately from `generated.ts`
because the supercategory is a UX-layer construct that doesn't
belong in the Pydantic source-of-truth.

### Where it surfaces

| Surface | Today | After |
|---|---|---|
| Sidebar dropdown | flat list of 7 product types | optgroup'd by supercategory; subtypes nested |
| `/` (ProductList) | first-load shows nothing until type is picked | optionally, a supercategory landing card |
| Dashboard tiles | per-product-type counts | aggregate per supercategory + drill-down |
| New `/actuators` route | does not exist | dedicated supercategory landing — see Part 3 |

The **dropdown grouping** is the cheapest lift and doesn't break the
flat-`product_type` API (the value committed is still the
subcategory's literal). It can ship in the same MVP PR as the
Actuator page; everything else can wait for real demand.

### What stays flat

- `product_type` discriminator on records — no change.
- `/api/products/categories` endpoint — still returns subcategory
  rows (`{type, count, display_name}`). Supercategory aggregation is
  a frontend concern.
- `/api/v1/search?type=<value>` — still takes a subcategory literal.
  Searching across a supercategory is the frontend issuing N parallel
  `?type=` calls and merging.

This **deliberately resists** the refactor where supercategory becomes
a query param. That refactor sounds clean but bakes the taxonomy into
the API surface; the wrong taxonomy at the API level is much harder to
walk back than a wrong frontend grouping. If supercategory aggregation
becomes hot, add a `/api/v1/search?supercategory=linear_motion`
endpoint that fans out internally — keep the per-type endpoint.

### Adding a new supercategory or subcategory

A new **subcategory** is the existing "add a new product type" flow
(see CLAUDE.md § Adding a new product type). The only extra step is
listing it in `SUPERCATEGORIES[<super>].subcategories`. If the
sidebar dropdown is grouped, an unlisted subcategory falls into an
`Other` group rather than vanishing — that's the failure mode we want
(visible misconfiguration, not silent omission).

A new **supercategory** is one new entry in the `SUPERCATEGORIES`
map. Adding it does not require any backend or model changes. If a
new supercategory has zero subcategories yet, the entry is still
useful as a documentation anchor — write it before the first
subcategory lands so the design intent is visible.

### Out of scope for MVP

- Multi-supercategory membership (e.g. "linear motor" being both
  Linear Motion *and* Motors). Punt until a real product type wants
  it; the right answer might be tags, not multi-membership.
- Per-supercategory custom landing layouts. The Actuator page is the
  only one that ships; everyone else can use a generic
  `<SupercategoryPage supercategory="..."/>` later.
- URL-based supercategory routing for every supercategory. Only
  `/actuators` ships in MVP — others get reachable from the dropdown
  + the existing `/?type=` selection.

---

## Part 2 — Procedural part-number configurator

### The problem

A Lintech 200-series rodless actuator has a part number like
`200-LBM-072-08-S-XX-A1` (made-up but representative). Each segment
encodes:

- `200` — frame size
- `LBM` — drive type (lead-screw belt-mount)
- `072` — travel length in inches
- `08` — lead pitch (0.8 turn/inch)
- `S` — motor mount style
- `XX` — accessory bundle bits
- `A1` — version revision

Variants per family, fully exploded: travel × lead × motor mount ×
accessories ≈ 10–10⁵ SKUs. Catalog ingest (`page_finder → LLM →
DynamoDB`) captures the **family** as one row (or 10–30 rows per
travel band), not the exploded SKU set. Trying to extract the
exploded set wedges Gemini's context window and produces low-value
duplicates that look identical except for trailing accessory codes.

The user's selection workflow doesn't need the exploded set:

> "I need 600 mm travel, 50 kg payload, 250 mm/s, NEMA 23 motor mount —
> what part number do I order?"

The answer is **synthesised**, not retrieved.

### The contract

A **vendor-pluggable part-number builder** lives alongside the family
record. Schema:

```python
# specodex/models/configurator.py (new file, MVP scaffolding)
from typing import Literal, Optional
from pydantic import BaseModel, Field

class ConfiguratorSegment(BaseModel):
    """One piece of a part number that the user can pick."""
    name: str                       # internal id ("travel", "lead")
    display_name: str               # "Travel length"
    kind: Literal["enum", "range", "literal"]
    # enum: discrete options like motor mount style
    options: Optional[list[dict]] = None   # [{value: "S", label: "NEMA 23"}, ...]
    # range: continuous, with allowed step
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: Optional[str] = None
    encode: str                     # how to format the chosen value
                                    # → e.g. "{value:03d}" for "072"
    required: bool = True

class ConfiguratorTemplate(BaseModel):
    """A vendor's part-number assembly recipe for one product family."""
    template: str                   # "200-{drive}-{travel}-{lead}-{mount}-XX-A1"
    segments: dict[str, ConfiguratorSegment]
    # Trailing modifier handling: anything we don't capture is a
    # placeholder (e.g. "XX" for accessory bundle) — preserved
    # verbatim in the synthesised number.
    placeholders: dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None     # free text — vendor quirks, the
                                    # "lead pitch is encoded as
                                    # tenths-of-inch" trick, etc.
```

**MVP storage:** templates ship as **frontend-side JSON fixtures**
keyed on `(manufacturer, series)` — see
`app/frontend/src/types/configuratorTemplates.ts`. No Pydantic
field, no DB migration, no LLM call. Two reasons:

- Templates are hand-curated artifacts. Storing them next to the UI
  that reads them keeps the loop tight (edit → reload → see effect).
- A Pydantic field would force every record-write path to either
  carry the template or carry `None`, which churns DB rows for a
  feature most records won't use.

**Post-MVP migration target:** the same shape moves onto the
Pydantic model when there's a reason to vary templates per record
(e.g. different revisions of the same family with different
encodings):

```python
class LinearActuator(ProductBase):
    # ... existing fields ...
    configurator: Optional[ConfiguratorTemplate] = None
```

Adding `configurator` to a model is **non-breaking**: it's optional,
and consumers that don't care about configurators just ignore it.
Don't migrate until the cost of the fixture-keyed-on-series shape
exceeds the cost of the model migration; the threshold is roughly
"a vendor ships two encodings of the same series."

### Where the templates come from

Three sources, in order of preference:

1. **Hand-authored from the catalog's "ordering information" page.**
   Most vendors publish a single page that lays out the encoding —
   that page is the human-readable version of `ConfiguratorTemplate`.
   The Late Night queue (`todo/README.md`) gets a new task: "audit the
   ordering page of any new actuator family and hand-write the
   configurator JSON". One-time work per family, ~15 minutes.
2. **Schemagen-style LLM extraction.** Gemini is good at parsing
   "ordering information" tables when given just that page. Cost
   ≈ a single `propose_model` call per family (~$0.005). Skip if a
   hand-authored template already exists.
3. **None.** The model field stays `None`; the UI hides the
   configurator panel for that record. Better than fabricating one
   from nothing.

The MVP ships with **two hand-authored templates** to prove the
shape — one Lintech, one Parker. Everything else is `None` until
filled in over time.

### Where it surfaces

The Actuator page (Part 3 below) renders a **configurator drawer**
when a record has `configurator != None`:

- The user picks values for each segment via the same UI primitives
  the filter chips use (`Dropdown`, `RangeSlider`, etc.).
- The synthesised part number updates live.
- A **"copy" button** copies the part number to clipboard. (Not
  "request quote" or any backend call — MVP stops at copy.)

The configurator panel is **read-only**: changing segments doesn't
mutate any record, doesn't write to DynamoDB. The synthesised
number is ephemeral.

### What this is NOT

- **Not a price quote.** Pricing varies by accessory bundle and
  distributor; the configurator does not touch `msrp`.
- **Not a stock check.** The synthesised SKU may not exist in any
  vendor's inventory system.
- **Not auto-extracted from arbitrary catalogs.** Each family
  needs a template authored once; the alternative (LLM proposes a
  template per ingest) was considered and rejected as too
  hallucination-prone for something users will copy into POs.

The footer of the panel says exactly this in plain English.

### Validation

A `synthesise(template, choices)` function:

- Returns the formatted part number when every required segment has
  a valid choice.
- Returns `None` + an error list otherwise.
- Catches the obvious failure modes: missing required segment,
  out-of-range value, enum value not in `options`.

It does **not** validate that the resulting SKU exists in the
vendor's catalog — that's the user's job, with the disclaimer above.

### Out of scope for MVP

- Vendor catalog round-trip (e.g. "click here to verify against
  Parker's online configurator"). Useful but every vendor's API is
  bespoke.
- Cross-segment dependencies (e.g. "if travel > 1500mm, motor mount
  must be S2 not S1"). Some vendors have these; first MVP fakes them
  by listing only the legal combinations as enum values, eats the
  combinatorial blowup until it hurts.
- Configurator versioning. When a vendor revises the encoding, the
  old template just gets edited in place. Records with the old
  encoding will silently misformat — surface this if it happens.
- Configurator templates for non-actuator types. Drives, contactors,
  motors are ordered by SKU more often than configured; revisit when
  a real configurable family lands in those types.

---

## Part 3 — MVP Actuator page (`/actuators`)

### Goal

A dedicated route that demonstrates the supercategory layer and the
configurator drawer using existing data. Success looks like:

> A user lands on `/actuators`, understands within 5 seconds that
> "Linear Motion" is the supercategory and there are two subtypes,
> can flip between them, can apply filters, can pick a configurable
> record and synthesise a part number.

### Route + entry point

- New route: `/actuators` → `<ActuatorPage/>` (lazy-loaded, same
  pattern as `Datasheets` / `Management`).
- Reachable from the header nav as a new top-level link, **not** by
  changing the existing `/` selection flow. Same page, different
  framing.

### Layout (text mockup)

```
┌──────────────────────────────────────────────────────────────────┐
│ Linear Motion                                                    │
│ Devices that translate a payload along a single axis.            │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────┐                      │
│  │ Linear Actuator  │  │ Electric Cylinder│  ← subtype tabs      │
│  │ rodless / stage  │  │ rod-style        │                      │
│  │ N records        │  │ N records        │                      │
│  └──────────────────┘  └──────────────────┘                      │
├──────────────────────────────────────────────────────────────────┤
│ [Filter chips: stroke, payload, drive, motor type]               │
├──────────────────────────────────────────────────────────────────┤
│ <ProductList scoped to selected subtype>                         │
│                                                                  │
│ Click a row →                                                    │
├──────────────────────────────────────────────────────────────────┤
│ Configurator drawer (only when record has configurator!=None):   │
│  [travel slider] [lead select] [mount select]                    │
│  Synthesised:  200-LBM-072-08-S-XX-A1   [copy]                   │
│  ⚠ This part number is generated from the selection above. Verify│
│  with the vendor before ordering.                                │
└──────────────────────────────────────────────────────────────────┘
```

### Component reuse

- `ProductList` already handles filtering + table rendering. The
  Actuator page is a thin wrapper that pre-selects the subtype + adds
  the supercategory chrome on top.
- Filter chips auto-derive from records (`deriveAttributesFromRecords`
  in `filters.ts`) — no per-type list needed.
- `Dropdown` / `MultiSelectFilterPopover` from existing UI primitives
  for configurator segments.

### What ships

- `/actuators` route with subtype tabs.
- Configurator drawer (collapsed by default; expands when a record
  with `configurator != None` is selected).
- Two hand-authored configurator templates: **one Lintech 200-series
  family**, **one Parker HD family** — enough to demo both vendors
  honestly. Either family can be a stub if the schema fit-check (Part
  4 below) shows the records won't ingest cleanly.
- Sidebar dropdown grouped by supercategory (cheap lift; fixes the
  flat-list confusion same PR).

### What does not ship

- Per-supercategory custom layouts beyond `/actuators`. Every other
  supercategory still routes through `/?type=`.
- Configurator backend persistence. The synthesised SKU is
  client-side ephemeral.
- "Find similar" / "explain this part number" — both are reverse-
  configurator features that need real data first.

---

## Part 4 — Schema fit-check (shipped 2026-05-08)

The `linear_actuator` Pydantic model was originally schemagen'd against
**Tolomatic, Rexroth, SMC, THK** sources. Lintech, Toyo, and Parker
were never in the source set. Before the Actuator MVP claimed to
support those vendors, this fit-check ran each through `page_finder`
+ a single Gemini call against the existing schema and reported field
coverage.

**Result:** schema fits cleanly. **Zero extras** — Gemini did not emit
any field the schema lacks. No new Pydantic fields needed.

**Coverage** (per `linear_actuator.md`): 14-18 fields populate per
fixture. 100% on identity (manufacturer, series, type, motor_type,
part_number); 67% on stroke / actuation_mechanism / screw_diameter;
33% on lead pitch / max speed / max push force / weight / operating
temp. The 0% block (bearing-load ratings, pitching/yawing moments,
electrical specs) is **vendor-mix-driven**, not schema-broken —
Lintech sells motorless mechanical units and Toyo sells compact
integrated drives, neither of which publishes the heavy-industrial
spec block that Tolomatic/Rexroth/SMC/THK do.

**Parker blocked.** Both URLs the user provided returned 403 through
Akamai (verified twice: original schemagen pass and the 2026-05-08
fit-check). The Parker HD configurator template ships from the
public ordering page only — no DB-backed validation.

**Real-world part-number formats observed** (used to rewrite the
configurator templates in this doc's Part 2):

- Lintech 200: `200<frame><travel>-WC<accessory>` (e.g. `200607-WC0`).
  The original hand-authored `200-{drive}-{travel}-{lead}-{mount}`
  template was a guess and didn't match real Lintech part numbers;
  replaced.
- Toyo Y-series: `<series>-<subtype>` (e.g. `Y43-L2`).
- Tolomatic TRS / BCS / ERD: `<family><frame>-BNM<lead>[-<travel>]`
  — the format that actually populates dev DB (53 records across
  the three families).

---

## Triggers — when to surface this doc

| Trigger | Surface |
|---|---|
| New product type added that conceptually belongs in an existing supercategory (e.g. voice-coil, linear motor) | This doc — extend `SUPERCATEGORIES` |
| A new vendor catalog whose SKUs are configurator-encoded (lots of trailing modifiers, "ordering information" page) | This doc — author a `ConfiguratorTemplate` |
| `app/frontend/src/types/categories.ts` (when it lands), `Supercategory` literal, sidebar dropdown grouping | This doc |
| User asks "how do I add a category", "how do I add a subcategory", "supercategory" | This doc |

---

## Open questions

- **Should the supercategory landing copy mention "discontinued" or
  "legacy" actuators differently?** Probably yes long-term, no in
  MVP. Lintech still ships their 2020 catalog; Parker is current.
  Annotate the configurator footer with last-seen-revision date once
  any vendor's encoding visibly drifts.
- **Configurator validation against real DB rows.** Once any
  Lintech/Parker records are in DB, the configurator's choice of
  template should match the record's `series` field. MVP relies on
  the user clicking a row first, which makes this a non-issue
  for now.
- **Should `configurator` go in `common.py` or a new
  `models/configurator.py`?** When/if it migrates from frontend
  fixture to Pydantic field, new file — it's a self-contained
  concept, not a unit family. Keeps `common.py` from sprawling.
