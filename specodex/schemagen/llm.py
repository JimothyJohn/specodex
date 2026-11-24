"""Gemini call that proposes a ProposedModel from one or more datasheets.

Uses Gemini's structured-output mode with ``response_schema=ProposedModel``
— the ``google.genai`` SDK converts the Pydantic class to Gemini's
OpenAPI-subset schema internally, so the response text is JSON matching
``ProposedModel``'s shape.

Accepts a mix of PDFs and images (PNG/JPEG/WEBP). Each source is tagged
with its own mime_type so a cropped spec-table screenshot can sit
alongside a full PDF catalog in the same call.

Multi-source: the CLI passes a list of pre-filtered byte blobs (one
per datasheet). Each source lands as its own content part with a tag
the LLM can cite back in ``ProposedModel.sources``; the prompt asks
for a schema that generalizes across them rather than being tuned to
any single vendor's quirks.

Large PDFs (>20 MB) route through the Files API to stay under the
inline upload cap; uploaded files are deleted after the call so
storage doesn't accumulate across runs.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Type

from google import genai
from google.genai import types as genai_types
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from specodex.config import MODEL, SCHEMA_CHOICES, GEMINI_HTTP_OPTIONS
from specodex.models.product import ProductBase
from specodex.schemagen.meta_schema import ProposedModel
from specodex.schemagen.prompt import build_system_prompt, build_user_prompt
from specodex.utils import _strip_json_fences

logger: logging.Logger = logging.getLogger(__name__)

GEMINI_MAX_TOKENS: int = 16000

# Route binary PDFs at or above this size through the Files API. Gemini's
# inline document cap is around 20 MB; bigger catalogs need the upload path.
FILES_API_SIZE_THRESHOLD_BYTES: int = 20 * 1024 * 1024


def _should_retry(exc: BaseException) -> bool:
    """Retry transient transport errors; bail on 4xx client errors."""
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.ClientError):
        return False
    return True


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=30),
    retry=retry_if_exception(_should_retry),
)
def propose_model(
    doc_bytes_list: Sequence[bytes],
    product_type: str,
    api_key: str,
    source_paths: Optional[Sequence[Path]] = None,
    mime_types: Optional[Sequence[str]] = None,
    schema_choices: Optional[Dict[str, Type[ProductBase]]] = None,
    model: str = MODEL,
    max_fields: int = 30,
) -> ProposedModel:
    """Send ``doc_bytes_list`` to Gemini and return a validated ``ProposedModel``.

    Large PDFs auto-upload via the Files API and are deleted afterwards.
    Images (PNG/JPEG/WEBP) always go inline — they're small enough.

    Args:
        doc_bytes_list: One or more datasheet byte blobs. PDFs should be
            pre-filtered through ``page_finder`` by the caller; images
            are passed through as-is.
        product_type: snake_case product type key (e.g. ``"contactor"``).
        api_key: Gemini API key. Falls through to ``GEMINI_API_KEY`` env
            var when empty.
        source_paths: Local filesystem paths for each blob in
            ``doc_bytes_list``, same order. Fed into the prompt so the
            LLM can cite them by filename in ``ProposedModel.sources``.
            Optional — if omitted, sources are labelled "source 1",
            "source 2", etc.
        mime_types: Per-source mime type. Same length as ``doc_bytes_list``
            when provided. Defaults to ``application/pdf`` for every
            source (backwards compatible with the PDF-only callers).
        schema_choices: Registry of existing product types to reflect over
            for examples and the reuse-registry. Defaults to the live
            ``SCHEMA_CHOICES`` — override in tests.
        model: Gemini model id.
        max_fields: Soft cap surfaced in the user prompt.

    Raises:
        google.genai.errors.ClientError: transport/auth errors (not retried
            for auth, file-size, or other non-transient conditions).
        ValueError: the LLM returned output that fails ``ProposedModel``
            validation or isn't valid JSON, or no sources were passed.
    """
    if not doc_bytes_list:
        raise ValueError("propose_model requires at least one document blob.")
    if schema_choices is None:
        schema_choices = SCHEMA_CHOICES

    if mime_types is None:
        mimes: List[str] = ["application/pdf"] * len(doc_bytes_list)
    else:
        if len(mime_types) != len(doc_bytes_list):
            raise ValueError(
                "mime_types length must match doc_bytes_list length "
                f"(got {len(mime_types)} vs {len(doc_bytes_list)})."
            )
        mimes = list(mime_types)

    # Normalize source labels so the prompt can reference each blob
    # with a stable identifier ("SOURCE 1: abb-af09.pdf").
    labels: List[str] = []
    for i, _ in enumerate(doc_bytes_list):
        if source_paths and i < len(source_paths):
            labels.append(source_paths[i].name)
        else:
            ext = "png" if mimes[i].startswith("image/") else "pdf"
            labels.append(f"source-{i + 1}.{ext}")

    client = (
        genai.Client(api_key=api_key, http_options=GEMINI_HTTP_OPTIONS)
        if api_key
        else genai.Client(http_options=GEMINI_HTTP_OPTIONS)
    )

    system_prompt = build_system_prompt(schema_choices)
    user_prompt = build_user_prompt(product_type, max_fields, source_labels=labels)

    uploaded_files: List[Any] = []
    parts: List[Any] = []

    try:
        for label, doc_bytes, mime in zip(labels, doc_bytes_list, mimes):
            # Files API is only meaningful for big PDFs. Images are
            # always small enough to send inline.
            use_files_api = (
                mime == "application/pdf"
                and len(doc_bytes) >= FILES_API_SIZE_THRESHOLD_BYTES
            )
            if use_files_api:
                logger.info(
                    "schemagen: %s is %.1f MB; uploading via Files API",
                    label,
                    len(doc_bytes) / (1024 * 1024),
                )
                uploaded = client.files.upload(
                    file=io.BytesIO(doc_bytes),
                    config={"mime_type": mime},
                )
                uploaded_files.append(uploaded)
                parts.append(uploaded)
            else:
                parts.append(
                    genai_types.Part.from_bytes(
                        data=doc_bytes,
                        mime_type=mime,
                    )
                )
            # Tag each source with its label so the LLM can cite it back
            # in ProposedModel.sources[*].name.
            parts.append(f"[SOURCE {len(parts) // 2}: {label}]")

        total_bytes = sum(len(b) for b in doc_bytes_list)
        logger.info(
            "schemagen: calling %s with %d sources, %d total bytes for product_type=%r",
            model,
            len(doc_bytes_list),
            total_bytes,
            product_type,
        )

        response = client.models.generate_content(
            model=model,
            contents=[*parts, user_prompt],
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=ProposedModel,
                max_output_tokens=GEMINI_MAX_TOKENS,
            ),
        )
    finally:
        # Files persist until deleted; clean up every upload regardless
        # of success so we don't leak storage on retry loops or
        # validation errors.
        for uploaded in uploaded_files:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as e:
                logger.warning(
                    "Failed to delete uploaded file %s: %s — clean up manually",
                    uploaded.name,
                    e,
                )

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        logger.info(
            "schemagen usage: input=%s output=%s cache_read=%s",
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
            getattr(usage, "cached_content_token_count", None),
        )

    raw_text = getattr(response, "text", None) or ""
    if not raw_text:
        raise ValueError("Gemini response has no text content.")
    raw_text = _strip_json_fences(raw_text)
    if not raw_text:
        raise ValueError("Empty Gemini response text; cannot parse JSON.")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini response was not valid JSON: {e}") from e

    pm = ProposedModel.model_validate(payload)

    # If the LLM returned sources, supplement missing local_path entries
    # with the actual filesystem paths we passed in — this way the .md
    # doc can always reference a concrete file even when the LLM only
    # cited the vendor name.
    if pm.sources and source_paths:
        path_by_name = {p.name: str(p) for p in source_paths}
        for src in pm.sources:
            if src.local_path:
                continue
            # Try to match the source's name against any of the input
            # filenames. Cheap fuzzy: substring either direction.
            for fname, full in path_by_name.items():
                if fname in src.name or src.name in fname:
                    src.local_path = full
                    break

    return pm
