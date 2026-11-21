---
paths:
  - "tests/**"
---

# Property testing — adversarial by default

The global `~/.claude/rules/testing.md` checklist and the one-line rules in `CLAUDE.md` always apply. This loads when you touch tests; it holds what is specific to this repo.

`hypothesis` is in `[dependency-groups].dev` (`pyproject.toml`). Every parser, coercer, and validator that eats LLM-emitted or user-controlled bytes has a `tests/unit/test_<surface>_property.py` companion to its example-based test file. Property rounds here have repeatedly found bugs the example tests missed — every one a case where the docstring said one thing and the code did another (`_coerce_ip_rating`, `_coerce_protocol_list`, `Gearhead.coerce_string_fields`, `units._round_converted` on non-finite floats, the Stober configurator parsers, the drawing-page-finder negative cap, the schemagen renderer's source-injection hole, `log_redact` multi-secret splicing). The property test pins the documented contract, and the code reveals itself.

## Convention

- **File name.** `tests/unit/test_<module>_property.py`, sibling to `test_<module>.py`. The example-based test pins the happy path; the property test pins the contract.
- **Strategy structure.** Build one or two `st.composite` / `st.one_of` strategies for "adversarial input to this surface": None, bool, int, NaN/inf floats, empty strings, unicode-laced strings, recursive dicts/lists, and a sampled list of real-world attack vectors when the function is security-relevant (see `test_url_safety_property.py` for the URL-attack list).
- **Contract template.** Every property test asserts at least: (1) the function never raises an exception type outside its documented set, and (2) the return value has the documented shape. For functions that return Pydantic instances, also assert the fields are well-formed (e.g. `device in EncoderDevice` enum values).
- **Example count.** `max_examples=200` for routine surfaces, `300` for security-relevant ones (`validate_url`, the BeforeValidators, `log_redact`). `HealthCheck.too_slow` is fine to suppress when the surface is slow per call (e.g. PyMuPDF `fitz.open`).
- **Logging.** Add an autouse fixture to silence per-row validation-error logging when running 200+ examples — otherwise one run produces 200 stack traces (see `test_parse_gemini_property.py`'s `_silence_validation_logs`).
- **Slow-path patches.** For tests that exercise tenacity retries or `lru_cache`-backed paths, override the slow-path attribute (`generate_content.retry.wait = wait_none()`) and clear the cache (`_client_for.cache_clear()`) in a module autouse fixture — see `test_resilience.py::_no_retry_waits`. A missing override is what bimodal 2 s vs 22 s CI timings look like.
- **Bug fix → regression test FIRST.** When hypothesis catches a real bug, add an explicit example-based regression case to the sibling `test_<module>.py` (e.g. `TestCoerceProtocolListEdgeCases`) **in addition to** the property. The property catches the contract violation; the explicit case pins the specific shape so it can't regress even if the strategy drifts. Both stay forever.
- **False positives.** When a property fails on a legitimate edge case (e.g. a user-supplied `'{'` frame_size tripping a "starts with `{`" dict-repr-leak assertion), tighten the assertion to the bug's exact signature (`startswith("{'")` or `startswith('{"')`) — don't widen the input filter, which hides future bug shapes.

## Lessons from past rounds

- **Vary the arity.** A property that fixes the count of its input (one secret, one page, one row) will not find bugs that live between instances — `log_redact`'s splice bug needed two secrets.
- **A specific co-occurrence needs its own strategy.** Hypothesis will not stumble onto `"AA[REDACTED]"` from free text, or onto "≥ 2 qualifying pages *and* a negative cap" (`candidates[:max_pages]` with a negative slice *expanded* the page set); build the qualifying document directly and pin the regression as a targeted property plus example cases.
- **A surface that emits code needs a structural assertion.** For `schemagen/renderer.py` the property that caught two live injections (a docstring closing early, a `class_name` smuggling statements) was "the parsed module body is imports plus one `ClassDef`, and that class body is the docstring plus the proposal's `AnnAssign`s and nothing else" — "it parses" passed both. Gemini controls those strings, so `meta_schema._reject_unsafe_identifier` gates bare-name slots (identifier, not a keyword, no leading underscore — Pydantic would silently demote `_foo` to private) and `renderer._docstring_literal` escapes quotes, backslashes, control characters and surrogates.
- **Guard every nested walk of vendor JSON.** The Stober parsers crashed on unhashable ids, truthy scalars where lists were documented, non-string keys, and bool/NaN floats (`float(True) == 1.0`); `_as_list`, `_text`, `_as_float` are the pattern.
- **Non-finite floats reach the validators.** `math.floor(math.log10(x))` raises on ±inf/NaN and a finite overflow (`1e306 × 1e3`) hits the same path; short-circuit non-finite inputs before arithmetic.
- **Fail closed in redaction.** Re-run to a fixpoint (bounded), return a bare `[REDACTED]` if the cap is exhausted with a secret still present, and tie-break equal-length secrets lexicographically so the result is reproducible run to run.

## Coverage map

| Surface | Property test | Sibling example test |
|---|---|---|
| `parse_gemini_response` (LLM JSON parser) | `test_parse_gemini_property.py` | inline in `test_utils.py` |
| `common.py` BeforeValidators | `test_common_validators_property.py` | `test_models_common.py` |
| `find_spec_pages_by_text` (PDF intake) | `test_page_finder_property.py` | `test_page_finder_edge.py`, `test_page_finder_proximity.py` |
| `find_drawing_pages_by_text` + `_score_drawing_page` + `find_drawing_pages_scored` (drawing-page intake for `mounting-extract`) | `test_drawing_page_finder_property.py` | `test_drawing_page_finder.py` |
| `coerce_protocol_string`, `_coerce_protocol_list`, `EncoderFeedback._coerce_legacy_freetext` | `test_encoder_coercers_property.py` | `test_encoder.py` |
| `drive._coerce_fieldbus_list` | `test_fieldbus_coercer_property.py` | `test_models.py` |
| `Gearhead.coerce_string_fields` | `test_gearhead_coerce_property.py` | (via `test_models_common.py`) |
| `validate_url` (SSRF defense) | `test_url_safety_property.py` | `test_url_safety.py` |
| `merge_per_page_products` | `test_merge_property.py` | `test_merge.py` |
| `double_tap.verifier` (`_encoder_is_ambiguous`, `verify`) | `test_double_tap_verifier_property.py` | `test_double_tap.py` |
| `cli/processor.py:parse_datasheet_id_from_key` (upload-queue S3 key parsing) | `test_processor_property.py` | `test_processor.py` |
| `cli/intake_guards.py` (triage → good_examples promotion gate) | `test_intake_guards_property.py` | `test_intake_guards.py` |
| `specodex/ingest_log.py` (`should_skip`, key formats) | `test_ingest_log_property.py` | `test_ingest_log.py` |
| `specodex/integration/compat.py` field-compat helpers (`_scalar`, `_range`, `_check_*`, `_roll_up`) | `test_compat_property.py` | `test_integration.py` |
| `specodex/spec_rules.py:validate_product` magnitude rules + identity check + duplicate pair | `test_spec_rules_property.py` | `test_spec_rules.py` |
| `specodex/quality.py:score_product` + `filter_products` partition | `test_quality_property.py` | `test_quality.py` + `test_quality_boundary.py` |
| `specodex/units.py:normalize_unit_value` (LLM-emitted value+unit canonicaliser) | `test_units_property.py` | `test_units.py` |
| `specodex/ids.py:normalize_string` + `compute_product_id` (deterministic UUID5 generation) | `test_ids_property.py` | `test_ids.py` |
| `specodex/placeholders.py:is_placeholder` (LLM "null"/"N/A" string filter) | `test_placeholders_property.py` | `test_placeholders.py` |
| `specodex/pricing/extract.py:_parse_bare_decimal` + `_parse_money` + `_parse_json_loose` | `test_pricing_parsers_property.py` | `test_pricing.py` |
| `specodex/relations.py` predicates (`_value_in_range`, `_range_within`, `_value_gte`, `_shaft_compatible`, `_meets_floor`, `_encoder_protocol_intersect`) + `compatible_actuators` / `compatible_motors` / `compatible_drives` / `compatible_gearheads` | `test_relations_property.py` | `test_relations.py` |
| `specodex/pricing/shopping.py:_parse_offers` + `filter_offers` + `pick_price` (Serper `/shopping` tier trust chain) | `test_shopping_property.py` | `test_shopping.py` |
| `specodex/pricing/inference.py:estimate_price` (price-comps-v1 DB-comparables engine) | `test_price_inference_property.py` | `test_price_inference.py` |
| `specodex/pricing/lead_time.py:parse_lead_statement` + `parse_lead_statement_range` (published vendor lead-time statements) | `test_lead_time_property.py` | `test_lead_time_inference.py` |
| `specodex/configurators/stober.py:parse_requirements` + `parse_group_selection` (vendor-configurator JSON deserializer) | `test_configurators_property.py` | `test_configurators.py` |
| `specodex/schemagen/renderer.py:render_model_file` + `render_product_type_patch` + `render_reasoning_doc`, and the `meta_schema.py` identifier gate (LLM proposal → generated Python source) | `test_schemagen_renderer_property.py` | `test_schemagen_renderer.py` |
| `specodex/log_redact.py:redact_secrets` + `secret_values` (credential scrubber on the `except`-site log path) | `test_log_redact_property.py` | `test_log_redact.py` |
| `specodex/mcp/api.py` argument validators (ids, types, where/sort clauses, limits, cursors) | `test_mcp_api_property.py` | `test_mcp_server.py` |

When a new untrusted-bytes surface lands, add its row here; don't leave the map stale for long.
