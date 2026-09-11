"""Property tests for the schemagen renderer + its meta-schema gate.

Sibling to ``test_schemagen_renderer.py`` (example-based). The surface here
is unusual among our adversarial boundaries: the untrusted bytes are an LLM
proposal, and the *output* is Python source that
``./Quickstart schemagen`` writes into ``specodex/models/`` — a package
``specodex.config._discover_schema_models`` imports at startup. So the
contract isn't just "don't crash", it's "never emit a statement the proposal
didn't ask for".

Documented contract, per the module docstrings of
``specodex/schemagen/renderer.py`` and
``specodex/schemagen/meta_schema.py``:

- ``render_model_file`` "always parses ... because only validated tokens
  reach the output" — so for *any* ``ProposedModel`` that survives
  validation it must not raise ``SyntaxError``.
- "the LLM never writes executable Python" — so the parsed module body must
  contain exactly the imports plus one ``ClassDef``, and that class body must
  contain exactly the docstring plus the annotated assignments the proposal
  asked for. Nothing else.
- ``render_product_type_patch`` raises ``ValueError`` (never anything else)
  when it can't find or parse the declaration, and is idempotent.
- ``render_reasoning_doc`` is pure text and never raises.
"""

from __future__ import annotations

import ast
import keyword

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from specodex.schemagen.meta_schema import (
    RESERVED_FIELD_NAMES,
    ProposedField,
    ProposedModel,
    ProposedSource,
)
from specodex.schemagen.renderer import (
    render_model_file,
    render_product_type_patch,
    render_reasoning_doc,
)


# 300 rather than the routine 200: the output of this surface is source code
# the CLI writes into an imported package, so it sits in the
# security-relevant bucket per CLAUDE.md.
_SETTINGS = settings(
    max_examples=300,
    suppress_health_check=[HealthCheck.too_slow],
)

# Text that has historically broken source generation: quote runs that close a
# docstring early, backslashes that escape the closer, newlines that end a
# comment, and a line of Python to land in the hole any of those open up.
_INJECTION_SNIPPETS = [
    '"""',
    '""""',
    'ends with "',
    "trailing backslash \\",
    '"""\nimport os\n"""',
    'x"""\n    import os\n    """',
    "\n    import os",
    "\n    import os  #",
    "--- \n    raise SystemExit(1)\n    # ---",
    "\r\n",
    "\r",
    "\x0c",
    "{'value': 0, 'unit': 'mm'}",
    "café — ünïcode ✓",
    "\x00",
]

# Lone surrogates are excluded from the general alphabet: Pydantic rejects
# them on `description`, which would starve generation. They reach the
# renderer through `section` instead (see `_section_text`), and through the
# explicit regression case in the sibling example test.
_ALPHABET = st.characters(exclude_categories=["Cs"])

_adversarial_text = st.one_of(
    st.sampled_from(_INJECTION_SNIPPETS),
    st.text(alphabet=_ALPHABET),
    st.text(alphabet=_ALPHABET, min_size=1, max_size=80),
)

_nonempty_adversarial_text = _adversarial_text.filter(lambda s: len(s) >= 1)

_optional_text = st.one_of(st.none(), _adversarial_text)

# `section` is only ever rendered into a `#` comment, so nothing filters
# surrogates out before the renderer sees them.
_section_text = st.one_of(
    _adversarial_text,
    st.sampled_from(["\ud800", "sec\ud800tion"]),
).filter(lambda s: s.strip() != "")

# Names that are legal for the renderer to emit bare.
_safe_field_name = st.from_regex(r"\A[a-z][a-z0-9_]{0,20}\Z").filter(
    lambda s: (
        s not in RESERVED_FIELD_NAMES
        and not keyword.iskeyword(s)
        and not s.startswith("_")
    )
)
_safe_class_name = st.from_regex(r"\A[A-Z][A-Za-z0-9]{0,20}\Z").filter(
    lambda s: not keyword.iskeyword(s)
)
_safe_product_type = st.from_regex(r"\A[a-z][a-z0-9_]{0,20}\Z")

# Names the identifier gate must reject: a bare-name slot the renderer would
# otherwise emit unquoted.
_unsafe_name = st.one_of(
    st.sampled_from(
        [
            "",
            "class",
            "True",
            "3phase",
            "rated current",
            "_private",
            "a-b",
            "a.b",
            "a\n    import os",
            "A(ProductBase): pass\nimport os\nclass B",
            "sürprise!",
        ]
    ),
    _adversarial_text,
).filter(lambda s: not s.isidentifier() or s.startswith("_") or keyword.iskeyword(s))

_UNIT_KINDS = ("value_unit", "min_max_unit")


@st.composite
def _proposed_fields(draw: st.DrawFn) -> ProposedField:
    """One field with an adversarial description / section / literal set."""
    kind = draw(
        st.sampled_from(
            ["int", "float", "str", "bool", "list_str", "literal", *_UNIT_KINDS]
        )
    )
    kwargs: dict[str, object] = {
        "name": draw(_safe_field_name),
        "kind": kind,
        "description": draw(_nonempty_adversarial_text),
        "section": draw(st.one_of(st.none(), _section_text)),
    }
    if kind in _UNIT_KINDS:
        kwargs["unit"] = draw(st.text(min_size=1, max_size=8))
    if kind == "literal":
        kwargs["literal_values"] = draw(
            st.lists(_adversarial_text, min_size=1, max_size=16)
        )
    return ProposedField(**kwargs)


