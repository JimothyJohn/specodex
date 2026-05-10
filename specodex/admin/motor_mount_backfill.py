"""Backfill ``motor_mount_pattern`` from ``frame_size`` for existing motors.

SCHEMA Phase 1 (PR #87) added the ``motor_mount_pattern`` field. Every
motor row written before that PR has ``motor_mount_pattern: None`` —
Phase 2's job is to derive the canonical pattern from the existing
``frame_size`` string and write it back.

The normalizer is pure and exhaustively unit-tested; the DB walker is
small and uses the existing ``DynamoDBClient.list`` / ``.update`` API
so it works against any stage. ``--dry-run`` is the default —
``--apply`` is the explicit opt-in for writes.

Per ``todo/SCHEMA.md`` Phase 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, get_args

from specodex.db.dynamo import DynamoDBClient
from specodex.models.common import MotorMountPattern
from specodex.models.motor import Motor


# Resolve the closed enum set at import time so a future
# ``MotorMountPattern`` widening (e.g. adding "MAX 55") doesn't
# require touching the normalizer.
_VALID_PATTERNS: frozenset[str] = frozenset(get_args(MotorMountPattern))


def normalize_frame_to_mount(frame: Optional[str]) -> Optional[str]:
    """Map a free-text ``frame_size`` to a canonical ``MotorMountPattern``.

    Returns ``None`` when the input is empty / null / unrecognised —
    callers should leave the existing ``motor_mount_pattern`` field
    untouched in that case (don't write ``None`` over ``None``; don't
    overwrite a value the operator may have hand-edited).

    Per the design note in ``todo/SCHEMA.md``: the "60mm" / "60"
    vendor-specific cases need a per-vendor lookup that's out of MVP
    scope. They return ``None`` here and stay backfill-pending for a
    follow-up pass.
    """
    if not frame or not isinstance(frame, str):
        return None
    raw = frame.strip()
    if not raw:
        return None

    upper = raw.upper()

    # NEMA / IEC / MAX prefixes — extract trailing digits.
    for prefix in ("NEMA", "IEC", "MAX"):
        if upper.startswith(prefix):
            digits = re.sub(r"\D", "", upper)
            if not digits:
                continue
            candidate = f"{prefix} {digits}"
            if candidate in _VALID_PATTERNS:
                return candidate
            return None  # prefix matched but size unknown — skip, don't guess.

    return None


@dataclass
class BackfillResult:
    """Summary of one backfill pass.

    Always pretty-printed via ``__str__`` for the CLI; serialised
    via ``to_dict`` for ``--json`` or programmatic callers.
    """

    considered: int = 0
    already_set: int = 0  # had a non-null motor_mount_pattern
    no_frame_size: int = 0  # frame_size was null/empty — can't derive
    unmatched_frame: int = 0  # frame_size present but didn't match
    matched: int = 0  # would write or did write
    written: int = 0  # actually written (== matched when --apply)
    applied: bool = False
    samples: List[Dict[str, str]] = field(default_factory=list)
    """First 10 (frame_size → pattern) decisions for spot-checking."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "considered": self.considered,
            "already_set": self.already_set,
            "no_frame_size": self.no_frame_size,
            "unmatched_frame": self.unmatched_frame,
            "matched": self.matched,
            "written": self.written,
            "applied": self.applied,
            "samples": self.samples,
        }


def backfill_motor_mounts(
    client: DynamoDBClient,
    *,
    apply: bool = False,
) -> BackfillResult:
    """Walk every Motor row; write ``motor_mount_pattern`` where derivable.

    Idempotent: never overwrites a non-null ``motor_mount_pattern``.
    Read-only when ``apply=False`` (the default).
    """
    result = BackfillResult(applied=apply)
    motors = client.list(Motor)
    for motor in motors:
        result.considered += 1

        if motor.motor_mount_pattern is not None:
            result.already_set += 1
            continue

        if not motor.frame_size:
            result.no_frame_size += 1
            continue

        derived = normalize_frame_to_mount(motor.frame_size)
        if derived is None:
            result.unmatched_frame += 1
            continue

        result.matched += 1
        if len(result.samples) < 10:
            result.samples.append(
                {"frame_size": motor.frame_size, "motor_mount_pattern": derived}
            )

        if apply:
            motor.motor_mount_pattern = derived  # type: ignore[assignment]
            if client.update(motor):
                result.written += 1

    return result
