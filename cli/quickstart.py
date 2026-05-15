#!/usr/bin/env python3
"""
Specodex CLI — single entry point for all stages.

Usage:
    ./Quickstart dev              Start local dev servers (default)
    ./Quickstart test             Run all unit tests
    ./Quickstart verify           Pre-push gate: lint + tests + build (alias: ci)
                                  Mirrors .github/workflows/ci.yml exactly.
                                  --only python|backend|frontend  Run one stage
                                  --integration                   Add integration tests
    ./Quickstart staging [URL]    Run staging contract tests
    ./Quickstart deploy [--stage] Deploy to AWS via CDK
    ./Quickstart smoke [URL]      Run post-deployment smoke tests
    ./Quickstart admin <sub>      Blacklist + dev/prod data movement
                                  (try: ./Quickstart admin -- --help)
    ./Quickstart bench            Benchmark the ingress pipeline
                                  (try: ./Quickstart bench --help)
    ./Quickstart price-enrich     Backfill MSRP on existing products
                                  (try: ./Quickstart price-enrich --help)
    ./Quickstart ingest-report    Group ingest-log quality-fails by manufacturer
                                  (try: ./Quickstart ingest-report --help)
    ./Quickstart audit-dedupes    DEDUPE — scan dev DB for prefix-drift duplicates.
                                  Phase 1 is read-only; --apply --safe-only --yes
                                  enables Phase 2 auto-merge of safe groups;
                                  --apply --from-review <md> --yes applies
                                  Phase 3 reviewer picks. --dry-run previews.
                                  (try: ./Quickstart audit-dedupes --help)
    ./Quickstart units-triage     Parse outputs/units_migration_review_*.md
                                  and group rows by pattern (auto-rescue vs
                                  manual). Read-only.
                                  (try: ./Quickstart units-triage --help)
    ./Quickstart growth preflight Pre-flight gate before high-traffic posts
                                  (Show HN, r/PLC, awesome-* PRs). Composes
                                  smoke + bench + board P0 check + git state
                                  into one go/no-go. See todo/GROWTH_CLI.md.
                                  (try: ./Quickstart growth preflight --help)
    ./Quickstart godmode          Data-quality observatory — coverage, oddities,
                                  distributions, outliers, drift, etc.
                                  Writes outputs/godmode/<ts>.html for review.
                                  (try: ./Quickstart godmode --help)
    ./Quickstart schemagen PDF --type NAME
                                  Propose a new Pydantic product model from a PDF
                                  (try: ./Quickstart schemagen --help)
    ./Quickstart gen-types        Regenerate app/frontend/src/types/generated.ts
                                  from the Pydantic models under specodex/models/.
                                  Run after editing any Pydantic model. CI fails
                                  if the committed file drifts from source.
                                  See todo/PYTHON_BACKEND.md (Phase 0).

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── Paths ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
LOG_DIR = ROOT / ".logs"

# ── Logging ────────────────────────────────────────────────────────

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "quickstart.log"),
    ],
)
log = logging.getLogger("quickstart")

# Subprocesses (uv, npm, cdk) inherit our env but pull in chatty third-party
# loggers when they invoke Python tooling. Mirror agent.py so a `Quickstart
# process` run doesn't fill quickstart.log with TLS handshake debug.
for _noisy in ("httpcore", "httpx", "google_genai.models.AFC", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Colors ─────────────────────────────────────────────────────────

_USE_COLOR = sys.stderr.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg: str) -> None:
    log.info(_c("0;32", f"==> {msg}"))


def warn(msg: str) -> None:
    log.info(_c("1;33", f"    {msg}"))


def fail(msg: str) -> None:
    log.error(_c("0;31", f"ERROR: {msg}"))
    sys.exit(1)


# ── Helpers ────────────────────────────────────────────────────────


def _local_ip() -> str:
    """Get the LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


def require_cmd(name: str) -> str:
    """Return the path to a command, or fail."""
    path = shutil.which(name)
    if not path:
        fail(f"Missing dependency: {name}. Install it and re-run.")
    return path


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a command, streaming output. Exit on failure with last stderr lines."""
    from collections import deque
    import threading

    merged = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=merged,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    tail: deque[str] = deque(maxlen=20)

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            sys.stderr.write(line)
            sys.stderr.flush()
            tail.append(line.rstrip())

    pump = threading.Thread(target=_pump_stderr, daemon=True)
    pump.start()
    proc.wait()
    pump.join(timeout=2)

    if proc.returncode != 0:
        tail_text = "\n  ".join(tail) if tail else "(no stderr captured)"
        fail(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"  Last stderr lines:\n  {tail_text}"
        )


def run_quiet(cmd: list[str], *, cwd: Path | None = None) -> str:
    """Run a command, capture output. Return stdout."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip()


