"""Tests for cli.growth — preflight gate composition.

The individual stage functions shell out to live tools (pytest, uv,
git), so we test the composition layer (run_preflight orchestration,
StageResult rendering) by injecting fake stage results. No live network
or live subprocess calls.
"""

from __future__ import annotations

import io
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
    r = StageResult("bench", passed=False, skipped=True, detail="skipped via --skip")
    out = r.render(use_color=False)
    assert "—" in out
    assert "bench" in out


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
        patch("cli.growth._check_git", _stub("git", True, "clean")),
    ):
        rc = run_preflight(url="https://example.test", out=out, use_color=False)
    text = out.getvalue()
    assert rc == 0
    assert "READY" in text
    assert "Safe to post" in text
    # Host-only check (not the full https://… URL) — this is an output
    # presence assertion, not URL sanitization, and the substring form
    # otherwise triggers CodeQL's py/incomplete-url-substring-sanitization
    # heuristic.
    assert "example.test" in text


def test_run_preflight_returns_one_on_any_fail():
    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", _stub("smoke", True)),
        patch("cli.growth._check_bench", _stub("bench", False, "exit 1 — bad")),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(out=out, use_color=False)
    text = out.getvalue()
    assert rc == 1
    assert "HOLD" in text
    assert "bad" in text
    assert "1 hold reason" in text


def test_run_preflight_skip_omits_stage():
    """--skip bench should not invoke _check_bench, and result is reported as skipped."""
    out = io.StringIO()
    bench_called = []
    with (
        patch("cli.growth._check_smoke", _stub("smoke", True)),
        patch(
            "cli.growth._check_bench",
            lambda: bench_called.append(1) or StageResult("bench", True, False, "ok"),
        ),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(skip=("bench",), out=out, use_color=False)
    text = out.getvalue()
    assert rc == 0
    assert bench_called == []  # _check_bench never invoked
    assert "skipped via --skip" in text
    assert "Note:" in text  # warning about skip


def test_run_preflight_skip_does_not_make_failure_into_pass():
    """If the un-skipped stages fail, the run still HOLDs."""
    out = io.StringIO()
    with (
        patch("cli.growth._check_smoke", _stub("smoke", False, "broken")),
        patch("cli.growth._check_bench", _stub("bench", True)),
        patch("cli.growth._check_git", _stub("git", True)),
    ):
        rc = run_preflight(skip=("bench",), out=out, use_color=False)
    assert rc == 1
    assert "HOLD" in out.getvalue()


def test_run_preflight_runs_stages_in_documented_order():
    """smoke → bench → git, matching the doc."""
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
        patch("cli.growth._check_git", trace("git")),
    ):
        run_preflight(out=out, use_color=False)
    assert order == ["smoke", "bench", "git"]


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
