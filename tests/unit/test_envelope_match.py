"""Envelope backfill matching + page selection (specodex/envelope/match.py)."""

from __future__ import annotations

import pytest

from specodex.envelope.match import (
    agreed_dims,
    match_rows,
    numbers_in,
    value_in_line,
    normalize_designator,
    select_envelope_pages,
    token_prefixes,
)


def row(pn=None, name=None, frame=None):
    return {"part_number": pn, "product_name": name, "frame_size": frame}


@pytest.mark.unit
class TestNormalize:
    def test_separators_and_case(self):
        assert normalize_designator(" mr-j5-10a ") == "MRJ510A"
        assert normalize_designator("SGM7J 01/A") == "SGM7J01A"

    @pytest.mark.parametrize("v", [None, 42, 1.5, [], {}])
    def test_non_strings(self, v):
        assert normalize_designator(v) == ""

    def test_token_prefixes_split_on_alpha_digit(self):
        assert token_prefixes("AB042-005-S2")[:3] == ["AB", "AB042", "AB042005"]
        assert "SGM7J01" in token_prefixes("SGM7J-01AFC6S")


@pytest.mark.unit
class TestMatchRows:
    def test_exact_part_number(self):
        rows = [row("MR-J5-10A"), row("MR-J5-20A")]
        ex = [("MR-J5-10A", "a"), ("MR-J5-20A", "b")]
        assert match_rows(ex, rows) == {0: 0, 1: 1}

    def test_family_prefix_applies_to_every_variant(self):
        rows = [row("AB042-005-S2-P2"), row("AB042-010-S2-P2"), row("AB060-005-S2-P2")]
        ex = [("AB042", "small"), ("AB060", "big")]
        assert match_rows(ex, rows) == {0: 0, 1: 0, 2: 1}

    def test_prefix_must_end_on_token_boundary(self):
        # AB042 is a different frame from AB0420.
        assert match_rows([("AB042", "x")], [row("AB0420-005")]) == {}

    def test_longer_prefix_wins(self):
        rows = [row("SGM7J-01AFC6S")]
        ex = [("SGM7J", "family"), ("SGM7J-01", "frame01")]
        assert match_rows(ex, rows) == {0: 1}

    def test_exact_beats_prefix(self):
        rows = [row("PS060-10")]
        ex = [("PS060", "family"), ("PS060-10", "exact")]
        assert match_rows(ex, rows) == {0: 1}

    def test_conflicting_tie_is_skipped(self):
        rows = [row("AB042-005")]
        ex = [("AB042", "one"), ("AB-042", "two")]
        assert match_rows(ex, rows) == {}

    def test_identical_tie_is_fine(self):
        rows = [row("AB042-005")]
        ex = [("AB042", "same"), ("AB 042", "same")]
        assert match_rows(ex, rows) == {0: 0}

    def test_frame_size_exact_only(self):
        rows = [row("XYZ-123", frame="60"), row("XYZ-600", frame="80")]
        assert match_rows([("60", "flange60")], rows) == {0: 0}

    def test_short_or_digitless_designators_never_prefix_match(self):
        rows = [row("ABC-100"), row("AB1-000")]
        assert match_rows([("ABC", "x"), ("AB", "y")], rows) == {}

    def test_product_name_used_when_no_part_number(self):
        assert match_rows([("C500", "x")], [row(None, "C500 Series 2")]) == {0: 0}

    @pytest.mark.parametrize("d", [None, "", "   ", "--"])
    def test_empty_designators_match_nothing(self, d):
        assert match_rows([(d, "x")], [row("", "", "")]) == {}


