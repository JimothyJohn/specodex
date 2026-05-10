# BUILD — requirements-first system assembler

The third page in Specodex's user-facing hierarchy, and the one the
rest of the application is meant to point at. Today the catalog has
two surfaces: **Selection** (`/`, the filterable component browser)
and **Actuators** (`/actuators`, a Linear-Motion supercategory landing
with a procedural part-number configurator). Build replaces and
generalises the Actuators page into the **first page a non-expert
should see** — the one that takes engineering requirements
(motion class, stroke, speed, payload, orientation) and assembles a
candidate motion system from the catalog.

The end-state hierarchy:

- **Wizard** (future, top of funnel) — natural-language problem
  description; an LLM derives requirements and hands off a *draft*
  Build state.
- **Build** (this doc, the core) — explicit requirements form; user
  reviews / edits; output is a list of vendor part numbers per system
  slot (actuator + motor + drive ± gearhead).
- **Selection** (existing, expert mode) — raw catalog browse, used
  when Build's recommendations need to be cross-checked, compared,
  or extended.

This doc covers Build only. Wizard appears as forward context where
the seam between them needs to be locked early (the requirement
schema in Part 2 must be JSON-serialisable so Wizard can hand it
off as URL state or a saved draft).

## Status (post-2026-05-10 sprint)

**Design-only.** Phase 1 implementation **unblocked** — both hard
prereqs shipped during the 2026-05-09 / 10 sprints. Promoted to the
top of the next-sprint user-facing-work queue in `todo/README.md`.

Hard prerequisites — both ✅ shipped:

- ~~**SCHEMA Phase 3** (`specodex/relations.py` + `/api/v1/relations/*`
  + `RelationsPanel` skeleton)~~ ✅ shipped via PRs #89/#90/#92.
- ~~The bug investigation in Part 5 (linear_actuator surfacing on
  Selection)~~ ✅ shipped via PR #86 (linear-actuator type
  discoverability fix).

Soft predecessors (already shipped):

- CATAGORIES Phase 0+1 (supercategory map + `/actuators` MVP) —
  the source the Build page absorbs.
- SCHEMA Phase 1 (additive cross-product fields:
  `motor_mount_pattern`, `compatible_motor_mounts`,
  `input_motor_mount`, `output_motor_mount`).

Phasing (full PR breakdown in Part 7):

- **Phase 1** — foundational refactor + Linear-Motion / horizontal
  Build with relations API consumption (4 PRs).
- **Phase 2** — vertical orientation + gravity vector + friction
  estimate + Save-to-Project + Build → Selection deep-link (3 PRs).
- **Phase 3** — Wizard handoff scaffolding (2 PRs).
- **Phase 4+** — out of MVP arc (rotary path, multi-axis,
  environmental fields, fieldbus, centre-of-gravity, MSRP totals,
  multi-segment motion profiles). Each item triggered by an explicit
  user request or downstream readiness; see Part 7 for the trigger
  table.

---

## Part 1 — The three-page model

Specodex's user-facing surface is three pages, each at a different
**altitude of abstraction**. They are not parallel; they form a
funnel from "I have a vague problem in my head" down to "I have
the exact part numbers I need to order."

```
            altitude (vague → specific)
            ────────────────────────────
            ↑
            │   Wizard       "I need to lift a heavy thing
            │   /wizard       quickly."  → LLM derives requirements
            │                 → hands off to Build with a draft
            │
            │   Build        "5 kg vertical, 200 mm in 200 ms,
            │   /build        2 s dwell."  → relations API returns
            │                 candidates per slot → user picks →
            │                 Copy BOM
            │
            │   Selection    "Show me every Yaskawa servo motor
            │   /             with rated torque ≥ 2 N·m, sorted
            │                 by inertia."  → raw catalogue browse
            ↓
            (most concrete — power-user / library mode)
```

### What each page is for

**Wizard (`/wizard`, future)** — the natural-language front door.
The user types or speaks a description of their motion problem;
an LLM extracts a `BuildRequirements` blob (Part 2's schema) and
hands off to Build with the requirements pre-populated. Wizard
covers the gap between "I know I need to move stuff" and "I know
my stroke + speed + payload." It is the right entry for users who
don't already think in motion-system terms.

**Build (`/build`, this doc)** — the requirements-first system
assembler. The user describes their motion application as
engineering inputs (motion class, orientation, stroke, move
time, dwell, payload mass), and Build narrows the catalogue per
slot (actuator → motor → drive → optional gearhead) using the
relations API. Output is a copy-able BOM of vendor part numbers.
Build is the right entry for users who *do* think in motion-system
terms but don't want to manually filter four product types and
cross-check compatibility by hand.

**Selection (`/`, existing)** — the raw catalogue browser. Filter
chips, sort, column resize, per-column unit toggles. No motion
system context, no compatibility narrowing, no slot-fill
sequence. Selection is the right entry for users who already
know which product type they want, who want to compare across
vendors regardless of any specific Build, who want to filter by
attributes Build's form deliberately doesn't surface (release
year, certification, manufacturer), or who want to browse
product types Build doesn't model (contactors, robot arms,
datasheets).

### Where users land

After Build ships, **Build is the default entry point.** Welcome's
"Make Your Selection" CTA rewrites to "Build a motion system" →
`/build`. The header nav reorders to put Build first. The catalog
itself still loads at `/` for direct-navigation users who already
have Selection bookmarked.

The funnel is intentionally **bidirectional at every level**:

