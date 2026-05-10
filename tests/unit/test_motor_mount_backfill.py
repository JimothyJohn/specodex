"""Unit tests for ``normalize_frame_to_mount``.

Pure function — no DB. The companion integration test in
``tests/integration/test_motor_mount_backfill.py`` exercises the
walker against moto.
"""

from __future__ import annotations

import pytest

from specodex.admin.motor_mount_backfill import normalize_frame_to_mount


class TestNormalizeFrameToMount:
    """Map ``frame_size`` strings to canonical ``MotorMountPattern``."""

    @pytest.mark.parametrize(
        "frame,expected",
        [
            # NEMA — the dominant case for stepper motors.
            ("NEMA 23", "NEMA 23"),
            ("NEMA 17", "NEMA 17"),
            ("NEMA 34", "NEMA 34"),
            # Casing / spacing variations.
            ("nema 23", "NEMA 23"),
            ("Nema23", "NEMA 23"),
            ("NEMA23", "NEMA 23"),
            ("  NEMA 23  ", "NEMA 23"),
            # IEC — servo motor convention.
            ("IEC 71", "IEC 71"),
            ("iec 80", "IEC 80"),
            ("IEC100", "IEC 100"),
            # MAX (Maxon) — the third recognised prefix.
            ("MAX 25", "MAX 25"),
            ("max13", "MAX 13"),
        ],
    )
    def test_canonical_mappings(self, frame: str, expected: str) -> None:
        assert normalize_frame_to_mount(frame) == expected

    @pytest.mark.parametrize(
        "frame",
        [
            None,
            "",
            "   ",
            "\t",
            "\n",
        ],
    )
    def test_empty_inputs_return_none(self, frame) -> None:
        assert normalize_frame_to_mount(frame) is None

    @pytest.mark.parametrize(
        "frame",
        [
            # Unrecognised vendor-specific encodings — per design,
            # these need a per-vendor lookup that's deliberately out
            # of MVP scope. Return None so they stay backfill-pending.
            "60mm",
            "60",
            "frame size 7",
            "1.5",
            # Right prefix, unknown size — don't fabricate a candidate.
            "NEMA 99",
            "IEC 999",
            "MAX 99",
        ],
    )
    def test_unrecognised_returns_none(self, frame: str) -> None:
        assert normalize_frame_to_mount(frame) is None

    def test_non_string_input_returns_none(self) -> None:
        """Defensive — the field is typed Optional[str] but DB rows
        from before strict typing might surface ints / dicts."""
        assert normalize_frame_to_mount(42) is None  # type: ignore[arg-type]
        assert normalize_frame_to_mount({"size": 23}) is None  # type: ignore[arg-type]
        assert normalize_frame_to_mount(["NEMA 23"]) is None  # type: ignore[arg-type]

    def test_idempotent_on_canonical_input(self) -> None:
        """``normalize(normalize(x))`` equals ``normalize(x)``.

        Important because the backfill walker reads the existing
        frame_size value and writes the normalized pattern back —
        if the frame_size happens to already be canonical, the
        result should match.
        """
        for frame in ("NEMA 23", "IEC 71", "MAX 25"):
            once = normalize_frame_to_mount(frame)
            assert once is not None
            twice = normalize_frame_to_mount(once)
            assert twice == once