@pytest.mark.unit
class TestSelectPages:
    def scores(self, *pairs):
        return [{"page": p, "score": s} for p, s in pairs]

    def test_spec_then_near_then_far(self):
        sc = self.scores((1, 0.9), (10, 0.5), (11, 0.95), (30, 0.99))
        assert select_envelope_pages(sc, [10], 3, total_pages=40) == [10, 11, 30]

    def test_spec_pages_take_at_most_half_the_cap(self):
        # 20 spec pages, two outline drawings right after the table block:
        # the drawings still make the cut.
        sc = self.scores((20, 0.9), (21, 0.9))
        out = select_envelope_pages(sc, list(range(0, 20)), 4, total_pages=60)
        assert out == [0, 1, 20, 21]

    def test_remaining_spec_pages_before_unrelated_drawings(self):
        sc = self.scores(
            (50, 0.99),
        )
        out = select_envelope_pages(sc, [0, 1, 2], 3, total_pages=60)
        assert out == [0, 1, 2]

    @pytest.mark.parametrize("cap", [0, -1, -100])
    def test_non_positive_cap_selects_nothing(self, cap):
        sc = self.scores((1, 0.9), (2, 0.9))
        assert select_envelope_pages(sc, [1, 2], cap, total_pages=5) == []

    def test_out_of_range_and_junk_dropped(self):
        sc = [
            {"page": 99, "score": 1.0},
            {"page": "3", "score": 1.0},
            {"page": 2, "score": float("nan")},
            {"page": 1, "score": 0.5},
        ]
        assert select_envelope_pages(sc, [-1, 7, True], 10, total_pages=5) == [1]

    def test_low_scoring_pages_excluded(self):
        sc = self.scores((1, 0.05), (2, 0.5))
        assert select_envelope_pages(sc, [], 10, total_pages=5) == [2]


@pytest.mark.unit
class TestRealCatalogShapes:
    """Designator/row pairs observed on the 2026-11-24 dev dry run."""

    def test_yaskawa_qualifier_words_and_alpha_run_prefix(self):
        rows = [row("SGMJV-01AAA61"), row("SGMJV-02AAA61")]
        ex = [
            ("SGMJV-01A A6 Without brake", "01"),
            ("SGMJV-02A A6 Without brake", "02"),
        ]
        assert match_rows(ex, rows) == {0: 0, 1: 1}

    def test_brake_variants_take_the_larger_envelope(self):
        rows = [row("SGMJV-01AAA61")]
        ex = [("SGMJV-01A A6 Without brake", 100), ("SGMJV-01A A6 With brake", 140)]
        assert match_rows(ex, rows, size_key=lambda p: p) == {0: 1}
        # No size_key → conflicting tie stays unmatched.
        assert match_rows(ex, rows) == {}

    def test_full_designator_conflict_is_never_size_broken(self):
        ex = [("SGMJV-01A", 100), ("SGMJV-01A", 140)]
        assert match_rows(ex, [row("SGMJV-01AAA61")], size_key=lambda p: p) == {}

    def test_stage_qualifier_in_part_number_selects_that_stage(self):
        rows = [row("ABR090 2-Stage 45:1"), row("ABR090 1-Stage 7:1")]
        ex = [("ABR090 1-stage", "one"), ("ABR090 2-stage", "two")]
        assert match_rows(ex, rows) == {0: 1, 1: 0}

    def test_parker_infix(self):
        rows = [row("637/K D6R 02.S3-7-DEV"), row("637/K D6R 16.S3-7-DEV")]
        ex = [("K D6R 02", "02"), ("K D6R 16", "16")]
        assert match_rows(ex, rows) == {0: 0, 1: 1}

    def test_series_title_product_name_is_not_a_key_when_part_number_exists(self):
        rows = [row("F10010-MEM", "FM060-5-CM")]
        assert match_rows([("FM060-5-CM", "x")], rows) == {}

    def test_frame_digit_run_never_cut(self):
        assert match_rows([("ACS880-01-02", "x")], [row("ACS880-01-025A-3")]) == {}

    def test_frame_only_designators_do_not_match_type_codes(self):
        rows = [row("ACS880-01-105A-3+D150", "ACS880")]
        assert match_rows([("R1 IP20", "x"), ("R2 IP21", "y")], rows) == {}


def _d(**axes):
    return {k: {"value": v, "unit": "mm"} for k, v in axes.items()}


