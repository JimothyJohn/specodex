"""Tests for cli.growth — preflight gate composition.

The individual stage functions shell out to live tools (pytest, uv,
gh, git), so we test the composition layer (run_preflight orchestration,
StageResult rendering, board JSON parsing) by injecting fake stage
results or fake subprocess outputs. No live network or live gh calls.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from cli import growth
from cli.growth import StageResult, run_preflight


# ── StageResult rendering ──────────────────────────────────────────


def test_stage_result_renders_pass_without_color():
    r = StageResult("smoke", passed=True, skipped=False, detail="all good")
    out = r.render(use_color=False)
    assert "✓" in out
    assert "smoke" in out
    assert "all good" in out
    assert "\033[" not in out  # no ANSI codes


def test_stage_result_renders_fail_without_color():
    r = StageResult("bench", passed=False, skipped=False, detail="exit 1")
    out = r.render(use_color=False)
    assert "✗" in out
    assert "bench" in out
    assert "exit 1" in out


def test_stage_result_renders_skip_without_color():
    r = StageResult("board", passed=False, skipped=True, detail="skipped via --skip")
    out = r.render(use_color=False)
    assert "—" in out
    assert "board" in out


def test_stage_result_renders_with_color():
    r = StageResult("smoke", passed=True, skipped=False, detail="ok")
    out = r.render(use_color=True)
    assert "\033[" in out  # ANSI codes present


def test_stage_result_includes_duration_when_set():
    r = StageResult("bench", passed=True, skipped=False, detail="ok", duration_s=12.3)
    out = r.render(use_color=False)
    assert "12.3s" in out


def test_stage_result_omits_duration_when_skipped():
    r = StageResult("git", passed=False, skipped=True, detail="skip", duration_s=12.3)
    out = r.render(use_color=False)
    assert "12.3s" not in out


# ── Board parsing (the only stage with parser logic worth unit-testing) ──


def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a fake CompletedProcess for subprocess.run mocking."""

    class Proc:
        pass

    p = Proc()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def test_board_passes_when_no_p0_open():
    payload = json.dumps(
        {
            "items": [
                {"title": "A", "priority": "P1", "status": "Backlog"},
                {"title": "B", "priority": "P2", "status": "Ready"},
                {"title": "C", "priority": "P0", "status": "Done"},
            ],
            "totalCount": 3,
        }
    )
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch("cli.growth.subprocess.run", return_value=_make_proc(stdout=payload)),
    ):
        r = growth._check_board()
    assert r.passed
    assert not r.skipped
    assert "no P0" in r.detail


def test_board_holds_when_p0_open():
    payload = json.dumps(
        {
            "items": [
                {
                    "title": "Stripe webhook broken",
                    "priority": "P0",
                    "status": "In progress",
                },
                {"title": "Other", "priority": "P1", "status": "Backlog"},
            ],
            "totalCount": 2,
        }
    )
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch("cli.growth.subprocess.run", return_value=_make_proc(stdout=payload)),
    ):
        r = growth._check_board()
    assert not r.passed
    assert not r.skipped
    assert "1 P0 open" in r.detail
    assert "Stripe webhook broken" in r.detail


def test_board_counts_multiple_p0():
    payload = json.dumps(
        {
            "items": [
                {"title": "A", "priority": "P0", "status": "Backlog"},
                {"title": "B", "priority": "P0", "status": "Ready"},
                {"title": "C", "priority": "P0", "status": "In review"},
            ],
            "totalCount": 3,
        }
    )
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch("cli.growth.subprocess.run", return_value=_make_proc(stdout=payload)),
    ):
        r = growth._check_board()
    assert not r.passed
    assert "3 P0 open" in r.detail
    assert "+2 more" in r.detail


def test_board_ignores_p0_in_done():
    """P0 cards in Done should not block — they're closed."""
    payload = json.dumps(
        {
            "items": [
                {"title": "Old P0", "priority": "P0", "status": "Done"},
            ],
            "totalCount": 1,
        }
    )
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch("cli.growth.subprocess.run", return_value=_make_proc(stdout=payload)),
    ):
        r = growth._check_board()
    assert r.passed


def test_board_skips_when_gh_missing():
    with patch("cli.growth.shutil.which", return_value=None):
        r = growth._check_board()
    assert r.skipped
    assert "gh CLI not on PATH" in r.detail