- A user on Wizard can opt out at any point ("just take me to
  Build" → opens `/build` empty for manual entry).
- A user on Build can drill down to Selection ("View this motor
  in the catalogue" → opens `/?type=motor&filter=<f>` per Phase
  2's deep-link, with all of Selection's filtering primitives
  available).
- A user on Selection can promote up to Build ("Use this motor
  in a Build" → opens `/build` with the motor pre-selected for
  its slot, requirements form blank for back-derivation).

The promote-from-Selection direction (Selection → Build) is in
scope for Phase 2; the drill-from-Build direction (Build →
Selection) is also Phase 2. Phase 1 ships the funnel one-way:
Wizard → Build → Copy BOM, with Selection accessible from the
nav but not deeply integrated.

### How they relate operationally

| Concern | Wizard | Build | Selection |
|---|---|---|---|
| Input shape | Natural language | `BuildRequirements` (form / URL params) | Filter chip set |
| Output shape | Build URL with `?wizard=1` | Copy-able BOM (slot picks) | None — read-only browse |
| Relations API usage | None — produces requirements only | Heavy — every slot's narrowing | None |
| Compat checks | None | `compat.ts` per junction | None (banner removed Phase 1) |
| Cross-supercategory | No (single application) | No (single application) | Yes — comparison surface |
| State persistence | Wizard session history (Wizard's problem) | URL state + localStorage + Project save (Phase 2) | localStorage column prefs only |
| Auth required | TBD by Wizard's spec | No (read-only API consumption) | No |

The same `BuildRequirements` JSON shape is the **shared currency**:
Wizard produces it, Build accepts it, Project save persists it.
Selection doesn't speak this currency — it operates one level
below, on individual product records. This asymmetry is by
design: Build is where motion-system thinking lives; Selection
is where catalogue thinking lives; Wizard is where natural-
language thinking lives.

### Why Build is the essence

Per Nick's framing — "this is the essence of this application."
Every distributor has a filterable catalogue; Specodex's
differentiator is that the catalogue is **wired to compatibility
relations**, so describing a motion application returns a viable
system. The rest of this doc treats Build as the noun; Wizard and
Selection are the supporting cast.

---

## Part 2 — Build's requirement vocabulary

The single load-bearing schema for the entire Build experience. Form
inputs, URL state, localStorage saves, and the Wizard handoff target
all bind to the same shape. Lock this first; everything downstream
(UI, relations API integration, Wizard contract, phasing) references
it concretely.

### Top-level shape

```ts
// app/frontend/src/types/buildRequirements.ts
//
// Single source of truth for Build's input form, URL serialiser,
// localStorage save, and Wizard handoff target. Validated by Zod at
// the form boundary; never sent to the relations API directly — the
// derivation engine (below) translates this into the numeric
// constraints the API consumes.

export type MotionClass = "linear" | "rotary";
export type LinearOrientation = "horizontal" | "vertical";
export type UnitsPreference = "metric" | "imperial";

/**
 * One discrete move + its trailing dwell. Day 1 ships with exactly
 * one entry; the schema is a list from the start so the future
 * "stack profiles to compute cumulative duty cycle" feature is
 * additive rather than a re-shape.
 */
export interface MotionProfile {
  /** Distance the carriage must travel in this move. */
  stroke: ValueUnit;          // canonical unit: mm
  /** How long the move takes (start of motion → end of motion). */
  move_time: ValueUnit;       // canonical unit: s
  /** Idle time between this move and the next. */
  dwell_time: ValueUnit;      // canonical unit: s
}

export interface BuildRequirements {
  motion_class: MotionClass | null;       // null = user hasn't picked yet
  /** Linear-only. null when motion_class !== "linear". */
  orientation: LinearOrientation | null;
  /** Strongly suggested for linear (not enforced). Form labels it
   *  required; validator allows null. 0 emits the warning below. */
  payload_mass: ValueUnit | null;         // canonical unit: kg
  motion_profiles: MotionProfile[];       // Day 1: length 1 enforced
  /** Display-only; persisted in localStorage. Default "metric". */
  units_preference: UnitsPreference;
}
```

`ValueUnit` is the existing `specodex/models/common.py` shape
(re-exported in `generated.ts`): `{ value: number, unit: string }`.
Unit conversions go through `app/frontend/src/utils/unitConversion.ts`,
the same path the catalog uses. Imperial display ⟶ metric storage
under the hood; the `units_preference` toggle never mutates the
canonical numbers.

### Field-by-field

| Field | Required for Day 1 | Default | Notes |
|---|---|---|---|
| `motion_class` | yes | `null` | Day 1 only `"linear"` is interactive; selecting `"rotary"` shows a placeholder ("rotary requirements not yet wired — pick a motor + gearhead via Selection"). |
| `orientation` | yes (when linear) | `null` | Drives whether gravity enters the force calculation. Side-mount deferred until centre-of-gravity work lands. |
| `payload_mass` | **strongly suggested** | `null` | Form labels it required (asterisk, copy below); the validator does NOT block submission on blank. Blank = "no force filter applied" banner; 0 = the warning copy below. The strong-suggestion framing pushes users to enter a real value without trapping the ones who genuinely don't know. |
| `motion_profiles[0].stroke` | yes | `null` | The distance the carriage must travel. If you have a range, enter the maximum. |
| `motion_profiles[0].move_time` | yes | `null` | Total time for one move (start → end). |
| `motion_profiles[0].dwell_time` | yes | `null` | Idle time between this move and the next. Drives duty-cycle derivation. |
| `units_preference` | no | `"metric"` | Display-only. Persisted in localStorage so it survives reloads. |

Every field except `units_preference` starts `null`. **Blank = no
constraint applied** (per the progressive-narrowing rule below). The
form does not block submission on partial input; it just narrows the
candidate set proportionally.

### The derivation engine

The user inputs are physically intuitive ("100 mm in 200 ms") rather
than the numeric constraints the relations API needs ("peak force ≥
N", "peak velocity ≥ mm/s"). A small derivation engine in
`app/frontend/src/utils/buildDerivation.ts` translates between them.

**Assumed motion profile: S-curve 1/3 trapezoidal.** One-third of
`move_time` is acceleration (0 → peak velocity), one-third is cruise
at peak velocity, one-third is deceleration (peak → 0). The S-curve
smooths the corners between phases — at this fidelity it doesn't
change the peak numbers, just the jerk. Day 1 ignores jerk; Phase 2+
revisits if motor sizing surfaces it as a gap.

Derived quantities (computed live as the form is filled):

```
avg_velocity     = stroke / move_time
peak_velocity    = 1.5 × avg_velocity
peak_acceleration = peak_velocity / (move_time / 3)
                 = 4.5 × stroke / move_time²

g_factor         = 9.81 m/s²   if orientation === "vertical"
                 = 0           if orientation === "horizontal"
                 (friction estimate folded in later)

peak_force       = payload_mass × (peak_acceleration + g_factor)
duty_cycle       = move_time / (move_time + dwell_time)
```

**Vertical sizing rule.** Per Nick's "the actuator will always have
to move in both directions" — `peak_force` sizes for the lifting
half of the bidirectional cycle (gravity adds to required thrust).
The lowering half (gravity subtracts) is the motor's regen problem,
not the actuator's force-rating problem; it surfaces in Phase 2+ when
drive selection cares about regen capability.

**Future-extension hook.** When `motion_profiles` grows past length
1, `duty_cycle` becomes a sum-over-profiles weighted by RMS torque
contribution, not a single ratio. The shape of the schema (list
from day one) supports this; the math swaps to a more honest formula
when the second profile lands.

The derived block surfaces in the UI as a **"What you're asking
for"** panel beside the form — peak velocity, peak acceleration,
peak force, duty cycle — so the user sees the implication of their
choices before they read which actuators meet them. (See Part 3 for
layout.)

### What the relations API consumes

Build sends **derived numbers**, not raw `BuildRequirements`. The
relations API doesn't need to know about S-curve profiles or
imperial display toggles — clean separation between requirement
authoring (frontend) and candidate matching (backend).

The request shape against `/api/v1/relations/actuators` (see
`todo/SCHEMA.md` Phase 3):

```ts
interface ActuatorQuery {
  min_stroke_mm: number;
  min_peak_force_n: number;
  min_peak_velocity_mm_s: number;
  min_duty_cycle: number;        // 0..1
  orientation?: "horizontal" | "vertical";  // hint for derating
}
```

This keeps the relations API stable as the requirement schema
evolves: add `repeatability` to `BuildRequirements` later, and the
API only needs to accept a new optional `max_repeatability_um`
parameter. The two schemas are coupled only at the derivation
function in the frontend.

### Narrowing semantics

**Progressive narrowing.** Build always renders *all* candidate
actuators with a count badge, narrowing as fields fill in. Empty
form on first load → "147 actuators in catalogue." Pick `vertical`
→ no change (orientation is a hint, not a hard filter — actuators
work either way). Enter `5 kg` mass → "147 actuators (no force
filter — set move time to compute peak force)." Enter `200 mm`
stroke → "84 actuators with stroke ≥ 200 mm." Enter `0.2 s` move
time → "12 actuators with peak force ≥ 175 N at this duty cycle."
The user always sees the candidate count, so they can tell which
input just narrowed and by how much.

**Blank = no constraint applied.** Every field is optional in the
sense of "unfilled fields don't filter." The mandatory marker on
`payload_mass` means the *form* asks for it (with copy explaining
why), but the user can still leave it blank and Build will surface
candidates without applying a force filter — they'll just see "no
force filter applied" in the active-constraints summary.

**The "extremely clear available products" surface.** Each input
field gets a live distribution panel beside it — a horizontal
histogram showing where the user's input lands in the catalogue.
Stroke slider shows "X actuators support this stroke or longer";
move time shows the speed-cluster the input falls into. This is the
mechanism by which a user notices their requirement is pushing
toward rare/expensive territory before they hit "no candidates
match." Same primitive the column-header sliders use in
`ProductList`; reuses `ColumnHeader.tsx`'s sparkline logic.

**Standardization hints.** When the user's input falls between two
common catalogue clusters (e.g. asking for 175 mm stroke when 150
mm and 200 mm are the popular sizes), surface a soft suggestion:
*"200 mm is a more standard size — 18 actuators vs 3 at 175 mm.
Designing to a standard size lowers cost and lead time."* Optional
toggle ("standard sizes only") for engineers who already know they
need exact-fit. Drives Nick's "design your system to fit common
products" framing — economy of scale comes from picking from the
fat part of the distribution, not the long tail.

### Schema location & serialisation

**Source file:** `app/frontend/src/types/buildRequirements.ts`.
TypeScript interface + Zod (or equivalent — match the existing
project pattern in `app/backend/src/schemas/`) validator. Single
source of truth.

**URL serialisation.** Build's full state encodes into the URL so
configurations are bookmarkable and shareable:

```
/build
  ?ml=linear           # motion_class
  &or=vertical         # orientation
  &pm=5kg              # payload_mass — value+unit
  &st=200mm            # stroke
  &mt=0.2s             # move_time
  &dw=2s               # dwell_time
  &up=metric           # units_preference (display only)
```

Compact-but-legible. ValueUnit fields encode as `<number><unit>`
with no separator (`5kg`, `200mm`, `0.2s`). Wizard's handoff is then
just a redirect to a `/build?...` URL with the LLM's derived params
pre-populated; no separate handoff API needed.

**localStorage save.** Same JSON shape, keyed under `buildRequirements`.
On Build mount, hydrate from URL first, fall back to localStorage,
fall back to the empty-form defaults. URL wins so a shared link
overrides a stale local draft.

**No Pydantic mirror on Day 1.** Build's requirements stay frontend-
only. The relations API accepts the *derived* numeric constraints
(force, velocity, stroke, duty cycle), not the raw input shape, so
there's nothing for the backend to validate against. When Phase 3
"save Build state to a Project" lands, mirror the shape into a
Pydantic model in `specodex/models/build_requirements.py`; not
before. Premature server-side modelling drags every requirement-
schema iteration through `gen-types` regen + DB-write paths the
feature doesn't actually need.

### Forward-compat hooks

Documented now so future-you doesn't have to re-derive them when the
trigger arrives:

| Field | Triggers | Fits where |
|---|---|---|
| `motion_profiles[1+]` | "stack multiple moves to compute cumulative duty cycle" | Already a list — extend with a "+ Add another move" button. |
| `repeatability: ValueUnit` | first user asks "I need ±10 µm positioning" | Add to `BuildRequirements`; relations API gains `max_repeatability_um?`. |
| `temperature_range: MinMaxUnit` | first cold-storage / outdoor application | Same shape pattern; environmental block. |
| `ip_rating: string` | first wash-down / dusty environment | Same. |
| `cleanroom_class: string` | first semicon / pharma application | Same. |
| `fieldbus: CommunicationProtocol[]` | confirmed Phase ≥ 3; "way later" per Nick | Lives on the Drive selection step, not the requirement form. |
| `encoder_feedback_support: string[]` | when servo selection gets opinionated | Same — Drive step, not requirements. |
| `centre_of_gravity: { x, y, z }` | when bearing-moment checks land | New `LinearOrientation` value `"side_mount"` becomes meaningful at this point. |
| `friction_coefficient: number` | when horizontal-motor sizing drifts low | Currently `0` for horizontal; surface as advanced field. |

### Adding a new requirement field — propagation checklist

When a new field lands (per Part 2's forward-compat hooks table —
`repeatability`, `temperature_range`, etc.), the following touch
list keeps the schema, form, URL, derivation, API, and copy in
sync. Walk the list top-to-bottom; CI catches the codegen step,
the rest is on the implementer.

1. **Schema.** Add the field to `BuildRequirements` in
   `app/frontend/src/types/buildRequirements.ts`. Always optional
   (per Part 6's "new fields are always optional" rule).
2. **URL serialiser.** Add the field's encoding to
   `app/frontend/src/utils/buildURLState.ts` — both the
   `serialiseBuildRequirementsToURL` direction and the
   `parseURLToBuildRequirements` inverse. Pick a short param key
   (2–3 chars, matching the existing pattern: `pm` / `st` / `mt`).
3. **Form control.** Add the input to `<RequirementsForm>` with
   the appropriate primitive (number input, range slider, enum
   dropdown). Bind to `requirements[<field>]` and call
   `onRequirementsChange`.
4. **Derivation.** If the new field affects derived numbers
   (peak_force, peak_velocity, duty_cycle), update
   `app/frontend/src/utils/buildDerivation.ts`. If not, skip.
5. **Relations API param.** Add the corresponding optional query
   param to the relations endpoint(s) in `app/backend/src/routes/relations.ts`
   (and `specodex/relations.py` once PYTHON_BACKEND lands).
6. **Wire the param to the API call.** In `<BuildPage>`'s
   relations call site, pass the new query param when the field
   is non-null. The relations API treats absent params as "no
   constraint applied" (same rule as the schema's optional-by-
   default).
7. **Tooltip + warning copy.** Add a row to Part 2's "Field-level
   copy" table. Implement the tooltip via `<Tooltip>` and the
   warning (if any) inline beneath the form control.
8. **Unit tests.** Add fixtures to `RequirementsForm.test.tsx`
   covering the new control's render + callback. Add a relations
   API test verifying the new param is honoured.
9. **Phase exit criteria update.** If the field unblocks a Phase
   2+ feature (e.g. `temperature_range` enables an environmental
   filter), update Part 7's phase exit criteria to reference it.

The codegen pipeline (`./Quickstart gen-types`) catches drift
between the Pydantic and TypeScript shapes when SCHEMA work
sits behind the field — but Build's `BuildRequirements` is
frontend-only today (per Part 2's "No Pydantic mirror"), so the
drift gate doesn't apply yet. When Save-to-Project mirrors the
shape server-side, this checklist gains a step 2.5: "Update the
Pydantic mirror in `specodex/models/build_requirements.py`."

### Field-level copy

The spec pins tooltips and warning copy verbatim so they don't drift
into vague generic strings during implementation. Per the
no-native-chrome rule (`CLAUDE.md` "Frontend UI conventions"), all
of these render through `Tooltip.tsx` / `<FormField>` (when STYLE
Phase 4 ships), never `title=`.

| Field | Tooltip text | Inline help / warning |
|---|---|---|
| `motion_class` | "Is the load translating along an axis (linear) or rotating (rotary)? Most actuator selections start with linear." | If `rotary`: "Rotary requirements not yet wired. Pick a motor + gearhead from Selection; this page will support rotary tables in a later release." |
| `orientation` | "Horizontal: gravity does no work. Vertical: gravity adds to the lifting force the actuator must provide." | — |
| `payload_mass` | "The load the actuator carries. For vertical applications this is the dominant force the actuator must lift." | If value === 0: "0 kg means no payload — confirm you actually have no load. Most useful Build results need a real mass." |
| `stroke` | "The distance the actuator's carriage must travel in one move. If you have a range, enter the maximum." | — |
| `move_time` | "How long one full stroke should take, end-to-end. Build assumes an S-curve trapezoidal motion profile (1/3 accelerating, 1/3 cruising, 1/3 decelerating)." | — |
| `dwell_time` | "How long the actuator sits idle between moves. Drives motor thermal sizing — the longer the dwell, the cooler the motor." | — |
| `units_preference` | "Display preference. Stored values are always metric; this only flips what you see and type." | — |

---

## Part 3 — The Build page surface

The page absorbs three pieces of existing UI: the **requirements
form** (greenfield), the **per-slot candidate lists** (replaces the
ActuatorPage records table), and the **system summary / BOM
output** (replaces the sticky `BuildTray.tsx`). Layout enforces the
left-to-right reading order Nick framed: *describe what you need →
see candidates → commit a system*.

### Goal

> Land on `/build`, fill three or four fields, see the catalogue
> narrow to a handful of candidates per slot, click to commit each
> slot, copy a four-line BOM. Total time-to-quote-request: under
> two minutes for a routine motion application.

### Route + entry point

- New route: `/build` → `<BuildPage/>` (lazy-loaded, same pattern as
  `Datasheets` / `Management` / the existing `ActuatorPage`).
- Becomes the **default landing for the nav's primary CTA.** The
  Welcome page's "Make Your Selection" link rewrites to "Build a
  motion system" → `/build`. The existing "Selection" nav item
  stays in place but loses CTA prominence (see Part 5).
- `/actuators` redirects to `/build?ml=linear&or=horizontal` once
  Build ships (preserving the existing route as a deep link to a
  pre-narrowed Linear-Motion Build, not as its own page).

### Layout

Two-pane primary surface with a sticky system summary at the bottom:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Build a motion system                          [Reset]  [Save]  [⋯]   │
├──────────────────────────┬─────────────────────────────────────────────┤
│  Requirements            │  Candidates                                 │
│  ────────────            │  ──────────                                 │
│                          │                                             │
│  Motion class            │  ▼ Actuator        47 → 12 candidates       │
│   ◉ Linear               │     ┌────────────────────────────────────┐  │
│   ○ Rotary               │     │ Tolomatic ERD15-BNM10-203          │  │
│                          │     │ stroke 203mm · thrust 220N · ...   │  │
│  Orientation             │     │ [Pick]  [Details]  [Configurator] │  │
│   ◉ Horizontal           │     ├────────────────────────────────────┤  │
│   ○ Vertical             │     │ Tolomatic BCS15-BNL05              │  │
│                          │     │ stroke 152mm (under 200mm) ⚠       │  │
│  Stroke      [200] mm    │     │ ...                                │  │
│  Move time   [0.2] s     │     └────────────────────────────────────┘  │
│  Dwell       [2  ] s     │                                             │
│  Mass        [5  ] kg    │  ▼ Motor                — locked            │
│                          │     Pick an actuator first to surface       │
│  ━━ What you're asking ━ │     compatible motors.                      │
│  Peak velocity 1500 mm/s │                                             │
│  Peak force    297 N     │  ▼ Drive                — locked            │
│  Duty cycle    9 %       │     ...                                     │
│                          │                                             │
│                          │  ▼ Gearhead (optional)  — locked            │
│                          │     ...                                     │
│                          │                                             │
├──────────────────────────┴─────────────────────────────────────────────┤
│  System: Actuator ✓ · Motor — · Drive — · Gearhead —     [Copy BOM] ✗ │
└────────────────────────────────────────────────────────────────────────┘
```

Three regions:

1. **Left pane — `<RequirementsForm>`.** The schema from Part 2
   bound to form controls. Sticky on scroll so the user can fill
   fields while watching the candidates pane react. Includes the
   "What you're asking for" derivation panel (peak velocity, peak
   force, duty cycle) computed live by `buildDerivation.ts`.
2. **Right pane — `<CandidatesPane>`.** Per-slot accordion: Actuator,
   Motor, Drive, Gearhead. Each section shows the candidate count
   *before vs after* the requirement filters apply ("47 → 12"), the
   list of candidates with their key specs, and per-row actions
   (Pick / Details / Configurator). Locked sections show why they're
   locked (waiting for upstream slot pick) instead of an empty list.
3. **Bottom strip — `<SystemSummary>`.** The absorbed BuildTray.
   Always visible; shows slot fill state + a Copy BOM button that
   activates when at least one slot is filled (matches today's
   BuildTray behaviour). Goes green-bordered when all required slots
   are filled and every junction passes `compat.check()`.

### Slot-fill sequence

Enforced left-to-right slot dependency — Build's narrowing relies
on each upstream pick to constrain the next slot's candidate set:

1. **Actuator** unlocks first (no upstream dep). Filters: stroke ≥
   `requirements.stroke`, peak force rating ≥ derived `peak_force`,
   peak velocity rating ≥ derived `peak_velocity`, orientation hint.
2. **Motor** unlocks once Actuator is picked. Filters:
   `motor_mount_pattern ∈ actuator.compatible_motor_mounts`, rated
   torque ≥ (derived peak torque / gear ratio guess), thermal
   sizing for derived `duty_cycle`.
3. **Drive** unlocks once Motor is picked. Filters: voltage envelope
   covers Motor's rated voltage, current envelope covers Motor's
   rated current, encoder protocol intersection non-empty.
4. **Gearhead** is optional. Surfaces ratio suggestions when the
   Motor's rated torque alone doesn't clear the actuator's required
   peak — same `snapGearUp()` heuristic the existing Selection page
   uses for `productType === 'motor'` linear-mode.

Phase 1 **reorders** `compat.ts`'s `BUILD_SLOTS` constant from
`['drive', 'motor', 'gearhead']` to
`['actuator', 'motor', 'drive', 'gearhead']` — both prepending
`'actuator'` AND moving `'motor'` before `'drive'` to match the
user-facing fill order. Adjacency rules in `compat.ts` are
name-keyed, not index-keyed, so the existing `drive↔motor` and
`motor↔gearhead` rules keep working unchanged. Phase 1 adds one
new rule: `actuator↔motor` (validates `motor.motor_mount_pattern
∈ actuator.compatible_motor_mounts`). The array reorder DOES
affect `ChainReviewModal.tsx`'s `adjacentFilledPairs` helper,
which iterates the array and yields adjacent pairs — see Part 7's
migration risks.

### The "extremely clear available products" surface

The Part 2 standardisation rule (input-side) has a results-side
twin in this pane:

- Each candidate row shows a **distribution position badge** —
  e.g. "8th most common stroke in catalogue" or "rare — only 3
  records at this spec." Reuses the histogram primitive from
  `ColumnHeader.tsx`.
- **Off-cluster warning** when a candidate is the only match
  because the user's requirement hit a gap: *"Only 3 actuators
  match exactly. Relaxing stroke to 250mm (next standard size)
  surfaces 18 candidates."* Click-to-relax button writes the
  suggested value back into the form.
- **Standard sizes only** toggle in the form's footer — when on,
  hides off-cluster candidates entirely so the user sees only the
  fat part of the distribution.

This is the mechanism by which Build steers users toward
high-availability parts without forcing them — the rare-cluster
candidates stay one click away.

### The configurator's role within Build

The existing `app/frontend/src/types/configuratorTemplates.ts`
templates (Tolomatic TRS / BCS / ERD, Lintech 200, Toyo Y, Parker
HD) **do not move to the requirements form**. They surface
**inside the Actuator slot's row actions** as a "[Configurator]"
button per matching candidate row.

Click flow:

1. User picks Actuator slot → row action "Configurator" appears
   on rows whose `(manufacturer, series)` resolves to a registered
   template.
2. Click → opens `<ConfiguratorDrawer>` (extracted from
   `ActuatorPage.tsx`'s `<ConfiguratorPanel>` function and the
   `<MotorSuggestions>` / `<DerivedSpecsRow>` / `<SegmentControl>`
   helpers it composes) inline beneath the candidate row. The drawer renders the vendor's segments (lead, mount,
   accessory bundle) with the user's `requirements.stroke`
   pre-populated into the stroke segment.
3. User adjusts vendor-specific segments → synthesised part number
   updates live → "Use this part number" button commits the
   synthesised SKU back into the slot pick.

Per Nick's "every template will have a way to adjust stroke/length
/travel" — the **stroke segment is the only segment Build's
requirements form pre-populates**. Other segments stay vendor-
specific and the drawer is the right surface for them.

### BuildTray absorption — what changes in `App.tsx`

Today: `<BuildTray />` is rendered unconditionally inside
`<AppShell>` in `App.tsx` (the `{!isLanding && <BuildTray />}`
expression after the `</ErrorBoundary>` closing tag), sticky at
the bottom of every non-`/welcome` page. Users add products to it from the
`<ProductDetailModal>`'s "Add to build" button.

After Build:

- `<BuildTray />` is **deleted as a separate component.** The
  3-slot bottom-strip UX moves into `<SystemSummary>` (the bottom
  region of the Build page only). It no longer follows the user
  across pages.
- The `<ProductDetailModal>`'s "Add to build" button is **removed
  from non-Build contexts** (Selection, Actuators, Projects). On
  the Build page only, the button persists with renamed copy
  ("Pick for [slot]") to disambiguate from the slot-fill flow.
- The `build` slice in `AppContext` (drive/motor/gearhead picks)
  **stays** — Build reads/writes the same state, plus a new
  `actuator` slot. Adding `actuator` to `BUILD_SLOTS` in
  `compat.ts` is the only schema change required.
- `compat.check()` gains an `actuator ↔ motor` adjacency rule
  (validates `motor_mount_pattern ∈ actuator.compatible_motor_mounts`).
  Today's `drive ↔ motor` and `motor ↔ gearhead` rules are
  unchanged.

### Component contracts

The three top-level components have well-defined prop boundaries
so each can be unit-tested in isolation. `<BuildPage>` is the
state-owner; the children are pure-ish renderers with callbacks.

```ts
// app/frontend/src/components/build/RequirementsForm.tsx
interface RequirementsFormProps {
  requirements: BuildRequirements;
  onRequirementsChange: (next: BuildRequirements) => void;
  /** Derived block from buildDerivation.ts. RequirementsForm
   *  renders it but doesn't compute it — that's BuildPage's job. */
  derived: DerivedSpecs | null;
}

interface DerivedSpecs {
  peak_velocity_mm_s: number;
  peak_acceleration_mm_s2: number;
  peak_force_n: number;
  duty_cycle: number;        // 0..1
}

// app/frontend/src/components/build/CandidatesPane.tsx
interface CandidatesPaneProps {
  /** Each slot's candidate list, fetched by BuildPage from the
   *  relations API. null = locked (upstream slot not picked).
   *  [] = no candidates match. */
  actuatorCandidates: ActuatorCandidate[] | null;
  motorCandidates: MotorCandidate[] | null;
  driveCandidates: DriveCandidate[] | null;
  gearheadCandidates: GearheadCandidate[] | null;
  /** The current picks. null = slot empty. */
  picks: Partial<Record<BuildSlot, Product>>;
  /** Which slot is currently loading (shows the row skeleton). */
  loadingSlot: BuildSlot | null;
  /** Per-slot error from the relations API (null = no error). */
  errors: Partial<Record<BuildSlot, string>>;
  /** Standardisation / off-cluster suggestions surfaced by the
   *  relations API's _distribution_position blocks. */
  warnings: Partial<Record<BuildSlot, string>>;
  /** Wizard-draft markers — slots whose pick was auto-selected
   *  from the ?wizard=1 handoff and not yet confirmed. */
  draftSlots: Set<BuildSlot>;
  onPick: (slot: BuildSlot, product: Product) => void;
  onUnpick: (slot: BuildSlot) => void;
  /** Click-to-relax handler: writes a suggested value back into
   *  the requirements form via BuildPage. */
  onRelaxRequirement: (field: keyof BuildRequirements, value: ValueUnit) => void;
}

// app/frontend/src/components/build/SystemSummary.tsx
interface SystemSummaryProps {
  picks: Partial<Record<BuildSlot, Product>>;
  /** Junction status from compat.check() per adjacent pair.
   *  Same shape BuildTray uses today. */
  junctions: JunctionInfo[];
  isComplete: boolean;
  onCopyBOM: () => void;
  /** Disabled in Phase 1 (placeholder); enabled in Phase 2. */
  onSaveToProject?: () => void;
  /** Disabled in Phase 1 (placeholder); enabled in Phase 3. */
  onOpenInWizard?: () => void;
}
```

`<BuildPage>` owns:
- `BuildRequirements` state (hydrated from URL → localStorage →
  defaults).
- The relations API call orchestration (per Part 4's fan-out
  pattern).
- The `picks` map (writes to `AppContext`'s `build` slice for
  cross-component access).
- The Wizard-draft state (`Set<BuildSlot>` of unconfirmed picks).
- The URL-state synchroniser (writes `BuildRequirements` back to
  the URL on form changes via `history.replaceState`).

The three children are renderer-with-callbacks. Their unit tests
mock the props and verify rendering + callback firing; integration
tests live at the `<BuildPage>` level and exercise the full
form-change → API-call → render-update loop with mocked
`apiClient`.

### Wizard-derived initial state

Build mounts → reads URL params → hydrates `BuildRequirements` from
them → if every required field is present *and* a `?wizard=1`
flag is set, **auto-picks the top candidate per slot** as a draft:

- Each draft pick gets a `data-source="wizard-draft"` attribute on
  the row + a small "Wizard pick — click to swap" badge.
- Drafts count as filled for `<SystemSummary>` slot indicators.
- User clicking a different candidate replaces the draft cleanly;
  the badge disappears and the slot is now user-confirmed.
- "Reset" clears requirements + drafts together. "Reset picks only"
  keeps the requirements but clears the drafts (useful when the
  user wants to re-explore candidates).

Without `?wizard=1`, Build never auto-picks — drafts only happen
on the explicit Wizard handoff path. URL-shared Build configs
without the flag still pre-populate requirements but leave the
candidate selection to the user.

### Empty / loading / error states

| State | Trigger | Render |
|---|---|---|
| First-load empty form | `/build` with no params | All slots locked. Left pane shows the form with all fields blank. Right pane shows a single primer line per slot ("Pick motion class to begin"). Bottom strip shows all slots empty, Copy BOM disabled. |
| Loading candidates | After form change, while relations API call is in flight | The active slot section shows a 3-row skeleton. Other slots stay in their prior state; don't re-skeleton already-rendered data. |
| Relations API error | 5xx, timeout, network | The active slot section shows the error inline (red banner) with a Retry button. Don't fall back to client-side filtering — the relations API is the source of truth, and silent best-effort matches lie to the user about what's actually compatible. |
| No candidates | Filter narrows to zero | Slot section shows the "off-cluster warning" with the closest standardisation suggestion ("Try relaxing stroke to 250mm — surfaces 18 candidates"). Click-to-relax updates the form. |
| Mass = 0 | User explicitly enters 0 | Per Part 2's verbatim copy, inline warning beneath the field. Form still computes derivations (`peak_force = 0`); candidates pane surfaces all actuators with a "no force filter applied" badge in the active-constraints summary. |

### What does NOT ship in Phase 1

Pinned to keep Phase 1 scope honest:

- **Rotary path UI.** `motion_class === "rotary"` selectable in the
  form but renders the "rotary not yet wired — use Selection"
  placeholder for the candidates pane. No motor + gearhead Build
  flow, no rotary tables.
- **Multi-axis systems.** A single linear axis only. No Cartesian /
  gantry / multi-axis robot composition.
- **Save to Project.** Button present but disabled with tooltip
  "Save lands in Phase 1.1." The Projects context already
  persists per-user collections; wiring Build state in is a small
  follow-up but it needs the URL-state shape to settle first.
- **Open in Wizard.** Button present but disabled with tooltip
  "Wizard handoff lands in Phase 3." Forward-compat placeholder
  only.
- **Per-candidate "View in Selection" deep-link.** Useful but
  needs Selection to support a `?type=<t>&filter=<f>` pre-narrow
  URL that doesn't exist yet. Punt to Phase 2.
- **Inline price totals.** MSRP backfill (`DB_CLEANUP` Phase 2) is
  queued but not shipped; without populated `msrp` fields, the
  total would mostly read "—". Add when ≥80% of candidate rows
  have an MSRP.

---

## Part 4 — Relations API integration (SCHEMA Phase 3)

Build's candidate lists are **not** filtered client-side. Every
slot's narrowing is a backend query against `/api/v1/relations/*`,
landed by `todo/SCHEMA.md` Phase 3. Build is **blocked on Phase 3
shipping first** — see "Why no client-side fallback" below.

This part defines the contract Build needs from the API. Anything
the relations module can't yet provide is either stubbed by SCHEMA
Phase 3's first cut OR explicitly deferred to Build Phase 2+ (with
a documented landing trigger).

### Required endpoints

Three `GET` endpoints under `/api/v1/relations/`. All return the
same envelope as `/api/v1/search` (`{success: true, data: [...]}`),
so the frontend reuses `apiClient.search()` plumbing.

#### `GET /api/v1/relations/actuators`

The first slot's narrowing query — driven by `BuildRequirements`
(translated through the derivation engine, not raw form input):

```ts
interface ActuatorQuery {
  min_stroke_mm: number;            // requirements.stroke
  min_peak_force_n: number;         // derived peak_force
  min_peak_velocity_mm_s: number;   // derived peak_velocity
  min_duty_cycle: number;           // 0..1, derived
  orientation?: "horizontal" | "vertical";  // derating hint
  /** When true, only return candidates whose stroke is in the top
   *  3 cluster sizes (drives the "Standard sizes only" toggle). */
  standard_sizes_only?: boolean;
}

interface ActuatorCandidate {
  // Same shape as a LinearActuator record (per generated.ts), plus:
  _distribution_position?: {
    spec: "stroke" | "peak_force_rating" | "peak_velocity_rating";
    rank: number;       // 1 = most common at this spec
    cluster_count: number;  // # records in the same cluster
  };
}
```

The `_distribution_position` block drives Part 3's "8th most common
stroke in catalogue" badge. Computed once per request from the
candidate set (not the full catalogue), so the rank is meaningful
relative to what passed the filter.

#### `GET /api/v1/relations/motors-for-actuator`

```ts
interface MotorsForActuatorQuery {
  actuator_id: string;              // UUID from the picked Actuator
  min_rated_torque_nm?: number;     // derived from peak_force / lead
  min_rated_speed_rpm?: number;     // derived from peak_velocity / lead
  /** Duty cycle from BuildRequirements; gates motor thermal sizing. */
  duty_cycle?: number;
}
```

Backend logic:

1. Resolve `actuator_id` → `LinearActuator` record.
2. Read `actuator.compatible_motor_mounts` (the literal list shipped
   in SCHEMA Phase 1).
3. Filter `Motor` records where `motor_mount_pattern ∈
   compatible_motor_mounts`.
4. Apply torque / speed / duty floor.
5. Return sorted by "best fit" — closest spec match without massive
   over-spec. Sort key TBD by SCHEMA Phase 3 implementation; a
   simple `(rated_torque_nm - min_rated_torque_nm)` ascending works
   for the first cut.

#### `GET /api/v1/relations/drives-for-motor`

```ts
interface DrivesForMotorQuery {
  motor_id: string;
}
```

Backend logic per `todo/SCHEMA.md` Part 3 `compatible_drives()`:

1. Resolve `motor_id` → `Motor`.
2. Filter `Drive` records where:
   - voltage envelope covers `motor.rated_voltage`,
   - current envelope covers `motor.rated_current`,
   - `set(motor.encoder_feedback_support) ∩ set(drive.encoder_feedback_support)`
     is non-empty.

Day-1 caveat: `encoder_feedback_support` shape harmonisation is in
`todo/SCHEMA.md` Phase 1.1 (BREAKING, deferred for sign-off). Until
that lands, the encoder-protocol intersection check in this endpoint
returns "compatible" optimistically (i.e. doesn't filter by encoder)
and the response includes `_warnings: ["encoder protocol check
disabled until SCHEMA Phase 1.1 ships"]` so Build can surface a
banner on the slot.

#### `GET /api/v1/relations/gearheads-for-motor`

```ts
interface GearheadsForMotorQuery {
  motor_id: string;
  /** Optional — Build sends only when the actuator slot's required
   *  torque exceeds the motor's rated torque, and a gearhead is
   *  needed to bridge the gap. */
  min_torque_multiplier?: number;
}
```

Backend logic per `todo/SCHEMA.md` Part 3 `compatible_gearheads()`:

1. Resolve `motor_id` → `Motor`.
2. Filter `Gearhead` records where:
   - `motor.motor_mount_pattern ∈ gearhead.input_motor_mount`,
   - shaft compatibility (`_shaft_compatible(motor.shaft_diameter,
     gearhead.input_shaft_diameter)`).
3. If `min_torque_multiplier` is set, additionally filter where
   `gearhead.gear_ratio ≥ min_torque_multiplier` (using the
   `snapGearUp()` heuristic from `ProductList`).

### The frontend call pattern

Build issues these in a deliberate fan-out order, not all at once:

```
            requirements form change
                    │
                    ▼
     ┌────────────────────────────────────┐
     │ /api/v1/relations/actuators        │  ← every form change
     │   ?min_stroke_mm=200&...           │
     └────────────────────────────────────┘
                    │
                    ▼ (user picks an Actuator)
     ┌────────────────────────────────────┐
     │ /api/v1/relations/motors-for-      │  ← only after actuator pick
     │   actuator?actuator_id=...         │
     └────────────────────────────────────┘
                    │
                    ▼ (user picks a Motor)
     ┌────────────────────────────────────┐  ┌────────────────────────────┐
     │ /api/v1/relations/drives-for-      │  │ /api/v1/relations/         │
     │   motor?motor_id=...               │  │   gearheads-for-motor?...  │
     └────────────────────────────────────┘  └────────────────────────────┘
              (parallel — neither blocks the other)
```

Every form-field change re-fires the actuators query (after a
~250ms debounce). Picking a slot fires the next query immediately
with no debounce.

### Caching

Two layers:

1. **In-memory request cache.** `apiClient` keyed on the full URL.
   Identical query within the same Build session returns the cached
   response. Invalidated on form reset or page unmount.
2. **No persistent cache.** Build's candidate lists must reflect
   the current catalogue — a freshly-ingested record should appear
   on the next form tweak, not after a page reload. Skip
   `localStorage` / `sessionStorage` here; the relations API is
   already cheap (single DynamoDB query + filter).

### Error handling

Per Part 3's empty-state table:

- **5xx / timeout / network.** Inline red banner in the slot's
  pane with a Retry button. Other slots' state is preserved.
- **400 (validation).** Means Build sent a malformed query — the
  derivation engine produced a NaN or the URL params were
  truncated. Log to `console.error` AND surface via toast (per
  `useToast()` once STYLE Phase 3 ships, `console.error` only
  for now). Don't show the user a "your input is invalid" banner;
  the bug is on the frontend, not the user's input.
- **Empty `data: []`.** Not an error — render the off-cluster
  warning per Part 3 with the standardisation suggestion.
- **`_warnings: [...]` in response.** Render as a yellow banner
  above the candidate list (e.g. the encoder-check-disabled
  warning until SCHEMA Phase 1.1 lands).

### Why no client-side fallback

The temptation is to load all `Motor` records into the browser
and filter client-side as a "graceful degradation" when the
relations API is down. **Reject this.** Three reasons:

1. **Volume.** 2,100 motors today, scaling toward 10K+. Loading
   the full table on every Build mount is wasteful at the current
   size and broken at the projected size.
2. **Compatibility logic forks.** The relations API encodes
   subtle pairing rules (`motor_mount_pattern` set intersection,
   voltage envelope coverage, encoder protocol intersection). A
   frontend re-implementation will drift from the backend over
   time, and the drift will silently mis-recommend pairings. The
   relations API is the source of truth; Build trusts it
   absolutely.
3. **The relations API can't be flaky enough to need a fallback.**
   It's a single DynamoDB scan + filter. If it fails repeatedly,
   the right fix is to fix the API, not paper over it on the
   frontend.

When the relations API is down, Build shows the error banner per
Part 3 and waits. The user sees what's wrong instead of getting
silent best-effort matches that lie about compatibility.

### What gates Build on SCHEMA Phase 3

Hard prerequisites — Build cannot ship Phase 1 without:

- All three `motors-for-actuator` / `drives-for-motor` /
  `gearheads-for-motor` endpoints landed and returning the
  contract shape above.
- The actuators endpoint (`/api/v1/relations/actuators`) — this
  one isn't in `todo/SCHEMA.md` Part 3's three-function design
  (which assumes the actuator is already chosen). Build needs
  the symmetric "actuators-for-requirements" entry point.
  **Action item for SCHEMA.md:** add a fourth function
  `compatible_actuators(requirements: ActuatorQuery, actuator_db)
  -> list[LinearActuator]` to `specodex/relations.py`'s scope.
- `_distribution_position` block — also a Build-driven addition
  to SCHEMA Phase 3 scope. Compute server-side from the candidate
  set; sort/badge logic stays trivial on the frontend.

Soft prerequisites — nice to have, but Build can ship without:

- SCHEMA Phase 1.1 (encoder protocol harmonisation). Without it,
  the drive-encoder check is disabled with a banner; not blocking.
- SCHEMA Phase 2 (motor mount backfill). Without it, motor
  candidates surface with `motor_mount_pattern: null`, which the
  relations API treats as "no constraint applied" — broader
  candidate list than ideal, but not wrong. The backfill closes
  the gap when it runs.
- SCHEMA Phase 4 (Force kg → kgf → N coercion). Without it,
  Lintech actuators may misreport their load ratings as Force vs
  Mass; affects 0% of dev DB today (no Lintech in DB) but lands
  on first Lintech ingest.

---

## Part 5 — Selection's diminished role + the linear-actuator bug

Selection (`/`, the existing `<ProductList>` page) doesn't go away
when Build lands — it stays as the **expert / library mode**: a
raw catalogue browser used when Build's recommendations need to be
cross-checked, compared across vendors, or extended into product
types Build doesn't yet model (contactors, robot arms).

But Selection loses three responsibilities that move to Build, and
one bug surfaces along the way that has to be fixed before Build
ships against the same data.

### What Selection still does

- **Browse the raw catalogue** with the full filter chip / column
  resize / sort / per-column unit toggle / density toggle stack.
  Every primitive in `ProductList.tsx` survives; only the entry
  surface changes.
- **Cross-supercategory comparison.** Pick `motor`, narrow to
  Yaskawa-vs-Allen-Bradley by manufacturer chip, compare side by
  side. Build is single-supercategory by design (one motion
  application at a time); Selection is the comparison surface.
- **Power-user filtering.** Selection's filter chips can express
  things Build's form deliberately doesn't (filter by manufacturer,
  by certification, by year). Build's form is opinionated for
  routine selections; Selection is the escape hatch for atypical
  ones.
- **Product-type coverage Build doesn't model.** Contactors, robot
  arms, datasheets — they stay reachable through the existing
  Selection dropdown. Build's `motion_class` enum doesn't cover
  switching gear or articulated robots.

### What gets stripped from Selection

Three pieces move to Build (or get deleted):

1. **The `rotary | linear | z-axis` transmission-type buttons**
   (`ProductList.tsx`'s `<div className="page-toolbar-transmission">`
   block and the surrounding `transmission-type-row` /
   `transmission-param` JSX). Today they appear only when
   `productType === 'motor'` and re-skin the motor specs into
   linear-application units (RPM → mm/s, Nm → N). After Build:
   - The buttons themselves are **removed** from Selection.
   - The underlying linear-mode display transform
     (`rpmToLinearSpeed`, `torqueToThrust`,
     `defaultStateForType`'s `appType / linearTravel / loadMass`
     fields) **moves to Build's Motor slot.** When Build's
     `motion_class === 'linear'`, the Motor candidate rows
     automatically display rated_speed in mm/s and rated_torque in
     N — same conversion math, automatic from form context, no
     button-click required.
   - The `linearTravel` and `loadMass` numeric inputs (currently
     in `<div className="page-toolbar-transmission">`) are
     **deleted from Selection.** Equivalent inputs live in Build's
     requirements form.
2. **The "Add to build" button in `<ProductDetailModal>`.**
   Removed in non-Build contexts. On the Build page only, the
   modal renders a "Pick for [slot]" button (per Part 3).
3. **The `<BuildTray>` strip at the bottom of the viewport.**
   Per Part 3, deleted as a separate component; its UX moves into
   Build's `<SystemSummary>`. Selection gets the bottom-of-viewport
   real estate back.

The `compat-filter-banner` and the compatibility-narrowed result
set on Selection (the `compatFilterActive` and `compatNarrowed`
JSX blocks rendered when `compatAnchors.length > 0`) **also go
away** — they only
make sense when there are anchor picks in the global build slice,
and the global build slice is now Build-page-local. Selection
returns to "show every record matching your filter chips,
period."

### Why ActuatorPage doesn't survive alongside Build

Tempting to keep `/actuators` as-is and add `/build` as a separate
route. Reject this. Three reasons:

1. **Two surfaces for the same scope split the user's attention.**
   ActuatorPage is "Linear Motion landing"; Build's Phase 1 scope
   IS Linear Motion. A user landing on Welcome would have to choose
   between them with no honest way to explain the difference. The
   ActuatorPage configurator is genuinely useful, so it migrates
   into Build's Actuator slot drawer (per Part 3); nothing is lost,
   and the choice is removed.
2. **ActuatorPage's record-table-first framing fights the
   requirements-first flow.** Today ActuatorPage opens with a
   subtype tab + records table; the configurator and motor
   suggestions are below. Build inverts this: requirements first,
   candidates second. Keeping ActuatorPage alive would invite
   users to skip Build's narrowing and browse a 46-row table —
   exactly the surface Selection already provides.
3. **The `/actuators` URL keeps its semantic value as a deep link.**
   Phase 1 PR 1D rewrites `/actuators` to redirect to
   `/build?ml=linear&or=horizontal`, so external links into the
   actuator surface still resolve sensibly — they just land on
   Build with the linear/horizontal scope pre-picked.

The ActuatorPage code's value is preserved (the configurator
extraction); only the standalone-page framing dies.

### Re-routing

| Today | After Build ships |
|---|---|
| `/` → ProductList (default landing) | Stays — `/` → ProductList (Selection) |
| `/welcome` → Welcome (with "Make Your Selection" CTA → `/`) | Welcome's CTA rewrites to "Build a motion system" → `/build` |
| `/actuators` → ActuatorPage | `/actuators` → `Navigate to="/build?ml=linear&or=horizontal" replace` (preserved as a pre-narrowed deep link) |
| Header nav: `Selection / Actuators / Projects / ...` | `Build / Selection / Projects / ...` (Build first, Selection second; Actuators removed) |

### The linear-actuator bug

**What you reported.** Linear actuators don't appear in Selection
results in some user path. Build will surface the same
`linear_actuator` records via Phase 4's relations API, so the
underlying issue must be diagnosed before Build ships against the
same data — a bug that hides records on Selection will hide them
on Build too.

**What we know (audited 2026-05-09 against dev API).**

- `/api/products/categories` returns `{type: "linear_actuator",
  count: 46, display_name: "Linear Actuators"}`. Records exist
  and the categories endpoint exposes them.
- The Selection dropdown is built from `categories.map(...)` →
  `linear_actuator` is therefore selectable from the dropdown.
- The dev DB has 46 `linear_actuator` records, all Tolomatic
  (per `todo/SCHEMA.md` Part 5 audit).
- Picking `linear_actuator` from the dropdown SHOULD trigger
  `loadProducts('linear_actuator')` via `useApp()`, which calls
  `/api/v1/search?type=linear_actuator&...` and renders 46 rows.

**Two likely root causes — investigate both.**

1. **UX trap (most likely).** You picked `motor` from the
   dropdown, then clicked the **"Linear"** transmission-type
   button (the one being deprecated above), and expected
   `linear_actuator` records to appear. They didn't, because that
   button doesn't switch product types — it only re-skins motor
   specs into linear-application units. The "linear" label
   conflates two different meanings (linear-application vs
   linear-actuator product type). Even if this turns out to be
   the only cause, it argues for the strip-down in this Part 5 —
   the button is a discoverability landmine.
2. **Default filter too strict (possible).** `buildDefaultFiltersForType`
   in `app/frontend/src/types/filters.ts` seeds curated default
   chips per product type. If the linear_actuator default chips
   are too narrow (e.g. seeded with a numeric floor that all 46
   Tolomatic records fall below), the user picks `linear_actuator`
   from the dropdown, the records load, but every default filter
   chip immediately hides them. Symptom: dropdown shows
   "Linear Actuators (46)" but the table shows 0 results.

**Diagnostic recipe (Phase 1 prerequisite).**

```
# Reproduction step 1: confirm the records load.
1. Open `/` (Selection).
2. Pick "Linear Actuators" from the type dropdown.
3. Open browser DevTools → Network tab → filter by `search`.
4. Expect: GET /api/v1/search?type=linear_actuator returns
   {success: true, data: [...46 records...]}.
5. If step 4 succeeds: bug is frontend-side (filter or render).
   If step 4 fails: bug is backend-side (route, auth, or zod
   enum).

# Reproduction step 2: check default filter chips.
6. With Linear Actuators selected, look at the chip strip above
   the table.
7. Are any chips pre-populated with numeric values? If so, that's
   the strict-default bug — fix is to remove the value
   pre-population from `buildDefaultFiltersForType` for
   `linear_actuator`.

# Reproduction step 3: confirm the UX trap.
8. From Selection, pick "Motors" instead.
9. Click the "Linear" transmission-type button.
10. The page now shows motor records with RPM displayed as mm/s
    and Nm as N. Rows are still motors. If your prior reproduction
    matched this, the bug is the conflated "linear" label.
```

**Fix.** Whichever root cause the recipe surfaces:

- **If UX trap:** the strip-down in this Part 5 is the fix —
  removing the transmission-type buttons removes the conflation.
  Land Part 5's removal as the first PR of Build Phase 1.
- **If strict-default chips:** edit
  `buildDefaultFiltersForType('linear_actuator')` to seed chips
  without values (matching the `motor` / `drive` defaults). Same
  PR as the strip-down — both touch `ProductList.tsx` and
  `filters.ts`.
- **If backend route / zod enum:** check
  `app/backend/src/routes/search.ts`'s zod schema includes
  `linear_actuator`. After the MODELGEN end-to-end work, the
  enum derives from `generated_constants.ts` automatically, so
  this should be impossible — but verify before assuming.

**Why this doesn't gate Build's design.** The bug is a Selection
issue with a small, localised fix. Build's design is independent —
even if the Selection fix is deferred, Build's relations API path
surfaces records correctly because it doesn't go through
`<ProductList>`'s filter chips. But shipping Build *without*
fixing Selection leaves a UX dead end for users who land on
Selection first and conclude "the catalogue has no linear
actuators." Fix Selection in the same PR sequence as Build's
strip-down to keep the data discoverable on both surfaces.

---

## Part 6 — Wizard handoff (forward context)

This part is **not the Wizard design**. Wizard ships in its own
spec (`todo/WIZARD.md`, future) and has its own page (`/wizard`,
future). What this part locks is the **seam between Wizard and
Build** — the contract Wizard's output must satisfy and the
behaviour Build promises when called by Wizard. Locking it now
keeps Build's Phase 1 schema decisions Wizard-friendly without
forcing Wizard's design into existence yet.

### The user journey

```
┌─────────┐        ┌───────────────────┐        ┌─────────┐
│ Wizard  │ ──→──→ │ /build?<params>   │ ──→──→ │ Quote / │
│ (NL in) │        │ &wizard=1         │        │  BOM    │
│         │        │ (form pre-filled, │        │ (output)│
│         │        │  drafts picked)   │        │         │
└─────────┘        └───────────────────┘        └─────────┘
                          │
                          │ user edits requirements
                          │ or swaps slot picks
                          ▼
                   draft → confirmed
```

Three phases:

1. **Wizard collects natural-language input.** "I need to lift a
   5 kg load 200 mm vertically in under a quarter second, every
   couple of seconds." The LLM derives a `BuildRequirements` blob.
2. **Wizard hands off to Build via URL redirect.** Wizard never
   directly calls the relations API or renders candidates — those
   are Build's job. Wizard is a requirements-derivation layer; it
   doesn't try to be the system assembler.
3. **Build renders the requirements pre-filled + auto-picks top
   candidates per slot as drafts.** User reviews / edits / swaps.
   Once satisfied, the same Copy BOM + (future) Save to Project
   actions complete the journey.

This split is deliberate: Wizard's job is to be *good at understanding
loose natural language*, Build's job is to be *good at rendering
constrained candidate sets and trusted picks*. Mixing them — e.g.
Wizard producing a full BOM from natural language without showing
the user what it picked or why — robs the user of the verification
moment.

### The contract: what Wizard must produce

Wizard's output is **exactly** a `BuildRequirements` blob (from
Part 2), URL-encoded into a Build link. No new schema, no
intermediate handoff format, no wrapper envelope. The same JSON
that Build accepts from a manually-typed URL is what Wizard
produces — so any client that can construct a `BuildRequirements`
JSON can hand off to Build identically.

```ts
// Wizard's only Build-facing function (lives in Wizard's code,
// not Build's — sketched here so Build's Phase 1 knows what to
// promise compatibility with).

import { type BuildRequirements } from '@/types/buildRequirements';
import { serialiseBuildRequirementsToURL } from '@/utils/buildURLState';

function handoffToBuild(requirements: BuildRequirements): string {
  const params = serialiseBuildRequirementsToURL(requirements);
  return `/build?${params}&wizard=1`;
}
```

`serialiseBuildRequirementsToURL` is the same helper Build uses
internally to make its URL bookmarkable (Part 2's URL serialisation
shape). One function, both directions — so the round-trip is
guaranteed.

### The contract: what Build promises in return

When Build mounts with `?wizard=1`:

1. **Pre-populate the form** with the URL-derived
   `BuildRequirements`. Same code path as a manually-typed Build
   URL — no Wizard-specific branch.
2. **Auto-pick the top candidate per slot** as a draft. Per Part
   3's "Wizard-derived initial state":
   - Each draft pick gets a `data-source="wizard-draft"` flag.
   - Drafts render with a "Wizard pick — click to swap" badge.
   - User clicking a different candidate replaces the draft
     cleanly; badge disappears, slot becomes user-confirmed.
3. **Render a top-of-page banner** acknowledging the Wizard
   handoff: *"Wizard derived these requirements from your
   description. Edit any field to refine, or swap any pick to
   override."* The banner has a "Back to Wizard" link that
   navigates to `/wizard?...` with the current requirements
   re-encoded — so the user can iterate on the natural-language
   description without losing their edits.
4. **Honour every user edit normally.** Once the user touches
   any field or swaps any pick, the `?wizard=1` flag is
   dropped from the URL on the next history push (so a copy-paste
   of the URL after the user customises it doesn't re-trigger
   auto-picks for the next viewer).

Build's Phase 1 implements points 1, 2, and 4. Point 3 (the banner
+ "Back to Wizard" link) ships in Build Phase 3 alongside Wizard
itself — there's no Wizard to back-link to until then. Phase 1
just tolerates the `?wizard=1` flag without rendering the banner.

