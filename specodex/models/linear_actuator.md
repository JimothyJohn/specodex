# LinearActuator Model — Design Notes & Sources

Companion to `linear_actuator.py`. Documents the schema for **rodless**
linear-motion modules (slides, stages, ball-screw / belt-driven
carriages) — the half of the "linear actuator" market that does not fit
the `ElectricCylinder` rod-style profile.

## Scope

A guided-rail module where the **payload rides on a carriage** that
travels along the body of the unit. Drive mechanism varies (ball screw,
lead screw, belt, linear motor). May ship with an integrated motor or
motorless for customer-supplied servo.

Covered form factors (`type` field): linear slide, linear stage,
rodless screw, rodless belt, LM-guide actuator.

**Out of scope:**
- Rod-style electric cylinders that push/pull from a rod tip — model
  those as `electric_cylinder`. Force comes out of the end, payload is
  external.
- Pneumatic / hydraulic linear actuators (different physics).
- Standalone LM-guide rails without a drive (model as a generic linear
  guide component, not a complete actuator).
- Cartesian gantries / multi-axis robots (composite systems).

## Reference sources

Multi-vendor schemagen pass on 6 sources (3 PDFs + 3 Tolomatic spec-table
images). Tolomatic-only would have biased the schema toward one
catalog's quirks; the 3 outside vendors round it out.

| Source | Type | Vendor | Relevance |
|---|---|---|---|
| `rexroth-ckk-ckr.pdf` | PDF, 16 pages | Bosch Rexroth | Lead-screw pitch, rotor inertia (Jsd), CKK/CKR ball-screw module specs. |
| `smc-lef.pdf` | PDF, 222 pages (38 spec) | SMC | Performance, electrical, environmental (IP, temp, humidity, cleanroom), motor variants. |
| `thk-kr.pdf` | PDF, 6 pages | THK | Rated thrust (`max_push_force`), backlash, static allowable moment (pitching/yawing/rolling). |
| `3600-4176_10_B3_cat-21.png` | image | Tolomatic | B3 belt-drive: linear speed, max thrust, repeatability, backlash, base inertia. |
| `3600-4231_01_GSA-ST-HT_cat-6.png` | image | Tolomatic | GSA slide: stroke, lead pitch, dynamic/static load, screw diameter. |
| `8300-4000_16_MXE_cat-21.png` | image | Tolomatic | MXE screw-driven: acceleration, rated load (kg), encoder type, rated electricals. |

Vendors attempted but blocked: Festo (403 on product page), Parker
(Akamai 403 on CDN), IAI (registration wall). Add later if the schema
needs another perspective.

Schemagen dry-run cost: ~16k input + 2.9k output tokens (~$0.005).

## Design decisions

### Two product types: `linear_actuator` vs `electric_cylinder`

Kept as siblings (not unified under a `LinearMotion` parent) because the
mechanical model differs:

- **Electric cylinder**: integrated motor pushes a rod; payload is
  external; force is the headline spec. Selection profile is force +
  stroke + voltage.
- **Linear actuator (rodless)**: payload rides the carriage; bearing
  load + moment ratings dominate selection. Stage/slide products are
  often sold motorless.

A unified type would force every consumer of either form factor to
reason about fields that don't apply (e.g., `max_pull_force` for a
slide, `static_allowable_moment_*` for an electric cylinder). Two types
also keeps the TS allowlist edits localised when adding a third linear
form factor later (e.g., voice-coil actuators).

### `type` is form factor, `actuation_mechanism` is drive

The schemagen dry-run conflated these into a single `Literal['ball_screw', 'belt_drive']`. They're orthogonal:

- `type`: physical envelope — slide, stage, rodless screw, rodless belt,
  LM-guide. What it looks like / how it mounts.
- `actuation_mechanism`: drive — `ball_screw | lead_screw | belt |
  linear_motor`. What moves the carriage.

A "rodless screw" carriage might be lead-screw or ball-screw driven —
both axes carry information.

### `motor_type` includes `motorless`

Rexroth, THK, and SMC all sell motorless variants. The schemagen
proposal had `Literal['step_motor', 'servo_motor']`, which would silently
drop the motor_type for any motorless row. Added `'motorless'` so the
field is preserved and queryable.

### `ip_rating` uses the `IpRating` shared validator

The schemagen proposal had `int`. Repo convention is the
`common.IpRating` Annotated type, which coerces "IP54", `{"value": 54}`,
and stray strings to `54`. Bare `int` would drop those rows on
validation.

### Static allowable moments as separate fields

`static_allowable_moment_pitching`, `_yawing`, `_rolling` are kept as
three `Torque` fields rather than a single dict or list. Vendor
datasheets always present them in three columns; consumers (filter
chips, sort) need to address them individually.

### `rotor_inertia` for a rodless module

Tolomatic and Rexroth both publish "base actuator inertia" (the
reflected inertia at the motor side from the carriage + screw + bearings).
Reused the existing `rotor_inertia` name even though there's no rotor
in a belt-drive — stays consistent with `Motor` and `Gearhead` field
naming so cross-type queries work.

### Why `rated_voltage` is `VoltageRange`, not `Voltage`

Drive-on-board variants typically publish a window ("24-48 VDC"). Same
rationale as `ElectricCylinder.rated_voltage`.

