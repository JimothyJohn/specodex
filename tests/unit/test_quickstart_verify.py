"""Contract tests for the local gate: verify stages + git hooks.

`./Quickstart verify` is the pre-push gate and the single source of
truth for what CI runs. These pin (1) that deploy-build is part of the
default stage set, (2) that argparse accepts every stage, and (3) that
the committed git hooks are executable, syntactically valid bash, and
wired to the gate.
"""

import subprocess
from pathlib import Path

import pytest

from cli.quickstart import (
    VERIFY_STAGES,
    build_parser,
    resolve_verify_stages,
)

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "scripts" / "hooks"


class TestVerifyStages:
    def test_default_runs_every_stage_including_deploy_build(self):
        stages = resolve_verify_stages(None)
        assert stages == list(VERIFY_STAGES)
        assert "deploy-build" in stages

    @pytest.mark.parametrize("stage", VERIFY_STAGES)
    def test_only_selects_exactly_one_stage(self, stage):
        assert resolve_verify_stages(stage) == [stage]

    def test_unknown_stage_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_verify_stages("lambda")

    @pytest.mark.parametrize("stage", VERIFY_STAGES)
    def test_argparse_accepts_every_stage(self, stage):
        args = build_parser().parse_args(["verify", "--only", stage])
        assert args.only == stage

    def test_argparse_rejects_unknown_stage(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["verify", "--only", "lambda"])

    def test_hooks_subcommand_defaults_to_status(self):
        args = build_parser().parse_args(["hooks"])
        assert args.action == "status"
        assert build_parser().parse_args(["hooks", "install"]).action == "install"


class TestGitHooks:
    @pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
    def test_hook_exists_and_is_executable(self, name):
        hook = HOOKS / name
        assert hook.is_file(), f"{hook} missing"
        assert hook.stat().st_mode & 0o111, f"{hook} is not executable"
        assert hook.read_text().startswith("#!/usr/bin/env bash")

    @pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
    def test_hook_is_valid_bash(self, name):
        result = subprocess.run(
            ["bash", "-n", str(HOOKS / name)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
    def test_hook_honours_skip_env(self, name):
        # SPECODEX_SKIP_HOOKS=1 must short-circuit before any tool runs,
        # so a hook can never block an emergency commit/push.
        result = subprocess.run(
            ["bash", str(HOOKS / name)],
            cwd=str(ROOT),
            env={"SPECODEX_SKIP_HOOKS": "1", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_pre_push_runs_the_full_gate(self):
        assert "./Quickstart verify" in (HOOKS / "pre-push").read_text()

    def test_pre_commit_is_the_fast_subset(self):
        text = (HOOKS / "pre-commit").read_text()
        assert "ruff check" in text and "ruff format --check" in text
        assert "tsc --noEmit" in text
        assert "Quickstart verify" not in text
