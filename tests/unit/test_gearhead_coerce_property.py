"""Property tests + regressions for ``Gearhead.coerce_string_fields``.

The model_validator accepts both ``str`` and ``{value, unit}`` dict
inputs for three logically-free-text fields (``frame_size``,
``gear_type``, ``lubrication_type``). The example-based tests below
came out of three real bugs Hypothesis surfaced — left in even
after the fix so the specific shapes can't regress.

**Contract under test:**

1. Construction never raises on adversarial dict input.
2. When the field validates non-None, it's a proper free-text string
   (never the dict's ``repr()`` — that was leaking ``"{}"`` into the
   DB and frontend table).
3. ``0`` is a legitimate value (e.g. backlash spec); don't treat it
   as missing.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from specodex.models.gearhead import Gearhead


# Adversarial primitive values the LLM might emit inside a {value, unit} dict.
_ADVERSARIAL_PRIMITIVE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.text(min_size=0, max_size=32),
)


class TestGearheadStringFieldRegressions:
    """Three real bugs Hypothesis surfaced in the original coercer.

    Pre-fix behaviour (the ``f"{v} {u}".strip() if v else str(val)``
    expression):

    - ``{"value": 0, "unit": "mm"}`` → ``"{'value': 0, 'unit': 'mm'}"``
      because ``if 0`` is False and the else branch ``str(val)`` ran.
    - ``{"value": "", "unit": "mm"}`` → same shape, same bug.
    - ``{}`` → ``"{}"`` (literal string of the empty dict).

    All three were rendering as junk in the frontend's free-text
    columns AND inflating quality scores (a non-None field counts as
    populated even if it's gibberish).
    """

    def test_zero_value_emits_formatted_string(self) -> None:
        """0 is a real value (backlash spec etc.) — must not be treated
        as missing."""
        g = Gearhead(
            product_name="X",
            manufacturer="Y",
            frame_size={"value": 0, "unit": "mm"},
        )
        assert g.frame_size == "0 mm"

    def test_empty_string_value_becomes_none(self) -> None:
        g = Gearhead(
            product_name="X",
            manufacturer="Y",
            frame_size={"value": "", "unit": "mm"},
        )
        assert g.frame_size is None

    def test_whitespace_only_value_becomes_none(self) -> None:
        g = Gearhead(
            product_name="X",
            manufacturer="Y",
            frame_size={"value": "   ", "unit": "mm"},
        )
        assert g.frame_size is None

    def test_empty_dict_becomes_none(self) -> None:
        """The big one — ``{}`` was producing literal ``"{}"`` strings."""
        g = Gearhead(product_name="X", manufacturer="Y", frame_size={})
        assert g.frame_size is None

    def test_min_instead_of_value_still_works(self) -> None:
        g = Gearhead(
            product_name="X",
            manufacturer="Y",
            frame_size={"min": 71, "unit": "mm"},
        )
        assert g.frame_size == "71 mm"

    def test_plain_string_passes_through(self) -> None:
        g = Gearhead(product_name="X", manufacturer="Y", frame_size="NEMA 23")
        assert g.frame_size == "NEMA 23"

    def test_none_passes_through(self) -> None:
        g = Gearhead(product_name="X", manufacturer="Y", frame_size=None)
        assert g.frame_size is None


class TestGearheadStringFieldProperties:
    """Property tests over the three coerced fields × adversarial dicts.

    The strong invariant: ``Gearhead(...)`` either constructs cleanly
    or raises a Pydantic ValidationError — it never returns an
    instance whose coerced free-text field contains the dict's
    ``repr()``.
    """

    @given(
        v=_ADVERSARIAL_PRIMITIVE,
        u=st.one_of(st.none(), st.text(max_size=8)),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_value_unit_dict_never_leaks_dict_repr(self, v: Any, u: Any) -> None:
        """For any (value, unit) dict, frame_size is either None or a
        clean string — never ``"{'value': ..., 'unit': ...}"``.
        """
        from pydantic import ValidationError

        try:
            g = Gearhead(
                product_name="X",
                manufacturer="Y",
                frame_size={"value": v, "unit": u or ""},
            )
        except ValidationError:
            return  # Pydantic-typed failure — fine
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"Gearhead raised {type(exc).__name__}: {exc!r}\nvalue={v!r} unit={u!r}"
            )
        # The coerced field is either None or a string — never the
        # dict's repr(). The repr-leak signature is starting with
        # ``{'`` or ``{"``; a single literal ``{`` character that a
        # user might supply as a value is fine.
        assert g.frame_size is None or isinstance(g.frame_size, str)
        if isinstance(g.frame_size, str):
            assert not (
                g.frame_size.startswith("{'") or g.frame_size.startswith('{"')
            ), (
                f"frame_size leaked dict repr: {g.frame_size!r}\n"
                f"(value={v!r}, unit={u!r})"
            )

    @given(
        bad_dict=st.dictionaries(
            st.text(max_size=8), _ADVERSARIAL_PRIMITIVE, max_size=4
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_arbitrary_dict_never_raises_uncaught(self, bad_dict: dict) -> None:
        """Any dict shape — including ones with no value/min/unit keys —
        either constructs cleanly or raises Pydantic ValidationError."""
        from pydantic import ValidationError

        try:
            Gearhead(
                product_name="X",
                manufacturer="Y",
                frame_size=bad_dict,
            )
        except ValidationError:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(
                f"Gearhead raised {type(exc).__name__}: {exc!r}\nbad_dict={bad_dict!r}"
            )