### Forward-compat: how the schema can grow without breaking Wizard

The seam relies on Wizard producing **valid** `BuildRequirements`
blobs. As Build's schema grows (new fields land per Part 2's
forward-compat hooks), Wizard's prompt template needs to stay in
sync. Two rules to keep them composable:

1. **New fields are always optional.** Adding `repeatability` or
   `temperature_range` later doesn't invalidate older Wizard
   outputs — Build treats absent fields as "no constraint." This
   is already the rule for human-typed URLs and falls out for
   free for Wizard.
2. **Field renames are forbidden.** Renaming `move_time` to
   `cycle_time_seconds` would break every Wizard-saved URL ever
   issued. If a rename is genuinely needed, ship it as a new
   optional field, deprecate the old field, dual-write for a
   quarter, then remove. Same discipline as the per-PR HTML doc
   convention — names are public contract once they ship.

Wizard's prompt template (when it ships) will reference
`buildRequirements.ts` directly via the codegen pipeline (similar
to how the frontend types ride on `gen-types`). A schema change
that touches `BuildRequirements` re-runs Wizard's prompt-template
generator → CI catches drift.

### What this part deliberately does NOT specify

- **Wizard's UI.** Single text box vs. multi-step? Voice input?
  Image upload (PDF datasheet → infer requirements)? All Wizard's
  decisions, made in `todo/WIZARD.md`.