# Minimum CDKToolkit bootstrap version we consider "current enough" to skip
# `cdk bootstrap` on. aws-cdk-lib has required bootstrap >= 6 since 2021, >= 21
# (the asset-publishing-role era) since mid-2024. The live stack as of
# 2026-05-10 is at version 30. Floor at 21 to allow any reasonably modern
# bootstrap to skip while still forcing an upgrade for ancient ones.
_CDK_BOOTSTRAP_MIN_VERSION = 21


def cdk_toolkit_is_current(region: str) -> bool:
    """Return True iff CDKToolkit stack is healthy and bootstrap version is current.

    `cdk bootstrap` is meant to run once per account/region (or when the CLI's
    bootstrap-template version requirement bumps), not on every deploy. We
    called it unconditionally for a long time because it's idempotent — but
    on 2026-05-10 we discovered the idempotent path still creates+deletes a
    no-op change set on CDKToolkit, requiring `cloudformation:CreateChangeSet`
    / `DescribeChangeSet` / `ExecuteChangeSet` / `DeleteChangeSet` permissions
    the CI deploy role doesn't grant. Skipping when the stack is current
    sidesteps the gap.

    If a future cdk-lib needs a newer bootstrap version, the actual `cdk
    deploy` fails with a clear "bootstrap version too old" error and we
    re-bootstrap from a local terminal (one-shot, with credentials that have
    the change-set permissions).
    """
    result = subprocess.run(
        [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            "CDKToolkit",
            "--region",
            region,
            "--query",
            "Stacks[0].{Status:StackStatus,Outputs:Outputs}",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    stack_status = data.get("Status") or ""
    if not stack_status.endswith("_COMPLETE") or "ROLLBACK" in stack_status:
        return False
    for output in data.get("Outputs") or []:
        if output.get("OutputKey") == "BootstrapVersion":
            try:
                return int(output.get("OutputValue", "0")) >= _CDK_BOOTSTRAP_MIN_VERSION
            except (TypeError, ValueError):
                return False
    return False


def check_node_version() -> str:
    require_cmd("node")
    version = run_quiet(["node", "-v"]).lstrip("v")
    major = int(version.split(".")[0])
    if major < 18:
        fail(f"Node.js >= 18 required (found {version})")
    return version


def check_python_version() -> str:
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 11):
        fail(f"Python >= 3.11 required (found {v.major}.{v.minor})")
    return f"{v.major}.{v.minor}"


def ensure_env_files() -> None:
    info("Checking environment files")
    root_env = ROOT / ".env"
    app_env = APP / ".env"
    if not root_env.exists():
        shutil.copy(ROOT / ".env.example", root_env)
        warn("Created .env from .env.example — edit it with your API keys")
    if not app_env.exists():
        shutil.copy(APP / ".env.example", app_env)
        warn("Created app/.env from app/.env.example — edit it with your AWS config")


def install_python_deps() -> None:
    info("Installing Python dependencies")
    run(["uv", "sync", "--quiet"], cwd=ROOT)


def install_node_deps() -> None:
    """Install Node deps strictly from the lockfile.

    `npm ci` (not `npm install`) so the dep tree is reproducible and the
    hoister can't drift across Node majors. `npm install` was the recurring
    cause of the tsx / esbuild postinstall mismatch (`Expected "0.27.7" but
    got "0.25.11"`) — every `./Quickstart dev` was silently re-resolving
    transitive ranges and rehoisting platform binaries to a layout the
    postinstall then rejected. See PR #96 for the full diagnosis.

    If the user genuinely changed `app/package.json`, `npm ci` fails fast
    with a clear lockfile-mismatch error; the user runs `npm install` once
    to update the lockfile and commits both files together.

    `npm ci` always wipes node_modules and reinstalls — ~5s on a warm
    macOS / npm cache. We don't try to skip when the install is already
    current; the simplest robust check (byte-comparing the committed
    lockfile against npm's internal `node_modules/.package-lock.json`)
    is wrong because npm strips the workspace root entry and adds
    per-package `ideallyInert` markers. A correct skip would need to
    canonicalise both files; not worth 5s of dev-start latency.
    """
    info("Installing Node.js dependencies")
    run(["npm", "ci", "--silent", "--no-audit", "--no-fund"], cwd=APP)


def health_check(url: str, retries: int = 30) -> bool:
    """Poll a health endpoint. Return True if it responds 200."""
    for _ in range(retries):
        try:
            req = Request(f"{url}/health")
            with urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (URLError, OSError):
            # Server may not be up yet — retry until exhausted.
            pass
        time.sleep(1)
    return False


def health_check_verbose(url: str, retries: int) -> tuple[bool, int]:
    """Poll /health, returning (healthy, last_status_code).

    Status code is 0 when every attempt failed at the network level. Used by
    cmd_wait_health to emit a diagnostic line on failure (CI parses it).
    """
    last_code = 0
    for _ in range(retries):
        try:
            req = Request(f"{url}/health")
            with urlopen(req, timeout=2) as resp:
                last_code = resp.status
                if last_code == 200:
                    return True, 200
        except URLError as e:
            inner = getattr(e, "reason", None)
            last_code = getattr(inner, "code", 0) or 0
        except OSError:
            last_code = 0
        time.sleep(1)
    return False, last_code


# ── Commands ───────────────────────────────────────────────────────


def cmd_dev(args: argparse.Namespace) -> None:
    """Start local dev servers with hot reload."""
    info("Checking dependencies")
    node_v = check_node_version()
    py_v = check_python_version()
    require_cmd("npm")
    require_cmd("uv")
    uv_v = run_quiet(["uv", "--version"]).split()[-1]
    log.info(f"  node {node_v}  python {py_v}  uv {uv_v}")

    ensure_env_files()
    install_python_deps()
    install_node_deps()

    port = os.environ.get("PORT", "3001")
    info(f"Starting backend (port {port}) and frontend (port 3000)")

    # `with` guarantees close on every exit path (signal-handler sys.exit
    # raises SystemExit, which propagates through the with-block as a
    # normal exception and runs __exit__).
    with (
        open(LOG_DIR / "backend.log", "w") as backend_log,
        open(LOG_DIR / "frontend.log", "w") as frontend_log,
    ):
        # Local dev always runs in admin mode (full write access)
        admin_env = {**os.environ, "APP_MODE": "admin"}

        backend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=APP / "backend",
            stdout=backend_log,
            stderr=subprocess.STDOUT,
            env=admin_env,
        )
        frontend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=APP / "frontend",
            stdout=frontend_log,
            stderr=subprocess.STDOUT,
        )

        procs = [backend, frontend]

        def shutdown(signum: int = 0, frame: object = None) -> None:
            info("Shutting down...")
            for p in procs:
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    p.kill()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        url = f"http://localhost:{port}"
        if not health_check(url):
            warn(f"Backend may not be ready — check {LOG_DIR}/backend.log")

        host = _local_ip()
        print()
        info("Specodex is running")
        print(f"  Frontend:  http://{host}:3000")
        print(f"  Backend:   http://{host}:{port}")
        print("  Mode:      admin (full access)")
        print(f"  Stage:     {os.environ.get('STAGE', 'dev')}")
        print(f"  Table:     {os.environ.get('DYNAMODB_TABLE_NAME', 'products-dev')}")
        print(f"  Logs:      {LOG_DIR}/")
        print()
        print("  Press Ctrl+C to stop")
        print()

        # Wait for either process to exit
        try:
            while True:
                for p in procs:
                    ret = p.poll()
                    if ret is not None:
                        # Treat SIGTERM (143/-15) and SIGINT (130/-2) as clean
                        # shutdowns — these fire whenever the user hits Ctrl-C
                        # or another process terminates ours, not real failures.
                        if ret in (0, 130, 143, -signal.SIGINT, -signal.SIGTERM):
                            info(f"Process exited cleanly (code {ret})")
                        else:
                            warn(f"Process exited with code {ret}")
                        shutdown()
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown()


