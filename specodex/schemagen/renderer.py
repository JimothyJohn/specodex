"""Deterministic source rendering from a ``ProposedModel``.

Three pure functions:

- ``render_model_file(pm)`` — returns the contents of a new
  ``specodex/models/<type>.py`` file.
- ``render_reasoning_doc(pm)`` — returns the companion
  ``specodex/models/<type>.md`` that explains the schema's
  scope, design decisions, and source citations. Every proposed
  model now ships with one; see ``contactor.md`` for the shape.
- ``render_product_type_patch(old_common_py_source, pm)`` — returns
  a new ``common.py`` source string with the new ``product_type``
  appended to the ``ProductType = Literal[...]`` line. Idempotent.

The output of ``render_model_file`` is passed through ``ast.parse``
before any caller writes it to disk — if we emit something that
doesn't parse, we want to fail before touching the repo.
"""

from __future__ import annotations

import ast
import re
from typing import List

from specodex.schemagen.meta_schema import ProposedField, ProposedModel


# Map ``ProposedField.kind`` → Python type annotation string. ``literal``
# is handled specially because it needs ``literal_values``.
_KIND_TO_ANNOTATION: dict[str, str] = {
    "int": "Optional[int]",
    "float": "Optional[float]",
    "str": "Optional[str]",
    "bool": "Optional[bool]",
    "list_str": "Optional[List[str]]",
    "value_unit": "Optional[ValueUnit]",
    "min_max_unit": "Optional[MinMaxUnit]",
}

# Kinds that need importing from ``specodex.models.common``.
_COMMON_KINDS: frozenset[str] = frozenset({"value_unit", "min_max_unit"})

# Kinds that need ``List`` from typing.
_LIST_KINDS: frozenset[str] = frozenset({"list_str"})


def _needs_escaping(char: str) -> bool:
    """True when ``char`` can't be dropped verbatim into a docstring body.

    ``"`` and ``\\`` can close the literal early or escape its closer. A
    carriage return is rewritten by Python's source line-ending
    normalisation, silently changing the docstring. Every other C0/C1 control
    character is either illegal in source (``compile()`` rejects a NUL
    outright) or invisible in the rendered file; ``\\n`` and ``\\t`` are the
    two that read fine inside a triple-quoted string. A lone surrogate —
    which ``json.loads`` will happily produce from a ``\\udNNN`` escape in the
    LLM's response — can't be encoded as UTF-8 source at all.
    """
    if char in ('"', "\\"):
        return True
    if char in ("\n", "\t"):
        return False
    code = ord(char)
    return code < 0x20 or code == 0x7F or 0xD800 <= code <= 0xDFFF


def _docstring_literal(text: str) -> str:
    """Render ``text`` as a triple-quoted string literal that always parses.

    The LLM controls the docstring, so it can contain a ``"``, a trailing
    backslash, a literal ``\"\"\"``, or a NUL — each of which either breaks the
    generated file or (with an embedded ``\"\"\"``) closes the docstring early
    and lets the rest of the text land as executable statements. Escaping every
    such character makes the literal total *and* exact: the rendered docstring
    always evaluates back to ``text``. The common case (no quotes, no
    backslashes, no control characters beyond newline/tab) is emitted verbatim
    so ordinary docstrings stay readable.
    """
    if not any(_needs_escaping(c) for c in text):
        return f'"""{text}"""'
    out: List[str] = []
    for char in text:
        if not _needs_escaping(char):
            out.append(char)
        elif char in ('"', "\\"):
            out.append("\\" + char)
        elif ord(char) > 0xFF:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(f"\\x{ord(char):02x}")
    return '"""' + "".join(out) + '"""'


def _comment_text(text: str) -> str:
    """Flatten ``text`` for use inside a single ``#`` comment line.

    A newline in a section label would end the comment and let the remainder
    render as class-body source; a NUL or a lone surrogate would make the whole
    file uncompilable. Those are dropped, and every whitespace run collapses to
    a single space.
    """
    # Quotes and backslashes are inert inside a comment, so only the
    # characters that break the file itself are dropped.
    stripped = "".join(
        c
        for c in text
        if c in ('"', "\\", "\n", "\t") or not _needs_escaping(c)  # noqa: E501
    )
    return " ".join(stripped.split())