- **The LLM prompt.** Few-shot examples, system prompt structure,
  which model (Claude, Gemini), how to handle ambiguity. Wizard's
  problem.
- **Multi-turn refinement.** Whether Wizard asks clarifying
  questions ("vertical or horizontal?") before producing the
  handoff URL, or one-shots from a single description. Wizard's
  problem.
- **Confidence scoring.** Whether Wizard surfaces a "I'm 80%
  sure about stroke, 50% sure about move_time" annotation in the
  handoff. If yes, that's a new optional field on the URL
  (`?confidence=...`) that Build can render as field-level
  badges; Build's Phase 1 doesn't need to support it.
- **Wizard session history.** Saving "the natural-language input
  that produced this Build" for later refinement. Storage,
  retrieval, sharing — all Wizard concerns. Build only ever sees
  the URL state.

### Why lock this seam in Build Phase 1, not later

Schema decisions ripple, and Build URLs are public contract from
day one (bookmarkable, shareable). Defining the URL shape with
Wizard as a known future consumer means Build's Phase 1 schema
discipline doubles as Wizard's handoff spec — one design, two
requirements. Build Phase 1 ships with no Wizard, but the door
is wired, hinged, and locked.

---

## Part 7 — Migration & phasing

A phased rollout, each phase reviewable as a small stack of PRs.
Phase ordering respects the dependency graph: SCHEMA Phase 3
unblocks everything; Selection's strip-down unblocks Build's UI
work; vertical / gravity is additive on top of the horizontal MVP;
Wizard handoff is forward-compat scaffolding.

