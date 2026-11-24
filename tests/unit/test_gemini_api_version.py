"""Every Gemini client must pin the v1beta API.

google-genai 3.6.1 (Dependabot, 2026-11-23, b1370a4) defaults to API
``v1``, which rejects JSON mode outright: ``400 INVALID_ARGUMENT — JSON mode
is not enabled for api version v1. Use api version v1beta instead.`` Every
extraction in this repo uses ``response_mime_type="application/json"``, so
an unpinned ``genai.Client(...)`` silently breaks ingest, mounting-extract,
envelope-extract, price extraction, schemagen and intake.

This guard walks the source tree and fails on any ``genai.Client(...)``
construction that doesn't pass ``http_options``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from specodex.config import GEMINI_HTTP_OPTIONS

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("specodex", "cli", "scripts", "app/backend_py")


def _client_calls():
    for d in SOURCE_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if ".venv" in path.parts or "dist" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Client"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "genai"
                ):
                    yield path.relative_to(ROOT), node


@pytest.mark.unit
def test_there_are_client_sites_to_check():
    assert len(list(_client_calls())) >= 5


@pytest.mark.unit
def test_every_genai_client_pins_http_options():
    unpinned = [
        f"{path}:{node.lineno}"
        for path, node in _client_calls()
        if not any(kw.arg == "http_options" for kw in node.keywords)
    ]
    assert not unpinned, f"genai.Client without http_options: {unpinned}"


@pytest.mark.unit
def test_pinned_version_supports_json_mode():
    assert GEMINI_HTTP_OPTIONS == {"api_version": "v1beta"}
