"""GROWTH — pre-flight gate before high-traffic posts.

Composes the existing health checks into one go/no-go before pressing post
on Show HN, r/PLC, awesome-* PRs, or any other channel where a broken
comparison costs more than a good one (the 90-day HN cooldown rule from
todo/MARKETING.md).

Stages run in order; each is independently skippable via --skip:

    smoke   pytest tests/post_deploy/      every canonical endpoint 200s
    bench   uv run python -m cli.bench     offline quality run, exit 0
    git                                    on master, clean, in sync with origin

Exit code 0 = READY (safe to post). Exit code 1 = HOLD (one or more stages
failed). Skipped stages don't fail the gate but are reported so the user
knows the gate is only as strong as what ran.

Usage:
    uv run python -m cli.growth preflight
    uv run python -m cli.growth preflight --url https://datasheets.advin.io
    uv run python -m cli.growth preflight --skip bench
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://datasheets.advin.io"
STAGES = ("smoke", "bench", "git")

log = logging.getLogger("growth")

_GREEN = "\033[0;32m"
_RED = "\033[0;31m"
_YELLOW = "\033[1;33m"
_RESET = "\033[0m"


@dataclass
class StageResult:
    name: str
    passed: bool
    skipped: bool
    detail: str
    duration_s: float = 0.0

    def render(self, use_color: bool) -> str:
        if self.skipped:
            mark, color = "—", _YELLOW if use_color else ""
        elif self.passed:
            mark, color = "✓", _GREEN if use_color else ""
        else:
            mark, color = "✗", _RED if use_color else ""
        reset = _RESET if use_color else ""
        ts = (
            f" ({self.duration_s:.1f}s)" if self.duration_s and not self.skipped else ""
        )
        return f"  {color}{mark}{reset} {self.name:6s} {self.detail}{ts}"


def _timed(fn, *args, **kwargs):
    start = time.monotonic()
    result = fn(*args, **kwargs)
    result.duration_s = time.monotonic() - start
    return result


def _check_smoke(url: str) -> StageResult:
    """Run pytest tests/post_deploy/ against a URL. Mirrors `./Quickstart smoke`."""
    if not shutil.which("uv"):
        return StageResult(
            "smoke", False, False, "uv not on PATH (required to run pytest)"
        )
    proc = subprocess.run(
        ["uv", "run", "pytest", "tests/post_deploy/", "-q", "--tb=line"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "API_BASE_URL": url},
    )
    if proc.returncode == 0:
        return StageResult(
            "smoke", True, False, f"all canonical endpoints healthy at {url}"
        )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    last = tail[-1] if tail else f"exit {proc.returncode}"
    return StageResult("smoke", False, False, f"FAILED at {url} — {last}")


def _check_bench() -> StageResult:
    """Run offline bench. Pass = exit 0. No regression check yet (phase 2)."""
    if not shutil.which("uv"):
        return StageResult(
            "bench", False, False, "uv not on PATH (required to run bench)"
        )
    proc = subprocess.run(
        ["uv", "run", "python", "-m", "cli.bench"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = tail[-1] if tail else f"exit {proc.returncode}"
        return StageResult("bench", False, False, f"FAILED — {last}")

    latest = ROOT / "outputs" / "benchmarks" / "latest.json"
    detail = "offline run completed"
    if latest.exists():
        try:
            data = json.loads(latest.read_text())
            n = len(data.get("fixtures", [])) if isinstance(data, dict) else 0
            if n:
                detail = f"offline run completed ({n} fixture{'s' if n != 1 else ''})"
        except (json.JSONDecodeError, OSError):
            # latest.json is a best-effort detail enricher. If it's
            # missing or malformed the bench stage already passed
            # (returncode==0 above), so fall back to the generic
            # "offline run completed" message — don't fail preflight
            # over a stale or absent metrics file.
            pass
    return StageResult("bench", True, False, detail)


def _check_git() -> StageResult:
    """Working tree clean, on master, master in sync with origin/master."""
    if not (ROOT / ".git").exists():
        return StageResult("git", False, False, "not a git repo")

    issues: list[str] = []

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return StageResult("git", False, False, "git status failed")
    if status.stdout.strip():
        n = len(status.stdout.strip().splitlines())
        issues.append(f"{n} uncommitted change{'s' if n != 1 else ''}")

    branch_proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    branch = branch_proc.stdout.strip() or "(detached)"

    if branch == "master":
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "rev-parse", "origin/master"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if remote.returncode == 0:
            remote_sha = remote.stdout.strip()
            if local != remote_sha:
                issues.append(
                    f"master diverges from origin/master "
                    f"({local[:7]} vs {remote_sha[:7]})"
                )
        else:
            issues.append("origin/master not found (run `git fetch`?)")
    else:
        issues.append(f"on '{branch}', not master")

    if issues:
        return StageResult("git", False, False, "; ".join(issues))
    return StageResult("git", True, False, "on master, clean, in sync with origin")


def run_preflight(
    url: str = DEFAULT_URL,
    skip: tuple[str, ...] = (),
    use_color: bool | None = None,
    out=sys.stdout,
) -> int:
    """Run all preflight stages and print results. Return 0 on READY, 1 on HOLD."""
    if use_color is None:
        use_color = out.isatty() if hasattr(out, "isatty") else False

    skip_set = set(skip)
    print("GROWTH PREFLIGHT — running...", file=out)
    print(f"  url = {url}", file=out)
    print("", file=out)

    results: list[StageResult] = []
    for name in STAGES:
        if name in skip_set:
            r = StageResult(name, False, True, "skipped via --skip")
            results.append(r)
            print(r.render(use_color), file=out)
            continue
        print(f"  → {name}...", file=out, flush=True)
        # Bare-name dispatch lets unittest.mock.patch rebind the module
        # attribute and have the patch picked up here at call time.
        if name == "smoke":
            r = _timed(_check_smoke, url)
        elif name == "bench":
            r = _timed(_check_bench)
        elif name == "git":
            r = _timed(_check_git)
        else:
            raise AssertionError(f"unknown stage: {name}")
        results.append(r)
        print(r.render(use_color), file=out, flush=True)

    fails = [r for r in results if not r.passed and not r.skipped]
    skipped = [r for r in results if r.skipped]

    print("", file=out)
    if fails:
        color = _RED if use_color else ""
        reset = _RESET if use_color else ""
        print(f"{color}GROWTH PREFLIGHT — HOLD{reset}", file=out)
        print(
            f"  {len(fails)} hold reason{'s' if len(fails) != 1 else ''}. "
            f"Fix before posting to a high-traffic channel.",
            file=out,
        )
        return 1

    color = _GREEN if use_color else ""
    reset = _RESET if use_color else ""
    print(f"{color}GROWTH PREFLIGHT — READY{reset}", file=out)
    if skipped:
        names = ", ".join(s.name for s in skipped)
        print(
            f"  Note: {len(skipped)} stage{'s' if len(skipped) != 1 else ''} "
            f"skipped ({names}) — gate is only as strong as what ran.",
            file=out,
        )
    print("  Safe to post.", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "preflight",
        help="Pre-flight gate before high-traffic posts (Show HN, r/PLC, etc.)",
    )
    p.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Prod base URL to smoke-test (default: {DEFAULT_URL})",
    )
    p.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=list(STAGES),
        help="Skip a stage (repeat for multiple). Skipped stages aren't passes.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    if args.command == "preflight":
        return run_preflight(url=args.url, skip=tuple(args.skip))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
