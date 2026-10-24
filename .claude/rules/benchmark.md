---
paths:
  - "tests/benchmark/**"
  - "cli/bench*.py"
  - "specodex/page_finder.py"
---

# Benchmarking the ingress pipeline

The one-line rules in `CLAUDE.md` always apply. This loads when you touch the bench harness, its fixtures, or the page finder.

`./Quickstart bench` measures the ingress pipeline against control datasheets with known ground truth.

## What it measures

| Metric | How |
|--------|-----|
| **Redundancy** | Pages skipped by text heuristic / total pages. Lower = more sent to the LLM than necessary. |
| **Speed** | Wall-clock ms for page-finding + LLM extraction per fixture. |
| **Cost** | Input/output tokens × configurable $/1M-token pricing. |
| **Quality** | Per-field precision/recall vs ground truth (5% tolerance on numerics, unit-aware). |

## Fixture layout

    tests/benchmark/
    ├── fixtures.json                  # manifest: slug → PDF, product_type, context, expected file
    ├── budgets.json                   # per-metric budgets enforced unless --no-enforce-budgets
    ├── datasheets/                    # control PDFs — GITIGNORED (~240 MB); local machines only
    │   ├── j5.pdf                     # 110 MB, 616 pages — full Mitsubishi MR-J5 catalog
    │   ├── j5-filtered.pdf            # 2 MB, 15 pages — pre-filtered spec pages
    │   ├── nidec-d-series-frameless.pdf
    │   ├── omron-g-series-servo-motors.pdf
    │   └── orientalmotor-nx-series.pdf
    ├── expected/                       # ground-truth JSON (one array of product dicts per fixture)
    │   ├── j5.json                     # 20 drive variants
    │   ├── nidec-d-series-frameless.json  # richest motor record
    │   ├── omron-g-series-servo-motors.json
    │   └── orientalmotor-nx-series.json   # empty placeholder, needs population
    └── cache/                          # cached LLM responses (--update-cache writes here)

## Usage

    ./Quickstart bench                          # offline: page-finding + quality diff against cached responses
    ./Quickstart bench --quality-only           # no PDFs needed: cached extraction vs expected — use this anywhere that isn't Nick's machine (CI, routines)
    ./Quickstart bench --live                   # live: calls Gemini, costs real money
    ./Quickstart bench --live --update-cache    # live + save responses for future offline runs
    ./Quickstart bench --filter j5-filtered     # single fixture
    ./Quickstart bench -o results.json          # custom output path
    ./Quickstart bench --double-tap | --ab      # verifier-loop and A/B harness variants (--help)

Results write to `outputs/benchmarks/<timestamp>.json` and `outputs/benchmarks/latest.json` (`outputs/` is gitignored).

## Adding a new fixture

1. Drop the PDF in `tests/benchmark/datasheets/`.
2. Add an entry to `tests/benchmark/fixtures.json` with slug, pdf filename, product_type, manufacturer, and product_name.
3. Create `tests/benchmark/expected/<slug>.json` with the ground-truth product array (or `[]` as a placeholder).
4. Run `./Quickstart bench --live --update-cache --filter <slug>` to populate the cache.

## Open issues

- **Nidec: 1/14 spec pages found.** Adding `cogging torque` / `thermal resistance` keywords didn't move the needle; the PDF may use non-English phrasings `page_finder` can't match. Revisit when frameless coverage matters.
- **Omron: 80% precision / 42% recall.** 13 variants extracted but missing over half the fields on the matched ground-truth record.

Resolved earlier and now pinned by tests: motor-centric page-finder keywords (now 20 `SPEC_KEYWORDS` groups; Mitsubishi contactor catalog went 4/410 → 77/410 spec pages), the `ambient_temp` wrong-family crash (per-family BeforeValidators drop wrong-family inputs to `None`; `tests/unit/test_models_common.py::TestTypedAliases`), and the scraper writing the unfiltered list instead of `passed_models`.
