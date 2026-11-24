"""Live Gemini JSON-mode round trip through the production client path.

Deselected in CI (`-m "not live"`); run by hand after a google-genai bump:
    uv run pytest tests/integration/test_gemini_live.py -m live -v

Regression for google-genai 3.6.1 (2026-11-23), whose default API version
``v1`` rejects JSON mode with ``400 INVALID_ARGUMENT``. The offline guard is
tests/unit/test_gemini_api_version.py; this proves the pinned version works.
"""

from __future__ import annotations

import json
import os

import pytest

from specodex.llm import generate_content

pytestmark = [pytest.mark.live, pytest.mark.integration]

HTML = (
    "<table><tr><th>Model</th><th>Rated torque</th><th>Rated speed</th></tr>"
    "<tr><td>ACME-M100</td><td>1.27 Nm</td><td>3000 rpm</td></tr></table>"
)


@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="needs GEMINI_API_KEY")
def test_json_mode_extraction_round_trip():
    response = generate_content(
        HTML,
        os.environ["GEMINI_API_KEY"],
        "motor",
        context={"product_name": "ACME-M100", "manufacturer": "Acme"},
        content_type="html",
    )
    rows = json.loads(response.text)
    assert isinstance(rows, list) and rows, response.text
