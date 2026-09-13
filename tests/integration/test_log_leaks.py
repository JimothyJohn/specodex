"""Secret-leak regression tests — the Python half of HARDENING 4.3.

Twin of ``app/backend/tests/log-leak.test.ts``. The cloud-security rule
"logs are an attack surface AND a forensic tool" is policy; this file is
the enforcement for the Python pipeline. It seeds the environment with
sentinel credentials, drives the error paths that log SDK exception
text, and asserts no captured log line — or DAL ``print`` — contains any
sentinel.

Two flavours of path:

- *Realistic* failures (an overloaded model, a missing table): the SDK
  message carries no secret, and our own code must not add one.
- *Echoing* failures: an exception whose message embeds the credential,
  the way a transport error can echo a request URL. The log site must
  scrub it (``specodex.log_redact.redact_secrets``). Before 2026-09-13
  these sites logged ``f"...: {e}"`` verbatim and this test failed.

Sentinels, never real values — the test has to work for anyone.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import boto3
import moto
import pytest
from tenacity import stop_after_attempt, wait_none

from specodex.log_redact import SECRET_ENV_VARS

SENTINELS: dict[str, str] = {
    "GEMINI_API_KEY": "AIzaSy-TEST-SENTINEL-GEMINI-1234567890ABCDEFGH",
    "AWS_ACCESS_KEY_ID": "AKIATESTSENTINELAWSACCESS",
    "AWS_SECRET_ACCESS_KEY": "TEST-SENTINEL-AWS-SECRET-abcdef1234567890",
    "AWS_SESSION_TOKEN": "TEST-SENTINEL-AWS-SESSION-TOKEN-0987654321",
    "SERPER_API_KEY": "TEST-SENTINEL-SERPER-KEY-1122334455",
    "STRIPE_SECRET_KEY": "sk_test_TEST_SENTINEL_STRIPE_KEY_12345",
    "STRIPE_WEBHOOK_SECRET": "whsec_TEST_SENTINEL_WEBHOOK_SECRET",
}


def test_every_redactable_env_var_has_a_sentinel():
    """If a credential joins SECRET_ENV_VARS it must join this suite too."""
    assert set(SECRET_ENV_VARS) == set(SENTINELS)


@pytest.fixture
def sentinel_env(monkeypatch):
    for name, value in SENTINELS.items():
        monkeypatch.setenv(name, value)
    return SENTINELS


def _captured(caplog, capsys) -> str:
    out = capsys.readouterr()
    return "\n".join(
        [caplog.text, *(r.getMessage() for r in caplog.records), out.out, out.err]
    )


def _assert_no_sentinel(text: str) -> None:
    for name, value in SENTINELS.items():
        assert value not in text, f"{name} sentinel reached a log line or stdout"


# ---------------------------------------------------------------------------
# Gemini extraction path — specodex.llm.generate_content
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_retries():
    from specodex.llm import _client_for, generate_content

    _client_for.cache_clear()
    wait, stop = generate_content.retry.wait, generate_content.retry.stop
    generate_content.retry.wait = wait_none()
    generate_content.retry.stop = stop_after_attempt(2)
    yield
    generate_content.retry.wait = wait
    generate_content.retry.stop = stop
    _client_for.cache_clear()


@pytest.mark.parametrize(
    "message",
    [
        "503 UNAVAILABLE: The model is overloaded. Please try again later.",
        # Echoing transport error — key rides along in a query string.
        "400 for url https://generativelanguage.googleapis.com/v1beta/models"
        "/gemini-2.5-flash:generateContent?key=" + SENTINELS["GEMINI_API_KEY"],
    ],
    ids=["realistic", "echoes-key"],
)
def test_generate_content_failure_never_logs_the_api_key(
    sentinel_env, fast_retries, caplog, capsys, message
):
    from specodex import llm

    caplog.set_level(logging.DEBUG)
    fake = MagicMock()
    fake.models.generate_content.side_effect = RuntimeError(message)
    with patch.object(llm, "_client_for", return_value=fake):
        with pytest.raises(Exception):
            llm.generate_content(
                b"%PDF-1.4 stub", api_key=sentinel_env["GEMINI_API_KEY"], schema="motor"
            )
    _assert_no_sentinel(_captured(caplog, capsys))


# ---------------------------------------------------------------------------
# Gemini page classification — specodex.page_finder.classify_pages
# (the except site that logs the exception text)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED: quota exceeded",
        "401 API key not valid: " + SENTINELS["GEMINI_API_KEY"],
    ],
    ids=["realistic", "echoes-key"],
)
def test_classify_pages_failure_is_logged_without_the_key(
    sentinel_env, caplog, capsys, message
):
    from specodex import page_finder

    caplog.set_level(logging.DEBUG)
    fake = MagicMock()
    fake.models.generate_content.side_effect = RuntimeError(message)
    with patch.object(page_finder.genai, "Client", return_value=fake):
        results = page_finder.classify_pages(
            [b"\xff\xd8 fake jpeg"], api_key=sentinel_env["GEMINI_API_KEY"]
        )
    # The failure is logged and swallowed: every page comes back
    # has_specs=False with a "classification failed" description — which
    # used to carry the raw exception text too (second leak vector).
    assert [r["has_specs"] for r in results] == [False]
    assert results[0]["description"].startswith("classification failed")
    text = _captured(caplog, capsys)
    assert "Error classifying pages" in text  # the site did log
    _assert_no_sentinel(text)
    _assert_no_sentinel(json.dumps(results))


# ---------------------------------------------------------------------------
# DynamoDB DAL — specodex.db.dynamo.DynamoDBClient (reports via print)
# ---------------------------------------------------------------------------


def test_dynamodb_missing_table_does_not_leak_credentials(
    sentinel_env, sample_motor, caplog, capsys
):
    from specodex.db.dynamo import DynamoDBClient
    from specodex.models.motor import Motor

    # DEBUG on purpose: botocore.auth logs the signed canonical request —
    # `x-amz-security-token:<token>` in clear — at DEBUG, and the pipeline
    # sets the ROOT level from LOG_LEVEL. quiet_sdk_debug_logging() (called
    # when the DAL imports) clamps the SDK loggers; this pins it.
    caplog.set_level(logging.DEBUG)
    with moto.mock_aws():
        boto3.resource(
            "dynamodb", region_name="us-east-1"
        )  # boto picks up sentinel creds
        client = DynamoDBClient(table_name="table-that-does-not-exist")
        assert client.read("00000000-0000-0000-0000-000000000000", Motor) is None
        assert client.create(sample_motor) is False
    text = _captured(caplog, capsys)
    assert "Error" in text  # the DAL did report the failure
    _assert_no_sentinel(text)