### Phase 1 — Foundational (horizontal-linear MVP)

**Scope.** Build can render `/build`, accept the requirements form,
return real candidate lists per slot, and produce a Copy BOM for a
horizontal linear motion application. All four slots
(actuator + motor + drive + optional gearhead) work end-to-end.

**PR sequence (4 PRs, mergeable in this order):**

| # | Branch | Scope | Touches | Prereq |
|---|---|---|---|---|
| 1A | `auto/relations-api-phase3-<date>` | SCHEMA Phase 3 backend: `specodex/relations.py` with `compatible_actuators / compatible_motors / compatible_drives / compatible_gearheads`, plus the four `/api/v1/relations/*` endpoints (Express side until PYTHON_BACKEND lands). Includes the `_distribution_position` block + `actuators` endpoint added to SCHEMA's scope per Part 4. | `specodex/`, `app/backend/src/routes/`, `cli/` test command | SCHEMA Phase 1 ✓ shipped |
| 1B | `auto/selection-stripdown-<date>` | Strip Selection per Part 5: delete transmission-type buttons, delete `BuildTray.tsx`, remove "Add to build" from `<ProductDetailModal>` in non-Build context, fix the linear-actuator-bug per Part 5's recipe (likely the UX-trap fix; if it's the strict-default chip bug, includes that fix too). Move the linear-mode display transforms (`rpmToLinearSpeed`, `torqueToThrust`) to a helper that Build's Motor slot will consume in PR 1C. | `app/frontend/src/components/ProductList.tsx`, `app/frontend/src/components/BuildTray.tsx` (deleted), `app/frontend/src/components/ProductDetailModal.tsx`, `app/frontend/src/types/filters.ts`, `app/frontend/src/utils/linearMode.ts` (new) | None |
| 1C | `auto/build-page-mvp-<date>` | New `/build` route + `<BuildPage />` + `<RequirementsForm />` + `<CandidatesPane />` + `<SystemSummary />` per Part 3. New `app/frontend/src/types/buildRequirements.ts` per Part 2. New `app/frontend/src/utils/buildDerivation.ts` (S-curve 1/3 trapezoidal math). Calls the relations API from PR 1A. Linear / horizontal only; rotary stub renders the placeholder. | `app/frontend/src/components/BuildPage.tsx` (new), `app/frontend/src/types/buildRequirements.ts` (new), `app/frontend/src/utils/buildDerivation.ts` (new), `app/frontend/src/utils/buildURLState.ts` (new), `app/frontend/src/utils/compat.ts` (add `actuator` to `BUILD_SLOTS` + new actuator↔motor adjacency rule), `app/frontend/src/App.tsx` (add lazy route + nav reshuffle) | 1A merged, 1B merged |
| 1D | `auto/actuators-redirect-<date>` | Replace `<ActuatorPage />` with `<Navigate to="/build?ml=linear&or=horizontal" replace />` at the `/actuators` route. Delete `ActuatorPage.tsx` + `ActuatorPage.css` + `ActuatorPage.test.tsx`. Move the `<ConfiguratorDrawer>` (extracted from ActuatorPage lines 398–556) into `app/frontend/src/components/build/ConfiguratorDrawer.tsx` so Build's Actuator slot can consume it. Move the configurator templates (`configuratorTemplates.ts`) untouched — their location stays the same. Update `docs/index.html` if it links to `/actuators` directly. | `app/frontend/src/components/ActuatorPage*` (deleted), `app/frontend/src/components/build/ConfiguratorDrawer.tsx` (new), `app/frontend/src/App.tsx` (route swap), `docs/index.html` (link audit) | 1C merged |

