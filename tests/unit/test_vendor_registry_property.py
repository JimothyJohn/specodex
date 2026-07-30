"""Property tests for ``specodex.vendors.registry.slug_for_manufacturer``.

The example-based companion (``test_vendor_registry.py``) pins a table
of real-world manufacturer names. This file feeds *adversarial* inputs
— unicode-laced strings, whitespace-only strings, all-punctuation runs,
already-normalized slugs — and asserts the documented contract holds
for every input the strategy can produce.

Why this matters: ``slug_for_manufacturer`` is the sole join key
between a DynamoDB ``manufacturer`` string and its ``data/vendors/*.json``
profile. A regression that stopped normalizing case, collapsed the
wrong runs to hyphens, or leaked a leading/trailing hyphen would
silently split matching manufacturers into miss + miss and the
citation-required vendor-facts feature would appear broken with no
loud failure.

**Contracts under test:**

1. Never raises on any ``Optional[str]`` input.
2. Return value is always a ``str``.
3. Empty / None / whitespace-only / all-punctuation input returns ``""``.
4. Every non-empty return value matches the module's ``_SLUG_RE``
   (``^[a-z0-9]+(-[a-z0-9]+)*$``) — the same shape ``VendorProfile._check_slug_shape``
   accepts.
5. Idempotent: ``slug(slug(x)) == slug(x)``.
6. Case-insensitive: ``slug(x) == slug(x.upper()) == slug(x.lower())``.
7. Whitespace at the ends does not affect the output.
"""

from __future__ import annotations

import re
from typing import Optional

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.vendors.registry import slug_for_manufacturer

# Mirror of the module-private regex — pinned here so a rename or
# widening in ``registry.py`` also has to update this test explicitly.
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# Adversarial input strategies
# ---------------------------------------------------------------------------


# Realistic catalog manufacturer strings — printable ASCII with the
# punctuation shapes we see in the wild (spaces, dashes, periods,
# parens, ampersands).
_CATALOG_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=40,
)


# Unicode-laced strings across the BMP and supplementary planes —
# catches zero-width joiners, control chars, RTL marks, and CJK ideographs
# a naive lowercaser or regex would mis-handle.
_UNICODE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
    min_size=0,
    max_size=30,
)


_OPTIONAL_STR = st.one_of(st.none(), _CATALOG_TEXT, _UNICODE_TEXT)


# ---------------------------------------------------------------------------
# Basic contract properties
# ---------------------------------------------------------------------------


