# Specodex

Cross-vendor industrial product specs (motors, drives, gearheads, contactors, actuators, robot arms, sensors): a filterable UI and typed API over DynamoDB, fed by an autonomous pipeline that reads PDF datasheets. Stupid simple on purpose. The pre-rebrand name `datasheetminer` survives in CloudFormation stack names (`DatasheetMiner-<Stage>-*`) and IAM principals.

One line per rule here. Longer checklists load on demand from `.claude/rules/` when you touch matching files (index at the bottom). The global `~/.claude/CLAUDE.md` always applies; this file only adds what is specific to this repo.

## Stack

- **Python ≥ 3.12** (`specodex/`, `cli/`, `scripts/`): `uv`, `ruff`, `pytest` + `hypothesis`, `mypy`. Pydantic models per product type under `specodex/models/`, auto-discovered by `specodex/config.py:_discover_schema_models`.
- **LLM: Gemini 2.5 Flash, single provider.** Everything uses `GEMINI_API_KEY`; the model id is pinned in `specodex/config.py` (`MODEL`). Deliberate — this repo does not use the Claude-API default from the global file.
- **Data: DynamoDB** (`specodex/db/`), an S3 upload queue, stages `dev` / `staging` / `prod`.
- **Web (`app/`, npm workspace):** Express API in TypeScript (`app/backend`), React + Vite UI (`app/frontend`), CDK infra (`app/infrastructure`). `app/backend_py/` is the FastAPI `/api/v2` port — code-complete, not live (`VITE_API_VERSION` is `v1`); plan in `todo/PYTHON_BACKEND.md`.
- **MCP:** `specodex/mcp/` exposes the public catalog API as read-only tools (`./Quickstart mcp`, `uv run specodex-mcp`).

## Entry point

Everything goes through `./Quickstart <command>` — a bash shim that delegates to `cli/quickstart.py`. That module's docstring is the complete command list; `./Quickstart <cmd> --help` gives flags. Don't put `python -m cli.foo` in docs or scripts without a reason.

    ./Quickstart dev                 Local dev servers (backend :3001, frontend :3000). Default.
    ./Quickstart test                Unit tests only (fast feedback)
    ./Quickstart verify              Pre-push gate = exactly what CI runs: lint + tests + build +
                                     deploy build (alias: ci). --only python|backend|frontend|deploy-build
                                     runs one stage; --integration adds tests/integration/.
    ./Quickstart hooks install       Per-clone git hooks: pre-commit ruff + tsc, pre-push full verify.
                                     SPECODEX_SKIP_HOOKS=1 or --no-verify bypasses once; `hooks uninstall`.
    ./Quickstart deploy [--stage]    Deploy to AWS via CDK
    ./Quickstart smoke [URL]         Post-deploy smoke suite (tests/post_deploy/)
    ./Quickstart staging [URL]       Staging contract tests (tests/staging/)
    ./Quickstart mcp                 MCP server over stdio (SPECODEX_API_URL picks the stage)
    ./Quickstart bench               Ingress-pipeline benchmark          → rules/benchmark.md
    ./Quickstart gen-types           Pydantic → TypeScript codegen       → rules/product-types.md
    ./Quickstart schemagen PDF... --type NAME   Propose a new product model → rules/product-types.md
    ./Quickstart process | admin | price-enrich | price-infer | price-book | mounting-extract |
                 ingest-report | vendor-facts | configurator | availability-enrich | audit-dedupes |
                 units-triage | godmode | growth | build-backend-py      → rules/pipeline.md

## Pipeline

PDF → `page_finder` (text heuristic, free) → Gemini extraction (`specodex/llm.py`) → Pydantic validation → quality gate (`specodex/quality.py`) → DynamoDB write. Module map and the enrichment CLIs are in `rules/pipeline.md`.

## Key directories

    cli/                    CLI modules (quickstart, bench, intake, processor, admin, batch_*, agent, triage, price_*, …)
    specodex/               Core library (llm, scraper, page_finder, quality, units, ids, relations, pricing/, mcp/, …)
    specodex/models/        Pydantic product models + schema builders (+ <type>.md reasoning docs)
    specodex/db/            DynamoDB client
    app/backend/            Express API (TypeScript, live)      app/backend_py/      FastAPI /api/v2 port (not live)
    app/frontend/           React + Vite UI                    app/infrastructure/  CDK stacks
    tests/                  unit/, integration/, staging/, post_deploy/, benchmark/
    todo/                   Backlog, one file per area; todo/README.md is the map
    docs/                   GitHub Pages: index.html, roadmap.html (generated), requests/<n>.html

## Hard rules