## Known gaps

- **Linear motor specs**: thrust constant, force ripple, magnet pitch
  aren't first-class. Add when a linear-motor-heavy vendor (e.g.,
  Tecnotion) is ingested.
- **Duty cycle / continuous force**: Tolomatic publishes both
  `max_push_force` and a continuous rating; only the peak is captured.
  Worth revisiting if filter UI needs continuous force as a column.
- **Cable carrier / bend radius**: relevant for high-cycle stages, not
  modelled.

## Fit-check pass — Lintech / Toyo (2026-05-08)

Closes the gap noted in the original Reference sources block: Parker
was Akamai-blocked then and remains blocked now (403 with desktop UA,
both URLs the user provided). Lintech and Toyo were never sourced on
the original schemagen pass; this fit-check ingests three of their
catalogs (Lintech 200, Lintech 150, Toyo Y-series) through
`page_finder` + the existing `linear_actuator` schema and reports
field coverage.

**Outcome:** schema fits cleanly. **Zero "extras"** — Gemini did not
emit any fields outside the registered schema. No new fields needed
in `linear_actuator.py` from this pass.

| Source | Variants extracted | Fields populated / 45 | Notes |
|---|---:|---:|---|
| Lintech 200 (catalog 2020-09) | 28 | 14 | Page-finder picked 13/28 spec pages. Part numbers like `200607-WC0` cleanly mapped to `series='200 Series', stroke=7in`. |
| Lintech 150 (catalog 2020-09) | 1 | 15 | Single-page summary; Gemini conflated variants. Per-variant extraction would need finer page-band targeting. |
| Toyo Y-series | 5 | 18 | Best coverage of the three. Japanese catalogs are the most schema-friendly: speed, force, repeatability, lead pitch, screw diameter all populate. |

**Coverage by field** (% of fixtures with ≥1 populated value):

- **100%**: `manufacturer`, `series`, `type`, `motor_type`, `part_number`, `product_name`, `product_family`, `datasheet_url`
- **67%**: `actuation_mechanism`, `screw_diameter`, `encoder_feedback_support`, `cleanroom_class`, `stroke`
- **33%**: `lead_screw_pitch`, `max_linear_speed`, `max_push_force`, `max_work_load`, `positioning_repeatability`, `rated_power`, `weight`, `operating_temp`
- **0% across all three fixtures**: `backlash`, `dynamic_load_rating`, `static_load_rating`, `holding_force`, `static_allowable_moment_*` (pitching/yawing/rolling), `ip_rating`, `rotor_inertia`, `max_acceleration`, `operating_humidity_range`, `rated_voltage`, `rated_current`, `peak_current`

The 0% list is **vendor mix-driven, not schema-broken**. Lintech and
Toyo publish their catalogs around two profiles:

1. **Motorless mechanical units** (Lintech) — no `rated_voltage` /
   `rated_current` / `peak_current` because the customer brings the
   motor. `motor_type='motorless'` carries this correctly.
2. **Compact integrated drive units** (Toyo) — publish electrical
   specs but not bearing-load ratings or pitching/yawing moments
   (those are deeper engineering tables, often on dimensional sheets
   `page_finder` doesn't classify as "spec pages").

Adding Tolomatic / Rexroth-style heavy-industrial catalogs (which is
what the existing schemagen sources covered) tends to populate the
0% block; Lintech/Toyo represent the lighter end of the same product
type. Keep both in the catalog mix; don't drop the 0% fields.

**Parker still blocked.** Both URLs the user provided returned 403
through Akamai (also tried with desktop UA + Referer header). Same
fingerprint as the original schemagen pass. Treat as deferred until
either (a) Parker exposes the catalogs through a non-Akamai surface,
or (b) somebody rehosts the PDFs. The hand-authored Parker HD
configurator template ships in
`app/frontend/src/types/configuratorTemplates.ts` based on the public
ordering page; it has no DB-backed validation.

**Real-world part-number formats** observed in this pass (used to
rewrite the configurator templates):

- Lintech 200: `200<frame><travel>-WC<accessory>` (e.g. `200607-WC0`,
  not the `200-L-072-08-N23` shape the original hand-authored
  template guessed). Catalog ordering page is the source of truth.
- Toyo Y-series: `<series>-<subtype>` (e.g. `Y43-L2`).
- Tolomatic TRS: `TRS<frame>-BNM<lead>` (e.g. `TRS165-BNM10`) —
  this is the format that actually populates dev DB.

## Fields

See `linear_actuator.py`. Current set:

- Identity: `type`, `series`
- Motion: `stroke`, `max_work_load`, `max_push_force`, `holding_force`,
  `dynamic_load_rating`, `static_load_rating`, `max_linear_speed`,
  `max_acceleration`, `positioning_repeatability`, `backlash`
- Drive: `actuation_mechanism`, `lead_screw_pitch`, `screw_diameter`,
  `static_allowable_moment_pitching`, `static_allowable_moment_yawing`,
  `static_allowable_moment_rolling`, `rotor_inertia`
- Motor (optional): `motor_type`, `encoder_feedback_support`,
  `rated_voltage`, `rated_current`, `peak_current`, `rated_power`
- Environmental: `ip_rating`, `operating_temp`,
  `operating_humidity_range`, `cleanroom_class`