def cmd_test(args: argparse.Namespace) -> None:
    """Run all unit tests across Python, backend, and frontend.

    For the full pre-push gate that mirrors CI exactly (lint + tests +
    build), use ``./Quickstart verify`` instead — green here means
    "tests passed" but CI may still fail on lint or build.
    """
    info("Checking dependencies")
    check_node_version()
    check_python_version()
    require_cmd("npm")
    require_cmd("uv")

    ensure_env_files()

    info("Python unit tests")
    run(["uv", "run", "pytest", "tests/unit/", "-m", "not slow", "-q"], cwd=ROOT)

    info("Backend unit tests")
    run(["npm", "test"], cwd=APP / "backend")

    info("Frontend unit tests")
    run(["npm", "test"], cwd=APP / "frontend", env={"CI": "true"})

    info("All unit tests passed")


def cmd_verify(args: argparse.Namespace) -> None:
    """Run exactly what CI runs — the pre-push gate.

    Mirrors ``.github/workflows/ci.yml`` per-job ``run:`` blocks. CI
    invokes the same command via ``./Quickstart verify --only <stage>``,
    so this is the single source of truth for what "tested" means.

    Stages:
      python   ruff check + ruff format --check + pytest tests/unit/
      backend  npm run lint + npm test + npm run build
      frontend npm run lint + npm test + npm run build

    Flags:
      --only <stage>   Run a single stage (CI uses this per job)
      --integration    Add tests/integration/ to the Python stage
    """
    only = getattr(args, "only", None)
    stages = [only] if only else ["python", "backend", "frontend"]
    do_integration = getattr(args, "integration", False)

    info("Checking dependencies")
    if "python" in stages:
        check_python_version()
        require_cmd("uv")
    if "backend" in stages or "frontend" in stages:
        check_node_version()
        require_cmd("npm")
        if not (APP / "node_modules").exists():
            fail("app/node_modules missing — run `(cd app && npm ci)` first.")

    if "python" in stages:
        info("Python: ruff check")
        run(["uv", "run", "ruff", "check"], cwd=ROOT)

        info("Python: ruff format --check")
        run(["uv", "run", "ruff", "format", "--check"], cwd=ROOT)

        # JUnit XML lands in outputs/test-reports/ for the CI step-summary
        # step + actions/upload-artifact. Local runs write the same files;
        # outputs/ is gitignored so it's invisible to the user.
        reports_dir = ROOT / "outputs" / "test-reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        info("Python: pytest tests/unit/ -m 'not slow'")
        run(
            [
                "uv",
                "run",
                "pytest",
                "tests/unit/",
                "-m",
                "not slow",
                "-v",
                f"--junitxml={reports_dir}/python-unit.xml",
            ],
            cwd=ROOT,
        )

        # FastAPI backend (app/backend_py/) — the Express → Python
        # migration target (todo/PYTHON_BACKEND.md). Its deps are in
        # the parent dev group so the same `uv run` env covers them.
        info("Python: pytest app/backend_py/tests/")
        run(
            [
                "uv",
                "run",
                "pytest",
                "app/backend_py/tests/",
                "-v",
                f"--junitxml={reports_dir}/python-backend-py.xml",
            ],
            cwd=ROOT,
        )

        # Billing Lambda (stripe_py/) — the Rust → Python port
        # (todo/PYTHON_STRIPE.md, PYTHON_BACKEND.md Phase 4). It's a
        # standalone uv project (its own pyproject + uv.lock, NOT a
        # workspace member), so it runs in its own directory; `uv run`
        # auto-syncs the stripe_py venv on first use.
        info("Python: pytest stripe_py/tests/")
        run(
            [
                "uv",
                "run",
                "pytest",
                "tests/",
                "-v",
                f"--junitxml={reports_dir}/python-stripe-py.xml",
            ],
            cwd=ROOT / "stripe_py",
        )

        if do_integration:
            info("Python: pytest tests/integration/")
            run(
                [
                    "uv",
                    "run",
                    "pytest",
                    "tests/integration/",
                    "-v",
                    f"--junitxml={reports_dir}/python-integration.xml",
                ],
                cwd=ROOT,
            )

    if "backend" in stages:
        info("Backend: lint")
        run(["npm", "run", "lint"], cwd=APP / "backend")

        info("Backend: test")
        run(["npm", "test"], cwd=APP / "backend")

        info("Backend: build")
        run(["npm", "run", "build"], cwd=APP / "backend")

    if "frontend" in stages:
        info("Frontend: style drift gates")
        _style_drift_check()

        info("Frontend: lint")
        run(["npm", "run", "lint"], cwd=APP / "frontend")

        info("Frontend: test")
        run(["npm", "test"], cwd=APP / "frontend", env={"CI": "true"})

        info("Frontend: build")
        run(["npm", "run", "build"], cwd=APP / "frontend")

    info("All verify stages passed")


