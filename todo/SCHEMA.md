# SCHEMA — fit-check, cross-product hygiene, device-relations

This doc captures the deferred Part 4 work from `todo/CATAGORIES.md`
plus the broader question Nick asked when handing off the actuator MVP:

> "Do an in-depth analysis of the schema and ensure it will correlate
> with this initiative. … Run all of the deferred tasks. The key to this
> whole project is integration so its huge that we can procedurally
> generate a solution by transforming the part numbers, performance, and
> relations between devices."

Three things landed in scope:

1. **Fit-check.** Does the existing `LinearActuator` Pydantic model
   actually extract the new vendors (Lintech, Toyo, Parker) cleanly?
   It was schemagen'd against Tolomatic / Rexroth / SMC / THK only.
2. **Cross-product field hygiene.** `encoder_feedback_support`,
   `fieldbus`, `motor_type`, `frame_size` exist on multiple models with
   *different types and shapes* — that breaks any cross-product
   "compatible motor for this drive" query before it starts.
3. **Device-relations layer.** The configurator's `derive(choices)` in
   `app/frontend/src/types/configuratorTemplates.ts` already computes
   `suggested_motor_frame` and `max_speed_mm_s` per actuator
   configuration — but there's nothing on the *backend* schema that
   lets a query bridge from "this actuator suggests NEMA 23" to "show
   me NEMA 23 motors with ≥3000 RPM and matching torque." That's the
   integration gap.

The path is: **lock the cross-product fields first, then bolt the
device-relations layer on top.** Renaming `fieldbus` to a consistent
shape AFTER the relations layer is built is much more painful than
doing it now.

## Status

- **Phase 0** (this doc) and **Phase 1** (additive cross-product field
  hygiene — `MotorMountPattern` literal, `motor_mount_pattern` on Motor
  / ElectricCylinder, `compatible_motor_mounts` on LinearActuator,
  `input_motor_mount` + `output_motor_mount` on Gearhead) **applied
  2026-05-08** on `feat-actuators-mvp-20260508` alongside the actuator
  MVP. `./Quickstart gen-types` regenerated `generated.ts` and
  `generated_constants.ts`; `./Quickstart verify` is green for Python
  (1125 tests), backend, and frontend.
- **Phase 1.1** (BREAKING type-harmonisation: `motor_type`, `fieldbus`,
  `encoder_feedback_support` shape unification) — designed below, NOT
  yet applied. Needs a one-shot data migration plus user sign-off
  because the existing dev DB has rows that would fail re-validation.
- **Phase 2** (backfill `motor_mount_pattern` from `frame_size`) — Late
  Night candidate; depends on Phase 1 (✓).
- **Phase 3** (relations API + `RelationsPanel` on `/actuators`) —
  focused PR; depends on Phase 1 (✓) and the actuator MVP merge.
- **Phase 4** (`kg → kgf → N` coercion for Force fields) — small
  follow-up; surfaced by the Lintech fit-check (Part 1 below).

---

## Part 1 — Schema fit-check (Lintech + Toyo)

### Headline

**Schema fits. No new fields needed in `linear_actuator.py` from this
pass.** The CATAGORIES.md Part 4 deferred task is complete with this
result. Two passes converged on the same answer:

