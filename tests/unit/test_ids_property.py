"""Property tests for ``specodex.ids`` — deterministic product ID generation.

The example-based companion (``test_ids.py``) pins specific shapes
documented by the Parker MPP family-prefix case study. This file
generates *adversarial* inputs — unicode-laced manufacturer names,
non-alphanumeric SKUs, families that overlap their parts in
unexpected ways — and asserts the documented contracts hold for
every input the strategy can produce.

Why this matters: every ingested product row writes a DynamoDB
primary key derived from ``compute_product_id``. A regression that
collapses two distinct SKUs to one UUID would silently overwrite
one with the other on the next ingest; a regression that splits a
single SKU across two UUIDs would inflate row counts and break
de-duplication. The contracts below pin both directions.

**Contracts under test:**

1. ``normalize_string`` always returns a ``str`` and the result
   contains only ``[a-z0-9]``. Idempotent: ``normalize(normalize(s))
   == normalize(s)``. Never raises on any ``Optional[str]`` input.

2. ``compute_product_id`` returns either a ``uuid.UUID`` in the
   ``PRODUCT_NAMESPACE`` (UUID5) or ``None``. Never raises on any
   combination of ``Optional[str]`` inputs.

3. Deterministic: same inputs → same output.

4. Normalization-equivalence: inputs that normalize identically
   produce identical UUIDs (case drift, punctuation drift, leading/
   trailing whitespace are all absorbed).

5. Sparsity rule: returns ``None`` exactly when the manufacturer
   normalizes to empty, OR when *both* part-number and product-name
   normalize to empty.

6. Part-number precedence: when part_number normalizes to a
   non-empty string, the ID depends on it (not on product_name).

7. Family-prefix safety: ``product_family`` only collapses inputs
   when it is a strict prefix of the normalized part_number AND the
   leftover satisfies the safety constraint (≥3 chars, contains a
   digit). When the family is not a prefix, the family argument is
   a no-op — ID matches the no-family call.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from specodex.ids import (
    PRODUCT_NAMESPACE,
    compute_product_id,
    normalize_string,
)


# ---------------------------------------------------------------------------
# Adversarial input strategies
# ---------------------------------------------------------------------------


# Realistic-looking manufacturer / part / name tokens. Mix of plain
# ASCII, spaces, common punctuation found in catalogs (dashes,
# slashes, periods, parens).
_CATALOG_TEXT = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x7E,
        # Keep printable ASCII — the realistic-input slice.
    ),
    min_size=0,
    max_size=40,
)


# Unicode-laced strings — BMP + supplementary planes. Catches mojibake,
# zero-width characters, RTL marks, control chars that a naive string
# splitter or hash function would mis-handle.
_UNICODE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
    min_size=0,
    max_size=30,
)


# Inputs typed ``Optional[str]`` per the function signature.
_OPTIONAL_STR = st.one_of(
    st.none(),
    _CATALOG_TEXT,
    _UNICODE_TEXT,
)


# ---------------------------------------------------------------------------
# normalize_string properties
# ---------------------------------------------------------------------------


class TestNormalizeStringProperties:
    """The function lowercases, strips non-alphanumeric, and never raises."""

    @given(s=_OPTIONAL_STR)
    @settings(max_examples=300, deadline=None)
    def test_returns_string_never_raises(self, s: Optional[str]) -> None:
        try:
            out = normalize_string(s)
        except Exception as exc:  # pragma: no cover — regression
            pytest.fail(
                f"normalize_string raised {type(exc).__name__}: {exc!r}\ninput: {s!r}"
            )
        assert isinstance(out, str), (
            f"normalize_string returned {type(out).__name__} (expected str)"
        )

    @given(s=_OPTIONAL_STR)
    @settings(max_examples=300, deadline=None)
    def test_output_is_lowercase_alphanumeric_only(self, s: Optional[str]) -> None:
        """Every char in the result is ``[a-z0-9]`` — the regex is the
        contract. A regression that leaks uppercase or punctuation
        would silently split previously-collapsed SKUs into separate IDs.
        """
        out = normalize_string(s)
        assert all(c.isascii() and (c.isdigit() or c.islower()) for c in out), (
            f"normalize_string emitted non-[a-z0-9] char: {out!r} from {s!r}"
        )

    @given(s=_OPTIONAL_STR)
    @settings(max_examples=200, deadline=None)
    def test_idempotent(self, s: Optional[str]) -> None:
        """``normalize(normalize(s)) == normalize(s)`` — applying it
        twice equals applying it once. If this fails, the family-prefix
        rule's ``part.startswith(family)`` check becomes order-dependent
        on how many times each side was normalized.
        """
        once = normalize_string(s)
        twice = normalize_string(once)
        assert once == twice, (
            f"normalize_string not idempotent: {s!r} → {once!r} → {twice!r}"
        )

    def test_none_returns_empty(self) -> None:
        """Pinned: None is the sparsity sentinel, must return ``""``."""
        assert normalize_string(None) == ""


# ---------------------------------------------------------------------------
# compute_product_id properties
# ---------------------------------------------------------------------------


class TestComputeProductIdProperties:
    """Determinism, normalization-equivalence, and the sparsity rule."""

    @given(
        mfg=_OPTIONAL_STR,
        pn=_OPTIONAL_STR,
        name=_OPTIONAL_STR,
        fam=_OPTIONAL_STR,
    )
    @settings(
        max_examples=300,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_returns_uuid_or_none_never_raises(
        self,
        mfg: Optional[str],
        pn: Optional[str],
        name: Optional[str],
        fam: Optional[str],
    ) -> None:
        """For any ``Optional[str]`` quadruple the result is either a
        ``uuid.UUID`` or ``None``. Anything else escaping is a
        regression at the DynamoDB primary-key boundary.
        """
        try:
            out = compute_product_id(mfg, pn, name, fam)
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"compute_product_id raised {type(exc).__name__}: {exc!r}\n"
                f"inputs: mfg={mfg!r} pn={pn!r} name={name!r} fam={fam!r}"
            )
        assert out is None or isinstance(out, uuid.UUID), (
            f"compute_product_id returned {type(out).__name__} "
            f"(expected UUID or None) from mfg={mfg!r} pn={pn!r} "
            f"name={name!r} fam={fam!r}"
        )

    @given(
        mfg=_OPTIONAL_STR,
        pn=_OPTIONAL_STR,
        name=_OPTIONAL_STR,
        fam=_OPTIONAL_STR,
    )
    @settings(max_examples=200, deadline=None)
    def test_deterministic(
        self,
        mfg: Optional[str],
        pn: Optional[str],
        name: Optional[str],
        fam: Optional[str],
    ) -> None:
        """Same inputs → same output. If this fails the ingest path
        will write multiple rows for the same physical product on
        repeated runs of the same PDF."""
        a = compute_product_id(mfg, pn, name, fam)
        b = compute_product_id(mfg, pn, name, fam)
        assert a == b, (
            f"compute_product_id not deterministic: {a!r} != {b!r} "
            f"for mfg={mfg!r} pn={pn!r} name={name!r} fam={fam!r}"
        )

    @given(
        mfg=_OPTIONAL_STR,
        pn=_OPTIONAL_STR,
        name=_OPTIONAL_STR,
    )
    @settings(max_examples=300, deadline=None)
    def test_sparsity_rule(
        self,
        mfg: Optional[str],
        pn: Optional[str],
        name: Optional[str],
    ) -> None:
        """``compute_product_id`` returns ``None`` exactly when
        normalized manufacturer is empty OR both part_number and
        product_name normalize to empty. Otherwise it returns a UUID.
        """
        out = compute_product_id(mfg, pn, name)
        norm_mfg = normalize_string(mfg)
        norm_pn = normalize_string(pn)
        norm_name = normalize_string(name)
        if not norm_mfg or (not norm_pn and not norm_name):
            assert out is None, (
                f"expected None for sparse inputs (norm_mfg={norm_mfg!r}, "
                f"norm_pn={norm_pn!r}, norm_name={norm_name!r}); got {out!r}"
            )
        else:
            assert isinstance(out, uuid.UUID), (
                f"expected UUID for non-sparse inputs; got {out!r}"
            )

    @given(
        mfg=_OPTIONAL_STR,
        pn=_OPTIONAL_STR,
        name=_OPTIONAL_STR,
    )
    @settings(max_examples=200, deadline=None)
    def test_result_is_uuid5_in_namespace(
        self,
        mfg: Optional[str],
        pn: Optional[str],
        name: Optional[str],
    ) -> None:
        """When the function returns a UUID, that UUID is reproducible
        as ``uuid.uuid5(PRODUCT_NAMESPACE, "<mfg>:<pn-or-name>")``.
        Pins the version-5 + namespace contract so any future swap to
        UUID4 (random!) or a different namespace would be caught here.
        """
        out = compute_product_id(mfg, pn, name)
        if out is None:
            return
        assert out.version == 5, f"expected UUID5, got version {out.version}"
        norm_mfg = normalize_string(mfg)
        norm_pn = normalize_string(pn)
        norm_name = normalize_string(name)
        # Reproduce the exact key the function uses.
        if norm_pn:
            expected = uuid.uuid5(PRODUCT_NAMESPACE, f"{norm_mfg}:{norm_pn}")
        else:
            expected = uuid.uuid5(PRODUCT_NAMESPACE, f"{norm_mfg}:{norm_name}")
        assert out == expected

    @given(
        mfg=_OPTIONAL_STR,
        pn=st.text(min_size=1, max_size=20),  # forced non-empty
        name=_OPTIONAL_STR,
    )
    @settings(max_examples=200, deadline=None)
    def test_part_number_precedence_when_present(
        self,
        mfg: str,
        pn: str,
        name: Optional[str],
    ) -> None:
        """If ``part_number`` normalizes to non-empty, the result must
        not depend on ``product_name``. Changing the name leaves the
        ID unchanged.
        """
        norm_pn = normalize_string(pn)
        assume(norm_pn)  # only test the path where part_number wins
        a = compute_product_id(mfg, pn, name)
        b = compute_product_id(mfg, pn, (name or "") + "x-extra-suffix")
        assert a == b, (
            f"part_number precedence violated: mfg={mfg!r} pn={pn!r} "
            f"name1={name!r} → {a!r}; name2 (with suffix) → {b!r}"
        )


class TestNormalizationEquivalence:
    """Inputs that normalize identically must collapse to one UUID."""

    @given(
        mfg=_CATALOG_TEXT,
        pn=_CATALOG_TEXT,
    )
    @settings(max_examples=300, deadline=None)
    def test_case_and_punctuation_drift_collapses(
        self,
        mfg: str,
        pn: str,
    ) -> None:
        """``"Nidec Corp."`` and ``"NIDEC-corp"`` normalize identically
        and must produce identical UUIDs. This is the core invariant
        that lets us de-duplicate the same SKU across vendor catalogs.
        """
        norm_mfg = normalize_string(mfg)
        norm_pn = normalize_string(pn)
        # Only test when both sides are non-empty — sparse inputs short-
        # circuit to None and the equivalence is trivial.
        assume(norm_mfg and norm_pn)

        # Construct an equivalent twin by re-casing + inserting drift
        # characters that normalize_string strips. We use the canonical
        # normalized form on one side and a re-uppercased/punctuated
        # version on the other.
        twin_mfg = mfg.upper() + "  "  # trailing whitespace is stripped
        twin_pn = "-".join(pn) + "."  # dashes + trailing dot are stripped
        # Confirm our twin actually normalizes the same — if not, the
        # test condition isn't met and we skip.
        assume(
            normalize_string(twin_mfg) == norm_mfg
            and normalize_string(twin_pn) == norm_pn
        )

        a = compute_product_id(mfg, pn, None)
        b = compute_product_id(twin_mfg, twin_pn, None)
        assert a == b, (
            f"equivalent inputs collapsed differently: "
            f"({mfg!r}, {pn!r}) → {a!r}; "
            f"({twin_mfg!r}, {twin_pn!r}) → {b!r}"
        )


class TestFamilyPrefixProperties:
    """Family-aware collapse only fires when family is a strict prefix
    AND the leftover passes the documented safety constraint."""

    @given(
        mfg=_CATALOG_TEXT,
        pn=_CATALOG_TEXT,
        fam=_CATALOG_TEXT,
    )
    @settings(max_examples=300, deadline=None)
    def test_unrelated_family_is_a_noop(
        self,
        mfg: str,
        pn: str,
        fam: str,
    ) -> None:
        """When ``product_family`` does NOT prefix the normalized
        part_number, passing it must be equivalent to omitting it.
        """
        norm_pn = normalize_string(pn)
        norm_fam = normalize_string(fam)
        # Force the "unrelated" branch — family is non-empty but is NOT
        # a prefix of part_number.
        assume(norm_fam and norm_pn and not norm_pn.startswith(norm_fam))

        with_fam = compute_product_id(mfg, pn, None, fam)
        without_fam = compute_product_id(mfg, pn, None)
        assert with_fam == without_fam, (
            f"unrelated family must be a no-op: "
            f"with_fam={with_fam!r} without_fam={without_fam!r} "
            f"mfg={mfg!r} pn={pn!r} fam={fam!r}"
        )

    @given(
        mfg=_CATALOG_TEXT,
        fam=st.from_regex(r"[A-Za-z]{2,6}", fullmatch=True),
        # leftover must have ≥3 chars AND contain a digit to trigger collapse
        leftover=st.from_regex(
            r"[A-Za-z0-9]{2,8}[0-9][A-Za-z0-9]{0,4}", fullmatch=True
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_safety_constraint_satisfied_collapses(
        self,
        mfg: str,
        fam: str,
        leftover: str,
    ) -> None:
        """When the leftover after stripping the family prefix has ≥3
        chars AND contains a digit, the prefixed and bare forms collapse.

        Builds the prefixed form from the bare leftover so the prefix
        relationship is guaranteed regardless of normalization quirks.
        """
        norm_mfg = normalize_string(mfg)
        norm_fam = normalize_string(fam)
        norm_leftover = normalize_string(leftover)
        # Drop cases where normalization erased the bits the test needs.
        assume(norm_mfg and norm_fam and norm_leftover)
        assume(len(norm_leftover) >= 3 and any(c.isdigit() for c in norm_leftover))
        # Also drop cases where the leftover itself happens to start
        # with the family — that would re-trigger collapse on the bare
        # side and confuse the symmetry.
        assume(not norm_leftover.startswith(norm_fam))

        prefixed_pn = norm_fam + norm_leftover  # already-normalized form
        bare_pn = norm_leftover

        prefixed_id = compute_product_id(mfg, prefixed_pn, None, fam)
        bare_id = compute_product_id(mfg, bare_pn, None, fam)
        assert prefixed_id == bare_id, (
            f"safety constraint satisfied but collapse did not fire: "
            f"prefixed={prefixed_id!r} bare={bare_id!r} "
            f"mfg={mfg!r} fam={fam!r} leftover={leftover!r}"
        )

    @given(
        mfg=_CATALOG_TEXT,
        fam=st.from_regex(r"[A-Za-z]{2,6}", fullmatch=True),
        # leftover that FAILS the safety constraint: <3 chars OR no digit
        leftover=st.one_of(
            st.from_regex(r"[A-Za-z0-9]{1,2}", fullmatch=True),  # too short
            st.from_regex(r"[A-Za-z]{3,8}", fullmatch=True),  # no digit
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_safety_constraint_violated_no_collapse(
        self,
        mfg: str,
        fam: str,
        leftover: str,
    ) -> None:
        """When the leftover after stripping fails the safety check
        (<3 chars or no digit), the function keeps the full SKU. The
        full-SKU ID must NOT equal what the stripped form would yield.
        """
        norm_mfg = normalize_string(mfg)
        norm_fam = normalize_string(fam)
        norm_leftover = normalize_string(leftover)
        assume(norm_mfg and norm_fam and norm_leftover)
        # Confirm the leftover actually fails the safety check after
        # normalization (post-normalize the chars may have shifted).
        assume(len(norm_leftover) < 3 or not any(c.isdigit() for c in norm_leftover))
        # Avoid the case where leftover itself starts with family.
        assume(not norm_leftover.startswith(norm_fam))

        prefixed_pn = norm_fam + norm_leftover
        bare_pn = norm_leftover

        prefixed_id = compute_product_id(mfg, prefixed_pn, None, fam)
        bare_id = compute_product_id(mfg, bare_pn, None, fam)
        assert prefixed_id != bare_id, (
            f"safety constraint failed but collapse fired anyway: "
            f"prefixed={prefixed_id!r} bare={bare_id!r} "
            f"mfg={mfg!r} fam={fam!r} leftover={leftover!r}"
        )

    @given(
        mfg=_OPTIONAL_STR,
        pn=_OPTIONAL_STR,
        name=_OPTIONAL_STR,
        fam=_OPTIONAL_STR,
    )
    @settings(max_examples=200, deadline=None)
    def test_family_argument_never_introduces_collisions_across_mfgs(
        self,
        mfg: Optional[str],
        pn: Optional[str],
        name: Optional[str],
        fam: Optional[str],
    ) -> None:
        """The family argument only affects part_number normalization;
        the manufacturer namespace stays distinct. Two different
        manufacturers must never produce the same UUID regardless of
        what family token is passed.
        """
        a = compute_product_id(mfg, pn, name, fam)
        # Pick a guaranteed-different mfg by appending a distinct
        # suffix that survives normalize_string.
        other_mfg = (mfg or "") + "xyz999"
        b = compute_product_id(other_mfg, pn, name, fam)
        assume(a is not None and b is not None)
        assert a != b, (
            f"different manufacturers collapsed to same UUID: "
            f"mfg={mfg!r} other_mfg={other_mfg!r} pn={pn!r} fam={fam!r}"
        )


# ---------------------------------------------------------------------------
# Explicit regression cases — pinned shapes so they can't drift even
# if the Hypothesis strategy shifts. Mirrors the convention from
# test_ids.py::TestFamilyPrefixCollapse but with edge-case inputs the
# example tests don't currently exercise.
# ---------------------------------------------------------------------------


class TestExplicitEdgeCases:
    def test_purely_punctuation_inputs_yield_none(self) -> None:
        """Inputs that strip to empty after normalization are sparsity-
        equivalent to None and must return None.
        """
        assert compute_product_id("---", "...", "///") is None
        assert compute_product_id("Acme", "----", None) is None
        assert compute_product_id("Acme", None, "//") is None

    def test_unicode_mfg_strips_to_ascii_alphanum(self) -> None:
        """``normalize_string`` strips non-ASCII; a pure-unicode mfg
        with no [a-z0-9] survivors normalizes to empty → None.
        """
        assert compute_product_id("日本電産", "M-100", None) is None

    def test_unicode_mfg_with_ascii_chars_works(self) -> None:
        """``"Nidec株式会社"`` keeps the ASCII chars, normalizes to
        ``"nidec"``, and produces a UUID.
        """
        out = compute_product_id("Nidec株式会社", "M-100", None)
        assert out is not None
        # Equivalent to passing the ASCII-only form.
        assert out == compute_product_id("Nidec", "M-100", None)

    def test_part_number_equals_family_short_leftover_keeps_full_sku(self) -> None:
        """Family exactly equals part_number means leftover is empty;
        empty leftover is <3 chars and fails the safety check, so the
        full SKU is kept. The ID equals the no-family call.
        """
        with_fam = compute_product_id("Parker", "MPP", None, "MPP")
        without_fam = compute_product_id("Parker", "MPP", None)
        assert with_fam == without_fam
        assert with_fam is not None

    def test_whitespace_only_part_number_falls_back_to_name(self) -> None:
        """``"   "`` normalizes to empty, so the function must fall back
        to product_name even though part_number is technically truthy.
        """
        out = compute_product_id("Nidec", "   ", "D-Series Motor")
        # Equivalent to passing part_number=None.
        assert out == compute_product_id("Nidec", None, "D-Series Motor")

    def test_empty_family_is_a_noop(self) -> None:
        """Empty family must not trigger any collapse logic."""
        a = compute_product_id("Parker", "MPP-1152C", None, "")
        b = compute_product_id("Parker", "MPP-1152C", None)
        assert a == b
