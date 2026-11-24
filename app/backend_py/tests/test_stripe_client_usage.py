"""``stripe_client.report_usage`` wire contract (PAYGATE.md follow-up,
2026-09-13).

The captured payload is validated against the billing Lambda's *actual*
``UsageRequest`` model from ``stripe_py`` — the strongest check the
repo can make that the two sides agree. Until this fix the client sent
``{user_id, tokens}``; ``UsageRequest`` tolerates that (its token fields
default to 0), which is exactly how the bug stayed silent.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.backend_py.src.services import stripe_client
from stripe_py.src.billing.models import UsageRequest


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test/")
    seen: dict[str, Any] = {}

    def fake_post(url: str, *, json: dict[str, Any], timeout: float) -> _Resp:
        seen["url"] = url
        seen["json"] = json
        return _Resp(200, {"total_tokens": 30, "recorded": True})

    monkeypatch.setattr(stripe_client.httpx, "post", fake_post)
    return seen


def test_report_usage_sends_the_usage_request_shape(capture: dict[str, Any]) -> None:
    out = stripe_client.report_usage("user-1", input_tokens=10, output_tokens=20)
    assert capture["url"] == "https://billing.test/usage"
    assert capture["json"] == {
        "user_id": "user-1",
        "input_tokens": 10,
        "output_tokens": 20,
    }
    assert "tokens" not in capture["json"]  # the old, silently-ignored field
    # The Lambda's own model accepts it AND sees the counts — the bug was
    # a payload the model accepted while recording zeros.
    parsed = UsageRequest(**capture["json"])
    assert (parsed.input_tokens, parsed.output_tokens) == (10, 20)
    assert out == {"total_tokens": 30, "recorded": True}


def test_report_usage_is_a_noop_when_billing_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STRIPE_LAMBDA_URL", raising=False)

    def explode(*_a: Any, **_k: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("httpx.post must not be called when disabled")

    monkeypatch.setattr(stripe_client.httpx, "post", explode)
    assert stripe_client.report_usage("u", input_tokens=1, output_tokens=1) is None


def test_report_usage_returns_none_on_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRIPE_LAMBDA_URL", "https://billing.test")
    monkeypatch.setattr(
        stripe_client.httpx, "post", lambda *_a, **_k: _Resp(503, {"error": "down"})
    )
    assert stripe_client.report_usage("u", input_tokens=1, output_tokens=1) is None