1. **Prior 3-PDF pass** documented in
   `specodex/models/linear_actuator.md` ("Fit-check pass — Lintech /
   Toyo, 2026-05-08"). Source: Lintech 200, Lintech 150, Toyo Y-series.
2. **This 9-PDF pass** for breadth, runner at
   `outputs/schema_fit_check/run_fit_check.py`. Source: all 8
   downloadable Lintech catalogs (90, 100/110/120, 130/140, 150,
   160/170/180, 200, 250, 610) + Toyo. Output:
   `outputs/schema_fit_check/SUMMARY.md`.

Both passes agree: Gemini emits ZERO fields outside the registered
schema. The fields the schema *has* but Lintech/Toyo *don't publish*
(`backlash`, `dynamic_load_rating`, `static_load_rating`,
`static_allowable_moment_*`, `ip_rating`, `rated_voltage`,
`rated_current`) are vendor-mix-driven gaps — heavy-industrial
catalogs (Tolomatic, Rexroth, THK) populate them — not schema bugs.
**Don't drop the 0%-coverage fields** even though Lintech/Toyo never
fill them.

### Parker still blocked

Both Parker URLs the user supplied (`Parker-Screw-Driven-
Positioners-and-Actuators.pdf`, `HD-Series-Catalog.pdf`) returned 403
Forbidden with a real Chrome User-Agent. Same Akamai-CDN fingerprint
as the original schemagen attempt. Workaround for a future operator:
download manually from Parker's cookie-gated portal and drop into
`outputs/schema_fit_check/datasheets/`; the runner picks it up by
filename. The hand-authored Parker HD configurator template in
`app/frontend/src/types/configuratorTemplates.ts` is based on the
public ordering page; it has no DB-backed validation today.

### Real-world part-number formats observed

Used to rewrite the configurator templates in
`app/frontend/src/types/configuratorTemplates.ts`:

- **Lintech 200**: `200<frame><travel>-WC<accessory>` (e.g.
  `200607-WC0`). The original hand-authored template guessed
  `200-L-072-08-N23` — wrong. Catalog ordering page is the source of
  truth.
- **Toyo Y-series**: `<series>-<subtype>` (e.g. `Y43-L2`).
- **Tolomatic TRS**: `TRS<frame>-BNM<lead>` (e.g. `TRS165-BNM10`) —
  the format that actually populates dev DB.

### Coverage matrix — 9-PDF pass (2026-05-08)

Generated by `outputs/schema_fit_check/run_fit_check.py`. Of 9 PDFs:
**5 extracted cleanly** (253 records total), **4 hit JSON truncation**
even at the 8-page cap (Lintech 130/140, 160/170/180, 610, 90 — the
densest catalogs). The truncation isn't a schema problem — it's a
bundled-extraction limit (`Unterminated string at char N` mid-JSON,
documented in the catalog-ingest skill). Per-page mode would close
that gap; out of scope for a fit-check.

| PDF | Records | Avg quality | Unique part numbers |
|---|---:|---:|---:|
| lintech_100_110_120 | 164 | 0.20 | 96 |
| lintech_130_140    | — | ERROR | — (truncated) |
| lintech_150        |  28 | 0.11 | 28 |
| lintech_160_170_180 | — | ERROR | — (truncated) |
| lintech_200        |  28 | 0.17 | 28 |
| lintech_250        |  28 | 0.17 | 28 |
| lintech_610        | — | ERROR | — (truncated) |
| lintech_90         | — | ERROR | — (truncated) |
| toyo               |   5 | 0.37 |  5 |

**Population by field** (number of records with non-null value):

- **All 5 successful PDFs (≥225 records)**: `part_number`, `type`,
  `series`, `stroke`, `motor_type`. The discriminator + identity
  fields. Schema fits.
- **Lintech-driven (≥164)**: `actuation_mechanism`, `cleanroom_class`.
  Lintech catalogs are explicit about cleanroom certification because
  their target market is semicon / pharma stages.
- **Toyo-only (5 records each)**: `max_work_load`, `max_push_force`,
  `max_linear_speed`, `positioning_repeatability`, `lead_screw_pitch`,
  `screw_diameter`, `rated_power`. Toyo Y-series publishes the rich
  tables Lintech hides behind motorless-mechanical assumptions.
- **Universal 0% (across this 9-PDF pass)**: `holding_force`,
  `dynamic_load_rating`, `static_load_rating`, `max_acceleration`,
  `backlash`, `static_allowable_moment_{pitching,yawing,rolling}`,
  `rotor_inertia`, `encoder_feedback_support`, `rated_voltage`,
  `rated_current`, `peak_current`, `ip_rating`, `operating_temp`,
  `operating_humidity_range`, `release_year`, `weight`, `msrp`,
  `warranty`. **These populate from heavy-industrial catalogs
  (Tolomatic, Rexroth, THK)** — they're vendor-mix gaps, not schema
  bugs. Don't drop them.

**Conclusion: schema fits. Zero out-of-schema fields emitted by Gemini
across 253 records.** No new actuator-side fields required from this
pass. The integration-driven additions in Phase 1 (motor_mount_pattern,
compatible_motor_mounts) remain the right call — they're not "missing
from the catalog data" but "missing for the device-relations layer."

### Part-number encodings observed (calibration for configurator templates)

| Vendor / family | Encoding | Sample |
|---|---|---|
| Lintech 100/110/120 | `10x<frame><travel>-CP<accessory>` | `10x402-CP0`, `10x424-CP2` |
| Lintech 150        | `150<frame><travel>-WC<accessory>` | `150406-WC1`, `150836-WC1` |
| Lintech 200        | `200<frame><travel>-WC<accessory>` | `200607-WCO`, `201236-WC1` |
| Lintech 250        | `250<frame><travel>-WC<accessory>` | `250607-WC0`, `251236-WC1` |
| Toyo Y-series      | `Y<frame>-<subtype>` | `Y43-12`, `Y62-6` |

The Lintech 200 template in
`app/frontend/src/types/configuratorTemplates.ts` is calibrated against
this format (per the prior pass's `linear_actuator.md` update). The
100-series uses a different prefix (`10x`) — extending the configurator
to cover it is a follow-up. The 250 and 150 families share the
WC-accessory shape with 200; one template parameterised on the leading
3 digits could cover all three. That's a CATAGORIES.md Phase 0 follow-up,
not SCHEMA.md scope.

### Real schema friction (not field-missing — coercion)

The fit-check surfaced one coercion issue worth flagging:

- **`dynamic_load_rating: Force` rejects `kg` units.** Lintech publishes
  load capacities like `{value: 703, unit: 'kg'}`. The schema's `Force`
  family expects newtons (or `kgf`, which Lintech does *not* spell out
  — they write plain `kg`). The unit normaliser drops these as
  wrong-family, leaving 0% coverage for `dynamic_load_rating` /
  `static_load_rating` on Lintech ingests.
- **Two readings of the field.** "Load rating" in Lintech catalogs
  refers to *bearing capacity in mass units* (kg), not thrust force in
  newtons. The schema treats it as Force. The right fix is one of:
  - (a) Accept `kg` as `kgf` (kilogram-force ≈ 9.81 N) for these specific
    fields, with an automatic conversion in the BeforeValidator. Pro:
    reads naturally. Con: hides a unit convention foot-gun the next
    person should know about.
  - (b) Rename the schema fields to `dynamic_load_capacity: Mass` /
    `static_load_capacity: Mass` and treat thrust separately. Pro:
    physically honest. Con: BREAKING for the existing 57 Tolomatic
    records (which publish in lbf/N and are correctly typed as Force).
  - (c) Leave it. Filter UI just won't show load-rating columns for
    Lintech rows. Pro: no work. Con: Lintech rows look incomplete in
    the Actuator page.

The recommendation is **(a)** — add `kg` and `kgf` to Force's accepted
units with the `kgf → N` conversion in the unit-normaliser, and a
validator-warning log line so the next time we see this we know it
applied. This is in scope for SCHEMA Phase 1 follow-up.

---

## Part 2 — Cross-product field hygiene

The four fields that cross product-type boundaries are inconsistently
typed today. Fix list:

### `encoder_feedback_support`

Five product types, three different shapes:

| Model | Type today |
|---|---|
| `Motor` | `Optional[str]` |
| `Drive` | `Optional[List[str]]` |
| `Gearhead` | not present |
| `LinearActuator` | `Optional[List[str]]` |
| `ElectricCylinder` | `Optional[str]` |

A motor can support multiple encoder protocols (incremental, absolute,
EnDat, BiSS-C); drives can speak many; cylinders typically integrate
one. The shape that fits all is `Optional[List[str]]`. Migration: widen
`Motor.encoder_feedback_support` and `ElectricCylinder.encoder_feedback_support`
to lists. Existing string-valued records auto-coerce via a
`BeforeValidator` that wraps a single string in a list.

### `fieldbus`

| Model | Type today |
|---|---|
| `Drive` | `Optional[List[CommunicationProtocol]]` |
| `ElectricCylinder` | `Optional[str]` (raw string) |
| `Motor` | not present (but ServoMotors with on-board comm exist — Maxon EPOS, etc.) |
| `LinearActuator` | not present (but on-board servo variants increasingly common) |

`Drive.fieldbus` uses the structured `CommunicationProtocol` model in
`specodex/models/communication_protocol.py`. That's the right shape.
Migration:

1. Promote `ElectricCylinder.fieldbus` from `str` to `Optional[List[CommunicationProtocol]]`.
2. Add the same field to `Motor` and `LinearActuator` (optional). Catalogs
   without on-board comm just leave it null.

### `motor_type`

| Model | Type today |
|---|---|
| `Motor.type` | `Literal["brushless dc", "brushed dc", "ac induction", "ac synchronous", "ac servo", "permanent magnet", "hybrid"]` |
| `ElectricCylinder.motor_type` | `Optional[str]` (free-form: "brushless dc", "brushed dc", ...) |
| `LinearActuator.motor_type` | `Optional[Literal["step_motor", "servo_motor", "motorless"]]` |

Three different vocabularies for the same concept. Worse, none of them
overlap — `LinearActuator` says "servo_motor" where `Motor` says
"ac servo" or "brushless dc". A user filtering by motor type can't
write a single query that covers all three.

Proposed unified literal (in `common.py`):

```python
MotorTechnology = Literal[
    "brushless_dc",      # BLDC servo, the most common modern type
    "brushed_dc",
    "ac_servo",
    "ac_induction",
    "ac_synchronous",
    "permanent_magnet",
    "stepper",
    "hybrid_stepper",
    "linear_motor",
    "motorless",         # the actuator ships without a motor (customer-supplied)
]
```

Migration:
- `Motor.type` → `MotorTechnology`
- `ElectricCylinder.motor_type` → `Optional[MotorTechnology]`
- `LinearActuator.motor_type` → `Optional[MotorTechnology]`

Existing records get a one-shot data migration: map old strings to the
new vocabulary in `cli/admin.py` (a new `harmonize-motor-type` subcommand).

### `frame_size`

| Model | Type today |
|---|---|
| `Motor.frame_size` | `Optional[str]` (free-form: "60", "NEMA 23", "IEC 80") |
| `Gearhead.frame_size` | `Optional[str]` (free-form, same shape) |
| `Contactor.vendor_frame_size` | `Optional[str]` (different concept — contactor frame size) |
| `LinearActuator` | not present (but motor-mount pattern is the same concept) |
| `ElectricCylinder` | not present |

This is the field most central to the integration story. A motor
publishes "NEMA 23"; a gearhead publishes "60mm" or "NEMA 23"; an
actuator publishes "compatible with NEMA 17/23"; a drive doesn't
publish this at all. There's no machine-readable bridge.

Two options:

**Option A (minimal):** keep `frame_size` as `Optional[str]` everywhere
and add a normalizer that maps known synonyms to a canonical token
(`"NEMA 23"`, `"NEMA 17"`, `"IEC 80"`, `"60mm"`, ...). Pro: no schema
break. Con: filters still don't compose — the normalizer has to live in
the query layer.

**Option B (recommended):** introduce a `MotorMountPattern` literal in
`common.py`:

```python
MotorMountPattern = Literal[
    "NEMA 8", "NEMA 11", "NEMA 14", "NEMA 17", "NEMA 23", "NEMA 34", "NEMA 42",
    "IEC 56", "IEC 63", "IEC 71", "IEC 80", "IEC 90", "IEC 100", "IEC 112", "IEC 132",
    "MAX 8", "MAX 13", "MAX 16", "MAX 20", "MAX 25", "MAX 30", "MAX 35", "MAX 40",  # Maxon
    "custom",
]
```

(The list is starter — observed frames from the existing 2,041 motor
records in dev DB plus Lintech's published mount tables would extend it.
Pull the actual list from `aws dynamodb scan` once before freezing.)

Migration:
- `Motor.frame_size`: keep as `Optional[str]` (vendor descriptor like "60mm" can carry).
- Add `Motor.motor_mount_pattern: Optional[MotorMountPattern]` derived from `frame_size` via a `model_validator`.
- Add `Gearhead.input_motor_mount: Optional[List[MotorMountPattern]]` (gearhead can take multiple frames via adapter plates).
- Add `LinearActuator.compatible_motor_mounts: Optional[List[MotorMountPattern]]` (a Lintech 200 takes NEMA 23 *and* NEMA 34).
- Add `ElectricCylinder.motor_mount_pattern: Optional[MotorMountPattern]`.

This is the **load-bearing change for the integration story.** Without
a unified motor-mount enum, "show me motors that fit this actuator" is
a string-comparison hack.

---

## Part 3 — Device-relations layer

### The framing

User's words: "transforming part numbers, performance, and relations
between devices." Three things that map onto three layers:

1. **Part numbers** — Lintech 200 → `200-LBM-072-08-S-XX-A1`. Already
   handled by `configuratorTemplates.ts`. Bidirectional (synthesise +
   parse). 6 templates shipped (3 Tolomatic + 1 Lintech + 1 Toyo + 1
   Parker). See `todo/CATAGORIES.md` Part 2.
2. **Performance** — given user choices (travel, lead, motor RPM),
   compute derived specs (max linear speed, suggested motor frame).
   Already partly handled by the `derive(choices)` callback on each
   template, which returns `DerivedSpecs { lead_mm, max_speed_mm_s,
   assumed_motor_rpm, suggested_motor_frame, caveat }`.
3. **Relations between devices** — given an actuator's
   `compatible_motor_mounts` and a target motor's `motor_mount_pattern`,
   compute which motors are valid pairings. **This is the gap.** The
   frontend can suggest "NEMA 23" but cannot, today, hand off to a
   motor query that returns concrete part numbers.

### Concrete proposal

A new module `specodex/relations.py` with:

```python
def compatible_motors(
    actuator: LinearActuator | ElectricCylinder,
    motor_db: list[Motor],
    *,
    min_torque: Optional[Torque] = None,
    min_speed: Optional[Speed] = None,
) -> list[Motor]:
    """Return motors that mount on `actuator` and meet the torque/speed envelope."""
    mounts = set(actuator.compatible_motor_mounts or [])
    if not mounts:
        return []
    return [
        m for m in motor_db
        if m.motor_mount_pattern in mounts
        and (min_torque is None or _gte(m.rated_torque, min_torque))
        and (min_speed is None or _gte(m.rated_speed, min_speed))
    ]


def compatible_drives(
    motor: Motor,
    drive_db: list[Drive],
) -> list[Drive]:
    """Return drives whose voltage/current envelope covers this motor."""
    return [
        d for d in drive_db
        if _voltage_covers(d.input_voltage, motor.rated_voltage)
        and _current_covers(d.rated_current, motor.rated_current)
        and (set(motor.encoder_feedback_support or []) & set(d.encoder_feedback_support or []))
    ]


def compatible_gearheads(
    motor: Motor,
    gearhead_db: list[Gearhead],
) -> list[Gearhead]:
    """Return gearheads whose input mount + shaft accept this motor."""
    return [
        g for g in gearhead_db
        if (motor.motor_mount_pattern in (g.input_motor_mount or []))
        and _shaft_compatible(motor.shaft_diameter, g.input_shaft_diameter)
    ]
```

Three pieces of plumbing this needs:

1. **The schema additions in Part 2.** Without `motor_mount_pattern` and
   `compatible_motor_mounts`, the relation queries are string-comparison
   hacks. Without typed `encoder_feedback_support` everywhere, the
   drive↔motor pairing falls apart.
2. **A backend endpoint.** `/api/v1/relations/motors-for-actuator?id=<uuid>`
   returns the candidate motor list. Same shape as `/api/v1/search` so
   the frontend table can render it without new code. Mirrored on
   `/relations/drives-for-motor` and `/relations/gearheads-for-motor`.
3. **Frontend "Combine" panel.** On the `/actuators` page, after a user
   configures a part number, surface a "Compatible motors" section that
   calls the relations endpoint. Click-through to the motor's detail page.

### Why not put the compatibility logic on the frontend

The frontend already has half of it (the `derive` callback per
template). Tempting to keep it there because it's where the user-facing
bits live. But:

- The compatibility query needs to fan out across **thousands of motor
  records** in DynamoDB. Loading the full motor table into the browser
  to filter client-side is wasteful at 2,041 motor records and broken
  at 50,000.
- The same compatibility logic should power admin tools (`./Quickstart
  query "motors that fit this actuator"`) and any future API consumers.
  Living it on the frontend forks it.
- Backend lives in Python where the Pydantic models *are* the schema.
  Compatibility logic that reads `motor.motor_mount_pattern` directly
  catches schema drift at type-check time.

### Why this is in scope for this initiative, not later

User said:

> "the key to this whole project is integration so its huge that we
> can procedurally generate a solution by transforming the part
> numbers, performance, and relations between devices."

The configurator alone (already shipped) is part 1 of "integration". A
synthesised part number that doesn't connect to a motor query is a
calculator, not an integration story. Without the backend relations
layer, the Actuator MVP is two unrelated screens (the configurator AND
the motor table) sitting side by side.

---

## Part 4 — Recommended migrations (in priority order)

All migrations are **additive** unless flagged BREAKING. Additive
changes don't need backfill — existing 57 Tolomatic linear_actuator
records keep validating, with the new fields null. BREAKING changes
need a one-shot data migration in `cli/admin.py`.

### Phase 1 — Cross-product field hygiene (additive — APPLIED 2026-05-08)

The additive subset shipped on this same `feat-actuators-mvp-20260508`
branch. `./Quickstart gen-types` regenerated `generated.ts` +
`generated_constants.ts`; `./Quickstart verify` green for Python +
backend + frontend.

| Change | Status | Files touched |
|---|---|---|
| Add `MotorMountPattern` literal to `common.py` | ✅ applied | `specodex/models/common.py` |
| Add `Motor.motor_mount_pattern: Optional[MotorMountPattern]` | ✅ applied | `specodex/models/motor.py` |
| Add `LinearActuator.compatible_motor_mounts: Optional[List[MotorMountPattern]]` | ✅ applied | `specodex/models/linear_actuator.py` |
| Add `Gearhead.input_motor_mount: Optional[List[MotorMountPattern]]` + `Gearhead.output_motor_mount: Optional[MotorMountPattern]` | ✅ applied | `specodex/models/gearhead.py` |
| Add `ElectricCylinder.motor_mount_pattern: Optional[MotorMountPattern]` | ✅ applied | `specodex/models/electric_cylinder.py` |
| Test fixture update: add `motor_mount_pattern="NEMA 23"` to `test_quality.py` motors | ✅ applied | `tests/unit/test_quality.py` |

### Phase 1.1 — BREAKING type harmonisation (deferred for sign-off)

| Change | Files touched | Migration |
|---|---|---|
| Widen `Motor.encoder_feedback_support: Optional[str]` → `Optional[List[str]]` with str-to-list coercer | `specodex/models/motor.py` | a coercer in the model handles the validation auto-wrap; existing data round-trips, no DB migration needed |
| Same for `ElectricCylinder.encoder_feedback_support` | `specodex/models/electric_cylinder.py` | same |
| **BREAKING:** unify `motor_type` → `MotorTechnology` literal | `motor.py`, `electric_cylinder.py`, `linear_actuator.py`, `common.py` | one-shot CLI: `./Quickstart admin harmonize-motor-type --stage dev --apply` |
| **BREAKING:** `ElectricCylinder.fieldbus: Optional[str]` → `Optional[List[CommunicationProtocol]]` | `electric_cylinder.py` | one-shot CLI: same harmonize subcommand |
| Add `Motor.fieldbus`, `LinearActuator.fieldbus` | `motor.py`, `linear_actuator.py` | none — additive |

These are flagged BREAKING because they re-shape the JSON envelope
that DynamoDB rows already carry. Existing rows with
`motor_type='ac servo'` (Motor) need to be rewritten to
`motor_type='ac_servo'` before the new literal accepts them. Without
the harmonize CLI, post-migration `client.list(Motor)` will silently
drop those rows in deserialisation. Don't apply until the CLI is
written and dry-run on dev shows the synonym table catches everything.

### Phase 2 — Backfill `motor_mount_pattern` from `frame_size`

Existing motor records have `frame_size: "NEMA 23"` (or "60mm", or
"60", or worse) but `motor_mount_pattern: None`. Write a one-shot CLI
that runs a normalizer over all motor records:

```python
def normalize_frame_to_mount(frame: str | None) -> Optional[MotorMountPattern]:
    if not frame:
        return None
    f = frame.strip().upper()
    if f.startswith("NEMA"):
        size = re.sub(r"\D", "", f)
        return f"NEMA {size}" if f"NEMA {size}" in get_args(MotorMountPattern) else None
    if f.startswith("IEC"):
        size = re.sub(r"\D", "", f)
        return f"IEC {size}" if f"IEC {size}" in get_args(MotorMountPattern) else None
    # "60", "60mm" → IEC 56/63/71/80? Need vendor-specific lookup; skip for MVP.
    return None
```

`./Quickstart admin backfill-motor-mounts --stage dev --apply`. Idempotent
(only writes when current value is None).

### Phase 3 — Device-relations module + API

`specodex/relations.py` (the three functions in Part 3) + three new
backend endpoints + a `RelationsPanel` component on `/actuators`.

Out of scope for the initial PR — ship Phase 1 first to unblock anything
else, write Phase 2 as a Late Night candidate, then Phase 3 lands as a
focused follow-up.

---

## Part 5 — Existing-records migration plan

Dev DB state (audited 2026-05-08):

- `linear_actuator`: 57 records, **all Tolomatic**. Lintech, Toyo,
  Parker not yet present. The fit-check above ingests them on a feature
  branch only.
- `motor`: 2,041 records, mixed manufacturers. **All `motor_mount_pattern`
  null after Phase 1** until Phase 2 backfill runs.
- `gearhead`: 252 records, mixed.
- `electric_cylinder`: 265 records.

Additive changes (Phase 1 non-BREAKING rows above) require zero data
work — Pydantic validates a record with a missing optional field as
`None` and `DynamoDBClient.list()` keeps reading them.

BREAKING changes (the two flagged in Phase 1) require a one-shot
migration **in dev only**, then validation against the dev record set
before promotion to prod. The CLI subcommand `harmonize-motor-type`
should:

1. List records of the affected type(s) in dev.
2. For each, re-validate against the new schema.
3. If `motor_type` is non-null and not in the new literal, look up via
   a hand-mapped synonym table (e.g. `"brushed dc" → "brushed_dc"`,
   `"servo_motor" → "ac_servo"` — needs human review of the 5-10
   ambiguous cases).
4. If `fieldbus` is a raw string, wrap in `[CommunicationProtocol(name=value)]`
   or split on `,` if multiple are listed.
5. Write back. Dry-run by default; `--apply` writes.

Dev → prod promotion uses the existing `./Quickstart admin promote`
flow with the standard quality gate.

### What about the current `feat-actuators-mvp-20260508` branch

The other agent's MVP branch has uncommitted work (`ActuatorPage.tsx`,
`configuratorTemplates.ts`, `categories.ts`, `CATAGORIES.md`). The
schema migrations in Part 4 should land on this same branch (or a
follow-up branched off it) so the MVP page can demonstrate the
relations layer immediately. Don't merge the MVP without at least
Phase 1 — the actuator page without a way to query compatible motors
delivers half the value Nick framed.

---

## Triggers — when to surface this doc

| Trigger | Surface |
|---|---|
| Editing `specodex/models/common.py` (`MotorMountPattern`, `MotorTechnology`, `ProductType`) | This doc — Part 4 migration table |
| Editing any `specodex/models/<linear_actuator|electric_cylinder|motor|drive|gearhead>.py` | This doc — Part 2 cross-product hygiene |
| User asks "which motor goes with this actuator", "compatible drives", "matching gearheads", "device pairing", "integration" | This doc — Part 3 relations layer |
| User asks "add a new vendor's actuator catalog" | This doc — Part 1 fit-check + the catalog-ingest skill |
| User asks "we need to migrate existing records" or "schema drift" | This doc — Part 5 |
| Adding a new `frame_size` value the normalizer doesn't know | This doc — extend `MotorMountPattern` literal |

---

## Open questions

- **Motor mount enumeration drift.** The starter `MotorMountPattern`
  list is hand-authored. As new vendors arrive (Maxon's MAX series,
  servo-frameless cans, custom drone motors), the list grows. At what
  point is a literal the wrong shape and we want a registry table?
  Probably around 50 entries. Today we're at ~25.
- **Gearhead-actuator coupling.** A gearhead between a motor and an
  actuator changes the effective input speed/torque. Should the
  relations layer compose these, or is "motor + gearhead → effective
  motor" a separate concept? Probably separate — `effective_motor()`
  helper in `specodex/relations.py` that produces a synthetic Motor
  with `rated_speed = motor.rated_speed / gearhead.gear_ratio` and
  `rated_torque = motor.rated_torque * gearhead.gear_ratio *
  gearhead.efficiency`.
- **Multi-vendor configurator templates.** The configurator currently
  lives in `app/frontend/src/types/configuratorTemplates.ts`. The doc
  says MVP keeps it frontend-only and migrates to a Pydantic field
  later. The trigger for migration is "a vendor ships two encodings of
  the same series." Worth re-reading `todo/CATAGORIES.md` Part 2
  before picking up this thread.
- **Cross-supercategory relations.** Right now the proposal is
  Motor↔Drive, Motor↔Gearhead, Actuator↔Motor. What about
  Drive↔Actuator (some actuators are drive-on-board)? Or
  Robot Arm↔Drive (the controller is part of the robot)? Defer until
  we have a record that genuinely needs the relation.
