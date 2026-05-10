# HARDENING — adversarial-by-default testing posture

**Audit date:** 2026-05-09
**Status:** 14 findings open. Phase 1 = immediate wins (~1.5h total). Phase 2 = real attacker surface (multi-day). Phases 3–4 = adversarial coverage + hygiene.
**Companion:** `~/.claude/CLAUDE.md` "Testing — adversarial by default" section. Match every card here back to a rule there.
**Trigger:** any new endpoint, parser, deserializer, IAM policy, log statement, or external integration.

## First hour — three immediate wins (ship tonight)

If you only have ~90 minutes, ship these three. Each is independent and closes a real hole.

1. **Rename `_test_security.py`** (30 seconds, [card 1.1](#11-activate-_test_securitypy-xs-p1)) — activates 376 lines of dormant pytest security tests.
2. **`uv sync --locked` sweep across CI workflows** (10 minutes, [card 1.2](#12-uv-sync---locked-across-ci-workflows-xs-p1)) — closes the supply-chain drift gap.
3. **Regression tests for log-injection PRs #82/#83/#84** (~1 hour, [card 1.3](#13-regression-tests-for-log-injection-prs-828384-s-p0)) — guards the freshly-shipped CodeQL fixes against future regression.

After those, pick from Phase 2 by impact. SSRF first if you have time for one HIGH; otherwise queue.

## Phase 1 — Immediate wins

### 1.1 Activate `_test_security.py` (XS, P1)

`tests/integration/_test_security.py` is collection-skipped by pytest (leading underscore). 376 lines of injection / oversized-payload tests never run. The `from tests.integration.test_api_gateway import TestApiGateway` import is broken (no such module).

**Steps:**
1. `git mv tests/integration/_test_security.py tests/integration/test_security.py`
2. Fix the broken import — either remove the inheritance trick or point at a real fixture base.
3. `./Quickstart test --filter test_security` and triage anything newly-failing.
4. If genuine bugs surface, file each as a follow-up HARDENING card on the board.

**Definition of done:** pytest collects + runs `test_security.py`, CI green or each failure has its own follow-up card.

### 1.2 `uv sync --locked` across CI workflows (XS, P1)

CI runs `uv sync --quiet` (unlocked) on every job. Lockfile drift or hostile transitive bumps land silently. The new "Supply chain integrity in CI" rule mandates `--locked`.

**Files (audit found):**
- `.github/workflows/ci.yml` lines 46, 123, 258, 390, 542
- `.github/workflows/staging.yml:34`
- `.github/workflows/security.yml:37`
- `.github/workflows/bench.yml`
- `cli/quickstart.py` `cmd_verify`

**Steps:**
1. `grep -rn "uv sync --quiet" .github/workflows/ cli/` to confirm the audit list.
2. Replace each `uv sync --quiet` with `uv sync --locked --quiet`.
3. `./Quickstart verify` locally — no drift expected since the lockfile is committed.

**Note:** Touches `.github/workflows/`, so the daily orchestrator will skip this. Nick ships manually.

**Definition of done:** every `uv sync` in the repo uses `--locked`; CI re-runs green on the PR.

### 1.3 Regression tests for log-injection PRs #82/#83/#84 (S, P0)

PRs #82 (inline `safeLog`), #83 (codeql-loginjection-inline), #84 (codeql-loginjection-audit) shipped fixes without regression tests. The next refactor through the log path could undo them silently.

**Files:**
- `app/backend/src/util/log.ts` (the `safeLog` helper — currently has zero tests)
- `app/backend/src/index.ts:38` (inline CR/LF stripper)

**Steps:**
1. Create `app/backend/tests/log.test.ts`.
2. Test 1: `safeLog('a\r\nINJECT')` does NOT contain `\n` or `\r`.
3. Test 2: 1000-character input truncates per the existing rule.
4. Test 3: ANSI escapes (`\x1b[31m`) get stripped.
5. Test 4 (integration): hit a real route with a `\r\n`-laced query param, assert captured log is single-line.
6. Confirm each test FAILS without the existing fix (temporarily revert per-test, run, revert the revert).

**Definition of done:** four passing tests. Each one verified to fail when its corresponding fix is removed.

## Phase 2 — Real attacker surface

### 2.1 SSRF defense for URL-fetching paths (M, P0)

`specodex/web_scraper.py`, `specodex/browser.py`, `specodex/pricing/extract.py` accept user-supplied URLs and fetch them via Playwright/httpx. No allowlist, no DNS validation. `169.254.169.254` (AWS metadata), `localhost`, `file://`, internal hostnames — all reachable server-side.

**Steps:**
1. Create `specodex/url_safety.py` exposing `validate_url(url: str) -> str` (raises on unsafe).
   - HTTPS only — reject `http://`, `file://`, `ftp://`, `gopher://`, etc.
   - Resolve hostname → reject RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`), loopback (`127.0.0.0/8`), and IPv6 equivalents (`fc00::/7`, `fe80::/10`, `::1`).
   - Re-resolve immediately before connecting (TOCTOU defense against DNS-rebinding).
2. Wire `validate_url` into `web_scraper.fetch_page`, `browser.navigate`, and the URL paths in `pricing/extract`.
3. Create `tests/unit/test_url_safety.py` covering: metadata IPs (`169.254.169.254`, IPv6 variants `fd00:ec2::254`), `localhost`, `[::1]`, `metadata.google.internal`, percent-encoded `%6c%6f%63%61%6c%68%6f%73%74`, double-encoded variants, redirects to internal IPs, DNS-rebinding via short-TTL records.

**Definition of done:** 100% of URL-fetching paths route through `validate_url`; test suite covers all the listed attacks; existing scraper tests still pass.

### 2.2 Backend integration tests against real DAL (L, P1)

All 16 `app/backend/tests/*.test.ts` files do `jest.mock('../src/db/dynamodb')`. Refactors that break the real DAL pass green.

**Steps:**
1. Add either `aws-sdk-client-mock` (light) or DynamoDB Local via jest globalSetup (heavier but exercises real client→server protocol). Pick DynamoDB Local — it's the one that matches the integration-test rule.
2. Pick the 3 highest-value tests to migrate first: `db.test.ts`, `search.contract.test.ts`, `routes.test.ts`. Don't touch the other 13 in this card.
3. Replace `jest.mock('../src/db/dynamodb')` with the real `DynamoDBService` pointed at `localhost:8000`.
4. Add a contract test: returned items round-trip through the Pydantic-generated `generated.ts` types without coercion.
5. File a follow-up HARDENING card: "migrate the remaining 13 mocked backend tests to real DAL."

**Definition of done:** three tests run against DynamoDB Local in CI and pass; jest globalSetup boots/teardowns the local instance; follow-up card exists.

### 2.3 IDOR + cross-tenant auth tests (M, P1)

Existing tests cover happy-path 200s and 401-on-no-token. None covers "user A's token requesting user B's data."

**Steps:**
1. Create `app/backend/tests/projects.idor.test.ts`.
2. Build two distinct mock JWTs (different `sub`, different `userId`) using the same signer.
3. For each protected endpoint:
   - User A creates a resource (project, upload, etc.).
   - User B's token requests user A's resource ID. Assert 403 or 404 (never 200 with A's data).
   - User B's token attempts write/delete on user A's resource. Assert 403 or 404.
4. Cover at minimum: `/api/projects/:id` (GET, PATCH, DELETE), `/api/projects/:id/items` (POST), any other resource-scoped endpoint.

**Definition of done:** every resource-scoped endpoint has at least one IDOR test; the test would fail if the route's auth check were removed.

### 2.4 Stripe webhook signature + replay tests (M, P1)

`app/backend/tests/subscription.test.ts` uses a hardcoded fake URL. No signature-verification negative tests, no replay/idempotency tests.

**Steps:**
1. Add `stripe-mock` as a test fixture (or use Stripe CLI's local webhook forwarding).
2. Test 1 (signature tamper): valid event body signed with wrong secret → webhook returns 400, no DB write.
3. Test 2 (replay): post the same `event.id` twice → second is a no-op (no DB write, no Stripe API call).
4. Test 3 (clock skew): event signed >5min ago → rejected.

**Definition of done:** three failing-without-the-defense tests, each one confirmed to fail when its specific check is bypassed.

## Phase 3 — Adversarial input coverage

### 3.1 Hypothesis property tests for parsers (M, P2)

Property tests today cover only `ValueUnit`/`MinMaxUnit`. Higher-risk parsers eat untrusted bytes with no `@given` coverage.

**Targets:**
- `parse_gemini_response` (`specodex/utils.py`) — eats LLM JSON
- `find_spec_pages_by_text` (`specodex/page_finder.py`) — reads PDF text
- BeforeValidators in `specodex/models/common.py`

**Steps:**
1. Confirm `hypothesis` in `[dependency-groups].dev`; add if missing.
2. Create `tests/unit/test_parse_gemini_property.py` with `@given(st.recursive(...))` strategies for malformed/empty/unicode-laced JSON. Assert: never raises uncaught; never returns half-validated objects.
3. Same pattern for `page_finder` (text fuzz) and the BeforeValidators.

**Definition of done:** property tests on all three targets, each running ≥100 examples in CI.

### 3.2 Atheris fuzz target for PDF intake (M, P2)

`specodex/page_finder.py` parses arbitrary PDFs via PyMuPDF/PyPDF2. Classic untrusted-bytes parser — atheris's wheelhouse.

**Steps:**
1. Add `atheris` to dev-deps.
2. Create `tests/fuzz/fuzz_page_finder.py` with `atheris.Setup(sys.argv, TestOnePDF)` against `find_spec_pages_by_text(pdf_bytes)`.
3. Gate behind `pytest -m fuzz`. Don't block CI; run as a nightly job in `.github/workflows/bench.yml`.
4. On crash, save corpus minimum to `tests/fuzz/corpus/`; commit.

**Definition of done:** fuzz target runs locally for ≥10min without crashing; nightly bench.yml job invokes it.

### 3.3 Schema forward/backward compat tests (S, P2)

`tests/unit/test_models_roundtrip.py` covers same-version roundtrip only. Nothing tests forward/backward compat across `ProductType` literal additions or model field changes.

**Steps:**
1. Create `tests/integration/test_schema_compat.py`.
2. Frozen fixtures: snapshot of one product row per `ProductType` from current dev DB (commit to `tests/integration/fixtures/schema_snapshots/`).
3. Test 1 (forward compat): each snapshot loads under current code via `<Model>.model_validate`.
4. Test 2 (rollback safety): when adding a model field, the previous version's `model_validate` accepts new data (extra fields ignored).
5. Refresh snapshots when a new `ProductType` ships.

**Definition of done:** snapshot fixtures committed; both compatibility directions covered; documentation in CLAUDE.md "Adding a new product type" mentions the snapshot refresh step.

### 3.4 Concurrent-write stress test (S, P2)

`scraper.batch_create` and `process_datasheet` write shared DynamoDB state. No test exercises concurrent writes with overlapping `product_id`s.

**Steps:**
1. Create `tests/integration/test_concurrent_writes.py`.
2. Use `moto` or DynamoDB Local. Spawn 20 parallel `batch_create` calls with deliberately overlapping IDs.
3. Assert: final state matches one of the writers (no torn rows, no field-by-field mixing).
4. Use `concurrent.futures.ThreadPoolExecutor`; seed timing with deliberate sleeps to trigger races.

**Definition of done:** test reliably catches a torn-write regression if optimistic concurrency control is removed; passes deterministically when CC is in place.

## Phase 4 — Hygiene + observability

### 4.1 Dev-deps for adversarial testing (XS, P2)

Add to `pyproject.toml [dependency-groups].dev`:
- `mutmut` — mutation testing
- `pytest-randomly` — random test order
- `freezegun` — time mocking

Wire `pytest-randomly` via auto-load (it auto-loads when installed); pin its seed in CI for reproducibility.
Wire `mutmut run --paths-to-mutate specodex/quality.py` as a weekly job in `.github/workflows/bench.yml`. Don't gate CI on mutation-catch rate yet — establish a baseline first.

**Definition of done:** deps installed; `pytest --randomly-seed=42` runs the suite in random order; mutmut weekly job exists.

### 4.2 Lockfile-drift gate post-install (XS, P2)

After `npm ci` / `uv sync --locked`, run `git diff --exit-code <lockfile>` and fail the build if the lockfile mutated. Drift on a `--locked` install means something hostile or buggy.

**Files to edit:**
- `cli/quickstart.py:cmd_verify` — add the drift check after each install
- `.github/workflows/ci.yml` — same

**Definition of done:** local `./Quickstart verify` exits non-zero if `package-lock.json` or `uv.lock` mutates during install; CI mirrors.

### 4.3 Log secret-leak assertion tests (S, P2)

No tests assert that secrets, tokens, JWTs, or full Stripe IDs never appear in logs at any level. The cloud-security rule "Logs are an attack surface AND a forensic tool" is a policy; this card makes it enforced.

**Steps:**
1. Create `app/backend/tests/log-leak.test.ts`.
2. In `beforeEach`, seed env with sentinel values for `GEMINI_API_KEY`, `AWS_SECRET_ACCESS_KEY`, etc.
3. Spy on `console.error`, `console.warn`, `console.log`.
4. Trigger error paths: bad JWT, malformed Stripe webhook, oversized upload, DB error.
5. After each, assert no captured string contains any sentinel value.
6. Mirror in Python (`tests/integration/test_log_leaks.py`) using pytest's `caplog`.

**Definition of done:** TS + Python log-leak tests run in CI; both fail loudly if a known-leaking line is reintroduced.

## Dependencies

**No hard blockers.** Most cards are independent of each other and of the rest of the backlog (CATAGORIES, SCHEMA, STRIPE, SEO, PYTHON_BACKEND, STYLE, API, CONFIGURATION).

**Soft sequencing:**
- Phase 1.2 (`uv sync --locked`) before Phase 4.2 (lockfile-drift gate) — drift gate assumes locked installs.
- Phase 4.1 (dev-deps) before Phase 3.1 (`hypothesis`) and 3.2 (`atheris`) — those tests need the deps.
- Phase 2.2 (real DAL tests) before any "rewrite all 16 mocked tests" follow-up card.

**Truly independent (run in any order, any spare slot):**
- Phase 1.1 (rename `_test_security.py`)
- Phase 1.3 (log-injection regressions)
- Phase 2.1 (SSRF defense)
- Phase 2.3 (IDOR tests)
- Phase 2.4 (Stripe webhook tests)
- Phase 3.3 (schema compat)
- Phase 3.4 (concurrent stress)
- Phase 4.3 (log secret-leak)

## Triggers

When working on any of these surfaces, surface this doc:

- New HTTP endpoint, middleware, or auth refactor in `app/backend/src/routes/` → Phase 2.3 (IDOR coverage).
- New URL-fetching path in `specodex/` (scraping, pricing, etc.) → Phase 2.1 (SSRF).
- New parser, deserializer, or `BeforeValidator` → Phase 3.1, 3.2.
- Change to `app/backend/src/util/log.ts` or new logging code path; CodeQL log-injection finding → Phase 1.3, 4.3.
- New external integration (Stripe webhooks, third-party API) → Phase 2.4.
- Adding a new `ProductType` or model field → Phase 3.3 (schema compat snapshot refresh).
