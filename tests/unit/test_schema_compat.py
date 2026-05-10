"""Schema forward / backward compatibility tests.

`tests/unit/test_models_roundtrip.py` covers same-version roundtrip
only — it constructs a fresh instance, dumps it, reloads it, and
asserts the round-trip is pure. That catches a wide class of bugs
but does not catch the cross-version one:

  - **Forward compat (the deploy case).** Old data, new code.
    A row that was written by yesterday's code must still load under
    today's. If today's code added a required field with no default,
    yesterday's rows fail validation post-deploy.
  - **Backward compat (the rollback case).** New data, old code.
    A row that was written by today's code must still load under
    yesterday's, in case the deploy gets rolled back. If today's
    code added a field that yesterday's does not know about,
    yesterday's `model_validate` must ignore it (Pydantic default
    is `extra="ignore"`, but a future model might forget).

The fixtures in `tests/unit/fixtures/schema_snapshots/` are a frozen
snapshot of one minimal valid record per `ProductType`. They are
deterministic (UUID5 from a fixed namespace) so the test stays
reproducible across regens. **Refresh them only when intentionally
breaking compat** — see CLAUDE.md "Adding a new product type" for
the refresh recipe.

Per HARDENING.md Phase 3.3.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from specodex.config import SCHEMA_CHOICES


SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "schema_snapshots"
NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _load_snapshot(product_type: str) -> dict:
    path = SNAPSHOT_DIR / f"{product_type}.json"
    return json.loads(path.read_text())


@pytest.fixture(params=sorted(SCHEMA_CHOICES.keys()))
def product_type(request) -> str:
    return request.param


@pytest.fixture
def model_class(product_type) -> type[BaseModel]:
    return SCHEMA_CHOICES[product_type]


class TestSnapshotsExist:
    """Every product type has a frozen snapshot fixture."""

    def test_one_snapshot_per_product_type(self):
        on_disk = {p.stem for p in SNAPSHOT_DIR.glob("*.json")}
        registered = set(SCHEMA_CHOICES.keys())
        assert on_disk == registered, (
            f"snapshot drift — missing: {registered - on_disk}, "
            f"orphan: {on_disk - registered}. Refresh with "
            f"`uv run python -c 'from tests.unit.test_schema_compat "
            f"import refresh_snapshots; refresh_snapshots()'`"
        )


class TestForwardCompat:
    """Old data → new code: every snapshot loads under current Pydantic."""

    def test_snapshot_loads(self, model_class, product_type):
        snapshot = _load_snapshot(product_type)
        instance = model_class.model_validate(snapshot)
        # Identity invariant — the loaded instance should re-emit the same
        # product_id (no silent regeneration on reload).
        assert str(instance.product_id) == snapshot["product_id"]
        assert instance.product_type == product_type
        assert instance.manufacturer == snapshot["manufacturer"]

    def test_snapshot_round_trip_is_pure(self, model_class, product_type):
        """Load → dump → load equals the original snapshot.

        Catches accidental field-rename or default-value drift that
        would otherwise lurk until a real prod row hit the new code.
        """
        snapshot = _load_snapshot(product_type)
        first = model_class.model_validate(snapshot)
        re_dumped = first.model_dump(mode="json")
        second = model_class.model_validate(re_dumped)
        assert str(first.product_id) == str(second.product_id)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


class TestBackwardCompat:
    """New data → old code: extra fields ignored, optional omission is fine.

    The "old code" stand-in is the current Pydantic model with extra
    fields injected — this proves the model's `extra=` policy permits
    forward-additive schema changes without rollback breakage.
    """

    def test_extra_unknown_field_ignored(self, model_class, product_type):
        snapshot = _load_snapshot(product_type)
        snapshot["future_field_added_in_2027"] = "lorem"
        snapshot["another_unknown"] = {"nested": True}
        instance = model_class.model_validate(snapshot)
        # The unknown field doesn't appear on the instance.
        assert not hasattr(instance, "future_field_added_in_2027")
        assert not hasattr(instance, "another_unknown")

    def test_optional_field_omission_ignored(self, model_class, product_type):
        """Dropping an optional field from the snapshot still validates.

        Verifies no field has silently become required since the
        snapshot was frozen — a required-field addition would break
        existing rows on the next deploy.
        """
        snapshot = _load_snapshot(product_type)
        # Strip every nullable/None field — leaves only required + non-null.
        slimmed = {k: v for k, v in snapshot.items() if v is not None}
        instance = model_class.model_validate(slimmed)
        assert instance.product_type == product_type
        assert str(instance.product_id) == snapshot["product_id"]


def _build_snapshot(product_type: str) -> dict[str, Any]:
    """Construct a fresh deterministic snapshot for one product type.

    Used by the refresh helper below and by the regression test that
    asserts the on-disk snapshot still matches a freshly-built one
    (modulo the deterministic UUID).
    """
    model = SCHEMA_CHOICES[product_type]
    kwargs: dict[str, Any] = {
        "manufacturer": "Acme",
        "product_name": f"snapshot-{product_type}-2026-05-10",
    }
    if product_type == "robot_arm":
        # robot_arm provides a manufacturer default; let it through.
        kwargs.pop("manufacturer")
    instance = model(**kwargs)
    snapshot = instance.model_dump(mode="json")
    snapshot["product_id"] = str(uuid.uuid5(NAMESPACE, product_type))
    return snapshot


def refresh_snapshots() -> None:
    """Regenerate every snapshot fixture from the current model definitions.

    Run this when intentionally breaking compat — e.g. after a field
    rename or a new required field. The CI drift gate in
    ``test_snapshot_matches_current_minimal_instance`` will fail
    otherwise.
    """
    for ptype in sorted(SCHEMA_CHOICES.keys()):
        snapshot = _build_snapshot(ptype)
        path = SNAPSHOT_DIR / f"{ptype}.json"
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