**Verification gate per PR.** `./Quickstart verify` green (Python +
backend + frontend) before merge. Each PR ships its
`docs/requests/<n>.html` per the per-PR HTML doc convention.

**Phase 1 exit criteria.**

1. Land on `/build` with no params → form is empty, all slots
   locked, no errors in console.
2. Fill the four required fields (orientation, stroke, move_time,
   dwell_time, mass) → Actuator slot unlocks with at least 5
   candidates against the dev DB.
3. Pick an Actuator → Motor slot unlocks with at least 3
   compatible motors; Motor's specs render in mm/s and N (linear
   mode auto-applied).
4. Pick a Motor → Drive slot unlocks with at least 1 compatible
   drive.
5. Copy BOM produces a 3- or 4-line BOM with valid part numbers.
6. `/actuators` deep-link redirects to `/build?ml=linear&or=horizontal`.
7. The linear-actuator bug from Part 5 no longer reproduces.
8. `./Quickstart verify` green.
9. Smoke staging post-deploy: `/health` 200, `/build` renders,
   `/api/v1/relations/actuators` returns `data` array.

### Phase 2 — Vertical + gravity + commercial polish

**Scope.** Vertical orientation enters the force calculation;
horizontal gets a friction estimate so it stops being optimistic;
Save-to-Project lands; "View in Selection" deep-link wires the
Build → Selection drill-down.