def _style_drift_check() -> None:
    """Fail if shipped STYLE.md phases regress.

    Each banned pattern was replaced by an app-native primitive in a
    shipped phase; the gate keeps new code from re-introducing native
    chrome. Phases 3 (toast) and 4 (form noValidate) are not yet shipped,
    so their gates aren't enforced yet.

    Banned today:
      - `title=` JSX attribute   (Phase 1, use <Tooltip>)
      - `target="_blank"` outside <ExternalLink> (Phase 6)

    Patterns are matched against .ts/.tsx/.js/.jsx under
    app/frontend/src/. Test files and the primitive's own implementation
    are allowlisted.
    """
    src = APP / "frontend" / "src"
    extensions = (
        "--include=*.tsx",
        "--include=*.ts",
        "--include=*.jsx",
        "--include=*.js",
    )

    def _grep(pattern: str) -> list[str]:
        result = subprocess.run(
            ["grep", "-RnE", *extensions, pattern, str(src)],
            capture_output=True,
            text=True,
        )
        # grep exits 1 on no matches, 0 on matches, 2 on error.
        if result.returncode not in (0, 1):
            fail(f"grep failed: {result.stderr.strip()}")
        return [line for line in result.stdout.splitlines() if line]

    issues: list[tuple[str, list[str]]] = []

    # Phase 1: title= JSX attribute. The <title> SVG element is naturally
    # excluded since the pattern requires `=` after `title`.
    title_hits = _grep(r'title=["{]')
    if title_hits:
        issues.append(("title= attributes (Phase 1 — use <Tooltip>)", title_hits))

    # Phase 6: target="_blank" outside <ExternalLink>. Allowlist the
    # primitive itself and its test file.
    blank_hits = [
        line
        for line in _grep(r'target="_blank"')
        if "/ui/ExternalLink.tsx" not in line
        and "/ui/ExternalLink.test.tsx" not in line
    ]
    if blank_hits:
        issues.append(('target="_blank" outside <ExternalLink> (Phase 6)', blank_hits))

    if issues:
        log.error("")
        log.error(_c("0;31", "STYLE drift gate failed:"))
        for label, hits in issues:
            log.error(_c("0;31", f"  {label}"))
            for hit in hits:
                log.error(f"    {hit}")
        log.error("")
        fail(
            "Native UI chrome re-introduced — see todo/STYLE.md for the app-native primitive to use instead."
        )


