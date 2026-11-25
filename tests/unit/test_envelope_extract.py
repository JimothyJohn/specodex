"""specodex/envelope extraction guards: designator verification + page remap."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from specodex.envelope.extract import EnvelopeExtraction, remap_pages
from specodex.envelope.match import designator_on_page, normalize_designator

AB_PAGE = "Dimensions (1-stage) / AB Series\nDimension AB042 AB060 AB090 AB115\nL1 42 60 90 115"


@pytest.mark.unit
class TestDesignatorOnPage:
    @pytest.mark.parametrize("d", ["AB090", "AB 090", "AB090 1-stage", "ab-090"])
    def test_printed_labels_verify(self, d):
        assert designator_on_page(d, AB_PAGE)

    def test_invented_label_rejected(self):
        # Live failure 2026-11-24: ABR part numbers stamped onto the AB table.
        assert not designator_on_page("ABR090 1-Stage 5:1", AB_PAGE)

    def test_short_first_word_uses_two(self):
        assert designator_on_page("R1 IP20", "Frame R1 IP20 dimensions")
        assert not designator_on_page("R1 IP20", "Frame R2 IP20 dimensions")

    @pytest.mark.parametrize("d", [None, "", "  ", "AB", "Series", "R IP"])
    def test_generic_or_empty_labels_never_verify(self, d):
        assert not designator_on_page(d, AB_PAGE + " AB Series R IP")

    @pytest.mark.parametrize("text", [None, "", 42])
    def test_no_text_layer_never_verifies(self, text):
        assert not designator_on_page("AB090", text)


@pytest.mark.unit
@settings(max_examples=300)
@given(
    d=st.one_of(st.none(), st.integers(), st.text(max_size=30)),
    text=st.one_of(st.none(), st.text(max_size=200)),
)
def test_designator_on_page_contract(d, text):
    ok = designator_on_page(d, text)
    assert isinstance(ok, bool)
    if ok:
        # Something with a digit, ≥3 chars, taken from the designator's
        # leading words, is printed on the page.
        words = d.split()
        heads = [
            normalize_designator(words[0]),
            normalize_designator(" ".join(words[:2])),
        ]
        assert any(
            len(h) >= 3
            and any(c.isdigit() for c in h)
            and h in normalize_designator(text)
            for h in heads
        )


@pytest.mark.unit
def test_remap_pages_maps_and_drops_out_of_range():
    ex = [
        EnvelopeExtraction(designator=d, page_number=n)
        for d, n in (("a", 1), ("b", 3), ("c", 0), ("d", 9))
    ]
    kept = remap_pages(ex, [40, 41, 52])
    assert [(e.designator, e.page_number) for e in kept] == [("a", 40), ("b", 52)]


def _two_page_pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for label, length in (("AB042", "94"), ("AB060", "121")):
        page = doc.new_page()
        page.insert_text((40, 100), label)
        page.insert_text((200, 100), length)
    return doc.tobytes()


@pytest.mark.unit
def test_truncated_multi_page_response_retries_page_by_page(monkeypatch):
    """Dense tables overflow one 4-page response (inductive-sensor catalogs,
    2026-11-24); the chunk is retried one page at a time, not dropped."""
    import json
    from types import SimpleNamespace

    from specodex.envelope import extract

    calls = []

    def fake_call(pdf_bytes, api_key, schema, prompt):
        import pymupdf

        n = len(pymupdf.open(stream=pdf_bytes, filetype="pdf"))
        calls.append(n)
        if n > 1:
            return SimpleNamespace(text='[{"designator": "AB04', usage_metadata=None)
        text = pymupdf.open(stream=pdf_bytes, filetype="pdf")[0].get_text()
        label = "AB042" if "AB042" in text else "AB060"
        length = 94 if label == "AB042" else 121
        body = [
            {
                "designator": label,
                "length": {"value": length, "unit": "mm"},
                "page_number": 1,
            }
        ]
        return SimpleNamespace(text=json.dumps(body), usage_metadata=None)

    monkeypatch.setattr(extract, "_call_gemini", fake_call)
    out = extract.extract_envelopes(_two_page_pdf(), [0, 1], "k")
    assert calls == [2, 1, 1]
    got = {(e["designator"], e["page_number"], e["length"]["value"]) for e in out}
    assert got == {("AB042", 0, 94.0), ("AB060", 1, 121.0)}
    assert all(e["unverified"] == [] for e in out)


@pytest.mark.unit
def test_truncated_single_page_response_is_skipped(monkeypatch):
    from types import SimpleNamespace

    from specodex.envelope import extract

    calls = []

    def fake_call(pdf_bytes, api_key, schema, prompt):
        calls.append(1)
        return SimpleNamespace(text='[{"designator": "AB04', usage_metadata=None)

    monkeypatch.setattr(extract, "_call_gemini", fake_call)
    assert extract.extract_envelopes(_two_page_pdf(), [0], "k") == []
    assert calls == [1]