@st.composite
def _proposed_models(draw: st.DrawFn) -> ProposedModel:
    fields = draw(st.lists(_proposed_fields(), min_size=0, max_size=6))
    # Duplicate names are rejected by the model validator; dedupe first so the
    # strategy stays productive.
    seen: set[str] = set()
    unique = []
    for f in fields:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)
    return ProposedModel(
        class_name=draw(_safe_class_name),
        product_type=draw(_safe_product_type),
        docstring=draw(_nonempty_adversarial_text),
        subtype_values=draw(
            st.one_of(st.none(), st.lists(_adversarial_text, min_size=1, max_size=6))
        ),
        scope_notes=draw(_optional_text),
        design_notes=draw(_optional_text),
        sources=draw(
            st.one_of(
                st.none(),
                st.lists(
                    st.builds(
                        ProposedSource,
                        name=_nonempty_adversarial_text,
                        url=_optional_text,
                        local_path=_optional_text,
                        relevance_notes=_optional_text,
                    ),
                    min_size=1,
                    max_size=4,
                ),
            )
        ),
        fields=unique,
    )


def _parse_rendered(pm: ProposedModel) -> tuple[ast.Module, ast.ClassDef]:
    source = render_model_file(pm)
    module = ast.parse(source)
    class_defs = [n for n in module.body if isinstance(n, ast.ClassDef)]
    assert len(class_defs) == 1, "expected exactly one class in the rendered module"
    return module, class_defs[0]


class TestRenderModelFile:
    """``render_model_file`` emits parseable source and nothing extra."""

    @given(pm=_proposed_models())
    @_SETTINGS
    def test_output_always_parses(self, pm: ProposedModel) -> None:
        # render_model_file ast.parse()s internally; a SyntaxError escaping
        # here is the documented "renderer bug" case.
        source = render_model_file(pm)
        assert isinstance(source, str)
        assert source.endswith("\n")

    @given(pm=_proposed_models())
    @_SETTINGS
    def test_no_statement_the_proposal_did_not_ask_for(self, pm: ProposedModel) -> None:
        """Module level is imports + one class; nothing executes on import."""
        module, cls = _parse_rendered(pm)
        for node in module.body:
            assert isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)), (
                f"unexpected top-level statement {type(node).__name__}"
            )
        assert cls.name == pm.class_name
        # The class body is the docstring plus annotated assignments only.
        body = list(cls.body)
        assert isinstance(body[0], ast.Expr)
        for node in body[1:]:
            assert isinstance(node, ast.AnnAssign), (
                f"unexpected class-body statement {type(node).__name__}"
            )

    @given(pm=_proposed_models())
    @_SETTINGS
    def test_docstring_round_trips_verbatim(self, pm: ProposedModel) -> None:
        _, cls = _parse_rendered(pm)
        assert ast.get_docstring(cls, clean=False) == pm.docstring

    @given(pm=_proposed_models())
    @_SETTINGS
    def test_every_proposed_field_is_declared_once(self, pm: ProposedModel) -> None:
        _, cls = _parse_rendered(pm)
        declared = [
            node.target.id
            for node in cls.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        ]
        for field in pm.fields:
            assert declared.count(field.name) == 1
        assert declared.count("product_type") == 1
        assert declared.count("series") == 1
        # `type` is rendered only when the proposal supplies subtype values.
        assert declared.count("type") == (1 if pm.subtype_values else 0)


class TestIdentifierGate:
    """Names that would break — or escape — the generated source are rejected."""

    @given(name=_unsafe_name)
    @_SETTINGS
    def test_unsafe_field_name_is_rejected_not_rendered(self, name: str) -> None:
        with pytest.raises(ValidationError):
            ProposedField(name=name, kind="int", description="d")

    @given(name=_unsafe_name)
    @_SETTINGS
    def test_unsafe_class_name_is_rejected_not_rendered(self, name: str) -> None:
        with pytest.raises(ValidationError):
            ProposedModel(
                class_name=name,
                product_type="widget",
                docstring="d",
                fields=[],
            )


class TestProductTypePatch:
    """The ``common.py`` patcher over arbitrary source."""

    @given(old_source=_adversarial_text, product_type=_safe_product_type)
    @_SETTINGS
    def test_only_raises_value_error(self, old_source: str, product_type: str) -> None:
        pm = ProposedModel(
            class_name="Widget",
            product_type=product_type,
            docstring="d",
            fields=[],
        )
        try:
            patched = render_product_type_patch(old_source, pm)
        except ValueError:
            return
        assert isinstance(patched, str)

    @given(
        existing=st.lists(_safe_product_type, min_size=1, max_size=6, unique=True),
        product_type=_safe_product_type,
    )
    @_SETTINGS
    def test_idempotent_and_parseable(
        self, existing: list[str], product_type: str
    ) -> None:
        body = ", ".join(repr(v) for v in existing)
        old_source = f"ProductType = Literal[{body}]\n"
        pm = ProposedModel(
            class_name="Widget",
            product_type=product_type,
            docstring="d",
            fields=[],
        )
        once = render_product_type_patch(old_source, pm)
        twice = render_product_type_patch(once, pm)
        assert once == twice
        values = ast.literal_eval(
            once[once.index("[") : once.rindex("]") + 1]  # noqa: E203
        )
        assert product_type in values
        assert set(existing) <= set(values)


class TestReasoningDoc:
    """``render_reasoning_doc`` is pure text: total, and always a document."""

    @given(pm=_proposed_models())
    @_SETTINGS
    def test_total_and_shaped(self, pm: ProposedModel) -> None:
        doc = render_reasoning_doc(pm)
        assert isinstance(doc, str)
        assert doc.startswith(f"# {pm.class_name} Model")
        assert doc.endswith("\n")
        assert "## Scope" in doc
        assert "## Sources" in doc
        assert "## Design decisions" in doc
        assert "## Fields" in doc