def cmd_staging(args: argparse.Namespace) -> None:
    """Run staging contract tests against a running server."""
    url = args.url
    info(f"Running staging contract tests against {url}")
    warn("Staging tests create/mutate data — do not run against production.")

    check_python_version()
    require_cmd("uv")

    run(
        ["uv", "run", "pytest", "tests/staging/", "-v"],
        cwd=ROOT,
        env={"API_BASE_URL": url},
    )
    info("Staging tests passed")


def _load_env_file(stage: str) -> dict[str, str]:
    """Load app/.env.{stage} if it exists. Returns key-value pairs."""
    env_file = APP / f".env.{stage}"
    if not env_file.exists():
        return {}
    info(f"Loading {env_file.relative_to(ROOT)}")
    result: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key and value:
            result[key.strip()] = value.strip()
    return result


def cmd_deploy(args: argparse.Namespace) -> None:
    """Deploy to AWS via CDK."""
    stage = args.stage
    info(f"Deploying to AWS (stage={stage})")

    check_node_version()
    require_cmd("npm")
    require_cmd("aws")

    # Load stage-specific env file (os.environ takes precedence)
    stage_env = _load_env_file(stage)

    # Validate AWS credentials
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail("AWS credentials not configured. Run: aws configure")

    account_id = os.environ.get("AWS_ACCOUNT_ID") or stage_env.get("AWS_ACCOUNT_ID")
    if not account_id:
        account_id = run_quiet(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--query",
                "Account",
                "--output",
                "text",
            ]
        )
        if not account_id:
            fail("AWS_ACCOUNT_ID not set. Export it or configure AWS CLI.")
        info(f"Auto-detected AWS_ACCOUNT_ID: {account_id}")

    if stage == "dev":
        warn("STAGE=dev (default). Use --stage prod for production deployments.")

    region = os.environ.get("AWS_REGION") or stage_env.get("AWS_REGION", "us-east-1")

    deploy_env = {
        "STAGE": stage,
        "APP_MODE": "public",
        "AWS_ACCOUNT_ID": account_id,
        "AWS_REGION": region,
        "CDK_DEFAULT_ACCOUNT": account_id,
        "CDK_DEFAULT_REGION": region,
        "DYNAMODB_TABLE_NAME": os.environ.get(
            "DYNAMODB_TABLE_NAME",
            stage_env.get("DYNAMODB_TABLE_NAME", f"products-{stage}"),
        ),
    }
    # Domain config: os.environ > stage env file. HOSTED_ZONE_NAME is
    # optional — the CDK defaults to the parent of DOMAIN_NAME and resolves
    # the zone via Route53 ListHostedZonesByName at synth time.
    required_domain_keys = ("DOMAIN_NAME", "CERTIFICATE_ARN")
    optional_domain_keys = ("HOSTED_ZONE_NAME",)
    for key in required_domain_keys + optional_domain_keys:
        val = os.environ.get(key) or stage_env.get(key)
        if val:
            deploy_env[key] = val

    # Prod must have domain config — refuse to deploy without it
    if stage == "prod":
        missing = [k for k in required_domain_keys if k not in deploy_env]
        if missing:
            fail(
                f"Production deploy requires domain config: {', '.join(missing)}. "
                f"Set them in app/.env.prod or export as environment variables."
            )

    info("Installing workspace dependencies")
    run(["npm", "install", "--silent"], cwd=APP)

    info("Building frontend (public mode)")
    # VITE_API_VERSION selects which backend the SPA talks to:
    # 'v1' (Express, default) or 'v2' (Python FastAPI at /api/v2/*).
    # See todo/PYTHON_BACKEND.md Phase 1.4 / Phase 2 cutover. The
    # deploy env (app/.env.<stage>) can set it; default is v1.
    run(
        ["npm", "run", "build"],
        cwd=APP / "frontend",
        env={
            "VITE_API_URL": "",
            "VITE_APP_MODE": "public",
            "VITE_API_VERSION": os.environ.get("VITE_API_VERSION", "v1"),
        },
    )

    # Lambda bundle: tsc → dist/, then install prod deps into dist/ so the
    # asset CDK zips is self-contained. Without this the Lambda boots with
    # `Cannot find module 'express'`. Clean dist/ first so stale compiled
    # files from removed sources don't leak into the zip.
    #
    # We generate a backend-rooted lockfile in dist/ (one shot, registry
    # resolve) then `npm ci` against it. The previous approach copied the
    # workspace lockfile (app/package-lock.json) into dist/ to reuse its
    # pins, but that file is workspace-shaped: when a backend dep diverges
    # from the workspace's hoisted version (e.g. backend's jest@30 vs the
    # workspace's hoisted jest@29), the lockfile carries the backend
    # version under `backend/node_modules/<pkg>` — which is unreachable
    # from a non-workspace `npm ci` rooted at dist/, so npm bails with
    # "Missing: <pkg> from lock file" and "Invalid: lock file's X does not
    # satisfy Y". The fix is to generate the dist/-rooted lockfile here.
    info("Building backend Lambda bundle")
    backend_dist = APP / "backend" / "dist"
    if backend_dist.exists():
        shutil.rmtree(backend_dist)
    run(["npm", "run", "build"], cwd=APP / "backend")
    shutil.copy(APP / "backend" / "package.json", backend_dist / "package.json")
    run(
        [
            "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--silent",
        ],
        cwd=backend_dist,
    )
    run(
        [
            "npm",
            "ci",
            "--omit=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--silent",
        ],
        cwd=backend_dist,
    )

    if cdk_toolkit_is_current(region):
        info(
            f"CDK toolkit current (>= v{_CDK_BOOTSTRAP_MIN_VERSION}) — skipping bootstrap"
        )
    else:
        info("Bootstrapping CDK")
        run(
            ["npx", "cdk", "bootstrap", f"aws://{account_id}/{region}"],
            cwd=APP / "infrastructure",
            env=deploy_env,
        )

    info("Deploying all stacks")
    run(
        [
            "npx",
            "cdk",
            "deploy",
            "--all",
            "--require-approval",
            "never",
            "--outputs-file",
            "cdk-outputs.json",
        ],
        cwd=APP / "infrastructure",
        env=deploy_env,
    )

    # Print results
    outputs_file = APP / "infrastructure" / "cdk-outputs.json"
    if outputs_file.exists():
        data = json.loads(outputs_file.read_text())
        site_url = cf_url = api_url = ""
        for stack in data.values():
            for key, val in stack.items():
                if "SiteUrl" in key:
                    site_url = val
                elif "CloudFrontUrl" in key:
                    cf_url = val
                elif "ApiEndpoint" in key:
                    api_url = val

        print()
        info("Specodex deployed successfully")
        print(f"  Stage:      {stage}")
        print(f"  Table:      {deploy_env['DYNAMODB_TABLE_NAME']}")
        if site_url:
            print(f"  App URL:    {site_url}")
            print(f"  CloudFront: {cf_url}")
        elif cf_url:
            print(f"  App URL:    {cf_url}")
        if api_url:
            print(f"  API URL:    {api_url}")
        print(f"  Region:     {region}")
        print(f"  Account:    {account_id}")
        base = site_url or cf_url
        if base:
            print(f"  Health:     {base}/health")
        print()


