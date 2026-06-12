"""Unit tests for cli/batch_servo_drives.py target loading and filtering.

Added with the product_type generalization (AC induction motor batch):
the targets file gained a top-level `product_type` key with per-target
override, defaulting to "drive" for the pre-existing drive lists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.batch_servo_drives import (
    DEFAULT_PRODUCT_TYPE,
    _filter,
    _load_targets,
    main,
)


def _write_targets(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(data))
    return path


class TestLoadTargets:
    def test_product_type_defaults_to_drive(self, tmp_path):
        path = _write_targets(
            tmp_path,
            {"manufacturer": "Copley Controls", "targets": [{"slug": "a", "url": "u"}]},
        )
        mfg, product_type, targets = _load_targets(path)
        assert mfg == "Copley Controls"
        assert product_type == DEFAULT_PRODUCT_TYPE == "drive"
        assert len(targets) == 1

    def test_top_level_product_type_read(self, tmp_path):
        path = _write_targets(
            tmp_path,
            {"product_type": "motor", "targets": [{"slug": "a", "url": "u"}]},
        )
        _, product_type, _ = _load_targets(path)
        assert product_type == "motor"

    def test_empty_targets_list(self, tmp_path):
        path = _write_targets(tmp_path, {"product_type": "motor"})
        _, product_type, targets = _load_targets(path)
        assert product_type == "motor"
        assert targets == []


class TestFilterProductType:
    def test_default_product_type_attached(self):
        out = _filter(
            [{"slug": "a", "url": "u"}],
            default_mfg="WEG",
            default_product_type="motor",
            only=None,
            manufacturer=None,
            limit=None,
        )
        assert out[0]["_product_type"] == "motor"
        assert out[0]["_manufacturer"] == "WEG"

    def test_per_target_override_wins(self):
        out = _filter(
            [
                {"slug": "a", "url": "u"},
                {"slug": "b", "url": "u", "product_type": "gearhead"},
            ],
            default_mfg="WEG",
            default_product_type="motor",
            only=None,
            manufacturer=None,
            limit=None,
        )
        assert [t["_product_type"] for t in out] == ["motor", "gearhead"]

    def test_default_product_type_param_defaults_to_drive(self):
        out = _filter(
            [{"slug": "a", "url": "u"}],
            default_mfg=None,
            only=None,
            manufacturer=None,
            limit=None,
        )
        assert out[0]["_product_type"] == "drive"


class TestMainValidation:
    def test_dry_run_ok_with_valid_type(self, tmp_path, capsys):
        path = _write_targets(
            tmp_path,
            {
                "manufacturer": "WEG",
                "product_type": "motor",
                "targets": [{"slug": "a", "url": "https://example.com/a.pdf"}],
            },
        )
        assert main(["--targets", str(path), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "motor" in out

    def test_unknown_product_type_fails_fast(self, tmp_path, capsys):
        path = _write_targets(
            tmp_path,
            {
                "product_type": "warp_drive",
                "targets": [{"slug": "a", "url": "https://example.com/a.pdf"}],
            },
        )
        assert main(["--targets", str(path), "--dry-run"]) == 2
        err = capsys.readouterr().err
        assert "warp_drive" in err

    def test_missing_targets_file(self, tmp_path):
        assert main(["--targets", str(tmp_path / "nope.json"), "--dry-run"]) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