def _annotation_for(field: ProposedField) -> str:
    if field.kind == "literal":
        # ``literal_values`` guaranteed non-empty by ProposedField validator.
        values = ", ".join(repr(v) for v in field.literal_values or [])
        return f"Optional[Literal[{values}]]"
    return _KIND_TO_ANNOTATION[field.kind]


def _field_line(field: ProposedField) -> str:
    annotation = _annotation_for(field)
    return (
        f"    {field.name}: {annotation} = "
        f"Field(None, description={field.description!r})"
    )


def _build_imports(pm: ProposedModel) -> List[str]:
    """Compute the set of imports needed for the rendered class."""
    kinds = {f.kind for f in pm.fields}
    has_literal_field = "literal" in kinds or bool(pm.subtype_values)
    has_list = bool(_LIST_KINDS & kinds)
    has_common = bool(_COMMON_KINDS & kinds)

    typing_imports: List[str] = ["Optional"]
    if has_list:
        typing_imports.append("List")
    if has_literal_field:
        typing_imports.append("Literal")
    typing_imports.sort()

    lines: List[str] = [
        "from __future__ import annotations",
        "",
        f"from typing import {', '.join(typing_imports)}",
        "",
        "from pydantic import Field",
        "",
    ]
    if has_common:
        common_imports = sorted(
            {
                "ValueUnit" if "value_unit" in kinds else None,
                "MinMaxUnit" if "min_max_unit" in kinds else None,
            }
            - {None}
        )
        # type: ignore[arg-type]  (the None filter keeps mypy happy at runtime)
        lines.append(
            "from specodex.models.common import "
            + ", ".join(str(name) for name in common_imports)
        )
    lines.append("from specodex.models.product import ProductBase")
    return lines


def _build_class_body(pm: ProposedModel) -> List[str]:
    lines: List[str] = [
        f"class {pm.class_name}(ProductBase):",
        f"    {_docstring_literal(pm.docstring)}",
        "",
        f"    product_type: Literal[{pm.product_type!r}] = {pm.product_type!r}",
    ]
    if pm.subtype_values:
        values = ", ".join(repr(v) for v in pm.subtype_values)
        lines.append(f"    type: Optional[Literal[{values}]] = None")
    # Auto-render a default `series` line only if the LLM didn't already
    # propose one — otherwise we'd emit the field twice.
    llm_proposed_series = any(f.name == "series" for f in pm.fields)
    if not llm_proposed_series:
        lines.append("    series: Optional[str] = None")

    current_section: str | None = None
    for field in pm.fields:
        if field.section and field.section != current_section:
            lines.append("")
            lines.append(f"    # --- {_comment_text(field.section)} ---")
            current_section = field.section
        lines.append(_field_line(field))
    return lines


def render_model_file(pm: ProposedModel) -> str:
    """Render the full source of a new ``specodex/models/<type>.py``.

    Raises ``SyntaxError`` if the generated source fails to parse — this is
    a last-line-of-defense check against a renderer bug; under normal
    operation the output always parses because only validated tokens
    (names, repr'd strings, dispatch-table annotations) reach the output.
    """
    import_lines = _build_imports(pm)
    class_lines = _build_class_body(pm)
    source = "\n".join(import_lines + ["", ""] + class_lines) + "\n"
    # Smoke-test parseability. Raises SyntaxError on failure.
    ast.parse(source)
    return source


# ---------------------------------------------------------------------
# ``common.py`` ProductType Literal patcher
# ---------------------------------------------------------------------

# Matches:   ProductType = Literal["motor", "drive", ...]
# Tolerates single- or double-quoted entries, multi-line declarations, and
# trailing commas. We capture the inside of the brackets so we can parse
# the existing values via ast and re-render them deterministically.
_PRODUCT_TYPE_RE = re.compile(
    r"^(?P<prefix>ProductType\s*=\s*Literal\[)(?P<body>.*?)(?P<suffix>\])\s*$",
    re.MULTILINE | re.DOTALL,
)


