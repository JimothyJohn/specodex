<div align="center">
  <img src="docs/logo.svg" alt="Specodex mark" width="128" height="128">

  # Specodex

  **A product selection frontend that only an engineer could love. Cross-vendor industrial spec data — motors, drives, gearheads, contactors, actuators, robot arms — indexed, filtered, and exportable. No marketing copy on the rows. No "request a quote" gates.**

  [![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
  [![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
  [![Documentation](https://img.shields.io/badge/docs-github%20pages-blue.svg)](https://jimothyjohn.github.io/specodex/)
  [![Live app](https://img.shields.io/badge/live-specodex.com-A88A1C.svg)](https://specodex.com)
</div>

---

## What it does

Specodex indexes industrial electromechanical product specs across manufacturers and exposes them through a filterable, comparable UI and a typed API — without the marketing copy or quote-gates that make every vendor's own catalog hostile to specify against.

The data pipeline behind it (internally `datasheetminer/`) reads PDF datasheets and product webpages, extracts structured specifications using Gemini, validates them against strict Pydantic schemas, and stores them in DynamoDB.

The pipeline:

```
PDF → page finder (text heuristic, free) → Gemini 2.5 Flash → Pydantic → quality gate → DynamoDB
```

`page_finder` strips a 600-page catalog to ~20 spec pages before any LLM call. Gemini emits structured JSON; validators map it to canonical `value;unit` compact strings; quality is scored before the row is written.

## Entry point

Everything goes through `./Quickstart <command>`. It's a bash shim that delegates to `cli/quickstart.py`.

| Command | What it does |
|---------|-------------|
| `./Quickstart dev` | Local dev servers (backend :3001, frontend Vite :5173) |
| `./Quickstart verify` | Pre-push gate — mirrors CI exactly: lint + tests + build |
| `./Quickstart test` | Unit tests only (fast feedback during dev) |
| `./Quickstart deploy [--stage]` | Deploy to AWS via CDK |
| `./Quickstart smoke [URL]` | Post-deploy smoke tests |
| `./Quickstart bench` | Benchmark the ingress pipeline against control datasheets |
| `./Quickstart process` | Process the S3 upload queue |
| `./Quickstart schemagen PDF... --type NAME` | Propose a new Pydantic model from 3-5 vendors' PDFs |
| `./Quickstart price-enrich` | Backfill MSRP on existing products |
| `./Quickstart admin <sub>` | Blacklist, data movement, purge |

See `cli/quickstart.py` for the full list. Don't run `python -m cli.foo` in scripts — the Quickstart shim is the canonical entry point.

## Data sources

| Source | Tool | How it works |
|--------|------|-------------|
| PDF datasheets | `specodex` CLI | Optional page extraction, then Gemini extracts structured rows |
| Product webpages | `web-scraper` CLI | Playwright renders JS-heavy pages, pulls JSON-LD + HTML, same pipeline |
| Manual entry | Web app (admin mode) | Direct CRUD via the product management UI |

Both CLI tools share the same extraction pipeline: Gemini emits structured output, parsed locally into `value;unit` compact strings, validated by Pydantic models and unit-family rules, quality-scored, and pushed to DynamoDB with deterministic UUIDs.

**Rule:** never feed a raw multi-hundred-page PDF to the LLM. Always pre-filter with `page_finder` first. The scraper auto-switches to per-page extraction when the page count is small enough to avoid Gemini truncating mid-string.

## Product types

| Type | Key specs |
|------|-----------|
| Motor | voltage, current, power, torque, speed, encoder, inertia, IP rating |
| Drive | input/output voltage, power, switching frequency, I/O counts, fieldbus, safety |
| Gearhead | ratio, backlash, continuous/peak torque, input speed, torsional rigidity |
| Electric Cylinder | stroke, push/pull force, linear speed, repeatability, lead-screw pitch |
| Linear Actuator | stroke, force, lead, screw type, duty cycle, IP rating |
| Robot Arm | payload, reach, repeatability, TCP speed, axes, per-axis torque/speed |
| Contactor | AC-1/AC-3 ratings, coil voltages, auxiliary contacts, short-circuit ratings |

New product types can be scaffolded with `./Quickstart schemagen <pdf>... --type <name>` (pass 3-5 vendors' datasheets so the schema generalizes). On the Python side they auto-discover; on the TypeScript side there are four hardcoded allowlists that need touching — see the "Adding a new product type" section in [CLAUDE.md](CLAUDE.md).

## Architecture

```
specodex/                Python core: LLM, page-finder, Pydantic models, validation, DynamoDB,
                         PDF scraper (scraper.py), web scraper (web_scraper.py + browser.py)
cli/                     CLI modules: bench, intake, processor, admin, schemagen, agent, triage
app/
  backend/               Express API on AWS Lambda via API Gateway (TypeScript)
  frontend/              React + Vite UI deployed to S3 + CloudFront (TypeScript)
  infrastructure/        AWS CDK stacks (DynamoDB, API Gateway, CloudFront, Lambda)
stripe/                  Rust Lambda for metered billing via Stripe
tests/                   unit/, integration/, staging/, post_deploy/, benchmark/
docs/                    Public landing page (GitHub Pages) + logo proofs
```

## Quick start

```bash
# Clone and install
git clone https://github.com/JimothyJohn/specodex.git
cd specodex
uv sync

# Set API keys (copy .env.example to .env and fill in)
cp .env.example .env

# Local dev — backend + frontend together
./Quickstart dev

# Pre-push gate (mirrors CI)
./Quickstart verify

# Extract specs from a PDF datasheet
uv run specodex \
  --url "https://example.com/motor-catalog.pdf" \
  --type motor --manufacturer "Acme" --product-name "X100" \
  --pages "3,4,5"

# Scrape a product webpage
uv run web-scraper \
  --url "https://shop.example.com/products/X100" \
  --type motor --manufacturer "Acme" --product-name "X100"

# Query the database
uv run dsm find --type motor \
  --where "rated_power>=1000" \
  --sort "rated_torque:desc" -n 10
```

## CLI reference

| Command | Description |
|---------|-------------|
| `specodex` | Extract specs from a PDF or webpage URL |
| `web-scraper` | Scrape JS-rendered product pages via headless browser |
| `page-finder` | Identify which PDF pages contain spec tables (no LLM call) |
| `dsm-agent` | Agent-facing CLI for batch datasheet-to-database workflows |
| `dsm` | Query products in DynamoDB with filters and sorting |

## Gear-aware filtering

When filtering motors by torque or speed, the system computes the optimal gear ratio each motor needs to meet the criteria. A motor producing 5 Nm at 3000 rpm becomes a valid match for a 50 Nm filter at 10:1 ratio (at 300 rpm output). Every motor is evaluated at its best ratio, and only those that can satisfy both torque and speed constraints at some ratio are shown.

## Web app

The web app runs in two modes:

- **Admin** (local dev): full CRUD, datasheet management, product upload pipeline
- **Public** (deployed): read-only with search, filtering, comparison, and datasheet links

Features: multi-attribute filtering with IS/NOT modes, range sliders, multi-column sort, gear-ratio computation, distribution charts, dark/light theme, mobile responsive.

See [app/README.md](app/README.md) for setup, API reference, and deployment.

## Testing & benchmarking

```bash
# Pre-push gate — runs the same lint + tests + build that CI runs
./Quickstart verify

# Python unit tests only
uv run pytest tests/unit/ -v

# Web app tests
cd app && npm test

# Ingress pipeline benchmark (page-finder gaps, recall, cost)
./Quickstart bench                          # offline against cached responses
./Quickstart bench --live --update-cache    # live (calls Gemini, costs money)
```

See [tests/COVERAGE.md](tests/COVERAGE.md) for the full coverage breakdown and `tests/benchmark/` for fixtures.

## Branding

The frontend is branded **Specodex** — field-manual aesthetic, OD-green and paper, mil-spec stencil. The primary mark and 25 alternative proofs live in [`docs/logos.html`](docs/logos.html); the canonical SVG is [`docs/logo.svg`](docs/logo.svg).

## For manufacturers, partners, takedown requests

See [PUBLIC.md](PUBLIC.md) — covers the project's data-source stance, what is and isn't reproduced from manufacturer datasheets, the takedown contact, the manufacturer opt-in, and the continuity plan if the maintainer is ever unreachable.

## License

MIT — see [LICENSE](LICENSE) for details.
