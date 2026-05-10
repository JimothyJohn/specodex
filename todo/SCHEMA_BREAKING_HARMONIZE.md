# SCHEMA Phase 1.1 — BREAKING type harmonisation

**Status:** 🔴 Designed; needs explicit sign-off before applying. The
rest of `todo/SCHEMA.md` (Phases 1, 2, 3, 4) all shipped during the
2026-05-09 / 10 sprints — this is the only piece deferred for the
sign-off conversation about whether to break existing DB rows.

The original `todo/SCHEMA.md` (623 lines covering the fit-check + 4
shipped phases + this one) was retired post-sprint. Recover the
full design rationale via `git log --diff-filter=D --follow --
todo/SCHEMA.md` if needed.

## Two open breaking changes

### 1. Unify `motor_type` → `MotorTechnology` literal

Three product types use three different vocabularies for the same
concept:

| Model | Today's type |
|---|---|
| `Motor.type` | `Literal["brushless dc", "brushed dc", "ac induction", "ac synchronous", "ac servo", "permanent magnet", "hybrid"]` |
| `ElectricCylinder.motor_type` | `Optional[str]` (free-form) |
| `LinearActuator.motor_type` | `Optional[Literal["step_motor", "servo_motor", "motorless"]]` |

None of the three overlap. A user filtering by motor type can't
write a single query that covers all three product types.

**Proposed unified literal** (in `specodex/models/common.py`):

```python
MotorTechnology = Literal[
    "brushless_dc",      # BLDC servo, most common modern type
    "brushed_dc",
    "ac_servo",
    "ac_induction",
    "ac_synchronous",
    "permanent_magnet",
    "stepper",
    "hybrid_stepper",
    "linear_motor",
    "motorless",         # actuator ships without a motor (customer-supplied)
]
```

**Migration:**
- `Motor.type` → `MotorTechnology`
- `ElectricCylinder.motor_type` → `Optional[MotorTechnology]`
- `LinearActuator.motor_type` → `Optional[MotorTechnology]`
- Existing records: a one-shot CLI `./Quickstart admin -- harmonize-motor-type --stage dev [--apply]` (dry-run default) maps old strings to the new vocabulary. Synonym table:

| Old value (any product) | New `MotorTechnology` |
|---|---|
| `"brushless dc"` | `"brushless_dc"` |
| `"brushed dc"` | `"brushed_dc"` |
| `"ac servo"` | `"ac_servo"` |
| `"ac induction"` | `"ac_induction"` |
| `"ac synchronous"` | `"ac_synchronous"` |
| `"permanent magnet"` | `"permanent_magnet"` |
| `"hybrid"` | `"hybrid_stepper"` |
| `"step_motor"` (LinearActuator) | `"stepper"` |
| `"servo_motor"` (LinearActuator) | `"ac_servo"` (default — refine per-vendor if known) |
| `"motorless"` | `"motorless"` |

### 2. `ElectricCylinder.fieldbus` → `Optional[List[CommunicationProtocol]]`

| Model | Today's type |
|---|---|
| `Drive.fieldbus` | `Optional[List[CommunicationProtocol]]` ✓ |
| `ElectricCylinder.fieldbus` | `Optional[str]` (raw string) |
| `Motor.fieldbus` | not present |
| `LinearActuator.fieldbus` | not present |

`Drive` already uses the structured `CommunicationProtocol` model
in `specodex/models/communication_protocol.py`. Promote
`ElectricCylinder.fieldbus` to the same shape; add the field to
`Motor` and `LinearActuator` (optional, nulls fine for catalogs
without on-board comm).

**Migration:** same `harmonize` CLI, separate sub-action.
`ElectricCylinder.fieldbus` strings get parsed via
`CommunicationProtocol`'s constructor (existing logic accepts
free-form strings like "ethercat" / "EtherCAT" / "ECAT").

## Why these are flagged BREAKING

Both reshape the JSON envelope DynamoDB rows already carry. Existing
rows with `motor_type='ac servo'` (Motor) need to be rewritten to
`motor_type='ac_servo'` before the new literal accepts them. Without
the harmonize CLI, post-migration `client.list(Motor)` will silently
drop those rows in deserialisation.

**Don't apply until** the harmonize CLI is written, dry-run on dev
shows the synonym table catches every existing value, and Nick
signs off on breaking the JSON envelope.

## What's NOT in this phase (already shipped or obsolete)

- `encoder_feedback_support` shape unification — was in the original
  Phase 1.1 plan, but DOUBLE_TAP (PR #91) replaced the
  `Optional[str]` / `Optional[List[str]]` field with the structured
  `EncoderFeedback` model entirely. The shape question is moot.
- `frame_size` / `motor_mount_pattern` — shipped via SCHEMA Phase 1
  (PR #87) and Phase 2 (PR #117 — backfill CLI; execution is the
  remaining operator action).

## Triggers — surface this doc when

- User asks "unify motor_type", "harmonize motor types", "BREAKING
  schema migration", "MotorTechnology literal".
- Touching `Motor.type`, `ElectricCylinder.motor_type`,
  `LinearActuator.motor_type`, or `ElectricCylinder.fieldbus`
  fields.
- Adding a new product type that has a `motor_type` field —
  consider whether to slot in here vs. leave the divergence.
