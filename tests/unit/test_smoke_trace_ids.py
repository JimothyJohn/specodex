"""Unit test for the smoke helper's trace-ID capture on failure.

Added after issue #104 (2026-05-10 prod smoke failure with
`x-deny-reason: host_not_allowed`). The original
``tests/post_deploy/test_smoke.py`` used raw ``urlopen()`` which
surfaced a bare ``HTTPError: HTTP Error 403: Forbidden`` with no
context — useless for chasing a transient edge issue across
CloudFront / WAF / API Gateway / Lambda.

The new ``_smoke_get`` wrapper attaches every AWS trace header
(`apigw-requestid`, `x-amz-cf-id`, `x-amz-cf-pop`, `x-cache`, plus
the custom `x-deny-reason` if present) to the assertion failure
message so the next incident is greppable in CloudWatch by
request-id.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError

import pytest


def _load_smoke_module():
    """Lazy import — returns the module so the test can patch its
    ``urlopen`` attribute directly (the smoke module imports the name
    via ``from urllib.request import urlopen``, so patching
    ``urllib.request.urlopen`` wouldn't intercept its lookup)."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parent.parent / "post_deploy" / "test_smoke.py"
    spec = importlib.util.spec_from_file_location("_smoke_mod", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSmokeGetFailurePath:
    """``_smoke_get`` raises AssertionError with AWS trace IDs on non-2xx."""

    def _make_http_error(
        self, status: int, body: bytes, headers: dict[str, str]
    ) -> HTTPError:
        """Build an HTTPError that mimics what urlopen raises on non-2xx."""
        from io import BytesIO

        return HTTPError(
            url="https://www.specodex.com/health",
            code=status,
            msg="Forbidden",
            hdrs=headers,  # type: ignore[arg-type]
            fp=BytesIO(body),
        )

    def test_includes_apigw_request_id_in_failure_message(self):
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        err = self._make_http_error(
            403,
            b"Forbidden",
            {"apigw-requestid": "abc-123-def", "x-amz-cf-pop": "DFW56-P4"},
        )
        with patch.object(mod, "urlopen", side_effect=err):
            with pytest.raises(AssertionError) as exc_info:
                _smoke_get("/health", timeout=1)

        msg = str(exc_info.value)
        assert "HTTP 403" in msg
        assert "apigw-requestid=abc-123-def" in msg
        assert "x-amz-cf-pop=DFW56-P4" in msg

    def test_includes_deny_reason_when_present(self):
        """Issue #104's exact failure shape — captured for the next one.

        `host_not_allowed` is in `_TRANSIENT_DENY_REASONS`, so the helper
        retries once. To verify the trace-ID-in-failure-message path we
        make both attempts fail (sustained transient is the only way the
        helper raises now); `time.sleep` is patched so the test doesn't
        wait the 30s retry delay."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        from io import BytesIO

        err_headers = {
            "x-deny-reason": "host_not_allowed",
            "x-amz-cf-id": "cf-trace-xyz",
            "content-type": "text/plain",
        }
        # Two fresh HTTPErrors so each attempt has its own readable body
        # buffer (BytesIO is single-shot).
        err_first = self._make_http_error(403, b"Host not in allowlist", err_headers)
        err_retry = HTTPError(
            url=err_first.url,
            code=err_first.code,
            msg=err_first.msg,
            hdrs=err_headers,  # type: ignore[arg-type]
            fp=BytesIO(b"Host not in allowlist"),
        )
        with (
            patch.object(mod, "urlopen", side_effect=[err_first, err_retry]),
            patch.object(mod.time, "sleep"),
        ):
            with pytest.raises(AssertionError) as exc_info:
                _smoke_get("/health", timeout=1)

        msg = str(exc_info.value)
        assert "x-deny-reason=host_not_allowed" in msg
        assert "x-amz-cf-id=cf-trace-xyz" in msg
        # Body preview is in the message for context.
        assert "Host not in allowlist" in msg

    def test_no_trace_headers_emits_explicit_marker(self):
        """When the origin sends a 5xx with no AWS headers (e.g. cold
        Lambda timeout before CloudFront stamps), the message says so
        explicitly rather than silently omitting trace info."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        err = self._make_http_error(502, b"Bad Gateway", {})
        with patch.object(mod, "urlopen", side_effect=err):
            with pytest.raises(AssertionError) as exc_info:
                _smoke_get("/health", timeout=1)

        msg = str(exc_info.value)
        assert "HTTP 502" in msg
        assert "no AWS trace headers" in msg

    def test_body_preview_truncated_to_200_chars(self):
        """A massive HTML error page from CloudFront / WAF shouldn't
        flood the assertion output."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        long_body = ("X" * 1000).encode()
        err = self._make_http_error(
            429, long_body, {"x-cache": "Error from cloudfront"}
        )
        with patch.object(mod, "urlopen", side_effect=err):
            with pytest.raises(AssertionError) as exc_info:
                _smoke_get("/health", timeout=1)

        msg = str(exc_info.value)
        # 200-char read cap means the body preview can't exceed that
        # plus the repr overhead. Sanity-check it doesn't include all
        # 1000 X's.
        x_count = msg.count("X")
        assert x_count <= 220, f"body preview not truncated; saw {x_count} 'X's"


class TestSmokeGetTransientRetry:
    """`_smoke_get` retries once when the failure matches a known
    self-resolving AWS-edge transient (currently `x-deny-reason:
    host_not_allowed`, the signature of issues #104 and #151)."""

    def _make_http_error(
        self, status: int, body: bytes, headers: dict[str, str]
    ) -> HTTPError:
        from io import BytesIO

        return HTTPError(
            url="https://www.specodex.com/health",
            code=status,
            msg="Forbidden",
            hdrs=headers,  # type: ignore[arg-type]
            fp=BytesIO(body),
        )

    def _make_ok_response(self):
        """Lightweight fake of a 200 urlopen response."""

        class _Resp:
            status = 200

            def read(self):
                return b'{"status":"healthy"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    def test_recovers_when_retry_succeeds(self, capsys):
        """First call hits the transient signature, retry returns 200."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        transient = self._make_http_error(
            403,
            b"Host not in allowlist",
            {
                "x-deny-reason": "host_not_allowed",
                "x-amz-cf-id": "cf-trace-1",
            },
        )
        ok = self._make_ok_response()

        # sleep patched to no-op so the test runs fast.
        with (
            patch.object(mod, "urlopen", side_effect=[transient, ok]),
            patch.object(mod.time, "sleep") as mock_sleep,
        ):
            resp = _smoke_get("/health", timeout=1)

        assert resp.status == 200
        # Recovery marker printed so cross-run pattern detection can grep it.
        out = capsys.readouterr().out
        assert "KNOWN_TRANSIENT_RECOVERED" in out
        assert "x-deny-reason=host_not_allowed" in out
        assert "path=/health" in out
        # And the sleep happened — the 30s isn't load-bearing in the
        # unit test, just confirming we paused before retrying.
        mock_sleep.assert_called_once_with(mod._TRANSIENT_RETRY_DELAY_S)

    def test_raises_when_retry_also_fails(self):
        """Sustained transient (both attempts return the same 403)
        means the signal is real — surface the failure for the smoke
        routine to act on."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        transient = self._make_http_error(
            403,
            b"Host not in allowlist",
            {"x-deny-reason": "host_not_allowed", "x-amz-cf-id": "cf-trace-2"},
        )
        # Different HTTPError instance for the retry to avoid reusing
        # an already-consumed body buffer.
        from io import BytesIO

        transient_retry = HTTPError(
            url=transient.url,
            code=transient.code,
            msg=transient.msg,
            hdrs={
                "x-deny-reason": "host_not_allowed",
                "x-amz-cf-id": "cf-trace-3",
            },  # type: ignore[arg-type]
            fp=BytesIO(b"Host not in allowlist"),
        )

        with (
            patch.object(mod, "urlopen", side_effect=[transient, transient_retry]),
            patch.object(mod.time, "sleep"),
        ):
            with pytest.raises(AssertionError) as exc_info:
                _smoke_get("/health", timeout=1)

        msg = str(exc_info.value)
        # The retry's trace ID (cf-trace-3) is what surfaces, since
        # that's the one that ultimately failed.
        assert "HTTP 403" in msg
        assert "x-deny-reason=host_not_allowed" in msg
        assert "x-amz-cf-id=cf-trace-3" in msg

    def test_non_transient_403_does_not_retry(self):
        """A 403 without the known deny-reason is a real failure —
        don't sleep 30s before failing the smoke run."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        # 403 with NO x-deny-reason → not in the transient allowlist.
        err = self._make_http_error(403, b"Forbidden", {"x-amz-cf-id": "cf-real-403"})
        # urlopen would only be called once because no retry should fire.
        with (
            patch.object(mod, "urlopen", side_effect=err) as mock_urlopen,
            patch.object(mod.time, "sleep") as mock_sleep,
        ):
            with pytest.raises(AssertionError):
                _smoke_get("/health", timeout=1)

        assert mock_urlopen.call_count == 1
        mock_sleep.assert_not_called()

    def test_5xx_does_not_retry(self):
        """5xx isn't in the transient allowlist either — sustained
        origin failure should fail fast, not after a 30s sleep."""
        mod = _load_smoke_module()
        _smoke_get = mod._smoke_get

        err = self._make_http_error(502, b"Bad Gateway", {})
        with (
            patch.object(mod, "urlopen", side_effect=err) as mock_urlopen,
            patch.object(mod.time, "sleep") as mock_sleep,
        ):
            with pytest.raises(AssertionError):
                _smoke_get("/health", timeout=1)

        assert mock_urlopen.call_count == 1
        mock_sleep.assert_not_called()
