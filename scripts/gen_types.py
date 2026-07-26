#!/usr/bin/env python3
"""Generate app/frontend/src/types/generated.ts from Pydantic models.

This is the codegen step that retires the hand-synced TypeScript interfaces
in ``app/backend/src/types/models.ts`` and ``app/frontend/src/types/models.ts``.
See ``todo/PYTHON_BACKEND.md`` for the migration plan; this script is the
Phase 0 deliverable.

The script imports every concrete Pydantic ``BaseModel`` subclass under
``specodex.models.*`` into a single shim module, then hands that module to
``pydantic2ts`` for TypeScript emission. ``pydantic2ts`` shells out to
``npx json-schema-to-typescript`` — Node 18+ must be on PATH.

Run via:

    ./Quickstart gen-types

CI fails if the committed ``generated.ts`` drifts from source; see
``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types
from pathlib import Path

from pydantic import BaseModel
from pydantic2ts import generate_typescript_defs


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "app" / "frontend" / "src" / "types" / "generated.ts"
# Express backend can't import from app/frontend/* (separate workspace,
# strict rootDir). Emit a small twin file under the backend tree so the
# Zod enum + allowlist can derive from the same source. The two files
# stay byte-identical apart from the path; gen_types.py writes both
# atomically so they can't drift independently of a Pydantic model edit.
# Backend will go away in PYTHON_BACKEND.md Phase 3 and this file with
# it; until then, "duplicate is cheaper" (see todo/MODELGEN.md Phase 0b).
OUTPUT_BACKEND_CONSTANTS = (
    ROOT / "app" / "backend" / "src" / "types" / "generated_constants.ts"
)

# Modules under ``specodex.models`` that hold Pydantic classes. ``llm_schema``
# is excluded because it builds Gemini-shaped schemas at runtime, not domain
# models, and ``common`` exports types we want re-exported through the
# product modules' fields.
_MODEL_MODULES = (
    "specodex.models.common",
    "specodex.models.product",
    "specodex.models.datasheet",
    "specodex.models.manufacturer",
    "specodex.models.motor",
    "specodex.models.drive",
    "specodex.models.gearhead",
    "specodex.models.robot_arm",
    "specodex.models.contactor",
    "specodex.models.electric_cylinder",
    "specodex.models.linear_actuator",
)


def _build_shim_module() -> types.ModuleType:
    """Return a synthetic module whose namespace contains every BaseModel.

    ``pydantic2ts`` walks the module namespace looking for Pydantic
    ``BaseModel`` subclasses; auto-discovering across the ``specodex.models``
    package by hand keeps the public ``__init__`` lean (it currently exports
    only ``Manufacturer``).
    """
    shim = types.ModuleType("specodex_models_codegen_shim")
    seen: set[str] = set()
    for mod_path in _MODEL_MODULES:
        mod = importlib.import_module(mod_path)
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if not issubclass(obj, BaseModel) or obj is BaseModel:
                continue
            if obj.__module__ != mod_path:
                # skip re-exports (e.g. ValueUnit imported into motor.py)
                continue
            if name in seen:
                continue
            setattr(shim, name, obj)
            seen.add(name)
    return shim


def _product_types_constant() -> str:
    """Return TypeScript source for the PRODUCT_TYPES constant.

    The list comes from ``specodex.config.SCHEMA_CHOICES`` (the auto-
    discovered set of concrete ``ProductBase`` subclasses, one per file
    under ``specodex/models/``) — NOT from the ``ProductType`` Literal
    in ``common.py``. The Literal is wider than the actual product set
    historically; ``SCHEMA_CHOICES`` is the truth.
    """
    # Imported here (not at module top) so the import side-effect chain
    # — config.py → models — runs after the shim has been registered.
    from specodex.config import SCHEMA_CHOICES

    type_names = sorted(SCHEMA_CHOICES.keys())
    items = ",\n  ".join(f'"{t}"' for t in type_names)
    return (
        "// ─────────────────────────────────────────────────────────────\n"
        "// Generated constants — derived from SCHEMA_CHOICES in\n"
        "// specodex/config.py (auto-discovered product types). Use the\n"
        "// PRODUCT_TYPES tuple as the single source of truth in TS\n"
        "// (e.g. for a Zod enum or an allowlist).\n"
        "// ─────────────────────────────────────────────────────────────\n"
        "export const PRODUCT_TYPES = [\n"
        f"  {items},\n"
        "] as const;\n"
        "export type ProductTypeLiteral = (typeof PRODUCT_TYPES)[number];\n"
    )


def _product_union_type() -> str:
    """Return TypeScript source for the ``Product`` discriminated union.

    ``pydantic2ts`` emits one interface per Pydantic class but does not
    emit unions. The hand-typed ``app/frontend/src/types/models.ts``
    has historically maintained a ``Product`` union by hand; this
    postscript closes that last gap so the generated module is a true
    superset and Phase 0a-ii (consumer rewire) can re-export ``Product``
    from here instead of hand-typing it.

    The members come from ``SCHEMA_CHOICES`` so adding a new product
    type under ``specodex/models/`` automatically widens the union with
    no further edits — same auto-discovery contract as ``PRODUCT_TYPES``.
    """
    from specodex.config import SCHEMA_CHOICES

    class_names = sorted(cls.__name__ for cls in SCHEMA_CHOICES.values())
    members = " | ".join(class_names)
    return (
        "\n"
        "// ─────────────────────────────────────────────────────────────\n"
        "// Generated discriminated union — same auto-discovery contract\n"
        "// as PRODUCT_TYPES (one interface per concrete ProductBase\n"
        "// subclass under specodex/models/). Discriminator is the\n"
        "// ``product_type`` literal on each interface.\n"
        "// ─────────────────────────────────────────────────────────────\n"
        f"export type Product = {members};\n"
    )


def _backend_constants_module() -> str:
    """Standalone TS module for the backend (Express can't reach into
    the frontend's generated.ts). Re-exports the same constant.
    """
    from specodex.config import SCHEMA_CHOICES

    type_names = sorted(SCHEMA_CHOICES.keys())
    items = ",\n  ".join(f'"{t}"' for t in type_names)
    return (
        "/* eslint-disable */\n"
        "/**\n"
        " * AUTO-GENERATED — do not edit by hand.\n"
        " * Regenerate with: ./Quickstart gen-types\n"
        " * Source: specodex.config.SCHEMA_CHOICES (auto-discovered\n"
        " * product types under specodex/models/).\n"
        " *\n"
        " * Twin of the PRODUCT_TYPES export at the bottom of\n"
        " * app/frontend/src/types/generated.ts. Express's tsconfig\n"
        " * pins rootDir to ./src so it can't import the frontend file\n"
        " * directly; this module is the workaround until the Express\n"
        " * backend retires (PYTHON_BACKEND.md Phase 3).\n"
        " */\n\n"
        "export const PRODUCT_TYPES = [\n"
        f"  {items},\n"
        "] as const;\n"
        "export type ProductTypeLiteral = (typeof PRODUCT_TYPES)[number];\n"
    )


def _unwrap_multiline_unions(source: str) -> str:
    """Collapse prettier-wrapped union types back onto one line.

    ``npx --yes json-schema-to-typescript`` resolves whatever version the
    local npx cache holds; version skew flips prettier's wrap *threshold*
    on borderline-length union types (e.g.
    ``Manufacturer.offered_product_types``), producing wrap-only
    ``generated.ts`` diffs that fail CI's ``test-codegen`` drift gate with
    no semantic change. Rather than guess which lines any given prettier
    would wrap, canonicalise to the fully-unwrapped form: join every
    continuation line prettier emits when wrapping a union — leading-``|``
    member lines and the ``)`` / ``)[]`` group closers — back onto the
    line that opened them. The canonical form is then independent of
    prettier's wrap decisions entirely. Idempotent: input with no
    continuation lines passes through byte-identical.
    """
    out: list[str] = []
    for raw in source.split("\n"):
        stripped = raw.lstrip()
        if not out or not stripped or stripped[0] not in "|)":
            out.append(raw)
            continue
        prev = out[-1].rstrip()
        if stripped[0] == ")":
            # Group closer: rejoin flush against the last member.
            out[-1] = prev + stripped
        elif prev.endswith((":", "=")):
            # First wrapped member: unwrapped form has no leading pipe.
            out[-1] = prev + " " + stripped[1:].lstrip()
        elif prev.endswith("("):
            # First member inside a paren group: no pipe, no space.
            out[-1] = prev + stripped[1:].lstrip()
        else:
            out[-1] = prev + " " + stripped
    return "\n".join(out)


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_BACKEND_CONSTANTS.parent.mkdir(parents=True, exist_ok=True)

    shim = _build_shim_module()
    sys.modules[shim.__name__] = shim

    # pydantic2ts shells out to ``json2ts`` (json-schema-to-typescript). We
    # don't want to require a global npm install — ``npx --yes`` resolves
    # the binary on demand, and pydantic2ts allows multi-word commands
    # (it skips its ``shutil.which`` precheck when the command contains a
    # space — see pydantic2ts/cli/script.py).
    generate_typescript_defs(
        shim.__name__,
        str(OUTPUT),
        json2ts_cmd="npx --yes json-schema-to-typescript",
    )

    banner = (
        "/* eslint-disable */\n"
        "/**\n"
        " * AUTO-GENERATED — do not edit by hand.\n"
        " * Regenerate with: ./Quickstart gen-types\n"
        " * Source: specodex/models/*.py (Pydantic BaseModel subclasses)\n"
        " * Plan:   todo/PYTHON_BACKEND.md\n"
        " */\n\n"
    )
    # Append the PRODUCT_TYPES postscript so consumers have one file to
    # import both interfaces and the canonical product-type tuple from.
    # Then append the Product union — pydantic2ts doesn't emit unions
    # itself, and the Phase 0a-ii rewire needs it generated, not hand-typed.
    OUTPUT.write_text(
        banner
        + _unwrap_multiline_unions(OUTPUT.read_text())
        + "\n"
        + _product_types_constant()
        + _product_union_type()
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)}")

    OUTPUT_BACKEND_CONSTANTS.write_text(_backend_constants_module())
    print(f"wrote {OUTPUT_BACKEND_CONSTANTS.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
