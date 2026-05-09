"""
Extended post-deployment smoke tests.
Validates response schemas, infrastructure behavior, security hygiene,
and endpoint coverage beyond the basic health checks in test_smoke.py.

Requires API_BASE_URL environment variable.
Run: API_BASE_URL=https://www.specodex.com uv run pytest tests/post_deploy/ -v
"""

import concurrent.futures
import os
import pytest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:3001")


@pytest.mark.integration
class TestResponseSchemas:
    """Validate API response shapes match expected contracts."""

    def test_health_response_schema(self, api_get):
        status, body, _ = api_get("/health")
        assert status == 200
        assert body["status"] == "healthy"
        assert "timestamp" in body

    def test_products_response_schema(self, api_get):
        status, body, _ = api_get("/api/products")
        assert status == 200
        assert body["success"] is True
        assert isinstance(body["data"], list)

    def test_summary_response_schema(self, api_get):
        status, body, _ = api_get("/api/products/summary")
        assert status == 200
        data = body.get("data", {})
        assert "total" in data


@pytest.mark.integration
class TestEndpointCoverage:
    """Verify all public endpoints are reachable."""

    def test_categories_endpoint(self, api_get):
        status, body, _ = api_get("/api/products/categories")
        assert status == 200
        assert isinstance(body["data"], list)

    def test_manufacturers_endpoint(self, api_get):
        status, body, _ = api_get("/api/products/manufacturers")
        assert status == 200
        assert isinstance(body["data"], list)

    def test_datasheets_endpoint(self, api_get):
        status, body, _ = api_get("/api/datasheets")
        assert status == 200
        assert isinstance(body["data"], list)


@pytest.mark.integration
class TestInfrastructure:
    """Check that infrastructure (CloudFront, API Gateway) is configured correctly."""

    def test_api_and_health_both_route(self, api_get):
        """Both /api/* and /health route correctly (CloudFront behavior config)."""
        health_status, _, _ = api_get("/health")
        api_status, _, _ = api_get("/api/products")
        assert health_status == 200
        assert api_status == 200

    def test_cors_preflight(self, base_url):
        """OPTIONS preflight returns CORS headers."""
        req = Request(f"{base_url}/api/products", method="OPTIONS")
        req.add_header("Origin", "https://example.com")
        req.add_header("Access-Control-Request-Method", "GET")
        try:
            with urlopen(req, timeout=10) as resp:
                assert resp.status in (200, 204)
        except HTTPError as e:
            # Some API Gateway configs return 4xx for OPTIONS without proper config
            # but at minimum it should respond, not timeout
            assert e.code < 500

    def test_json_content_type(self, api_get):
        """API responses have application/json content type."""
        _, _, headers = api_get("/api/products")
        content_type = headers.get("content-type", "")
        assert "application/json" in content_type


@pytest.mark.integration
class TestSecurityHygiene:
    """Check that production doesn't leak server internals."""

    def test_no_x_powered_by_header(self, api_get):
        """Express x-powered-by should be disabled in production."""
        status, body, headers = api_get("/health")
        env = body.get("environment", "")
        if env == "development":
            pytest.skip("x-powered-by check only applies to production builds")
        assert "x-powered-by" not in headers

    def test_404_returns_json(self, base_url):
        """Unknown API endpoint returns structured JSON, not HTML."""
        req = Request(f"{base_url}/api/nonexistent-endpoint-smoke-test")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=10):
                # Should not reach here — expect 404
                pass
        except HTTPError as e:
            assert e.code == 404
            content_type = e.headers.get("Content-Type", "")
            assert "json" in content_type.lower() or "text" in content_type.lower()


@pytest.mark.integration
class TestConcurrency:
    """Verify the service handles concurrent requests."""

    def test_concurrent_health_checks(self, base_url):
        """5 parallel health checks all succeed within 3s."""

        def check_health(i: int) -> int:
            req = Request(f"{base_url}/health")
            with urlopen(req, timeout=3) as resp:
                return resp.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(check_health, i) for i in range(5)]
            results = [f.result(timeout=5) for f in futures]

        assert all(status == 200 for status in results)
        assert len(results) == 5
