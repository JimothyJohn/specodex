"""Regression tests for scripts/gen_types.py's wrap-drift normalizer.

Local ``npx --yes json-schema-to-typescript`` version skew can wrap long
union types across lines where CI's regeneration emits them unwrapped,
failing the ``test-codegen`` drift gate with no semantic change (bit PRs
#344-era, #348, and three CI runs on 2026-07-25). ``_unwrap_multiline_unions``
canonicalises both forms to CI's unwrapped one; these tests pin that
contract and pin the committed ``generated.ts`` as a fixed point of the
normalizer.
"""

from pathlib import Path


def _load_gen_types():
    """Load scripts/gen_types.py by path (scripts/ is not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "gen_types.py"
    spec = importlib.util.spec_from_file_location("gen_types", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = _load_gen_types()
_unwrap = _MOD._unwrap_multiline_unions

# The exact drift shape from commit dd9aaba (2026-07-25): the locally-wrapped
# Manufacturer union vs CI's unwrapped form.
WRAPPED = (
    "export interface Manufacturer {\n"
    "  offered_product_types?:\n"
    '    | ("motor" | "drive" | "gearhead" | "robot_arm" | "contactor"'
    ' | "electric_cylinder" | "linear_actuator")[]\n'
    "    | null;\n"
    "}\n"
)
UNWRAPPED = (
    "export interface Manufacturer {\n"
    "  offered_product_types?:"
    ' ("motor" | "drive" | "gearhead" | "robot_arm" | "contactor"'
    ' | "electric_cylinder" | "linear_actuator")[] | null;\n'
    "}\n"
)


class TestUnwrapMultilineUnions:
    def test_dd9aaba_drift_shape_unwraps_to_ci_form(self):
        assert _unwrap(WRAPPED) == UNWRAPPED

    def test_idempotent_on_unwrapped_input(self):
        assert _unwrap(UNWRAPPED) == UNWRAPPED
        assert _unwrap(_unwrap(WRAPPED)) == _unwrap(WRAPPED)

    def test_break_after_colon_shape_unwraps(self):
        """CI's prettier emits this shape (no pipes, whole type on the next
        line) for Manufacturer.offered_product_types — the exact diff that
        failed Test Codegen on PR #367's first run."""
        wrapped = (
            "  offered_product_types?:\n"
            '    ("motor" | "drive" | "gearhead")[] | null;\n'
        )
        expected = (
            '  offered_product_types?: ("motor" | "drive" | "gearhead")[] | null;\n'
        )
        assert _unwrap(wrapped) == expected

    def test_jsdoc_trailing_colon_does_not_join(self):
        """A JSDoc body line ending in ':' must not swallow the next line."""
        src = "/**\n * Options:\n */\nexport interface Foo {\n}\n"
        assert _unwrap(src) == src

    def test_nested_paren_group_unwraps(self):
        wrapped = (
            "  contactor_type?:\n"
            "    | (\n"
            '        | "ac operated"\n'
            '        | "vacuum"\n'
            "      )\n"
            "    | null;\n"
        )
        expected = '  contactor_type?: ("ac operated" | "vacuum") | null;\n'
        assert _unwrap(wrapped) == expected

    def test_nested_paren_array_group_unwraps(self):
        wrapped = (
            "  fieldbus?:\n"
            "    | (\n"
            '        | "EtherCAT"\n'
            '        | "PROFINET"\n'
            "      )[]\n"
            "    | null;\n"
        )
        expected = '  fieldbus?: ("EtherCAT" | "PROFINET")[] | null;\n'
        assert _unwrap(wrapped) == expected

    def test_wrapped_type_alias_unwraps(self):
        wrapped = 'export type Foo =\n  | "a"\n  | "b"\n  | null;\n'
        assert _unwrap(wrapped) == 'export type Foo = "a" | "b" | null;\n'

    def test_jsdoc_comment_lines_untouched(self):
        src = "/**\n * List of product types offered (e.g., 'motor')\n */\n"
        assert _unwrap(src) == src

    def test_plain_interface_untouched(self):
        src = "export interface Motor {\n  rated_torque?: ValueUnit | null;\n}\n"
        assert _unwrap(src) == src

    def test_committed_generated_ts_is_a_fixed_point(self):
        """CI's form must pass through byte-identical — if this fails, the
        committed artifact itself carries wrapped unions (drift landed)."""
        generated = (
            Path(__file__).resolve().parents[2]
            / "app"
            / "frontend"
            / "src"
            / "types"
            / "generated.ts"
        )
        text = generated.read_text()
        assert _unwrap(text) == text