**PR sequence (3 PRs):**

| # | Scope | Notes |
|---|---|---|
| 2A | Vertical orientation + gravity vector. `buildDerivation.ts` adds the `g_factor` block; `<RequirementsForm />` adds the orientation-conditional warning copy ("Vertical sizing assumes worst-case lifting; lowering uses the same picks"). | Form-side only; relations API already accepts the `orientation` hint per Part 4. |
| 2B | Friction estimate for horizontal. Default `friction_estimate ≈ 0.05 × payload_mass × g`; advanced field exposes the coefficient when an engineer wants to override. Form adds a collapsible "Advanced" section. | Pure derivation change; no API contract break. |
| 2C | Save to Project + View in Selection deep-link. Wire Build state into the existing `ProjectsContext`; add `Selection?type=motor&filter=<f>` URL params support to `<ProductList>`. | Depends on Projects already shipping (it has). Adds the "Build state" slot to the per-project record shape. |

**Phase 2 exit criteria.** Vertical Build produces sane peak_force
numbers (5 kg vertical lift at 1 m/s² accel → ~74 N, not 5 N); a
horizontal build sized for a 10 kg payload chooses a beefier
actuator than today's frictionless calc would (verifies friction
estimate is wired); Save to Project round-trips a Build state
across sessions.

### Phase 3 — Wizard handoff scaffolding

**Scope.** Build supports the `?wizard=1` flag end-to-end (banner,
auto-pick drafts, draft-vs-confirmed visual differentiation,
"Back to Wizard" link). No actual Wizard yet — the link goes to
`/wizard` which renders a "Wizard ships in a future release"
placeholder.

**PR sequence (2 PRs):**

| # | Scope |
|---|---|
| 3A | `?wizard=1` flag handling: auto-pick top candidate per slot as a draft, render the wizard-pick badge per Part 3, banner per Part 6. |
| 3B | `/wizard` route placeholder (separate spec lands when Wizard is designed). |

**Phase 3 exit criteria.** Build with `?wizard=1` and full
requirements pre-populated auto-picks a draft per slot;
single-click to swap clears the draft; user-confirmed picks
persist across reload via the URL.

### Phase 4+ — Out of Build's MVP arc

These are explicitly deferred and tracked elsewhere (or not yet
tracked, with the trigger noted). They are NOT part of Build's
shipping path; they're listed so future-you knows where the next
threads land.

| Feature | Trigger | Lands in |
|---|---|---|
| Rotary path (motor + gearhead Build, no actuator) | First user asks "I need a rotary motion application Build" | New `/build?ml=rotary` flow; reuses requirements form's motion class enum |
| Multi-axis systems (Cartesian, gantry, robot) | First multi-axis Build request | Likely a new top-level page (`/system-build`) — Build stays single-axis |
| Environmental requirements (temperature, IP rating, cleanroom) | First user asks for them | Additive to `BuildRequirements`; relations API gains optional filters |
| Fieldbus selection step | "Way later" per Nick — when servo selection becomes opinionated | Lives on the Drive slot, not the requirements form |
| Centre-of-gravity + bearing-moment checks | Bearing-load incident or first user asks | New `LinearOrientation` value `"side_mount"` + a CoG inputs section |
| MSRP totals in `<SystemSummary>` | DB_CLEANUP Phase 2 (MSRP backfill) ships AND ≥80% of candidate rows have an MSRP | Inline price line beneath the BOM |
| Multiple stacked motion profiles | First user asks for a complex duty cycle | Already shaped for in Part 2's `MotionProfile[]` list |

