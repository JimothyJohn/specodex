# DIMENSIONS — physical envelope for every product, exact interface geometry for mating parts

> **Status:** 🚧 in progress. Phase 1 (typed envelope + migration + columns) in PR; Phase 2 (envelope backfill) next; Phase 3 (interfaces) 📐 planned.

Nick, 2026-11-24: *"I want to have an idea of the physical form factor of what I'm
ordering. Doesn't have to be 100% accurate since these aren't drawings but I should
have an idea of how big and heavy it is, especially in the largest dimension.
Interface dimensions must be perfect, shaft, collet, threads, etc. but footprint is
not as critical."*

Two different accuracy contracts, so two different data paths:

| | Envelope (L×W×H, largest dim, weight) | Interface (shaft, collet/bore, threads, pilot, bolt circle) |
|---|---|---|
| Accuracy | approximate is fine | exact or nothing |
| Source | outline drawing / spec table, one Gemini read | standard tables + drawing read, cross-checked |
| On doubt | store best read | **withhold**; UI deep-links the drawing page |

Decisions from the 2026-11-24 interview: envelope on every type first, interfaces
later; typed per-axis Length + migrate existing rows; sortable/filterable "Largest
Dimension" + "Weight" columns plus the detail modal; interfaces are cross-check-or-withhold.

## Baseline (products-dev, 2026-11-24)

| type | rows | any L/W/H | weight | interface today |
|---|---|---|---|---|
| motor | 13,140 | 35% (10% all three) | 32% | shaft Ø 13%, `mounting_flange` 0 |
| drive | 13,295 | 23% | 28% | — |
| gearhead | 9,434 | 4% | 13% | output shaft Ø 6%, input flange 0 |
| robot_arm | 505 | 2% | 27% | no tool-flange field |
| electric_cylinder | 276 | 36% | 32% | no rod-end thread field |
| inductive_sensor | 1,815 | ~0 | ~0 | housing length 66%, thread in `housing_size` |

40% of stored dimensions were inches; 653 rows held `{"unit": "mm"}` and nothing else.

## Phase 1 — typed envelope (PR, 2026-11-24)

- `ProductBase.dimensions` → per-axis `Length` (`{value, unit}`), normalised to mm in
  the model (`product.py:_coerce_envelope_length`); legacy `{floats…, unit}` lifts in.
  Non-positive / non-finite / > 20 m / wrong-family → axis None, never a dead row.
- `weight` normalised to kg (lb/lbs/g/oz/Kg); > 50 t or ≤ 0 → None.
- `max_dimension` derived on every validation (never LLM-asked, not quality-scored).
- `./Quickstart admin normalize-envelope --stage dev [--apply]`: conditional
  `UpdateItem` of only those three attributes; refuses prod (prod gets rows via
  `admin promote`). Dry run on dev: 9,502 of 37,263 rows to update, 653 empty stubs dropped.
- Frontend: `envelopeAttributes()` gives every curated type Largest Dimension + Weight,
  authored into `COLUMN_ORDER`, visible once ≥ 25% of loaded rows carry them.

**Rollback note:** pre-Phase-1 Python (`Dimensions` with float fields) fails to
validate a migrated row. Run the migration only after the PR is on `dev`; rebase
long-lived worktrees that read `products-dev` through the models.

## Phase 2 — envelope backfill (next PR)

`./Quickstart envelope-extract` modelled on `mounting-extract`: for rows missing
`dimensions` or `weight`, group by `datasheet_url`, pick outline/dimension pages with
`page_finder.find_drawing_pages_scored` (capped), one Gemini call per page batch
returning `{part_number | frame_designator, length, width, height, weight, page}`,
match back by part number then frame/family, fill only empty fields (enrich-only),
`--apply` dev/staging only. Also: make the main extraction prompt ask for the
outline envelope so new ingests land populated. Report coverage before/after per type.

Cost guard: estimate distinct datasheets × pages before running; `--limit` and
`--manufacturer` flags; dry-run prints the plan and projected Gemini calls.

## Phase 3 — interface geometry, cross-check or withhold (📐 planned)

Fields (new, per type, all exact-mm `Length` unless noted):

- **Motor:** `shaft_diameter` (exists), `shaft_length` (exists), keyway / flat
  (`shaft_modification`, exists), `mounting_flange` (bolt circle, pilot Ø/depth, bolt
  hole Ø — exists, 0 rows), **new** `mounting_thread` (structured thread spec).
- **Gearhead:** **new** input clamp/collet bore (`input_shaft_diameter` is "max
  accepted", not the bore set), `output_shaft_diameter` (exists), output keyway,
  `input_mounting_flange` (exists, 0 rows), output flange.
- **Electric cylinder / linear actuator:** rod-end thread, mounting interface.
- **Robot arm:** tool flange (ISO 9409-1 designation, e.g. `50-4-M6`).
- **Inductive sensor:** barrel thread already in `housing_size`; promote to the
  structured thread type.

Shared building block: a `ThreadSpec` model (`M12x1.0`, `M6x1.0`, `1/4-20 UNC`,
`G1/4`) with a deterministic parser + property test — never free text.

Cross-check-or-withhold rule:

1. **Standard frame known** (NEMA 17/23/34/42, IEC B5/B14, ISO 9409 flange): value
   comes from a checked-in standards table. The drawing read must agree within
   tolerance (0.05 mm) or the row is flagged for review, value withheld.
2. **Proprietary frame:** two independent reads must agree — the drawing-page
   extraction and the spec-table extraction (or two drawing pages / a second pass).
   Agreement → store with `source_page`. Disagreement or single source → withhold.
3. Withheld interface values render in the UI as "see drawing p.N" with the
   `datasheet_url#page=N` deep link, never as a number.
4. Every stored interface value carries provenance (`source`: `standard` /
   `cross_checked`, page numbers).

Open question for Nick (answer in this doc or TODO.md): is the 0.05 mm agreement
tolerance right, or should agreement be exact-to-printed-precision?
