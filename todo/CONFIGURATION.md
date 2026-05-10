# CONFIGURATION — rethinking the configurator architecture

> Companion to `todo/CATAGORIES.md`. CATAGORIES.md is the *MVP* — what
> shipped on `feat-actuators-mvp-20260508`. This doc is the *rethink*
> after building the MVP and learning what scales and what doesn't.

The MVP works. It also has six structural limits that will hurt
within the next ten templates. This doc names them and proposes the
next architecture so we don't paint ourselves into a corner.

## Status

Not in flight. Discovery + design only. Pick up after the MVP soaks
for a couple weeks and we have ≥ 3 user-visible feedback signals
("the configurator missed my Lintech 100 family"; "I need to verify
that motor against my drive"; "the speed estimate is wildly off
because my application is short-stroke").

## What the MVP got right (keep these)

1. **Two-level taxonomy** (supercategory → product_type → family).
   Models how users actually shop: "I need linear motion" → "rod-style
   or rodless" → "which family".
2. **Bidirectional sync** (synthesise + parse, with round-trip tests
   pinning the contract). Click a record → configurator pre-fills.
   This is the integration win the user named, and it generalises
   beyond actuators (drives, motors, anything with encoded SKUs).
3. **`(manufacturer, series)` lookup with aliases.** Real records
   have heterogeneous spellings of the family field; alias lists
   absorb the variance without breaking the match.
4. **Frontend-only fixtures for MVP storage.** No DB migration
   required, no LLM call required, fast iteration. Right scope for
   the MVP.
5. **Schema fit-check before claiming vendor coverage.** Found that
   the original hand-authored Lintech template didn't match a single
   real Lintech part number — caught before any user saw the demo.
6. **Word-boundary motor matching** (after the smoke-test caught
   "Ø230" matching "NEMA 23"). Heuristics need to fail loudly, not
   silently.

## What the MVP got wrong (or barely got away with)

### 1. Templates as imperative TypeScript fixtures don't scale

Each template is a TS object with `segments: [...]`, a hand-written
`parseRegex`, and a hand-written `derive(choices)` function. Six
templates is fine. Sixty would be a maintenance burden, and the
artifact is dev-coupled — a vendor can't write one without learning
TypeScript and React build tools.

The right shape is **declarative configuration** — YAML or JSON,
authored once per family, parsed at build time into both the
TypeScript objects the UI needs AND a Python equivalent the backend
can use.

```
specodex/configurators/
├── tolomatic/
│   ├── trs.yaml
│   ├── bcs.yaml
│   └── erd.yaml
├── lintech/
│   ├── 200_series.yaml
│   └── 150_series.yaml
└── parker/
    └── hd_series.yaml
```

`./Quickstart gen-types` extends to also generate
`app/frontend/src/types/generatedConfigurators.ts` from these YAMLs,
the same way it currently generates Pydantic-derived TS.

### 2. The regex-twin parser is fragile

Today: `template: "TRS{frame}-{drive}{lead}"` PLUS
`parseRegex: /^TRS(?<frame>\d+)-(?<drive>BNM|BNL|...)(?<lead>\d+)/`.
Two pieces in lockstep. Editing the template without updating the
regex (or vice versa) is a silent break — round-trip tests catch it
in CI but only because we pinned every legal combination.

**Use a grammar.** A single spec that compiles to BOTH the encoder
and the decoder. Two viable approaches:

- **PEG grammar** (via `peggy` or hand-rolled). Each segment is a
  rule; the grammar emits a parser, and the same rules drive the
  template formatter.
- **Tagged template DSL.** Less power than PEG but enough for the
  vendor part numbers we've seen. Looks like:

  ```yaml
  pattern:
    - lit: "TRS"
    - field: frame
      kind: enum
      options: [165, 235, 305]
    - lit: "-"
    - field: drive
      kind: enum
      options:
        BNM: { lead_units: mm,  rpm: 3000, label: "Ball, Normal, Metric" }
        BNL: { lead_units: in,  rpm: 3000, label: "Ball, Normal, English" }
        BSM: { lead_units: mm,  rpm: 3000, label: "Ball, Short, Metric" }
        BSL: { lead_units: in,  rpm: 3000, label: "Ball, Short, English" }
    - field: lead
      kind: int
      width: 2
      pad: zero
    - opt:
        - lit: "-"
        - field: trailing
          kind: rest
  ```

  The DSL has primitives: `lit`, `field`, `opt`, `alt`, `rep`. Enough
  to express every part number we've encountered. From this single
  spec the codegen produces:
  - the encoder (string template)
  - the decoder (regex or recursive descent)
  - the TypeScript `ChoiceMap` type
  - the form schema for the UI (radio/select/range)
  - the markdown ADR (auto-generated, edited by hand)

### 3. Performance derivation is shallow and ad-hoc

Today each template has a `derive(choices)` function that returns
`{lead_mm, max_speed_mm_s, suggested_motor_frame}`. Tolomatic TRS,
BCS, and ERD all duplicate the same lead-from-drive-suffix logic
because there's no shared library.

**Replace with a derivation graph.** Each segment's option carries
metadata (e.g. drive=BNM has `lead_units: mm, rpm_class: high`).
A small expression language composes those into derived facts:

```yaml
derives:
  - field: lead_mm
    expr: "lead * (drive.lead_units == 'in' ? 2.54 : 1)"
  - field: max_speed_mm_s
    expr: "lead_mm * assumed_rpm / 60"
    depends: [lead_mm, assumed_rpm]
  - field: assumed_rpm
    expr: "drive.kind == 'lead_screw' ? 1800 : 3000"
  - field: continuous_force_n
    expr: "lookup('frame_size_force', frame)"  # vendor table
```

Two important properties:

- **Composable.** "Speed at rated load" depends on "lead_mm" and
  "rpm class"; both are independently derived. A single change
  (e.g. updating the vendor's rated RPM) ripples cleanly.
- **Vendor-overridable.** Most actuator families share the same
  `lead_mm × rpm / 60` formula. Vendor-specific quirks (Tolomatic
  uses a different efficiency factor; SMC publishes "speed at
  rated load" not "max speed") get expressed as overrides on the
  base formula, not bespoke `derive()` functions.

### 4. Cross-device matching is heuristic, not strict

Today: word-boundary substring match on `frame_size`. Catches
"NEMA 23" → "Size 23"; misses everything else. No check that the
motor's voltage range fits the drive's supply, that the motor's
shaft diameter matches the actuator's coupling bore, or that the
motor's torque clears the application's continuous force demand.

The existing `app/frontend/src/utils/compat.ts` already implements
strict checks (ok/partial/fail) for drive↔motor↔gearhead pairs.
**Extend it to actuator↔motor and actuator↔drive.**

| Pair | Checks |
|---|---|
| actuator + motor | shaft_diameter fit · mounting_pattern match · voltage range · continuous force ≥ demand · inertia ratio (motor:load) · max RPM ≥ derived spec |
| actuator + drive | voltage compatibility · current capacity · control mode (servo/stepper) · feedback type · brake support |
| motor + drive | (already implemented in compat.ts) |
| system: actuator + motor + drive | all-pairs ok + duty cycle thermals |

**Build a "system" view** — the existing `BuildTray` infrastructure
already concatenates compatible drive/motor/gearhead picks; extend
to a 4-slot tray (actuator + motor + drive + power supply).

### 5. No vendor-authoring path

A developer with a vendor's catalog page authors a configurator
template by hand. Takes ~1 hour per family. **Auto-generate
configurator templates the same way `cli/schemagen.py` proposes
Pydantic models.**

```
./Quickstart configgen <pdf> --type linear_actuator --vendor Lintech --family "200 Series"
```

Internally:
1. `page_finder` narrows to the "ordering information" + spec pages.
2. Gemini extracts the part-number grammar (treat the "How to
   order" table as the source of truth — vendors usually publish
   it explicitly).
3. Emit YAML to `specodex/configurators/lintech/200_series.yaml`
   plus a markdown ADR like the existing `<type>.md` reasoning docs.
4. Round-trip the proposal: synthesise → parse → check against
   sample part numbers from the same catalog. Reject the proposal
   if round-trip fails on > 5% of samples.

Cost per family: ~$0.01–0.05 (one Gemini call). Saves the hand-
authoring hour and removes the dev-only ergonomics.

### 6. No reverse cross-vendor search

User says: "I need 1m travel, 50 kg load, ball-screw, NEMA 23 mount,
under $5k". Today they have to pick a vendor first. The configurator
is family-first; should be **need-first**.

Build `/actuators/design`: a form that takes the application
constraints and surfaces every vendor family whose configuration
space contains a feasible point. Click a candidate → configurator
opens pre-filled to the closest legal configuration.

This is the inverse of synthesise: instead of `(template, choices) →
part_number`, it's `(application_spec) → list[(template, candidate
choices, fit score)]`. Surfaces cross-vendor comparison naturally.

### 7. No version awareness

When a vendor revises their catalog and changes the encoding, every
record ingested under the old encoding silently fails to parse.
There's no signal that an old template no longer matches today's
records.

Templates carry `valid_from` / `valid_to` dates. Records carry an
ingestion date (already in DB via `created_at` if we add it).
Lookup combines both; a stale template gets flagged in the UI as
"this part number was authored under a deprecated encoding —
verify with the vendor".

### 8. Templates are frontend-only

Backend can't:

- Validate part numbers passed to `/api/v1/search?part_number=...`
- Auto-suggest a part number during catalog ingest when Gemini
  emits a partial spec
- Surface "this catalogued record's part number is malformed
  per its declared family"

Moving templates to YAML at the repo root with a Python loader
(parallel to the existing TS loader, both reading the same source)
unlocks all three.

## Migration path (MVP → full)

Six steps, none of them blocking. Order matters: each step
preserves the previous step's behavior end-to-end.

### Phase 1 — Lift templates to YAML (mechanical refactor)

- Move the 6 MVP templates from `configuratorTemplates.ts` to
  `specodex/configurators/<vendor>/<family>.yaml`.
- Write a generator at `scripts/gen_configurators.py` that emits
  `app/frontend/src/types/generatedConfigurators.ts` (twin of the
  existing `gen_types.py`).
- Wire into `./Quickstart gen-types` and the `test-codegen` CI gate.
- The TS shape stays identical — same `ConfiguratorTemplate`
  interface, same `synthesise` / `parsePartNumber` / `findTemplate`
  exports.

Backwards-compatible. CI catches drift. Templates can now be
edited by anyone who can edit YAML.

### Phase 2 — Replace regex-twin with declarative grammar

- Define the grammar DSL (`lit`, `field`, `opt`, `alt`, `rep`).
- Write the codegen: YAML grammar → encoder + decoder.
- Migrate the 6 templates one at a time, with round-trip CI gating
  each migration.

After this phase, `parseRegex` is no longer hand-authored — it's
emitted from the same source as `template`.

### Phase 3 — Performance derivation graph

- Replace per-template `derive()` functions with a small expression
  language (a `?:` ternary + `lookup()` calls is enough for the
  patterns we have).
- Move common formulas (lead_mm, max_speed_mm_s, force_n) into a
  shared library; vendor-specific quirks override.
- Surface the derivation chain in the UI (click a derived value →
  see the formula and the inputs that produced it).

### Phase 4 — `./Quickstart configgen`

- Mirror `cli/schemagen.py` for configurator templates.
- Pre-filter PDFs through `page_finder` (spec + ordering pages).
- Round-trip validation on the proposal: reject if synth/parse
  fails on > 5% of sample part numbers from the same catalog.

### Phase 5 — Strict cross-device compat

- Extend `app/frontend/src/utils/compat.ts` with `actuator+motor`
  and `actuator+drive` checks.
- Replace the heuristic `MotorSuggestions` panel with a real
  strict-status surface (ok/partial/fail per check) — same
  contract `BuildTray` already uses.
- Add `actuator` to `BUILD_SLOTS`. The 4-slot system tray
  (actuator + motor + drive + power supply) becomes the buildable
  unit.

### Phase 6 — Need-first design surface

- `/actuators/design`: form-driven application input
  (travel, payload, speed, mount, budget).
- Solver: enumerate every template's configuration space, score
  feasible candidates by application fit + price + lead time.
- Click candidate → configurator opens pre-filled to the closest
  legal configuration.

## Open questions

1. **Grammar DSL: bespoke or borrow?** PEG.js / peggy is mature but
   adds a build dependency. A bespoke 200-line DSL covers everything
   we've seen. Lean toward bespoke for now; revisit if we hit
   context-sensitive parts (e.g. "if drive=BSL then lead is in
   tenths-of-an-inch" — the *valid options* depend on a prior
   choice).

2. **Where does the expression language for `derives` come from?**
   Options: write a 50-line interpreter (cheap, tailored, no deps),
   embed `expr-eval` (JS expr lib, small), or use CEL via WASM
   (overkill). MVP: bespoke. CEL if we ever need WASM-backed
   sandboxing.

3. **Do configurator templates belong with Pydantic models?** Same
   directory, different filename: `specodex/models/linear_actuator.py`
   plus `specodex/models/linear_actuator/<vendor>/<family>.yaml`?
   Or sibling: `specodex/configurators/<vendor>/<family>.yaml`
   referenced by a `product_type` field?
   Sibling is cleaner — configurator data is vendor-keyed, not
   product-type-keyed (you can have a configurator for any product
   type once we have non-actuator examples).

4. **What's the test pyramid for this?** The MVP has unit tests
   per template + a Playwright smoke. After the lift to YAML +
   grammar:
   - Unit: synth/parse/derive per template
   - Property-based (fast-check?): for every YAML-grammar legal
     combination, round-trip preserves identity
   - Integration: the configgen pipeline against fixture PDFs
   - Smoke: still Playwright, end-to-end click flows
   Property-based is the new addition that catches what unit tests
   miss when we have 60 templates.

5. **What's the rollback story?** If a YAML template is broken,
   the codegen step fails CI; we don't ship a broken template. If a
   `configgen` proposal is wrong, the round-trip validation catches
   it before commit. If a template silently misparses an old
   record at runtime (because of catalog version drift) — the UI
   shows the "deprecated encoding" badge and surfaces a verify-
   with-vendor CTA.

## Triggers — when to surface this doc

| Trigger | Surface |
|---|---|
| Adding a 7th configurator template (MVP+1) | This doc — Phase 1 (lift to YAML) gets cheaper as templates accumulate |
| User asks "does this motor fit this actuator?" beyond frame substring | Phase 5 (strict compat) |
| Catalog ingest produces records with malformed part numbers per their declared family | Phase 7 (version awareness — not yet detailed) |
| User asks "what actuator families fit my application?" / cross-vendor design search | Phase 6 (need-first surface) |
| Developer touches `app/frontend/src/types/configuratorTemplates.ts` | This doc — first read it, then act |

## What NOT to do

A non-exhaustive list of detours that look attractive but aren't:

- **Don't ingest random vendor PDFs to find more configurators.**
  The catalog ingest pipeline is for spec records, not
  configurators. `configgen` is the right tool, against the
  vendor's "ordering information" page specifically.
- **Don't try to support every configurator option in the UI.**
  Form/fit/function: stroke, payload, drive, mount. Trailing
  accessory codes (cable carriers, wiper kits, etc.) stay as
  free-text "trailing" segments forever.
- **Don't validate against vendor catalog systems online.** Live
  validation against vendor APIs would be valuable but each
  vendor is bespoke; the configurator is starting-point-for-quote,
  not order-confirmation.
- **Don't move configurator data into DynamoDB.** Templates are
  authored, slow-changing artifacts. Source-of-truth is git.
  DynamoDB stores the records that match the templates.
- **Don't unify configurators with Pydantic models prematurely.**
  Pydantic models describe the *extracted* spec shape; configurators
  describe the *vendor's encoding*. Same product type can have
  multiple configurators (one per family); same configurator only
  ever applies to one product type. The relationship is
  product_type ↔ N configurators, not 1↔1.