def test_board_holds_when_gh_fails():
    """gh on PATH but auth/network fails — that's a HOLD, not a SKIP."""
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "cli.growth.subprocess.run",
            return_value=_make_proc(stderr="gh: not authenticated\n", returncode=1),
        ),
    ):
        r = growth._check_board()
    assert not r.passed
    assert not r.skipped
    assert "FAILED" in r.detail


def test_board_holds_on_non_json_response():
    with (
        patch("cli.growth.shutil.which", return_value="/usr/bin/gh"),
        patch("cli.growth.subprocess.run", return_value=_make_proc(stdout="not json")),
    ):
        r = growth._check_board()
    assert not r.passed
    assert "non-JSON" in r.detail


# ── run_preflight orchestration ────────────────────────────────────


def _stub(name: str, passed: bool, detail: str = "ok", skipped: bool = False):
    return lambda *a, **kw: StageResult(
        name=name, passed=passed, skipped=skipped, detail=detail
    )


def test_run_preflight_returns_zero_when_all_green():
    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", _stub("smoke", True, "ep ok")),
        patch("cli.growth._check_bench", _stub("bench", True, "bench ok")),
        patch("cli.growth._check_board", _stub("board", True, "no P0")),
        patch("cli.growth._check_git", _stub("git", True, "clean")),
    ):
        rc = run_preflight(url="https://example.test", out=out, use_color=False)
    text = out.getvalue()
    assert rc == 0
    assert "READY" in text
    assert "Safe to post" in text
    assert "https://example.test" in text


def test_run_preflight_returns_one_on_any_fail():
    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", _stub("smoke", True)),
        patch("cli.growth._check_bench", _stub("bench", True)),
        patch("cli.growth._check_board", _stub("board", False, "1 P0 open — bad")),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(out=out, use_color=False)
    text = out.getvalue()
    assert rc == 1
    assert "HOLD" in text
    assert "bad" in text
    assert "1 hold reason" in text


def test_run_preflight_skip_omits_stage():
    """--skip board should not invoke _check_board, and result is reported as skipped."""
    out = io.StringIO()
    board_called = []
    with (
        patch("cli.growth._check_smoke", _stub("smoke", True)),
        patch("cli.growth._check_bench", _stub("bench", True)),
        patch(
            "cli.growth._check_board",
            lambda: board_called.append(1) or StageResult("board", True, False, "ok"),
        ),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(skip=("board",), out=out, use_color=False)
    text = out.getvalue()
    assert rc == 0
    assert board_called == []  # _check_board never invoked
    assert "skipped via --skip" in text
    assert "Note:" in text  # warning about skip


def test_run_preflight_skip_does_not_make_failure_into_pass():
    """If the un-skipped stages fail, the run still HOLDs."""
    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", _stub("smoke", False, "broken")),
        patch("cli.growth._check_bench", _stub("bench", True)),
        patch("cli.growth._check_board", _stub("board", True)),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(skip=("board",), out=out, use_color=False)
    assert rc == 1
    assert "HOLD" in out.getvalue()


def test_run_preflight_runs_stages_in_documented_order():
    """smoke → bench → board → git, matching the doc."""
    order: list[str] = []

    def trace(name, passed=True):
        def fn(*a, **kw):
            order.append(name)
            return StageResult(name, passed, False, "ok")

        return fn

    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", trace("smoke")),
        patch("cli.growth._check_bench", trace("bench")),
        patch("cli.growth._check_board", trace("board")),
        patch("cli.growth._check_git", trace("git")),
    ):
        run_preflight(out=out, use_color=False)
    assert order == ["smoke", "bench", "board", "git"]


# ── argparse wiring ────────────────────────────────────────────────


def test_main_accepts_preflight_subcommand(monkeypatch):
    called = {}

    def fake_run(**kw):
        called.update(kw)
        return 0

    monkeypatch.setattr("cli.growth.run_preflight", fake_run)
    rc = growth.main(["preflight", "--url", "https://example.test", "--skip", "bench"])
    assert rc == 0
    assert called["url"] == "https://example.test"
    assert called["skip"] == ("bench",)


def test_main_rejects_invalid_skip():
    with pytest.raises(SystemExit):
        growth.main(["preflight", "--skip", "totally-not-a-stage"])


def test_main_requires_subcommand():
    with pytest.raises(SystemExit):
        growth.main([])
