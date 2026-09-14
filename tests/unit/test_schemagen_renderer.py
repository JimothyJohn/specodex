"""Unit tests for the schemagen renderer.

Deterministic given a ``ProposedModel`` — no LLM involved. Covers every
``kind``, subtype-discriminator rendering, literal-downgrade behavior, the
reserved-name check, and ``ast.parse`` roundtrip.
"""

from __future__ import annotations

import ast

import pytest
from pydantic import ValidationError

from specodex.schemagen.meta_schema import (
    ProposedField,
    ProposedModel,
    ProposedSource,
)
from specodex.schemagen.renderer import (
    render_model_file,
    render_product_type_patch,
    render_reasoning_doc,
)


def _model_with_fields(fields: list[ProposedField], **kwargs) -> ProposedModel:
    return ProposedModel(
        class_name=kwargs.pop("class_name", "TestProduct"),
        product_type=kwargs.pop("product_type", "test_product"),
        docstring=kwargs.pop("docstring", "A test product."),
        fields=fields,
        **kwargs,
    )


def test_render_model_file_every_kind() -> None:
    pm = _model_with_fields(
        [
            ProposedField(
                name="rated_voltage",
                kind="min_max_unit",
                unit="V",
                description="Rated voltage range.",
                section="Electrical",
            ),
            ProposedField(
                name="rated_current",
                kind="value_unit",
                unit="A",
                description="Rated current.",
            ),
            ProposedField(name="poles", kind="int", description="Pole count."),
            ProposedField(
                name="efficiency", kind="float", description="Efficiency ratio 0-1."
            ),
            ProposedField(
                name="ip_rating",
                kind="str",
                description="IP rating string.",
                section="Environmental",
            ),
            ProposedField(
                name="reversible",
                kind="bool",
                description="Whether contacts are reversible.",
            ),
            ProposedField(
                name="approvals",
                kind="list_str",
                description="List of regulatory approvals.",
            ),
            ProposedField(
                name="mounting_style",
                kind="literal",
                literal_values=["din-rail", "panel"],
                description="How the device is mounted.",
            ),
        ]
    )
    source = render_model_file(pm)

    # Smoke-parse.
    ast.parse(source)

    # Imports
    assert "from specodex.models.common import MinMaxUnit, ValueUnit" in source
    assert "from specodex.models.product import ProductBase" in source
    assert "from typing import List, Literal, Optional" in source

    # Class skeleton
    assert "class TestProduct(ProductBase):" in source
    assert "    product_type: Literal['test_product'] = 'test_product'" in source
    assert "    series: Optional[str] = None" in source

    # Every field lands
    assert "rated_voltage: Optional[MinMaxUnit]" in source
    assert "rated_current: Optional[ValueUnit]" in source
    assert "poles: Optional[int]" in source
    assert "efficiency: Optional[float]" in source
    assert "ip_rating: Optional[str]" in source
    assert "reversible: Optional[bool]" in source
    assert "approvals: Optional[List[str]]" in source
    assert "mounting_style: Optional[Literal['din-rail', 'panel']]" in source

    # Sections rendered as grouping comments
    assert "# --- Electrical ---" in source
    assert "# --- Environmental ---" in source


def test_render_model_file_with_subtype_discriminator() -> None:
    pm = _model_with_fields(
        [
            ProposedField(
                name="rated_current",
                kind="value_unit",
                unit="A",
                description="Rated current.",
            ),
        ],
        class_name="Contactor",
        product_type="contactor",
        subtype_values=["magnetic", "solid-state", "reversing"],
    )
    source = render_model_file(pm)
    ast.parse(source)
    assert (
        "    type: Optional[Literal['magnetic', 'solid-state', 'reversing']] = None"
        in source
    )
    # Without subtype_values, the line should be absent.
    pm_no_subtype = _model_with_fields(
        [
            ProposedField(
                name="rated_current",
                kind="value_unit",
                unit="A",
                description="Rated current.",
            ),
        ],
    )
    assert "type: Optional[Literal[" not in render_model_file(pm_no_subtype)


def test_render_model_file_dedupes_series_when_proposed() -> None:
    """If the LLM proposes `series` with its own description, don't emit the default line too."""
    pm = _model_with_fields(
        [
            ProposedField(
                name="series",
                kind="str",
                description="Product series or frame designation (e.g. T10, T65).",
            ),
            ProposedField(
                name="rated_current",
                kind="value_unit",
                unit="A",
                description="Rated current.",
            ),
        ],
    )
    source = render_model_file(pm)
    ast.parse(source)
    # Exactly one `series:` field line, and it's the LLM-proposed one.
    series_lines = [ln for ln in source.splitlines() if "series:" in ln]
    assert len(series_lines) == 1
    assert "Field(None, description=" in series_lines[0]