class TestSlugForManufacturerBasicContract:
    @given(name=_OPTIONAL_STR)
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_string_never_raises(self, name: Optional[str]) -> None:
        try:
            out = slug_for_manufacturer(name)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"slug_for_manufacturer raised {type(exc).__name__}: {exc!r}\n"
                f"input: {name!r}"
            )
        assert isinstance(out, str), (
            f"slug_for_manufacturer returned {type(out).__name__} (expected str)"
        )

    @given(name=_OPTIONAL_STR)
    @settings(max_examples=300, deadline=None)
    def test_non_empty_return_matches_slug_shape(self, name: Optional[str]) -> None:
        """Every non-empty slug must satisfy ``_SLUG_RE`` — the same
        shape ``VendorProfile._check_slug_shape`` enforces. If this
        fails, a lookup-derived slug could produce a value the profile
        loader would reject on the round-trip.
        """
        out = slug_for_manufacturer(name)
        if out:
            assert _SLUG_RE.match(out), (
                f"slug {out!r} from {name!r} does not match {_SLUG_RE.pattern!r}"
            )

    def test_none_returns_empty(self) -> None:
        assert slug_for_manufacturer(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        assert slug_for_manufacturer("") == ""


# ---------------------------------------------------------------------------
# Invariants under equivalent-input transformations
# ---------------------------------------------------------------------------


class TestSlugForManufacturerInvariants:
    @given(name=_OPTIONAL_STR)
    @settings(max_examples=300, deadline=None)
    def test_idempotent(self, name: Optional[str]) -> None:
        """``slug(slug(x)) == slug(x)`` — applying it a second time to
        an already-normalized slug must be a no-op. If this breaks, the
        loader-vs-lookup slug can drift between the write path
        (``VendorProfile.slug`` in the JSON file) and the read path
        (``slug_for_manufacturer`` on the DB string).
        """
        once = slug_for_manufacturer(name)
        twice = slug_for_manufacturer(once)
        assert once == twice, (
            f"slug_for_manufacturer not idempotent: {name!r} → {once!r} → {twice!r}"
        )

    @given(name=_CATALOG_TEXT)
    @settings(max_examples=300, deadline=None)
    def test_case_insensitive(self, name: str) -> None:
        """Lowercasing is unconditional; ``slug(x) == slug(x.upper()) == slug(x.lower())``.
        Real DB rows carry inconsistent case (``"OMRON"`` vs
        ``"Omron"``); both must resolve to the same profile.
        """
        a = slug_for_manufacturer(name)
        b = slug_for_manufacturer(name.upper())
        c = slug_for_manufacturer(name.lower())
        assert a == b == c, (
            f"case-insensitivity broken: {name!r} → {a!r}; "
            f".upper() → {b!r}; .lower() → {c!r}"
        )

    @given(
        name=_CATALOG_TEXT,
        # Whitespace-only prefix/suffix — a mix of spaces, tabs, newlines.
        pad_prefix=st.text(alphabet=" \t\n\r", min_size=0, max_size=5),
        pad_suffix=st.text(alphabet=" \t\n\r", min_size=0, max_size=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_leading_trailing_whitespace_stripped(
        self,
        name: str,
        pad_prefix: str,
        pad_suffix: str,
    ) -> None:
        """Padding a real name with pure whitespace on either end does
        not change the slug. Pins the ``.strip()`` behaviour so a
        regression to e.g. ``.rstrip()`` alone would surface.
        """
        base = slug_for_manufacturer(name)
        padded = slug_for_manufacturer(pad_prefix + name + pad_suffix)
        assert base == padded, (
            f"whitespace padding changed slug: {name!r} → {base!r}; padded → {padded!r}"
        )


# ---------------------------------------------------------------------------
# Shape / structural properties of the returned slug
# ---------------------------------------------------------------------------


class TestSlugForManufacturerShape:
    @given(name=_OPTIONAL_STR)
    @settings(max_examples=200, deadline=None)
    def test_no_leading_or_trailing_hyphen(self, name: Optional[str]) -> None:
        """The final ``.strip("-")`` guarantees no leading/trailing
        hyphens in the output. A leading hyphen would break the
        ``_SLUG_RE`` round-trip; ``VendorProfile._check_slug_shape``
        would reject any profile file named for such a slug.
        """
        out = slug_for_manufacturer(name)
        if out:
            assert not out.startswith("-"), (
                f"slug {out!r} from {name!r} leaks leading hyphen"
            )
            assert not out.endswith("-"), (
                f"slug {out!r} from {name!r} leaks trailing hyphen"
            )

    @given(name=_OPTIONAL_STR)
    @settings(max_examples=200, deadline=None)
    def test_no_consecutive_hyphens(self, name: Optional[str]) -> None:
        """Consecutive non-alphanumerics collapse to a *single* hyphen.
        ``"Allen  Bradley"`` and ``"Allen___Bradley"`` both become
        ``"allen-bradley"`` — a regression that emitted ``"allen--bradley"``
        would fail the ``_SLUG_RE`` shape gate and split matching rows.
        """
        out = slug_for_manufacturer(name)
        assert "--" not in out, f"slug {out!r} from {name!r} leaks consecutive hyphens"

    @given(name=_OPTIONAL_STR)
    @settings(max_examples=200, deadline=None)
    def test_only_lowercase_alnum_and_hyphen(self, name: Optional[str]) -> None:
        """The result carries only ``[a-z0-9-]``. No uppercase, no
        punctuation, no unicode letters escape.
        """
        out = slug_for_manufacturer(name)
        for ch in out:
            assert ch == "-" or (ch.isascii() and (ch.isdigit() or ch.islower())), (
                f"slug {out!r} from {name!r} leaks illegal char {ch!r}"
            )


# ---------------------------------------------------------------------------
# Explicit regression cases — pinned shapes so they can't drift even
# if the Hypothesis strategy shifts. Mirrors the convention from
# test_vendor_registry.py::TestSlugNormalization.
# ---------------------------------------------------------------------------


class TestSlugForManufacturerExplicitEdgeCases:
    @pytest.mark.parametrize(
        "name",
        [
            "   ",  # pure whitespace
            "\t\n",  # tab/newline
            "---",  # dashes-only
            "***",  # punctuation-only
            "()()",  # parens-only
            "!@#$%^&*",  # symbols only
            "。、",  # CJK punctuation only — no [a-z0-9] survives
            "日本電産",  # non-ASCII letters only
            "🚀🎉",  # emoji only
        ],
    )
    def test_no_alphanumeric_survivors_yields_empty(self, name: str) -> None:
        assert slug_for_manufacturer(name) == ""

    def test_ascii_chars_in_unicode_mfg_survive(self) -> None:
        """A mix of unicode letters and ASCII alphanumerics keeps the
        ASCII chars — matches the ``test_ids_property.py`` behaviour
        for ``normalize_string``, since both apply the same
        ``[^a-z0-9]+`` regex after ``.lower()``.
        """
        assert slug_for_manufacturer("Nidec株式会社") == "nidec"

    def test_consecutive_punctuation_collapses(self) -> None:
        assert slug_for_manufacturer("Allen   Bradley") == "allen-bradley"
        assert slug_for_manufacturer("Allen___Bradley") == "allen-bradley"
        assert slug_for_manufacturer("Allen-_-Bradley") == "allen-bradley"

    def test_dotted_corporate_suffix_collapses(self) -> None:
        assert (
            slug_for_manufacturer("Oriental Motor U.S.A. Corp.")
            == "oriental-motor-u-s-a-corp"
        )

    def test_already_normalized_slug_is_stable(self) -> None:
        """A slug returned from an earlier call must survive re-entry
        unchanged — the loader-vs-lookup round trip depends on it.
        """
        assert slug_for_manufacturer("mitsubishi-electric") == "mitsubishi-electric"
        assert slug_for_manufacturer("abb") == "abb"
