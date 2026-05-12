"""
Post-deployment smoke tests.
Lightweight checks to verify the system is operational after deployment.
Requires API_BASE_URL environment variable.
Run: API_BASE_URL=https://api.prod.example.com uv run pytest tests/post_deploy/ -v
"""

import json
import os
import time
import pytest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:3001")

# AWS trace headers worth surfacing in smoke-failure assertions so
# the next investigation has request IDs to grep CloudWatch with.
# Added 2026-05-10 after issue #104 (transient prod 403 with
# x-deny-reason: host_not_allowed) — the failure output captured no
# trace IDs, leaving the root cause untraceable.
_TRACE_HEADERS = (
    "apigw-requestid",
    "x-amzn-requestid",
    "x-amz-cf-id",
    "x-amz-cf-pop",
    "x-cache",
    "x-deny-reason",
)

# AWS-edge transient fingerprints we've seen self-resolve in minutes
# without any deploy or human intervention. Issues #104 (2026-05-10)
# and #151 (2026-05-11) both landed with the exact `host_not_allowed`
# deny-reason at the same edge layer, both recovered before the
# investigating session finished. Treating them as test failures and
# opening a fresh `prod-smoke-fail` issue per occurrence is noise:
# the underlying signal (sustained AWS edge outage) is only meaningful
# if it persists past one retry.
_TRANSIENT_DENY_REASONS = frozenset({"host_not_allowed"})
_TRANSIENT_RETRY_DELAY_S = 30


def _smoke_get(path: str, *, timeout: int = 5, _retried: bool = False):
    """GET helper that on non-2xx attaches AWS trace headers to the error.

    A bare urlopen() failure prints "HTTP Error 403: Forbidden" with
    no context — useless for chasing a transient edge issue across
    CloudFront / WAF / API Gateway / Lambda. This wrapper re-raises
    the HTTPError as an AssertionError whose message includes every
    header CloudFront and API Gateway echo back (request IDs, cache
    state, edge POP, custom deny-reason if any), so the next failure
    is greppable in CloudWatch by request-id.

    Transient retry: when the failure carries an `x-deny-reason` we've
    observed self-resolving in minutes (see `_TRANSIENT_DENY_REASONS`),
    sleep `_TRANSIENT_RETRY_DELAY_S` and retry once. If the retry
    succeeds, print a `KNOWN_TRANSIENT_RECOVERED` marker so the run
    output still records the hiccup (pattern-detection across runs can
    grep for it). If the retry also fails, the issue has outlived its
    transient window — raise normally so the smoke routine flags it.
    """
    req = Request(f"{BASE_URL}{path}")
    try:
        return urlopen(req, timeout=timeout)
    except HTTPError as exc:
        deny_reason = exc.headers.get("x-deny-reason") if exc.headers else None
        if not _retried and deny_reason in _TRANSIENT_DENY_REASONS:
            time.sleep(_TRANSIENT_RETRY_DELAY_S)
            resp = _smoke_get(path, timeout=timeout, _retried=True)
            print(
                f"KNOWN_TRANSIENT_RECOVERED path={path} "
                f"x-deny-reason={deny_reason} retry_after={_TRANSIENT_RETRY_DELAY_S}s"
            )
            return resp

        trace_parts = []
        for hname in _TRACE_HEADERS:
            hval = exc.headers.get(hname) if exc.headers else None
            if hval:
                trace_parts.append(f"{hname}={hval}")
        try:
            body_preview = exc.read(200).decode("utf-8", errors="replace")
        except Exception:
            body_preview = "<unreadable>"
        trace = " ".join(trace_parts) if trace_parts else "no AWS trace headers"
        raise AssertionError(
            f"{path} → HTTP {exc.code} ({trace}); body[:200]={body_preview!r}"
        ) from exc


@pytest.mark.integration
class TestSmoke:
    def test_health_check_200(self):
        """Service is running and healthy."""
        with _smoke_get("/health") as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert body["status"] == "healthy"

    def test_products_endpoint_200(self):
        """Products endpoint is accessible."""
        with _smoke_get("/api/products") as resp:
            assert resp.status == 200

    def test_summary_endpoint_200(self):
        """Summary endpoint returns data."""
        with _smoke_get("/api/products/summary") as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert "total" in body.get("data", {})

    def test_response_time_under_5s(self):
        """Health check responds within 5 seconds."""
        start = time.time()
        with _smoke_get("/health") as resp:
            elapsed = time.time() - start
            assert resp.status == 200
            assert elapsed < 5.0, f"Response took {elapsed:.2f}s"

    def test_datasheets_endpoint_200(self):
        """Datasheets endpoint is accessible."""
        with _smoke_get("/api/datasheets") as resp:
            assert resp.status == 200