def render_product_type_patch(old_source: str, pm: ProposedModel) -> str:
    """Return ``old_source`` with ``pm.product_type`` added to ``ProductType``.

    Idempotent: if ``pm.product_type`` is already present, returns
    ``old_source`` unchanged. Raises ``ValueError`` if the ``ProductType =
    Literal[...]`` declaration can't be located.
    """
    match = _PRODUCT_TYPE_RE.search(old_source)
    if not match:
        raise ValueError(
            "Could not find 'ProductType = Literal[...]' declaration in "
            "common.py source. Refusing to patch."
        )

    body = match.group("body")
    # Parse the existing values via ast to avoid fragile string parsing.
    try:
        parsed = ast.literal_eval(f"[{body}]")
    except (SyntaxError, ValueError) as e:
        raise ValueError(
            f"Failed to parse existing ProductType values: {e!r}. "
            "Refusing to patch common.py."
        ) from e

    if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
        raise ValueError(
            "ProductType declaration doesn't parse as a list of strings; "
            "refusing to patch."
        )

    if pm.product_type in parsed:
        return old_source  # idempotent

    new_values = parsed + [pm.product_type]
    new_body = ", ".join(repr(v) for v in new_values)
    start, end = match.span("body")
    return old_source[:start] + new_body + old_source[end:]


# ---------------------------------------------------------------------
# Reasoning-doc renderer (<type>.md)
# ---------------------------------------------------------------------

_FALLBACK_SCOPE = (
    "Scope notes weren't provided by the LLM. Fill in which products this "
    "schema is meant to cover and explicit non-goals before merging."
)

_FALLBACK_DESIGN = (
    "Design notes weren't provided by the LLM. Document any non-obvious "
    "decisions — list-vs-scalar choices, unit conventions, normalization "
    "traps — before merging."
)


def _format_sources_section(pm: ProposedModel) -> str:
    if not pm.sources:
        return (
            "## Sources\n\n"
            "_No sources were cited by the LLM. Add the datasheets or "
            "standards this schema is grounded in before merging._\n"
        )
    rows = ["| Source | Relevance |", "|---|---|"]
    for src in pm.sources:
        label = src.name
        if src.url:
            label = f"[{src.name}]({src.url})"
        elif src.local_path:
            label = f"{src.name} (`{src.local_path}`)"
        note = src.relevance_notes or ""
        # Collapse newlines inside table cells.
        note = note.replace("\n", " ")
        rows.append(f"| {label} | {note} |")
    return "## Sources\n\n" + "\n".join(rows) + "\n"


def _format_fields_section(pm: ProposedModel) -> str:
    """Emit a flat table of every proposed field grouped by section.

    Gives the reader a quick audit surface for what the schema captures
    without opening the .py.
    """
    lines: List[str] = ["## Fields", ""]
    current_section: str | None = None
    for field in pm.fields:
        section = field.section or "General"
        if section != current_section:
            lines.append(f"### {section}\n")
            lines.append("| Field | Kind | Unit | Description |")
            lines.append("|---|---|---|---|")
            current_section = section
        kind = field.kind
        if field.kind == "literal" and field.literal_values:
            preview = ", ".join(field.literal_values[:4])
            if len(field.literal_values) > 4:
                preview += ", …"
            kind = f"literal[{preview}]"
        unit = field.unit or ""
        desc = (field.description or "").replace("\n", " ").replace("|", "\\|")
        lines.append(f"| `{field.name}` | {kind} | {unit} | {desc} |")
        # Mark the end of this section group with a blank line when the
        # next field has a different section.
    lines.append("")
    return "\n".join(lines)


def render_reasoning_doc(pm: ProposedModel) -> str:
    """Render the companion `<type>.md` doc for a ProposedModel.

    The shape mirrors `specodex/models/contactor.md`: title,
    scope, source citations, design decisions, and an auto-generated
    field table. The free-form sections come from the LLM's
    ``scope_notes`` and ``design_notes`` fields; when either is empty
    we emit a placeholder instead of omitting the section so the doc
    structure stays predictable.
    """
    title = f"# {pm.class_name} Model — Design Notes & Sources"
    intro = (
        f"Companion to `{pm.product_type}.py`. Explains the field set, "
        "why it was chosen, and the datasheets / standards the design "
        "was grounded in. Regenerate this doc if the model changes "
        "materially."
    )

    scope = pm.scope_notes or _FALLBACK_SCOPE
    design = pm.design_notes or _FALLBACK_DESIGN

    parts = [
        title,
        "",
        intro,
        "",
        "## Scope",
        "",
        scope,
        "",
        _format_sources_section(pm),
        "## Design decisions",
        "",
        design,
        "",
        _format_fields_section(pm),
    ]
    return "\n".join(parts).rstrip() + "\n"
