"""Unit tests for specodex/llm.py generate_content function."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from specodex.llm import _client_for, generate_content


@pytest.mark.unit
class TestGenerateContent:
    """Tests for the generate_content function."""

    def setup_method(self) -> None:
        """Disable retry delays and re-raise original exceptions."""
        from tenacity import stop_after_attempt, wait_none

        generate_content.retry.wait = wait_none()
        generate_content.retry.stop = stop_after_attempt(1)
        generate_content.retry.reraise = True

        # _client_for is lru-cached for prod hot-path reuse; that cache
        # leaks across tests (each test installs its own genai mock but the
        # cached client returned by an earlier test ignores the patch). Wipe
        # it so every test sees its own mock.
        _client_for.cache_clear()

    @patch("specodex.llm.genai")
    def test_pdf_content(self, mock_genai: MagicMock) -> None:
        """PDF bytes are sent via Part.from_bytes with application/pdf mime type."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = Mock()
        mock_client.models.generate_content.return_value = mock_response

        mock_part = Mock()
        mock_genai.types.Part.from_bytes.return_value = mock_part

        result = generate_content(b"pdf bytes", "test-key", "motor", content_type="pdf")

        assert result == mock_response
        mock_genai.Client.assert_called_once_with(
            api_key="test-key", http_options={"api_version": "v1beta"}
        )
        mock_genai.types.Part.from_bytes.assert_called_once_with(
            data=b"pdf bytes",
            mime_type="application/pdf",
        )

    @patch("specodex.llm.genai")
    def test_html_content(self, mock_genai: MagicMock) -> None:
        """HTML string is sent as inline text, not as a Part."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_response = Mock()
        mock_client.models.generate_content.return_value = mock_response

        result = generate_content(
            "<html><body>specs</body></html>",
            "test-key",
            "motor",
            content_type="html",
        )

        assert result == mock_response
        mock_genai.Client.assert_called_once_with(
            api_key="test-key", http_options={"api_version": "v1beta"}
        )
        # HTML content is passed as a single text string, no Part.from_bytes call
        mock_genai.types.Part.from_bytes.assert_not_called()
        call_args = mock_client.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[1].get("contents")
        assert len(contents) == 1
        assert "HTML Content:" in contents[0]
        assert "<html><body>specs</body></html>" in contents[0]

    @patch("specodex.llm.genai")
    def test_with_context(self, mock_genai: MagicMock) -> None:
        """When context is provided, prompt includes context fields as known-data."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = Mock()
        mock_genai.types.Part.from_bytes.return_value = Mock()

        context = {
            "product_name": "Test Motor",
            "manufacturer": "Acme Corp",
            "product_family": "Series X",
            "datasheet_url": "https://example.com/ds.pdf",
        }

        generate_content(
            b"pdf bytes", "test-key", "motor", context=context, content_type="pdf"
        )

        call_args = mock_client.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[1].get("contents")
        prompt_text = contents[1]
        # Prompt calls out each known field by name so Gemini doesn't re-emit it.
        assert "product_name" in prompt_text
        assert "Test Motor" in prompt_text
        assert "Acme Corp" in prompt_text
        assert "Series X" in prompt_text
        assert "https://example.com/ds.pdf" in prompt_text

    @patch("specodex.llm.genai")
    def test_without_context(self, mock_genai: MagicMock) -> None:
        """When context is None, prompt uses generic extraction instructions."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = Mock()
        mock_genai.types.Part.from_bytes.return_value = Mock()

        generate_content(
            b"pdf bytes", "test-key", "motor", context=None, content_type="pdf"
        )

        call_args = mock_client.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args[1].get("contents")
        prompt_text = contents[1]
        assert "ALREADY KNOWN" not in prompt_text
        assert "extracting product specifications" in prompt_text
        # Prompt describes the structured value/unit output shape.
        assert "value" in prompt_text and "unit" in prompt_text

    @patch("specodex.llm.genai")
    def test_uses_json_response_schema(self, mock_genai: MagicMock) -> None:
        """Generated config passes a JSON schema to Gemini."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = Mock()
        mock_genai.types.Part.from_bytes.return_value = Mock()

        generate_content(b"pdf bytes", "test-key", "drive", content_type="pdf")

        call_args = mock_client.models.generate_content.call_args
        config = call_args.kwargs.get("config") or call_args[1].get("config")
        assert config["response_mime_type"] == "application/json"
        schema = config["response_schema"]
        assert schema["type"] == "ARRAY"
        assert schema["items"]["type"] == "OBJECT"
        # Drive-specific: fieldbus is an array of enum-string.
        props = schema["items"]["properties"]
        assert props["fieldbus"]["type"] == "ARRAY"
        assert props["fieldbus"]["items"]["type"] == "STRING"
        assert "EtherCAT" in props["fieldbus"]["items"]["enum"]

    @patch("specodex.llm.genai")
    def test_invalid_content_type(self, mock_genai: MagicMock) -> None:
        """Unsupported content_type raises ValueError."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        with pytest.raises(ValueError, match="Unsupported content_type: xml"):
            generate_content(b"data", "test-key", "motor", content_type="xml")

    @patch("specodex.llm.genai")
    def test_bytes_for_html_raises(self, mock_genai: MagicMock) -> None:
        """Passing bytes when content_type='html' raises ValueError."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        with pytest.raises(ValueError, match="HTML content must be string"):
            generate_content(b"bytes", "test-key", "motor", content_type="html")

    @patch("specodex.llm.genai")
    def test_str_for_pdf_raises(self, mock_genai: MagicMock) -> None:
        """Passing string when content_type='pdf' raises ValueError."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        with pytest.raises(ValueError, match="PDF content must be bytes"):
            generate_content("string data", "test-key", "motor", content_type="pdf")

    @patch("specodex.llm.genai")
    def test_uses_correct_model(self, mock_genai: MagicMock) -> None:
        """Verify the call uses the configured MODEL constant."""
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = Mock()
        mock_genai.types.Part.from_bytes.return_value = Mock()

        generate_content(b"pdf bytes", "test-key", "motor", content_type="pdf")

        call_args = mock_client.models.generate_content.call_args
        model_arg = call_args.kwargs.get("model") or call_args[1].get("model")
        assert model_arg == "gemini-2.5-flash"