@pytest.mark.unit
class TestAgreedDims:
    def test_identical_readings_agree(self):
        a = _d(length=179.0, width=90.0, height=90.0)
        assert agreed_dims(a, a) == a

    def test_within_ten_percent_keeps_larger_per_axis(self):
        a = _d(length=126.5, width=90.0)
        b = _d(length=120.0, width=92.0)
        assert agreed_dims(a, b) == _d(length=126.5, width=92.0)

    def test_exactly_ten_percent_agrees(self):
        assert agreed_dims(_d(length=100.0), _d(length=90.0)) == _d(length=100.0)

    def test_eleven_percent_on_one_axis_withholds_all(self):
        # Apex AB090 shape: one pass reads the Ø80 pilot as the length.
        a = _d(length=126.5, width=90.0, height=90.0)
        b = _d(length=80.0, width=90.0, height=90.0)
        assert agreed_dims(a, b) is None
        assert agreed_dims(_d(length=100.0), _d(length=89.0)) is None

    def test_axis_missing_in_one_pass_withholds(self):
        a = _d(length=179.0, width=90.0, height=90.0)
        b = _d(length=179.0, width=90.0)
        assert agreed_dims(a, b) is None

    def test_none_or_empty_never_agrees(self):
        a = _d(length=179.0)
        for other in (None, {}, "179", [a]):
            assert agreed_dims(a, other) is None
            assert agreed_dims(other, a) is None
        assert agreed_dims(None, None) is None
        assert agreed_dims({}, {}) is None

    def test_mismatched_units_or_bad_values_never_agree(self):
        assert (
            agreed_dims(_d(length=10.0), {"length": {"value": 10.0, "unit": "in"}})
            is None
        )
        for bad in (0, -5.0, float("nan"), float("inf"), True, "10", None):
            junk = {"length": {"value": bad, "unit": "mm"}}
            assert agreed_dims(junk, junk) is None


# Word boxes copied from the text layer of the Apex AB/ABR catalog, page 11
# (2-stage ABR dimension table): (x0, y0, x1, y1, text, block, line, word).
ABR_WORDS = [
    (57.9, 382.4, 97.7, 390.4, "Dimension", 0, 0, 0),
    (223.0, 382.0, 253.0, 390.0, "ABR090", 86, 2, 0),
    (268.0, 382.0, 302.0, 390.0, "ABR090A", 86, 8, 0),
    (309.0, 382.0, 339.0, 390.0, "ABR115", 86, 3, 0),
    (69.0, 457.0, 80.0, 465.0, "D7", 78, 1, 0),
    (251.0, 457.0, 264.0, 464.0, "116", 37, 6, 0),  # merged ABR090|ABR090A cell
    (337.0, 457.0, 350.0, 464.0, "152", 37, 7, 0),  # ABR115's D7
    (69.0, 551.0, 78.0, 559.0, "L8", 66, 7, 0),
    (229.0, 551.0, 248.0, 559.0, "206.5", 13, 7, 0),
    (416.0, 253.0, 453.0, 261.0, "ABR090A", 125, 0, 2),  # drawing label
]

# A row-oriented spec table: model in the first column, values across.
ROW_WORDS = [
    (40.0, 100.0, 90.0, 108.0, "SGMGV-05D", 1, 0, 0),
    (200.0, 100.0, 215.0, 108.0, "179", 1, 0, 1),
    (260.0, 100.0, 272.0, 108.0, "□90", 1, 0, 2),
    (300.0, 100.0, 312.0, 108.0, "5,5", 1, 0, 3),
    (40.0, 120.0, 90.0, 128.0, "SGMGV-09D", 1, 1, 0),
    (200.0, 120.0, 215.0, 128.0, "201", 1, 1, 1),
    (40.0, 140.0, 60.0, 148.0, "AB", 2, 0, 0),
    (62.0, 140.0, 80.0, 148.0, "090", 2, 0, 1),
    (200.0, 140.0, 215.0, 148.0, "126.5", 2, 0, 2),
]


