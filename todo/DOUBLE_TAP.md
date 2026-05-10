# DOUBLE_TAP — encoder feedback schema rethink + verifier-loop extraction

**Status (post-2026-05-10 sprint):** ✅ all phases shipped via PR #91
(2026-05-09). Phases 1+2 (closed `EncoderDevice` / `EncoderProtocol`
taxonomy + structured `EncoderFeedback` + typed compat) and 3+4+5+6
(verifier-loop runner + bench A/B harness + scraper integration
behind `SPECODEX_DOUBLE_TAP=1` + frontend codegen). Doc + appendix
(`DOUBLE_TAP_encoder_taxonomy.md`) retained as architecture reference
for the closed taxonomy and verifier-loop pattern. Property-test
coverage on the verifier surface added in PR #123; on the legacy
free-text shim (`coerce_protocol_string`, `_coerce_protocol_list`,
`EncoderFeedback._coerce_legacy_freetext`) added in PR #116 (which
also caught a real `_coerce_protocol_list` empty-string bug).

This doc has two things stapled together because they're driven by the
same observation:

1. **The schema is too loose.** `encoder_feedback_support: Optional[str]`
   on `Motor` and `Optional[List[str]]` on `Drive` lets the LLM emit any
   vendor-flavored phrase ("EnDat 2.2", "BiSS-C 26-bit", "Incremental
   2500 ppr", "Mitsubishi serial encoder"). The compatibility layer
   (`specodex/integration/compat.py:_compare_feedback`) does string
   membership against this — wrong vendor casing or version suffix
   means a real match looks like a fail. We currently mask the
   problem by `_soften`-ing every fail to `partial` (see
   `compat.py:268`), which hides bugs.
2. **One-shot extraction has no recovery.** When Gemini punts on a
   field — either omits it, picks the wrong unit, or fills in a
   fuzzy free-text where a structured value belongs — there's no
   second pass. We trip the quality gate at `min_quality=0.25`
   (`specodex/quality.py:42`), or worse, the row sails through with
   ambiguous strings. The vendor outreach pipeline
   (`./Quickstart ingest-report`) treats those as "vendor's catalog
   is incomplete," but in many cases it's actually our extractor
   missing a value the catalog *does* publish.

The thesis: **a closed encoder taxonomy + a primed second pass that
sees the first pass's gaps will lift extraction quality enough to
justify roughly doubling the LLM token spend on the affected pages.**
Whether that thesis holds is the bench's job to prove, not mine to
assume — see Phase 4.

## Part 1 — encoder taxonomy

The full taxonomy reference lives in `todo/DOUBLE_TAP_encoder_taxonomy.md`
(written by a research subagent in parallel with this doc; treat that
file as the appendix). The headline shape:

```
EncoderFeedback
├── device       — physical sensor type (closed enum)
│   ├── incremental_optical
│   ├── absolute_optical
│   ├── magnetic_incremental
│   ├── magnetic_absolute
│   ├── sin_cos_analog          (1 Vpp / 11 µAss)
│   ├── resolver
│   ├── inductive               (Renishaw RESOLUTE etc.)
│   ├── hall_only               (commutation)
│   ├── tachometer              (DC, AC)
│   └── unknown                 (sentinel — first pass must justify)
│
├── protocol     — wire format (closed enum, optional)
│   ├── biss_c
│   ├── endat_2_1 / endat_2_2
│   ├── hiperface / hiperface_dsl
│   ├── ssi
│   ├── tamagawa_smart_abs
│   ├── mitsubishi_j3 / j4 / j5
│   ├── panasonic_a6
│   ├── fanuc_alpha_i
│   ├── yaskawa_sigma_v / sigma_7
│   ├── quadrature_ttl / quadrature_rs422
│   ├── analog_1vpp
│   └── unknown
│
├── mode         — Literal["incremental", "absolute"] | None
├── turns        — Literal["single_turn", "multi_turn"] | None
├── resolution   — { value: int, unit: "bits" | "ppr" | "lpr" }
├── multi_turn_bits — Optional[int]   (only when turns=multi_turn)
└── battery_backed — Optional[bool]   (multi-turn implementation hint)
```

The discriminated union lives in `specodex/models/encoder.py` (new
file, single source of truth). `Motor.encoder_feedback`,
`Drive.encoder_feedback_support`, `ElectricCylinder.encoder_feedback`,
and `LinearActuator.encoder_feedback_support` all retype to this:

- **Motor** (provides): `Optional[EncoderFeedback]` — a motor ships
  with one encoder. Backwards-compat shim: a `BeforeValidator` that
  parses the legacy `Optional[str]` form into a best-effort
  `EncoderFeedback` (or returns the raw string in a `raw` field if
  the parser can't resolve it).
- **Drive** (supports): `Optional[List[EncoderFeedback]]` — a drive
  accepts a list. Same shim, applied per element.
- **ElectricCylinder / LinearActuator**: same as motor side, except
  actuators sometimes accept a list (Lintech 200 takes both incremental
  and absolute feedback options) — pattern with motor by default,
  switch to list if a vendor surfaces the multi-option case.

### How extraction stays sane

Closed enums are brittle when the LLM sees a vendor flavor we forgot
to enumerate. Two mitigations:

1. **`unknown` sentinel + `raw` passthrough** on every enum field. If
   the LLM can't classify, it emits `device="unknown"` with the raw
   spec text in `raw`. Quality scorer counts `unknown` as missing.
2. **System-prompt taxonomy injection.** The new prompt fragment
   includes the full enum list with synonyms — "if catalog says
   'optical encoder', map to `incremental_optical` unless an
   explicit absolute bit count is shown." Lives in
   `specodex/llm_prompt/encoder.py` so the same text drives the
   prompt and the test fixture set.

### Compatibility check upgrade

`_compare_feedback` becomes structural:

```python
def _compare_feedback(motor_side, drive_side):
    motor_fb = motor_side.provides
    if motor_fb is None or drive_side.supports is None:
        return [partial("encoder", "missing on one side")]
    for accepted in drive_side.supports:
        if _feedback_subsumes(accepted, motor_fb):
            return [ok("encoder")]
    return [fail("encoder", f"motor {motor_fb.protocol} not in drive support")]
```

`_feedback_subsumes` is the core subtlety: a drive that says
`endat_2_2` accepts an `endat_2_1` motor (downward compatible per the
Heidenhain spec). A drive that says `biss_c` does **not** accept a
generic "absolute" motor. The full subsumption matrix lives in
`specodex/models/encoder.py:SUBSUMES`.

This is the change that lets `_soften` (`compat.py:268`) be deleted
in a follow-up — the typed comparison no longer false-positives on
casing/version drift, so honest fails can stay fails.

## Part 2 — the double-tap framework

The seam is **`specodex/extract.py:call_llm_and_parse`**. Both the PDF
scraper (per-page and bundled paths) and the web scraper route through
it. Wrap it in a `double_tap.py:extract_with_recovery` that:

1. Calls `call_llm_and_parse` once with the existing prompt.
2. Runs `verifier.probe(parsed_models, source=doc_data)` — returns a
   `Probe` describing what's missing or low-confidence.
3. If `probe.empty()` (first pass was clean): return immediately. No
   second-call cost; the bench needs to prove this is rare enough to
   justify always-on double-tap, or we make it conditional.
4. If `probe.fires()`: build a primed second-pass prompt that
   includes:
    - The first-pass JSON (so the model can correct in place rather
      than restart).
    - Per-field uncertainty notes ("encoder_feedback emitted as
      free-text 'Smart Absolute'; map to one of these enum
      values").
    - A list of **field captions to look for** — for missing
      fields, include the catalog text labels associated with them
      (e.g. for `rotor_inertia`, prompt with "look for 'rotor
      inertia', 'inertia', 'GD²/4', 'J' followed by units of kg·m²
      or kg·cm²"). Field-caption mapping lives in
      `specodex/double_tap/captions.py` — one dict per product type.
5. Re-extract with the primed prompt. The schema is unchanged; only
   the system-prompt prefix is augmented.
6. Merge: prefer the second pass for any field the probe flagged;
   keep the first pass for fields it didn't (so the second call
   can't *regress* a previously-good field by hallucinating). The
   merge logic is dumb on purpose — `for field in probe.fields:
   merged[field] = second[field] if second[field] is not None else
   first[field]`.
7. Re-verify after merge. If the probe still fires on a critical
   field, log the row's `product_id` to a `double_tap_unresolved.jsonl`
   sidecar so we have a feedback loop for the verifier rules.

### Probe shape

```python
@dataclass
class FieldProbe:
    field: str
    reason: Literal[
        "missing",          # value is None and field is "common" for this type
        "ambiguous",        # value parsed as `unknown` enum or `raw` shim
        "out_of_range",     # value violates a model_validator soft check
        "wrong_unit",       # value is a unit we don't recognize
    ]
    captions: list[str]     # field labels to point the LLM at on pass 2
    primer: str             # human-readable note for the prompt

@dataclass
class Probe:
    fields: list[FieldProbe]
    def empty(self) -> bool: return not self.fields
    def fires(self) -> bool: return bool(self.fields)
```

### What the verifier checks

For v1, three rules — additive later:

1. **Encoder ambiguity.** `encoder_feedback.device == "unknown"` or
   `protocol == "unknown"` with a populated `raw` field → flag for
   re-pass with the taxonomy primer.
2. **Common-field missing.** Per product type, a small list of
   "almost always populated" fields. Motor: `rated_voltage`,
   `rated_torque`, `rated_speed`, `rotor_inertia`. Drive: `input_voltage`,
   `rated_current`, `rated_power`. Missing → flag for re-pass with
   field-caption primer.
3. **Wrong-unit drop.** When `common.py`'s `BeforeValidator` returns
   `None` for a wrong unit family (e.g. `{"value": 5, "unit": "V"}`
   for a torque field), it currently silently nulls the field. The
   verifier intercepts the original raw payload (cached on the parse
   step), notes the unit mismatch, and re-prompts with "this field
   needs units of X, Y, or Z; the catalog had `5 V` which is wrong-
   family — find the right value."

The third rule is the most expensive to implement (needs the parser
to carry forward the raw input) but the highest-leverage — it's
catching a known silent-data-loss bug today.

### Generalization knob

**Encoder is the test case, but the framework is generic.** Other
fields that benefit on day 2 once the harness is proven:

- `Drive.fieldbus` — same vendor-string-soup problem.
- `Motor.frame_size` — partially addressed by MotorMountPattern on
  the wip branch.
- `Gearhead.ratio` — frequently emitted as `"100:1"` or `100` or
  `"100"` and a verifier can normalize.
- `IpRating` — vendors write `IP65/67`, `IP-65`, `Class IP65`.

Each of those is a one-day add once the verifier-loop infrastructure
exists. The plan ships **encoder only** to keep the diff reviewable
and the bench measurement clean.

## Part 3 — wiring + telemetry

`specodex/double_tap/runner.py` is the public entry point. It returns
a `DoubleTapResult`:

```python
@dataclass
class DoubleTapResult:
    products: list[ProductBase]
    first_pass_tokens: tuple[int, int]    # (input, output)
    second_pass_tokens: tuple[int, int]   # (0, 0) when probe was empty
    probes_fired: int
    fields_recovered: list[str]           # fields that went None → value
    fields_corrected: list[str]           # fields that changed value
    unresolved: list[FieldProbe]          # still-flagged after pass 2
```

The scraper's `process_datasheet` calls `runner.extract_with_recovery`
in place of `call_llm_and_parse`, and writes the new fields to the
ingest log:

- `gemini_input_tokens` / `gemini_output_tokens` already exist; the
  log just sums first + second pass like it does for per-page calls.
- New columns: `double_tap_fired` (bool), `double_tap_recovered`
  (int — count of fields lifted N→V), `double_tap_corrected` (int).

`./Quickstart godmode` (the data-quality observatory) gets a new
panel showing daily probe-fire rate and recovery yield, so we can
see whether the framework is paying its way in production.

## Part 4 — bench protocol

This is the deciding gate. If double-tap doesn't measurably improve
quality on the existing benchmark set, we ship the encoder schema
work and *kill* the runner — better to know than guess.

### What changes in `cli/bench.py`

Add `--double-tap` flag that runs each fixture twice in the same
session (single-pass cached run + double-tap live run with the same
PDF) and emits a comparison table:

```
Fixture                  Single  Double  Δ Recall   Δ Tokens   Δ Cost   Worth it?
nidec-d-series-frameless  42%     71%    +29pp      +180%      +$0.0042  ✅ keep
omron-g-series-servo      42%     58%    +16pp      +90%       +$0.0021  ✅ keep
j5-filtered                81%     82%    +1pp       +110%      +$0.0019  ❌ skip
```

Threshold for "worth it": **+5pp recall OR +5pp precision per +100%
tokens**. Below that, the second pass is a tax we don't want.

### New encoder-heavy fixtures

The current fixture set is motor/drive-heavy but doesn't stress
encoder ambiguity specifically. Add two new fixtures whose ground
truth includes the structured encoder schema:

1. **`mitsubishi-mr-j5-encoder.pdf`** (subset of the existing
   `j5.pdf`) — Mitsubishi proprietary encoder that today gets
   extracted as the free-text "26-bit absolute encoder". Ground
   truth: `protocol=mitsubishi_j5, mode=absolute, turns=multi_turn,
   resolution=26 bits`.
2. **`yaskawa-sigma7-encoder.pdf`** (need to source) — Sigma-7
   serial encoder, equally vendor-locked.

These fixtures validate **both** the taxonomy (does the parser map
"26-bit absolute" to the right protocol?) and the double-tap (does
the second pass recover when the first pass picks `unknown`?).

### A/B mode

`./Quickstart bench --ab single,double-tap --filter <slug>` runs
both modes and writes the comparison table to
`outputs/benchmarks/ab/<timestamp>.json`. CI gets a new job
`bench-double-tap-ab` that runs nightly on the fixture set with
`--quality-only` (so it's free; no live calls needed once the cache
covers both modes).

## Part 5 — phasing

Each phase is a separate commit on this branch; the PR opens after
Phase 4 is green so the bench numbers are in the PR description.

| # | Phase                                  | Files                                                                                  | Verify                                  |
|---|----------------------------------------|----------------------------------------------------------------------------------------|-----------------------------------------|
| 1 | Encoder taxonomy + EncoderFeedback     | `specodex/models/encoder.py`, `motor.py`, `drive.py`, `electric_cylinder.py`, `linear_actuator.py` | `pytest tests/unit/test_encoder.py`     |
| 2 | Compat upgrade + `_soften` audit       | `specodex/integration/compat.py`, `ports.py`, `adapters.py`                            | `pytest tests/unit/test_integration.py` |
| 3 | Verifier + Probe + captions            | `specodex/double_tap/{verifier,probe,captions,prompt}.py`                              | `pytest tests/unit/test_double_tap_verifier.py` |
| 4 | Runner + scraper integration           | `specodex/double_tap/runner.py`, edit `specodex/scraper.py`, ingest-log fields         | `./Quickstart bench --double-tap`       |
| 5 | Bench A/B harness + new fixtures       | `cli/bench.py`, `tests/benchmark/fixtures.json`, `tests/benchmark/expected/*.json`     | `./Quickstart bench --ab single,double-tap` |
| 6 | Codegen + frontend                     | `./Quickstart gen-types`, surface `EncoderFeedback` in column-derived attribute logic  | `./Quickstart verify`                   |
| 7 | Per-PR doc page                        | `docs/requests/<n>.html`, `docs/requests/index.html`                                   | `open docs/requests/<n>.html`           |

## Part 6 — risks and what could kill this

- **Closed enum drag.** Vendors will ship something we didn't
  enumerate. Mitigation is the `unknown` sentinel + `raw` field, but
  if `unknown` rate exceeds 10% in production we need to widen the
  enum monthly. Watch the godmode panel.
- **Second pass regresses good fields.** The merge logic specifically
  prevents this by only accepting second-pass values for fields the
  probe flagged. If a future contributor "improves" the merge to
  prefer second-pass everywhere, the bench will catch it (or should
  — Phase 5 needs a regression-direction column).
- **Token cost balloons.** Bench is the gate. If real-world probe-fire
  rate exceeds bench rate by >2x, godmode will surface it and we
  conditionally disable double-tap on cheap-quality fixtures (set a
  `min_first_pass_score` floor below which double-tap is skipped —
  a 5%-quality first pass means the page is garbage, not ambiguous).
- **The taxonomy is wrong.** Most likely failure mode: someone with
  deeper servo experience than me reads `SUBSUMES` and finds three
  bad relationships. Mitigation: ship Phase 1 with the matrix
  empty (all `False` except identity), let real fixtures populate it
  via observation. Better to under-match than over-match.

## Part 7 — open questions for Nick

None blocking — every section above takes a position. The two
genuinely uncertain calls:

1. **Always-on vs probe-conditional double-tap.** I'm shipping
   probe-conditional (skip second pass when first is clean) because
   it makes the bench math defensible. If you want always-on for the
   "every product gets a verification pass even when the first looks
   good" property, the runner takes a `mode="always"|"on_probe"` arg
   — flip the default later.
2. **Resolver pole-pair count.** The taxonomy as drafted treats
   resolvers as a single device type. Real resolvers vary by
   pole-pair count (1, 2, 4) and this matters for high-precision
   applications. Encoded as `resolver_pole_pairs: Optional[int]` on
   `EncoderFeedback` for now; tell me if you want a richer model.

---

**Linked board card:** none yet — open after Phase 1 lands so the
card can link to a real branch + draft PR.