def cmd_wait_health(args: argparse.Namespace) -> None:
    """Poll <url>/health until 200 or retries exhausted, exit 0/1.

    Replaces the inline `for i in $(seq 1 N); do curl ... sleep 1` bash loop
    in CI. Uses the same `health_check_verbose` helper that `cmd_smoke` uses,
    so the local pre-deploy health gate and CI's wait-for-deploy gate agree
    on what "healthy" means.
    """
    label = args.label or "Service"
    info(f"Waiting for {label} {args.url}/health (timeout {args.retries}s)")
    healthy, code = health_check_verbose(args.url, retries=args.retries)
    if healthy:
        info(f"{label} healthy")
        return
    fail(
        f"{label} /health returned {code} after {args.retries}s "
        f"(expected 200). URL: {args.url}/health"
    )


def cmd_smoke(args: argparse.Namespace) -> None:
    """Run post-deployment smoke tests."""
    url = args.url
    info(f"Smoke testing {url}")

    check_python_version()
    require_cmd("uv")

    # Quick health check before running the full suite
    info("Checking health endpoint")
    if not health_check(url, retries=5):
        warn(f"Health check failed at {url}/health — running tests anyway")

    run(
        ["uv", "run", "pytest", "tests/post_deploy/", "-v"],
        cwd=ROOT,
        env={"API_BASE_URL": url},
    )
    info("Smoke tests passed")