@pytest.mark.unit
class TestNumbersIn:
    def test_plain_decorated_and_comma_decimals(self):
        assert numbers_in("206.5") == {206.5}
        assert numbers_in("□90") == {90.0}
        assert numbers_in("Ø80h7") == {80.0, 7.0}
        assert 5.5 in numbers_in("5,5")
        assert numbers_in("M8x1.25P") >= {8.0, 1.25}

    def test_non_strings_and_empty(self):
        for junk in (None, 5, "", "—", "N/A"):
            assert numbers_in(junk) == set()


@pytest.mark.unit
class TestValueInLine:
    def test_value_under_designator_column(self):
        assert value_in_line("ABR090", 206.5, ABR_WORDS)

    def test_neighbouring_column_rejected(self):
        # Both Gemini readings put ABR115's 152 on ABR090.
        assert not value_in_line("ABR090", 152, ABR_WORDS)
        assert value_in_line("ABR115", 152, ABR_WORDS)

    def test_merged_cell_spanning_designator_accepted(self):
        assert value_in_line("ABR090", 116, ABR_WORDS)

    def test_number_inside_model_code_never_counts(self):
        # Gemini reading the frame size (090) as a length must not verify.
        assert not value_in_line("ABR090", 90, ABR_WORDS)
        assert not value_in_line("AB090 1-stage", 90, ROW_WORDS)

    def test_value_not_printed_rejected(self):
        # Pass b's invented 7.8 kg; and a computed sum L2+L8.
        assert not value_in_line("ABR090", 7.8, ABR_WORDS)
        assert not value_in_line("ABR090", 254.5, ABR_WORDS)

    def test_value_in_designator_row(self):
        assert value_in_line("SGMGV-05D", 179, ROW_WORDS)
        assert value_in_line("SGMGV-05D", 90.0, ROW_WORDS)
        assert value_in_line("SGMGV-05D", 5.5, ROW_WORDS)
        assert not value_in_line("SGMGV-05D", 201, ROW_WORDS)
        # 179 sits in SGMGV-05D's row; it is not in the 09D row or column.
        assert not value_in_line("SGMGV-09D", 179, ROW_WORDS)

    def test_qualifier_words_ignored_and_split_label_joined(self):
        assert value_in_line("SGMGV-05D Without brake", 179, ROW_WORDS)
        assert value_in_line("AB090 1-stage", 126.5, ROW_WORDS)

    def test_label_with_option_placeholders(self):
        # Omron I74E p.10 prints "SGMJV-A5A@A6@"; Gemini copies "SGMJV-A5A A6".
        words = [
            (35.0, 142.0, 94.0, 149.0, "SGMJV-A5A@A6@", 6, 0, 0),
            (183.0, 142.0, 191.0, 149.0, "94", 6, 1, 0),
            (35.0, 151.0, 94.0, 158.0, "SGMJV-01A@A6@", 6, 13, 0),
            (179.0, 151.0, 196.0, 158.0, "107.5", 6, 14, 0),
        ]
        assert value_in_line("SGMJV-A5A A6 Without brake", 94, words)
        assert value_in_line("SGMJV-01A", 107.5, words)
        assert not value_in_line("SGMJV-01A", 94, words)

    def test_longer_model_code_is_not_the_designator(self):
        # ABR090A is its own column; its values never verify for ABR090.
        words = [
            (223.0, 382.0, 253.0, 390.0, "ABR090", 86, 2, 0),
            (268.0, 382.0, 302.0, 390.0, "ABR090A", 86, 8, 0),
            (275.0, 551.0, 290.0, 559.0, "251", 13, 8, 0),
        ]
        assert not value_in_line("ABR090", 251, words)
        assert value_in_line("ABR090A", 251, words)

    def test_designator_absent_or_junk(self):
        assert not value_in_line("ZZ999", 179, ROW_WORDS)
        for d in (None, "", "  ", 42):
            assert not value_in_line(d, 179, ROW_WORDS)
        for v in (None, float("nan"), float("inf"), True, "179"):
            assert not value_in_line("SGMGV-05D", v, ROW_WORDS)
        assert not value_in_line("SGMGV-05D", 179, [])
        assert not value_in_line("SGMGV-05D", 179, None)