def test_proposed_field_rejects_reserved_name() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ProposedField(
            name="manufacturer",
            kind="str",
            description="shadow attempt",
        )
    assert "reserved" in str(excinfo.value).lower()


def test_proposed_field_rejects_unit_mismatch() -> None:
    # value_unit without unit
    with pytest.raises(ValidationError):
        ProposedField(name="rated_power", kind="value_unit", description="Rated power.")
    # non-unit kind with unit
    with pytest.raises(ValidationError):
        ProposedField(name="poles", kind="int", unit="V", description="Pole count.")


def test_proposed_field_rejects_empty_description() -> None:
    with pytest.raises(ValidationError):
        ProposedField(name="foo", kind="str", description="")


def test_proposed_field_literal_downgrade() -> None:
    values = [f"v{i}" for i in range(20)]  # > MAX_LITERAL_VALUES
    field = ProposedField(
        name="frame_size",
        kind="literal",
        literal_values=values,
        description="Frame size.",
    )
    # Downgraded to str.
    assert field.kind == "str"
    assert field.literal_values is None


def test_proposed_model_rejects_duplicate_fields() -> None:
    with pytest.raises(ValidationError):
        ProposedModel(
            class_name="Dup",
            product_type="dup",
            docstring="dup",
            fields=[
                ProposedField(name="x", kind="int", description="a"),
                ProposedField(name="x", kind="str", description="b"),
            ],
        )


def test_render_product_type_patch_appends() -> None:
    old = 'from typing import Literal\nProductType = Literal["motor", "drive"]\nx = 1\n'
    pm = _model_with_fields(
        [ProposedField(name="foo", kind="int", description="x")],
        product_type="contactor",
    )
    new = render_product_type_patch(old, pm)
    assert "'motor', 'drive', 'contactor'" in new
    assert new.endswith("x = 1\n")


def test_render_product_type_patch_idempotent() -> None:
    old = 'ProductType = Literal["motor", "contactor"]\n'
    pm = _model_with_fields(
        [ProposedField(name="foo", kind="int", description="x")],
        product_type="contactor",
    )
    assert render_product_type_patch(old, pm) == old


def test_render_product_type_patch_missing_anchor() -> None:
    old = "# nothing interesting here\n"
    pm = _model_with_fields(
        [ProposedField(name="foo", kind="int", description="x")],
        product_type="contactor",
    )
    with pytest.raises(ValueError, match="ProductType"):
        render_product_type_patch(old, pm)


def test_render_product_type_patch_multiline() -> None:
    """Tolerate multi-line Literal declarations (common after formatters)."""
    old = 'ProductType = Literal[\n    "motor",\n    "drive",\n    "gearhead",\n]\n'
    pm = _model_with_fields(
        [ProposedField(name="foo", kind="int", description="x")],
        product_type="contactor",
    )
    new = render_product_type_patch(old, pm)
    assert "'contactor'" in new
    # Re-parse the result to confirm it's a valid ProductType declaration.
    assert new.count("ProductType = Literal") == 1


def test_render_reasoning_doc_full_output() -> None:
    pm = _model_with_fields(
        [
            ProposedField(
                name="rated_voltage",
                kind="min_max_unit",
                unit="V",
                description="Rated operational voltage range.",
                section="Electrical",
            ),
            ProposedField(
                name="ip_rating",
                kind="int",
                description="IP protection rating.",
                section="Environmental",
            ),
        ],
        class_name="Contactor",
        product_type="contactor",
        docstring="Electromagnetic contactor.",
        scope_notes="General-purpose IEC 60947-4-1 contactors.",
        design_notes="Ratings stored as headline scalars for filterability.",
        sources=[
            ProposedSource(
                name="ABB AF09-AF38",
                url="https://docs.galco.com/techdoc/abbg/cont_af09-af38_td.pdf",
                relevance_notes="IEC headline-voltage convention.",
            ),
            ProposedSource(
                name="Mitsubishi MS-T/N",
                local_path="tests/benchmark/datasheets/mitsubishi-contactors-catalog.pdf",
                relevance_notes="Initial draft source.",
            ),
        ],
    )
    doc = render_reasoning_doc(pm)

    # Section headers present in order.
    assert doc.startswith("# Contactor Model")
    assert "## Scope" in doc
    assert "## Sources" in doc
    assert "## Design decisions" in doc
    assert "## Fields" in doc
    assert doc.index("## Scope") < doc.index("## Sources")
    assert doc.index("## Sources") < doc.index("## Design decisions")
    assert doc.index("## Design decisions") < doc.index("## Fields")

    # Custom scope + design strings render.
    assert "IEC 60947-4-1 contactors" in doc
    assert "headline scalars for filterability" in doc

    # Sources table renders both the URL source and the local-path
    # source with their relevance notes.
    assert "[ABB AF09-AF38](https://docs.galco.com" in doc
    assert "`tests/benchmark/datasheets/mitsubishi-contactors-catalog.pdf`" in doc
    assert "IEC headline-voltage convention" in doc

    # Field tables are grouped by section.
    assert "### Electrical" in doc
    assert "### Environmental" in doc
    assert "`rated_voltage`" in doc
    assert "`ip_rating`" in doc