### What gets retired

Pinned so the deletion is unambiguous. Deletion happens in PRs 1B
and 1D as listed; this table is the audit summary.

| Component / file | Status after Phase 1 | Why |
|---|---|---|
| `app/frontend/src/components/ActuatorPage.tsx` | DELETED | Replaced by `<BuildPage />`; configurator extracted into `<ConfiguratorDrawer />`. |
| `app/frontend/src/components/ActuatorPage.css` | DELETED | Same reason. |
| `app/frontend/src/components/ActuatorPage.test.tsx` | DELETED | Same reason. |
| `app/frontend/src/components/BuildTray.tsx` | DELETED | Absorbed into `<SystemSummary />` inside `<BuildPage />`; no longer follows the user across pages. |
| Transmission-type buttons in `<ProductList>` (`page-toolbar-transmission` block) | DELETED | Replaced by `BuildRequirements.motion_class` + `orientation`. |
| `linearTravel` / `loadMass` inputs in `<ProductList>`'s toolbar | DELETED | Equivalent inputs in Build's requirements form. |
| `defaultStateForType`'s `appType / linearTravel / loadMass` fields | DELETED | Selection no longer has app-type state. |
| "Add to build" button in `<ProductDetailModal>` (non-Build context) | RENAMED + SCOPED | Becomes "Pick for [slot]" and only appears on Build page. |
| `compat-filter-banner` in `<ProductList>` (the `compatFilterActive` JSX blocks) | DELETED | Selection no longer reflects build state. |

### Migration risks worth naming

- **The `build` slice in `AppContext` is shared state.** Today it's
  read by `ProductList` (for the compat-filter banner), `BuildTray`
  (for slot rendering), and `ProductDetailModal` (for the "Add to
  build" button). After the strip-down, only `<BuildPage />` reads
  it. Any future feature that taps the `build` slice from outside
  Build needs to first decide whether the tap belongs in Build's
  scope or whether `build` should formally page-local. Phase 1
  leaves it global; if a Phase 2+ feature wants it, that's the
  trigger to revisit.
- **`compat.ts`'s `BUILD_SLOTS` reorder.** Today: `['drive',
  'motor', 'gearhead']`. Phase 1 reorders to
  `['actuator', 'motor', 'drive', 'gearhead']` (prepends
  `'actuator'` AND moves `'motor'` ahead of `'drive'` to match
  user-facing fill order). Existing name-keyed adjacency rules
  (`drive↔motor`, `motor↔gearhead`) keep working unchanged; PR 1C
  adds the new `actuator↔motor` rule. **The reorder DOES affect
  `ChainReviewModal.tsx`'s `adjacentFilledPairs` helper** — its
  output yields different junctions than before (today: drive→motor,
  motor→gearhead; after: actuator→motor, motor→drive, drive→gearhead).
  Audit ChainReviewModal for any hardcoded slot pairs or index
  assumptions when PR 1C lands. Update its junction labels to match
  the new array order.
- **The `apiClient.search()` envelope.** Relations endpoints
  reuse the same `{success: true, data: [...]}` shape. If a
  future relations endpoint needs pagination or streaming
  semantics, it'll break the convention — handle by versioning
  (`/api/v2/relations/*`) rather than mutating the v1 envelope.
- **CDN cache for `/build`.** The route is a SPA path; CloudFront
  serves `index.html` for it. If CDK's `defaultRootObject`
  config doesn't already 404-fallback to `index.html`, `/build`
  will 404 on direct navigation. Verify in PR 1C; this bit
  Specodex once before with the Welcome route.

---

## Triggers — when to surface this doc

| Trigger (files / topics in your current task) | Surface |
|---|---|
| `app/frontend/src/components/BuildPage.tsx`, `app/frontend/src/components/build/`, `app/frontend/src/types/buildRequirements.ts`, `app/frontend/src/utils/buildDerivation.ts`, `app/frontend/src/utils/buildURLState.ts` | This doc — the new Build module |
| `app/frontend/src/components/ActuatorPage.tsx` (still extant pre-Phase-1), `app/frontend/src/components/BuildTray.tsx` (still extant pre-Phase-1) | This doc — both retired in Phase 1 PRs 1B and 1D |
| `app/frontend/src/types/configuratorTemplates.ts` | This doc *and* `todo/CATAGORIES.md` — Build's Actuator slot consumes these via the extracted `<ConfiguratorDrawer />` |
| `app/frontend/src/components/ProductList.tsx` (transmission-type buttons, `linearTravel` / `loadMass` inputs, `compat-filter-banner`) | This doc — Selection strip-down per Part 5 |
| `app/frontend/src/utils/compat.ts` (`BUILD_SLOTS`, adjacency rules) | This doc — Phase 1 prepends `'actuator'` and adds the actuator↔motor adjacency rule |
| `specodex/relations.py`, `app/backend/src/routes/relations.ts`, `/api/v1/relations/*` endpoints | This doc *and* `todo/SCHEMA.md` Phase 3 — Build is the only consumer of these endpoints today |
| `app/frontend/src/App.tsx` route table or nav order | This doc — Phase 1 PR 1C reshuffles nav and adds `/build`; PR 1D rewrites `/actuators` to a redirect |
| User asks about "the Build page", "requirements form", "/build", "Build a motion system" | This doc |
| User asks "how does Wizard hand off to Build", "Build URL params", "Wizard contract" | This doc — Part 6 |
| User asks "why don't linear actuators show up on Selection" | This doc — Part 5's bug investigation + the strip-down fix |
| User asks "what's the difference between Selection / Build / Wizard" | This doc — Part 1 |
| User asks "what does the relations API do" or proposes a client-side fallback for it | This doc — Part 4's "no client-side fallback" rule |

---

## Open questions

Items the spec deliberately leaves open. Each comes with the
**trigger** that converts it from open question into a Phase X
decision. None of these block Phase 1.

- **Friction model fidelity.** Phase 2 ships a flat `0.05 × m × g`
  estimate for horizontal. Real friction varies by drive
  mechanism (ball screw ≈ 0.02, lead screw ≈ 0.10, belt-driven
  ≈ 0.05) and the configurator templates have enough metadata to
  pick per-template. **Trigger:** first under-sized horizontal
  Build (motor stalls, customer reports thrust shortfall). Until
  then the flat estimate is fine for routine selections.
- **Per-slot ranking.** Phase 1 sorts candidates by "closest spec
  match without massive over-spec" (a simple
  `rated - required` ascending). That heuristic ignores price,
  lead time, and vendor preference. **Trigger:** users reporting
  Build's top pick is always the most expensive option, or the
  one with 16-week lead time. Ranking becomes a Phase 2+ feature
  with weights — the relations API gains a `sort_by` parameter.
- **Multi-vendor constraint.** Today Build can pick a Tolomatic
  actuator + a Yaskawa motor + a Beckhoff drive — three vendors,
  three POs, three lead times. Most procurement workflows prefer
  fewer vendors. **Trigger:** explicit user complaint or a
  procurement-aware feature request. Then Build adds a "Same
  vendor where possible" toggle that re-ranks candidates by
  shared `manufacturer` field.
- **Saved-Build versioning.** When Phase 2's Save-to-Project
  lands, a saved Build snapshots the requirements + picks at
  save time. If the catalogue changes (a picked product is
  discontinued, a new actuator with better fit lands), the saved
  Build becomes stale. **Trigger:** first user reopens a saved
  Build a quarter later. Build then needs a "refresh against
  current catalogue" affordance and a way to mark stale picks.
- **Wizard's prompt-template upkeep.** Part 6 promises Wizard's
  prompt template auto-syncs against `BuildRequirements` via
  codegen. Until Wizard ships there's nothing to sync; once it
  ships, the codegen pipeline needs a CI check that catches
  prompt-template-vs-schema drift. **Trigger:** Wizard's spec
  (`todo/WIZARD.md`) lands.
- **Build for non-actuator-anchored applications.** Today's Build
  treats the actuator as the load-anchor. Some applications
  start with the motor (e.g. selecting a motor for an
  already-specified custom mechanical assembly) or with the
  drive (selecting a drive for an existing motor with no
  catalogue analogue). **Trigger:** user request. The fix is
  probably a "start from" toggle in the requirements form
  (anchor = actuator | motor | drive) that flips the slot-fill
  sequence's lock order. Out of MVP scope; flag if it surfaces.
- **Rotary table device class.** Nick mentioned rotary tables
  "much later." When that lands, rotary-with-actuator becomes a
  meaningful flow (the rotary table IS the load-anchor for a
  rotary application, same role the linear actuator plays for
  linear). **Trigger:** new product type added to
  `specodex/models/`; SCHEMA's `MotorMountPattern` literal may
  also need rotary-table-specific entries.
- **Performance profile editor.** Part 2's `MotionProfile[]` is
  a list-of-1 today; eventually users will want to compose
  multi-segment moves (e.g. "start fast, then settle slow") and
  see the cumulative duty cycle. **Trigger:** first user asks
  for it OR an internal evaluation of motor thermal sizing
  surfaces a duty-cycle mis-estimation that a stacked profile
  would catch. Probably becomes a separate "Motion profile
  editor" component embedded in the requirements form.
- **Build state in URL vs. opaque ID.** Part 6's URL
  serialisation uses readable params (`?ml=linear&st=200mm`).
  This is great for sharing and bookmarking but exposes the
  schema field names publicly — every rename breaks bookmarks.
  Opaque IDs (`/build/<uuid>`) would let the schema evolve
  freely but lose human-readability. **Trigger:** first
  Build-URL-rename pain (a field rename breaks user bookmarks).
  Solution direction: keep readable params for ephemeral use,
  add opaque IDs for Save-to-Project's persistent links.
- **Mobile / touch UX.** The two-pane layout in Part 3 assumes
  desktop width. Mobile collapses to a single column with the
  form on top, candidates scrolling below, system summary
  pinned to the bottom — but the spec doesn't pin the
  breakpoint or the touch-target sizes. **Trigger:** mobile
  traffic measurably picks up (per the deferred analytics
  doc, `todo/longterm/SEO.md`'s organic acquisition story).
  Until then, desktop-first per the rest of the app.