- **Never feed a raw multi-hundred-page PDF to the LLM.** Route through `page_finder` first (`find_spec_pages_by_text`, free, or `find_spec_pages_scored`); bundled extraction tops out around 30 pages before Gemini truncates the JSON mid-string.
- **Enrichment `--apply` writes dev/staging only, never prod; dry-run is the default.** Moving data to prod is `./Quickstart admin promote`, and that is Nick's call.
- **Never hand-edit generated TypeScript** (`app/frontend/src/types/generated.ts`, `app/backend/src/types/generated_constants.ts`). Fix the Pydantic model and run `./Quickstart gen-types`; CI's `test-codegen` job fails on drift.
- **A new `ProductType` is five steps**, including the schema-compat snapshot refresh — `rules/product-types.md`.
- **Every parser, coercer, or validator that eats LLM-emitted or user-controlled bytes gets a `tests/unit/test_<module>_property.py` companion** — `rules/property-testing.md`.
- **No native browser/OS chrome in the frontend**; every user action has an app-native primitive (`Tooltip`, `ConfirmDialog`, `Toast`, …) — `rules/frontend.md`. A new UI primitive ships its CSS in a sibling `.css`, not `App.css`.
- **The backlog is `todo/`.** There is no GitHub Project board — never recreate one or reference `github.com/users/JimothyJohn/projects/1`. `todo/longterm/` is inert until Nick names a doc.
- **`docs/requests/<n>.html` pages are for major functional changes only** — not chores, bumps, lint, or doc syncs — `rules/backlog.md`.

## Shipping a change

- Two deploy branches: push to **`dev`** auto-deploys the staging stack (no prod); **`master`** runs tests → staging deploy → smoke → `environment: production` approval (JimothyJohn is the only reviewer) → prod deploy → smoke.
- **Branch off `dev`, never `master`.** Unattended/routine work uses `auto/<area>-<short-slug>-<yyyymmdd>`.
- Open the PR with `--base dev`, non-draft when CI is green and the diff is routine; enable auto-merge (merge commit) — required checks are the approval, no human review on `dev`.
- **Keep the PR draft and hand back** when the diff touches `app/infrastructure/**`, `.github/workflows/**`, IAM/secrets/KMS policy, Stripe live mode, prod deploy, or shared infra.
- **`dev` → `master` is Nick's manual merge.** Never open `dev` → `master` PRs, never push `master`.
- **Green PR ≠ green `dev`:** Deploy Staging re-resolves the Lambda bundle from a fresh lockfile. `tests/integration/test_deploy_readiness.py` catches the known breakages at PR time; extend it when you touch the bundle step or the infra toolchain — `rules/deploy.md`.
- After `./Quickstart deploy`, confirm with `./Quickstart smoke <URL>` before closing the loop — URLs and failure fingerprints in `rules/deploy.md`.

## Hard rules for autonomous / unattended work

Skip the row if any apply. A skipped row costs a day; an unauthorized infra change costs much more. Borderline → skip.

- Anything blocked per `todo/README.md`'s dependency map.
- Edits to `app/infrastructure/**`, `.github/workflows/**`, or anything that runs an AWS-mutating command (`./Quickstart deploy`, `./Quickstart admin promote`, `--stage prod`).
- DynamoDB writes outside dev (and on dev, prefer dry-run).
- Stripe live-mode keys, real charges, or webhook secret rotation.
- Cherry-picking across worktrees or rewriting shared history.

## Environment

- `.env` at the repo root: `GEMINI_API_KEY`, `DYNAMODB_TABLE_NAME`, `AWS_REGION` (full key list in `.env.example`). `app/.env` plus `app/.env.dev` / `app/.env.prod`: web and CDK config (`app/.env.example`). Values are intentional — read them, don't re-prompt.
- Two GitHub-Actions AWS principals exist; CI deploys with the OIDC role `gh-deploy-datasheetminer` (inline policy `CdkDeploy`). Add deploy permissions there, never only to the `datasheetminer-cicd` user policy — `rules/deploy.md`.
- SSH keys aren't loaded in a Claude session: push over HTTPS with `git -c credential.helper='!gh auth git-credential' push https://github.com/JimothyJohn/specodex.git <branch>`. A push touching `.github/workflows/*` needs the `workflow` token scope (`gh auth refresh -s workflow` is interactive — hand it off as a `scratch/` script per the global hand-off rule).

## Rules index (`.claude/rules/`)

| File | Loads when you touch | Covers |
|---|---|---|
| `pipeline.md` | `specodex/**`, `cli/**`, `data/vendors/**` | Module map, page-finder rule, MCP server internals, every enrichment CLI and its guards |
| `product-types.md` | `specodex/models/**`, `scripts/gen_types.py`, generated TS, schema-compat test | Codegen, adding a product type, end-to-end smoke of a new type |
| `frontend.md` | `app/frontend/**` | Attribute derivation, no-native-chrome table and drift gates, per-component CSS |
| `benchmark.md` | `tests/benchmark/**`, `cli/bench*.py`, `specodex/page_finder.py` | Metrics, fixtures, usage, open issues |
| `property-testing.md` | `tests/**` | Convention, coverage map, lessons from past rounds |
| `deploy.md` | `app/infrastructure/**`, `.github/workflows/**`, `cli/quickstart.py`, deploy tests | Post-deploy verification, Lambda-bundle fresh resolve, AWS auth paths, apex domains |
| `backlog.md` | `todo/**`, `docs/**`, `scripts/gen_roadmap.py` | todo entry shape, how work gets done, per-PR HTML pages |