def test_render_reasoning_doc_falls_back_when_fields_missing() -> None:
    pm = _model_with_fields(
        [ProposedField(name="foo", kind="int", description="a foo")],
        product_type="widget",
    )
    doc = render_reasoning_doc(pm)
    # Fallbacks surface so the doc structure is predictable even when
    # the LLM leaves scope_notes / design_notes empty.
    assert "weren't provided by the LLM" in doc  # scope fallback
    assert "Design notes weren't provided" in doc
    assert "No sources were cited" in doc


def test_render_reasoning_doc_literal_preview() -> None:
    many = [f"v{i}" for i in range(10)]
    pm = _model_with_fields(
        [
            ProposedField(
                name="mode",
                kind="literal",
                literal_values=many[:5],
                description="Operation mode.",
            )
        ],
        product_type="widget",
    )
    doc = render_reasoning_doc(pm)
    # Shows first 4 values + ellipsis when there are more.
    assert "literal[v0, v1, v2, v3, …]" in doc


# ---------------------------------------------------------------------
# Regression cases for the 2026-09-08 source-injection round.
#
# Each of these rendered (or crashed) before the identifier gate in
# meta_schema.py and the safe docstring / comment rendering in
# renderer.py. The property companion is
# tests/unit/test_schemagen_renderer_property.py; these pin the exact
# shapes so they can't regress if the strategy drifts.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "class",  # Python keyword -> SyntaxError
        "3phase",  # leading digit -> "invalid decimal literal"
        "rated current",  # space -> SyntaxError
        "",  # empty -> SyntaxError
        "_private",  # pydantic would treat it as a private attr
        "ok: int = 1\n    import os",  # newline -> statement injection
    ],
)
def test_unsafe_field_name_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        ProposedField(name=name, kind="int", description="A description.")


@pytest.mark.parametrize(
    "class_name",
    [
        "Test Product",  # space -> SyntaxError
        "True",  # keyword that passes the PascalCase check
        "A(ProductBase): pass\nimport os\nclass B",  # statement injection
    ],
)
def test_unsafe_class_name_rejected(class_name: str) -> None:
    with pytest.raises(ValidationError):
        _model_with_fields(
            [ProposedField(name="poles", kind="int", description="Pole count.")],
            class_name=class_name,
        )


@pytest.mark.parametrize(
    "docstring",
    [
        'contains """ inside',
        'trailing quote "',
        "trailing backslash \\",
        'x"""\n    import os\n    """',  # closes the docstring, injects a statement
        "carriage\r\nreturn",
        "null \x00 byte",
    ],
)
def test_adversarial_docstring_renders_and_round_trips(docstring: str) -> None:
    pm = _model_with_fields(
        [ProposedField(name="poles", kind="int", description="Pole count.")],
        docstring=docstring,
    )
    source = render_model_file(pm)
    module = ast.parse(source)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef))
    # Nothing but imports and the class reaches module level — the injected
    # `import os` survives only as docstring *text*, never as a statement.
    assert all(
        isinstance(n, (ast.Import, ast.ImportFrom, ast.ClassDef)) for n in module.body
    ), [type(n).__name__ for n in module.body]
    assert all(isinstance(n, (ast.Expr, ast.AnnAssign)) for n in cls.body), [
        type(n).__name__ for n in cls.body
    ]
    # ... and the docstring survives byte-for-byte.
    assert ast.get_docstring(cls, clean=False) == docstring


@pytest.mark.parametrize(
    "section",
    [
        "Electrical\n    import os  #",  # newline -> escapes the comment
        "Electrical\x00",  # NUL -> "source code cannot contain null bytes"
        "Electrical\ud800",  # lone surrogate -> UnicodeEncodeError
    ],
)
def test_section_cannot_break_out_of_the_comment(section: str) -> None:
    pm = _model_with_fields(
        [
            ProposedField(
                name="poles",
                kind="int",
                description="Pole count.",
                section=section,
            )
        ],
    )
    source = render_model_file(pm)
    module = ast.parse(source)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef))
    # Docstring + product_type + series + poles — no injected statement.
    assert all(isinstance(n, (ast.Expr, ast.AnnAssign)) for n in cls.body), [
        type(n).__name__ for n in cls.body
    ]
    assert "    # --- Electrical" in source
