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

## Status

**Design-only as of 2026-05-09.** No code shipped.

Hard prerequisites:

- **SCHEMA Phase 3** (`specodex/relations.py` + `/api/v1/relations/*`
  + `RelationsPanel` skeleton) — see `todo/SCHEMA.md`. Build's
  "compatible motors / drives for this actuator" panel reads the
  relations API directly; no frontend-only fallback.
- The bug investigation in Part 5 — `linear_actuator` records exist
  in the DB (46 in dev) and the categories endpoint returns them,
  but they're not surfacing on Selection in some path. Diagnose
  before Build ships, since Build will surface the same records.

Soft predecessors (already shipped):

- CATAGORIES Phase 0+1 (supercategory map + `/actuators` MVP) —
  the source the Build page absorbs.
- SCHEMA Phase 1 (additive cross-product fields:
  `motor_mount_pattern`, `compatible_motor_mounts`,
  `input_motor_mount`, `output_motor_mount`).

Phasing (filled in by Part 7):

- **Phase 1** — foundational refactor + Linear-Motion / horizontal
  Build with relations API consumption.
- **Phase 2** — vertical orientation + gravity vector.
- **Phase 3** — Wizard handoff scaffolding.

---

## Part 1 — The three-page model

[To be drafted. What Selection / Build / Wizard each are for, when
the user lands on each, how they relate (Wizard → Build →
Selection drill-down). Frames the rest of the doc.]

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
  /** MANDATORY for linear. 0 is accepted with a warning. */
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
| `payload_mass` | **mandatory** | `null` | Required to surface any candidates. Accepts 0 with the warning copy below; doesn't accept blank. |
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

[To be drafted. UI layout sketch, BuildTray absorption (the existing
sticky-bottom 3-slot tray moves up onto Build), where the
configurator's stroke segment lives, what populates the tray as the
user fills in requirements.]

---

## Part 4 — Relations API integration (SCHEMA Phase 3)

[To be drafted. What Build consumes from `/api/v1/relations/*`, the
contract shape, the fan-out pattern (actuator → compatible motors →
compatible drives → compatible gearheads), what gates Build's ship
date on the relations API landing first.]

---

## Part 5 — Selection's diminished role + the linear-actuator bug

[To be drafted. What Selection still does after Build lands (raw
catalog browse, expert mode, cross-supercategory comparison). What
gets stripped: the rotary/linear/z-axis transmission-type buttons
that today live on the Motor view in `ProductList`. Investigation
of why `linear_actuator` records (46 in dev DB, exposed via
categories endpoint) aren't surfacing on Selection in some user
path — root cause + fix.]

---

## Part 6 — Wizard handoff (forward context)

[To be drafted. NOT designing Wizard — just locking the seam.
Wizard produces a *draft* Build state (the schema from Part 2);
Build renders it as user-editable; user commits. Why the
requirement schema must be JSON-serialisable: URL state, saved
drafts, Wizard handoff, project save-and-resume all use the same
shape.]

---

## Part 7 — Migration & phasing

[To be drafted. What ships in what order, what gates each step,
what gets retired. Sequence: SCHEMA Phase 3 (prereq) → Selection
bug fix → Phase 1 Build (horizontal-linear) → Phase 2 (vertical
+ gravity) → /actuators redirect → BuildTray-as-component
retirement → Phase 3 Wizard handoff scaffolding.]

---

## Triggers — when to surface this doc

[To be drafted. File-level triggers (edits to ActuatorPage.tsx,
BuildTray.tsx, App.tsx routes, the requirement schema files when
they exist) and topic triggers (user asks about "the Build page",
"requirements form", "actuator selection flow", "Wizard handoff").]

---

## Open questions

[To be drafted at the end, after every other section is settled.]