def cmd_cdk_outputs(args: argparse.Namespace) -> None:
    """Look up a value from app/infrastructure/cdk-outputs.json by key substring.

    Tries each --key in order; the first match across any stack wins. Used by CI
    to read CloudFront URLs and distribution IDs without inline python heredocs
    in YAML — keep parsing in Python, where it's testable.
    """
    path = APP / "infrastructure" / "cdk-outputs.json"
    if not path.exists():
        fail(f"cdk-outputs.json not found at {path}")

    data = json.loads(path.read_text())
    for needle in args.key:
        for stack in data.values():
            for k, v in stack.items():
                if needle in k:
                    print(v)
                    return
    if args.allow_missing:
        return
    fail(f"No key matching {args.key} in {path}")


def cmd_process(args: argparse.Namespace) -> None:
    """Process queued PDF uploads from S3."""
    stage = args.stage
    bucket = (
        args.bucket
        or f"datasheetminer-uploads-{stage}-{os.environ.get('AWS_ACCOUNT_ID', 'unknown')}"
    )

    info(f"Processing upload queue: s3://{bucket}/queue/")
    check_python_version()
    require_cmd("uv")

    process_env = {
        "STAGE": stage,
        "DYNAMODB_TABLE_NAME": os.environ.get(
            "DYNAMODB_TABLE_NAME", f"products-{stage}"
        ),
        "AWS_REGION": os.environ.get("AWS_REGION", "us-east-1"),
    }

    run(
        [
            "uv",
            "run",
            "python",
            "-c",
            f"from cli.processor import run; run('{bucket}', once={'True' if args.once else 'False'})",
        ],
        cwd=ROOT,
        env=process_env,
    )


# ── CLI ────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Quickstart",
        description="Specodex — dev, test, deploy, and verify.",
    )
    sub = parser.add_subparsers(dest="command")

    # dev (default)
    sub.add_parser("dev", help="Start local dev servers with hot reload")

    # test
    sub.add_parser("test", help="Run all unit tests (Python + backend + frontend)")

    # verify (alias: ci) — full pre-push gate, mirrors CI exactly
    p = sub.add_parser(
        "verify",
        aliases=["ci"],
        help="Run exactly what CI runs (lint + tests + build) — pre-push gate",
    )
    p.add_argument(
        "--only",
        choices=["python", "backend", "frontend"],
        help="Run a single stage (CI uses this per job)",
    )
    p.add_argument(
        "--integration",
        action="store_true",
        help="Add tests/integration/ to the Python stage (requires AWS creds / moto)",
    )

    # staging
    p = sub.add_parser("staging", help="Run staging contract tests against a server")
    p.add_argument(
        "url",
        nargs="?",
        default="http://localhost:3001",
        help="API base URL (default: localhost:3001)",
    )

    # deploy
    p = sub.add_parser("deploy", help="Deploy to AWS via CDK")
    p.add_argument(
        "--stage",
        default=os.environ.get("STAGE", "dev"),
        choices=["dev", "staging", "prod"],
        help="Deployment stage (default: dev)",
    )

    # smoke
    p = sub.add_parser("smoke", help="Run post-deployment smoke tests")
    p.add_argument(
        "url",
        nargs="?",
        default="http://localhost:3001",
        help="API base URL (default: localhost:3001)",
    )

    # wait-health — used by CI to gate smoke jobs on the deploy actually
    # being live. Single-source-of-truth for what "healthy" means.
    p = sub.add_parser(
        "wait-health",
        help="Poll <url>/health until 200 or retries exhausted",
    )
    p.add_argument("url", help="Base URL (e.g. https://www.specodex.com)")
    p.add_argument(
        "--retries",
        type=int,
        default=60,
        help="Max attempts at 1s intervals (default: 60)",
    )
    p.add_argument(
        "--label",
        help="Display label for log output (e.g. Staging, Production)",
    )

    # process
    p = sub.add_parser("process", help="Process queued PDF uploads from S3")
    p.add_argument(
        "--stage",
        default=os.environ.get("STAGE", "dev"),
        choices=["dev", "staging", "prod"],
        help="Stage (determines bucket and table names)",
    )
    p.add_argument("--bucket", default=None, help="Override S3 bucket name")
    p.add_argument(
        "--once", action="store_true", help="Process queue once and exit (don't poll)"
    )

    # cdk-outputs — extract a value from app/infrastructure/cdk-outputs.json
    # by key-substring match. CI uses this to read CloudFront URL / distribution
    # ID without inline python heredocs in YAML.
    p = sub.add_parser(
        "cdk-outputs",
        help="Read app/infrastructure/cdk-outputs.json by key substring",
    )
    p.add_argument(
        "--key",
        action="append",
        required=True,
        help="Key substring to match (repeat for fallbacks; first hit wins)",
    )
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="Print nothing and exit 0 if no key matches (default: exit 1)",
    )

    # admin is intercepted in main() before argparse runs, so the remaining
    # args can flow through to cli.admin's own parser.

    return parser


def main() -> None:
    # Intercept "admin" before argparse — the admin CLI has its own nested
    # subparsers and should see its own argv slice, not Quickstart's. Run it as
    # a module subprocess so package imports resolve correctly (quickstart.py
    # itself is invoked as a script, not a package module).
    if len(sys.argv) >= 2 and sys.argv[1] == "admin":
        run(["uv", "run", "python", "-m", "cli.admin", *sys.argv[2:]], cwd=ROOT)
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "bench":
        run(["uv", "run", "python", "-m", "cli.bench", *sys.argv[2:]], cwd=ROOT)
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "schemagen":
        run(["uv", "run", "python", "-m", "cli.schemagen", *sys.argv[2:]], cwd=ROOT)
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "price-enrich":
        run(
            ["uv", "run", "python", "-m", "cli.price_enrich", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "ingest-report":
        run(
            ["uv", "run", "python", "-m", "cli.ingest_report", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "batch-drives":
        run(
            ["uv", "run", "python", "-m", "cli.batch_servo_drives", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "audit-dedupes":
        run(
            ["uv", "run", "python", "-m", "cli.audit_dedupes", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "growth":
        # `growth preflight` exits 1 to signal HOLD (a feature, not a failure),
        # so don't wrap in run() — that would print an ERROR banner over the
        # legitimate HOLD/READY summary the subcommand already rendered.
        rc = subprocess.call(
            ["uv", "run", "python", "-m", "cli.growth", *sys.argv[2:]],
            cwd=ROOT,
        )
        sys.exit(rc)

    if len(sys.argv) >= 2 and sys.argv[1] == "godmode":
        run(
            ["uv", "run", "python", "-m", "cli.godmode", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "inspect":
        run(
            ["uv", "run", "python", "-m", "cli.inspect_datasheet", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "gen-types":
        # Regenerate app/frontend/src/types/generated.ts from Pydantic models.
        # See todo/PYTHON_BACKEND.md for the rollout plan; pydantic2ts shells
        # out to `npx json-schema-to-typescript`, so Node 18+ must be on PATH.
        info("Generating TypeScript types from Pydantic models")
        check_python_version()
        require_cmd("uv")
        require_cmd("npx")
        run(
            ["uv", "run", "python", "scripts/gen_types.py", *sys.argv[2:]],
            cwd=ROOT,
        )
        return

    parser = build_parser()
    args = parser.parse_args()

    # Default to dev if no subcommand given
    if not args.command:
        args.command = "dev"
        args = parser.parse_args(["dev"])

    commands = {
        "dev": cmd_dev,
        "test": cmd_test,
        "verify": cmd_verify,
        "ci": cmd_verify,  # alias for verify
        "staging": cmd_staging,
        "deploy": cmd_deploy,
        "smoke": cmd_smoke,
        "wait-health": cmd_wait_health,
        "process": cmd_process,
        "cdk-outputs": cmd_cdk_outputs,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
