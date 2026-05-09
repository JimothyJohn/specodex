#!/usr/bin/env python3
"""
CLI entry point for specodex.

AI-generated comment: This module provides a command-line interface for the specodex
application, allowing users to run document analysis locally without needing to deploy
to AWS Lambda. It serves as a wrapper around the core analysis functionality and can
be easily extended for MCP (Model Context Protocol) integration.

Usage:
    uv run specodex --url "https://example.com/doc.pdf" --x-api-key $KEYVAR
    uv run specodex --help
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, List, Optional, Type


from specodex.config import SCHEMA_CHOICES
from specodex.db.dynamo import DynamoDBClient
from specodex.ids import compute_product_id
from specodex.ingest_log import (
    STATUS_EXTRACT_FAIL,
    STATUS_QUALITY_FAIL,
    STATUS_SUCCESS,
    build_record,
    should_skip,
)
from specodex.merge import merge_per_page_products
from specodex.models.product import ProductBase
from specodex.quality import score_product, spec_fields_for_model
from specodex.utils import (
    extract_pdf_pages,
    get_document,
    get_web_content,
    is_pdf_url,
    validate_api_key,
    UUIDEncoder,
    get_product_info_from_json,
)
from specodex.extract import call_llm_and_parse
from specodex.page_finder import find_spec_pages_by_text  # noqa: E402

PAGES_PER_CHUNK = int(os.environ.get("PAGES_PER_CHUNK", "4"))
MAX_PER_PAGE_CALLS = int(os.environ.get("MAX_PER_PAGE_CALLS", "30"))
# Bridge small gaps between page_finder hits so a non-keyword continuation
# page rides along in the same chunk as the table it continues. See
# todo/CHUNKS.md for the algorithm and the failure mode that motivated it.
BRIDGE_GAP = int(os.environ.get("BRIDGE_GAP", "1"))
# Number of chunk-extraction LLM calls to run in parallel within one PDF.
# Each chunk is independent (its own slice of the PDF); the scheduler is
# bound by the LLM provider's RPM, not by anything in our code. 4 fits
# comfortably under any paid tier; drop to 1 if you're rate-limit pinned.
MAX_CONCURRENT_LLM_CALLS = int(os.environ.get("MAX_CONCURRENT_LLM_CALLS", "4"))


def _chunk_pages(
    pages_0idx: List[int],
    chunk_max: int = 4,
    bridge_gap: int = 1,
) -> List[List[int]]:
    """Group page_finder hits into chunks of up to ``chunk_max`` pages.

    Two adjustments vs naive stride-N chunking:

    * Pages whose 0-indexed gap is ``≤ bridge_gap`` are treated as one
      run, and the run is **filled** with the missing pages between them
      (e.g. ``[3, 5]`` becomes ``[3, 4, 5]``). This keeps a spec-table
      continuation page that didn't match the keyword set in the same
      chunk as the table it continues.
    * A run longer than ``chunk_max`` is split into back-to-back chunks
      of size ≤ ``chunk_max``. Tables that straddle a chunk boundary
      still get split, but at most once instead of every page.

    See ``todo/CHUNKS.md`` for the algorithm + worked examples.
    """
    if not pages_0idx:
        return []
    sorted_pages = sorted(set(pages_0idx))
    runs: List[List[int]] = []
    current: List[int] = []
    for p in sorted_pages:
        if not current or p - current[-1] - 1 <= bridge_gap:
            current.append(p)
        else:
            runs.append(current)
            current = [p]
    if current:
        runs.append(current)
    chunks: List[List[int]] = []
    for run in runs:
        expanded = list(range(run[0], run[-1] + 1))
        for i in range(0, len(expanded), chunk_max):
            chunks.append(expanded[i : i + chunk_max])
    return chunks


class ElapsedTimeFormatter(logging.Formatter):
    """
    AI-generated comment: This custom logging formatter converts log timestamps into
    an elapsed time format (M:SS), making it easier to track the duration of
    different stages of the program execution. It's initialized once and calculates
    all subsequent log times relative to its creation.
    """

    def __init__(self, fmt=None, datefmt=None, style="%"):
        super().__init__(fmt, datefmt, style)
        self.start_time = time.time()

    def formatTime(self, record, datefmt=None):
        """
        AI-generated comment: This method is overridden from the base Formatter class.
        It calculates the time elapsed since the program started and formats it
        as M:SS.
        """
        elapsed_seconds = record.created - self.start_time
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{int(minutes)}:{int(seconds):02}"


# Configure logging for CLI
# AI-generated comment: A handler is created and equipped with the custom
# ElapsedTimeFormatter. This handler is then passed to logging.basicConfig to ensure
# all log messages will be formatted with elapsed time.
handler = logging.StreamHandler()
handler.setFormatter(
    ElapsedTimeFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    handlers=[handler],
)
logger: logging.Logger = logging.getLogger(__name__)


def main() -> None:
    """
    Datasheetminer CLI - Analyze PDF documents and web pages using Gemini AI.

    AI-generated comment: This is the main CLI function that orchestrates the document
    analysis process. It handles argument parsing, validation, and coordinates the
    analysis workflow while providing user-friendly output and error handling.
    The scraper now intelligently detects whether the URL is a PDF or webpage and
    handles each appropriately.

    Examples:
        # Analyze a PDF datasheet
        export GEMINI_API_KEY="your-api-key"
        uv run specodex --url "https://example.com/motor.pdf"

        # Analyze a product webpage
        uv run specodex --url "https://example.com/product-specs"

        # Save output to file
        uv run specodex -u "https://example.com/spec.pdf" -o analysis.json
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Datasheetminer CLI - Analyze PDF documents and web pages using Gemini AI.",
        epilog="""
    Examples:
        # Analyze a PDF datasheet
        export GEMINI_API_KEY="your-api-key"
        specodex --url "https://example.com/motor.pdf"

        # Analyze a product webpage (automatically detected)
        specodex --url "https://example.com/product-specs"

        # Save output to file
        specodex -u "https://example.com/spec.pdf" -o analysis.json

        Note: The tool automatically detects whether the URL is a PDF or webpage.
        For PDFs, you can specify page ranges. For webpages, the entire page is analyzed.
    """,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "-t",
        "--type",
        required=False,
        help="The type of schema to use for analysis (motor, drive, gearhead, robot_arm, etc)",
    )

    parser.add_argument(
        "--x-api-key",
        help="Gemini API key (can also be set via GEMINI_API_KEY environment variable)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output.json"),
        help="Output file path for saving the response (optional)",
    )
    parser.add_argument(
        "--from-json",
        type=str,
        help="Path to a JSON file with product info.",
    )
    parser.add_argument(
        "--json-index",
        type=int,
        default=0,
        help="Index of the item in the JSON file to process (default: 0)",
    )

    parser.add_argument(
        "--scrape-from-db",
        action="store_true",
        help="Fetch datasheet info from DynamoDB using product name and family.",
    )
    parser.add_argument(
        "--scrape-all",
        action="store_true",
        help="Iterate through ALL datasheets in the DB and scrape them if not already processed.",
    )
    parser.add_argument(
        "--url",
        help="Datasheet URL (required if not using --from-json, --scrape-from-db, or --scrape-all)",
    )
    parser.add_argument("--pages", help="Comma-separated list of pages (e.g. '1,2,3')")
    parser.add_argument("--product-name", help="Product name")
    parser.add_argument("--manufacturer", help="Manufacturer")
    parser.add_argument("--product-family", help="Product family")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the ingest log and re-run even on previously-successful URLs.",
    )

    args: argparse.Namespace = parser.parse_args()
    client: DynamoDBClient = DynamoDBClient()

    # Manually handle API key validation. parser.error() exits the
    # process, but flow analyzers can't see that — pre-bind to keep the
    # type as plain `str` for the long downstream usage.
    api_key: Optional[str] = args.x_api_key or os.environ.get("GEMINI_API_KEY")
    validated_api_key: str = ""
    try:
        validated_api_key = validate_api_key(api_key)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    # Handle Scrape All Mode
    if args.scrape_all:
        logger.info("Starting bulk scrape of all datasheets...")
        all_datasheets = client.get_all_datasheets()
        logger.info(f"Found {len(all_datasheets)} datasheets in DB.")

        success_count = 0
        skip_count = 0
        fail_count = 0

        for ds in all_datasheets:
            logger.info(f"Processing datasheet: {ds.product_name} ({ds.datasheet_id})")
            try:
                result = process_datasheet(
                    client=client,
                    api_key=validated_api_key,
                    product_type=ds.product_type,
                    manufacturer=ds.manufacturer
                    or "Unknown",  # Should not happen if schema enforced
                    product_name=ds.product_name,
                    product_family=ds.product_family or "",
                    url=ds.url,
                    pages=ds.pages,
                    output_path=None,  # Don't write individual files for bulk scrape
                    force=args.force,
                )
                if result == "skipped":
                    skip_count += 1
                elif result == "success":
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Error processing datasheet {ds.datasheet_id}: {e}")
                fail_count += 1

        logger.info(
            f"Bulk scrape completed. Success: {success_count}, Skipped: {skip_count}, Failed: {fail_count}"
        )
        sys.exit(0)

    # Determine source of information for single scrape
    url_raw: Optional[str] = None
    pages: Optional[List[int]] = None
    manufacturer_raw: Optional[str] = None
    product_name_raw: Optional[str] = None
    product_family_raw: Optional[str] = None
    product_type_raw: Optional[str] = args.type

    if args.from_json:
        try:
            info = get_product_info_from_json(
                args.from_json, f"{args.type}", args.json_index
            )
            url_raw = info.get("url")
            pages = info.get("pages")
            manufacturer_raw = info.get("manufacturer")
            product_name_raw = info.get("product_name")
            product_family_raw = info.get("product_family")
            if not product_type_raw:
                product_type_raw = info.get("product_type")
        except (FileNotFoundError, ValueError) as e:
            parser.error(str(e))

    elif args.scrape_from_db:
        # Query DB for datasheet
        datasheets = []

        if args.product_name:
            # Try finding by product name first
            datasheets = client.get_datasheets_by_product_name(args.product_name)
        elif args.product_family:
            # Try finding by family
            datasheets = client.get_datasheets_by_family(args.product_family)
        elif args.manufacturer:
            # Fallback to getting all and filtering
            all_ds = client.get_all_datasheets()
            datasheets = [ds for ds in all_ds if ds.manufacturer == args.manufacturer]
        else:
            # If only type is provided (or nothing else specific), fetch all
            datasheets = client.get_all_datasheets()

        # Filter results based on other provided criteria
        filtered_datasheets = []
        for ds in datasheets:
            match = True
            # Filter by type (required arg)
            if args.type and ds.product_type != args.type:
                match = False

            if args.product_name and ds.product_name != args.product_name:
                match = False
            if args.product_family and ds.product_family != args.product_family:
                match = False
            if args.manufacturer and ds.manufacturer != args.manufacturer:
                match = False

            if match:
                filtered_datasheets.append(ds)

        if not filtered_datasheets:
            criteria = []
            if args.type:
                criteria.append(f"type='{args.type}'")
            if args.product_name:
                criteria.append(f"name='{args.product_name}'")
            if args.product_family:
                criteria.append(f"family='{args.product_family}'")
            if args.manufacturer:
                criteria.append(f"manufacturer='{args.manufacturer}'")

            logger.error(
                f"No datasheet found in DB matching criteria: {', '.join(criteria)}"
            )
            sys.exit(1)

        # Process all matching datasheets
        logger.info(f"Found {len(filtered_datasheets)} matching datasheets in DB.")

        success_count = 0
        skip_count = 0
        fail_count = 0

        for ds in filtered_datasheets:
            logger.info(f"Processing datasheet: {ds.product_name} ({ds.datasheet_id})")
            try:
                result = process_datasheet(
                    client=client,
                    api_key=validated_api_key,
                    product_type=ds.product_type,
                    manufacturer=ds.manufacturer or "Unknown",
                    product_name=ds.product_name,
                    product_family=ds.product_family or "",
                    url=ds.url,
                    pages=ds.pages,
                    output_path=None,  # Don't write individual files for bulk scrape
                    force=args.force,
                )
                if result == "skipped":
                    skip_count += 1
                elif result == "success":
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"Error processing datasheet {ds.datasheet_id}: {e}")
                fail_count += 1

        logger.info(
            f"Scrape from DB completed. Success: {success_count}, Skipped: {skip_count}, Failed: {fail_count}"
        )
        sys.exit(0)

    else:
        # Manual CLI args
        url_raw = args.url
        if args.pages:
            try:
                pages = [int(p.strip()) for p in args.pages.split(",")]
            except ValueError:
                parser.error("Pages must be a comma-separated list of integers")

        manufacturer_raw = args.manufacturer
        product_name_raw = args.product_name
        product_family_raw = args.product_family

    # Validation
    if not url_raw:
        parser.error("URL is required (via --url, --from-json, or --scrape-from-db)")

    # If not scraping from DB, type is required
    if not args.scrape_from_db and not args.scrape_all and not product_type_raw:
        parser.error(
            "Product type is required (via -t/--type) when not scraping from DB."
        )

    if not manufacturer_raw:
        parser.error("Manufacturer is required")
    if not product_name_raw:
        parser.error("Product name is required")
    if not product_type_raw:
        # If we are here, it means we are scraping from DB but the datasheet entry didn't have a type?
        # Or we are iterating and some entries might be missing type.
        # But wait, if we are scraping from DB, we get type from the DB entry.
        # If we are doing manual URL, we enforced it above.
        # So this check is mostly for safety.
        parser.error("Product type is required")

    # Type narrowing
    manufacturer_str: str = manufacturer_raw
    product_name_str: str = product_name_raw
    product_family_str: str = product_family_raw or ""
    url_str: str = url_raw
    product_type_str: str = product_type_raw

    try:
        process_datasheet(
            client=client,
            api_key=validated_api_key,
            product_type=product_type_str,
            manufacturer=manufacturer_str,
            product_name=product_name_str,
            product_family=product_family_str,
            url=url_str,
            pages=pages,
            output_path=args.output,
            force=args.force,
        )
    except Exception as e:
        logger.error(f"Error during document analysis: {e}")
        sys.exit(1)


def _extract_bundled_pdf(full_pdf: bytes, pages_0idx: List[int]) -> bytes:
    """Extract a subset of pages from a PDF into a new PDF, return bytes."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_in:
        tmp_in.write(full_pdf)
        tmp_in_path = Path(tmp_in.name)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_out:
        tmp_out_path = Path(tmp_out.name)
    extract_pdf_pages(tmp_in_path, tmp_out_path, pages_0idx)
    data = tmp_out_path.read_bytes()
    tmp_in_path.unlink()
    tmp_out_path.unlink()
    return data


def _save_failure_artifacts(
    save_dir: Path,
    *,
    url: str,
    status: str,
    source_bytes: Optional[bytes | str],
    content_type: str,
    parsed_models: List[Any],
    pages_detected: int,
    pages_used: List[int],
    page_finder_method: Optional[str],
    manufacturer: str,
    product_type: str,
    product_name_hint: str,
    product_family_hint: str,
    error_message: Optional[str] = None,
) -> None:
    """Write a failed-extraction snapshot to disk for manual inspection.

    Layout (one directory per failed URL, keyed by URL hash):
        <save_dir>/<sha16>/
            datasheet.pdf | datasheet.html   — original source bytes (if downloaded)
            metadata.json                    — url, status, manufacturer, error, pages
            parsed.json                      — Pydantic-validated rows (may be empty)

    Best-effort: any failure to write artifacts is logged at WARN and swallowed
    so it doesn't mask the original failure.
    """
    try:
        import hashlib

        slug = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        dest = save_dir / slug
        dest.mkdir(parents=True, exist_ok=True)

        if source_bytes is not None:
            ext = "pdf" if content_type == "pdf" else "html"
            doc_path = dest / f"datasheet.{ext}"
            if isinstance(source_bytes, bytes):
                doc_path.write_bytes(source_bytes)
            else:
                doc_path.write_text(source_bytes, encoding="utf-8")

        metadata = {
            "url": url,
            "status": status,
            "manufacturer": manufacturer,
            "product_type": product_type,
            "product_name_hint": product_name_hint,
            "product_family_hint": product_family_hint,
            "content_type": content_type,
            "pages_detected": pages_detected,
            "pages_used": pages_used,
            "page_finder_method": page_finder_method,
            "products_extracted": len(parsed_models),
            "error_message": error_message,
        }
        (dest / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        parsed_payload = [m.model_dump(mode="json") for m in parsed_models]
        (dest / "parsed.json").write_text(
            json.dumps(parsed_payload, indent=2, cls=UUIDEncoder), encoding="utf-8"
        )

        logger.info("Saved failure artifacts to %s", dest)
    except Exception as exc:
        logger.warning("Failed to save failure artifacts to %s: %s", save_dir, exc)


def _extract_per_page(
    full_pdf: bytes,
    pages_0idx: List[int],
    api_key: str,
    product_type: str,
    context: dict,
    content_type: str,
    tokens: Optional[dict] = None,
) -> List[Any]:
    """Extract products from each chunk in parallel, tagging each with source pages.

    Per-chunk failures are isolated: one bad chunk doesn't kill the rest.
    Order of completion is non-deterministic, but the merge step downstream
    (merge_per_page_products) is order-independent, and each product carries
    its own ``pages`` annotation so source-page traceability is preserved.
    """
    chunks = _chunk_pages(
        pages_0idx,
        chunk_max=max(1, PAGES_PER_CHUNK),
        bridge_gap=max(0, BRIDGE_GAP),
    )
    if not chunks:
        return []

    def _run_chunk(chunk: List[int]) -> List[Any]:
        pages_1idx = [p + 1 for p in chunk]
        logger.info("Extracting page(s) %s (1-indexed)", pages_1idx)
        try:
            page_pdf = _extract_bundled_pdf(full_pdf, chunk)
            page_context = dict(context, single_page_mode=True)
            products = call_llm_and_parse(
                page_pdf, api_key, product_type, page_context, content_type, tokens
            )
            for model in products:
                model.pages = pages_1idx
            logger.info("Got %d products from page(s) %s", len(products), pages_1idx)
            return products
        except Exception as e:
            logger.error("Failed to extract page(s) %s: %s", pages_1idx, e)
            return []

    workers = max(1, min(len(chunks), MAX_CONCURRENT_LLM_CALLS))
    all_products: List[Any] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_chunk, c) for c in chunks]
        for fut in as_completed(futures):
            all_products.extend(fut.result())
    return all_products


DEFAULT_FAILED_DATASHEETS_DIR = Path("outputs/failed_datasheets")


def process_datasheet(
    client: DynamoDBClient,
    api_key: str,
    product_type: str,
    manufacturer: str,
    product_name: str,
    product_family: str,
    url: str,
    pages: Optional[List[int]],
    output_path: Optional[Path] = None,
    force: bool = False,
    save_failed_to: Optional[Path] = DEFAULT_FAILED_DATASHEETS_DIR,
) -> str:
    """
    Process a single datasheet: check existence, scrape, parse, and save to DB.

    Every non-short-circuit outcome writes one ingest-log record so the
    scraper can skip already-processed URLs and ``ingest-report`` can
    group quality-fails by manufacturer for vendor outreach.

    Args:
        force: if True, ignore the ingest log and re-run even on URLs
            that previously succeeded. The in-DB ``product_exists`` check
            still runs (to avoid UUID collisions on repeat rows).
        save_failed_to: directory to drop a snapshot (source PDF/HTML +
            metadata + partial parsed rows) into on every quality_fail
            / extract_fail. Defaults to ``outputs/failed_datasheets/``;
            pass ``None`` to disable. Lets you re-open a problem
            datasheet locally and decide whether the catalog is broken or
            our pipeline is.

    Returns: "success", "skipped", or "failed".
    """
    model_class: Type[ProductBase] = SCHEMA_CHOICES[product_type]

    # Pre-flight: skip URLs the ingest log says were already processed
    # successfully (or unlikely to improve on re-run). The scraper path
    # gains its cheapest win here — a Query instead of download + LLM.
    if not force:
        last = client.read_ingest(url)
        if should_skip(last):
            logger.info(
                "Skipping %s — prior attempt %s on %s (fields_filled_avg=%s/%s)",
                url,
                last.get("status"),  # type: ignore[union-attr]
                last.get("SK"),  # type: ignore[union-attr]
                last.get("fields_filled_avg"),  # type: ignore[union-attr]
                last.get("fields_total"),  # type: ignore[union-attr]
            )
            return "skipped"

    if client.product_exists(product_type, manufacturer, product_name, model_class):
        logger.warning(
            f"⚠️  Product '{product_name}' by manufacturer '{manufacturer}' with product_type '{product_type}' "
            f"already exists in the database. Skipping scraping to avoid duplicates."
        )
        # Per ENRICH.md: skipped_dup writes no log — the existing product
        # record is the source of truth and re-asserting adds only noise.
        return "skipped"

    # Context for LLM
    context = {
        "product_name": product_name,
        "manufacturer": manufacturer,
        "product_family": product_family,
        "datasheet_url": url,
        "pages": pages,
    }

    is_pdf: bool = is_pdf_url(url)
    content_type: str = "pdf" if is_pdf else "html"

    logger.info(f"Starting document analysis for: {url}")
    logger.info(f"Content type detected: {content_type}")
    if is_pdf and pages:
        logger.info(f"Pages: {pages}")

    # Per-attempt ingest-log scratch. Populated throughout the call and
    # written once at the end (or in the failure handler).
    tokens: dict = {"input": 0, "output": 0}
    pages_detected: int = 0
    pages_used: List[int] = []
    page_finder_method: Optional[str] = None
    parsed_models: List[Any] = []
    # Source bytes for the failed-pdf snapshot — populated post-download.
    source_bytes: Optional[bytes | str] = None

    def _maybe_save_failure(status: str, error_message: Optional[str] = None) -> None:
        if save_failed_to is None:
            return
        _save_failure_artifacts(
            save_failed_to,
            url=url,
            status=status,
            source_bytes=source_bytes,
            content_type=content_type,
            parsed_models=parsed_models,
            pages_detected=pages_detected,
            pages_used=pages_used,
            page_finder_method=page_finder_method,
            manufacturer=manufacturer,
            product_type=product_type,
            product_name_hint=product_name,
            product_family_hint=product_family,
            error_message=error_message,
        )

    try:
        doc_data: Optional[bytes | str] = None

        if is_pdf:
            full_pdf = get_document(url)
            if full_pdf is None:
                logger.error("Could not retrieve PDF document.")
                _write_ingest_log(
                    client,
                    url=url,
                    manufacturer=manufacturer,
                    product_type=product_type,
                    product_name_hint=product_name,
                    product_family_hint=product_family,
                    status=STATUS_EXTRACT_FAIL,
                    error_message="pdf_download_failed",
                )
                _maybe_save_failure(STATUS_EXTRACT_FAIL, "pdf_download_failed")
                return "failed"

            source_bytes = full_pdf

            # Auto-detect spec pages when none specified
            if not pages:
                detected = find_spec_pages_by_text(full_pdf)
                if detected:
                    pages = detected
                    pages_detected = len(detected)
                    page_finder_method = "text_keyword"
                    logger.info(f"Auto-detected {len(pages)} spec pages: {pages}")
                    context["pages"] = pages
            else:
                pages_detected = len(pages)
                page_finder_method = "explicit"

            if pages and len(pages) <= MAX_PER_PAGE_CALLS:
                pages_used = list(pages)
                parsed_models = _extract_per_page(
                    full_pdf,
                    pages,
                    api_key,
                    product_type,
                    context,
                    content_type,
                    tokens,
                )
            elif pages:
                logger.warning(
                    "Spec pages (%d) exceeds MAX_PER_PAGE_CALLS (%d), falling back to bundled extraction",
                    len(pages),
                    MAX_PER_PAGE_CALLS,
                )
                pages_used = list(pages)
                doc_data = _extract_bundled_pdf(full_pdf, pages)
                parsed_models = call_llm_and_parse(
                    doc_data, api_key, product_type, context, content_type, tokens
                )
                for model in parsed_models:
                    model.pages = [p + 1 for p in pages]
            else:
                doc_data = full_pdf
                parsed_models = call_llm_and_parse(
                    doc_data, api_key, product_type, context, content_type, tokens
                )
        else:
            if pages:
                logger.warning("Pages parameter is ignored for web content")
            doc_data = get_web_content(url)
            if doc_data is None:
                logger.error("Could not retrieve web content.")
                _write_ingest_log(
                    client,
                    url=url,
                    manufacturer=manufacturer,
                    product_type=product_type,
                    product_name_hint=product_name,
                    product_family_hint=product_family,
                    status=STATUS_EXTRACT_FAIL,
                    error_message="html_download_failed",
                )
                _maybe_save_failure(STATUS_EXTRACT_FAIL, "html_download_failed")
                return "failed"
            source_bytes = doc_data
            parsed_models = call_llm_and_parse(
                doc_data, api_key, product_type, context, content_type, tokens
            )

        if not parsed_models:
            logger.error("No valid products extracted.")
            _write_ingest_log(
                client,
                url=url,
                manufacturer=manufacturer,
                product_type=product_type,
                product_name_hint=product_name,
                product_family_hint=product_family,
                status=STATUS_EXTRACT_FAIL,
                pages_detected=pages_detected,
                pages_used=pages_used,
                page_finder_method=page_finder_method,
                gemini_input_tokens=tokens["input"],
                gemini_output_tokens=tokens["output"],
                error_message="no_products_extracted",
            )
            _maybe_save_failure(STATUS_EXTRACT_FAIL, "no_products_extracted")
            return "failed"

        products_extracted_raw = len(parsed_models)

        # Merge partial records from per-page extraction
        parsed_models = merge_per_page_products(parsed_models)

        # Inject source metadata and deterministic IDs
        valid_models: List[Any] = []

        for model in parsed_models:
            if model.pages:
                model.datasheet_url = f"{url}#page={model.pages[0]}"
            else:
                model.datasheet_url = url

            mfg = model.manufacturer or manufacturer
            family_for_id = getattr(model, "product_family", None) or product_family
            pid = compute_product_id(
                mfg, model.part_number, model.product_name, family_for_id
            )
            if pid is None:
                logger.error(
                    "Could not generate ID for product '%s'. "
                    "Missing Manufacturer AND (Part Number OR Product Name). Skipping.",
                    model.product_name,
                )
                continue

            model.product_id = pid
            logger.info(f"Generated ID {model.product_id}")

            existing_item = client.read(model.product_id, type(model))
            if existing_item:
                logger.info(
                    f"Product with ID {model.product_id} already exists. Skipping."
                )
                continue

            valid_models.append(model)

        # Quality filter — reject products with too many missing spec fields.
        # Use the post-merge list for the "missing fields" computation so the
        # log reflects what the vendor would actually see as gaps. We score
        # all merged models (passed + rejected) to build the union of gaps.
        from specodex.quality import filter_products

        scored_models = parsed_models  # full merged set for missing-fields union
        passed_models, rejected_models = filter_products(valid_models)
        if rejected_models:
            logger.warning(
                "Dropped %d low-quality products (too many N/A fields)",
                len(rejected_models),
            )

        # Quality metrics over all extracted products (post-merge), regardless
        # of whether they passed the gate — the outreach use case wants the
        # full picture of what the vendor is missing.
        total_fields = len(spec_fields_for_model(model_class))
        missing_union: set[str] = set()
        filled_total = 0
        scored_count = 0
        for m in scored_models:
            _score, filled, _total, missing = score_product(m)
            filled_total += filled
            scored_count += 1
            missing_union.update(missing)
        fields_filled_avg = (filled_total / scored_count) if scored_count else 0.0

        extracted_part_numbers = [
            m.part_number for m in scored_models if getattr(m, "part_number", None)
        ]

        parsed_data: List[Any] = [item.model_dump() for item in passed_models]

        if output_path:
            try:
                formatted_response: str = json.dumps(
                    parsed_data, indent=2, cls=UUIDEncoder
                )
                output_path.write_text(formatted_response, encoding="utf-8")
                print(f"Response saved to: {output_path}", file=sys.stderr)
            except Exception as e:
                print(f"Error saving response: {e}", file=sys.stderr)

        success_count: int = client.batch_create(passed_models)
        failure_count: int = len(parsed_data) - success_count
        logger.info(
            f"Successfully pushed {success_count} items to DynamoDB, {failure_count} items failed"
        )

        # Ingest-log: success if anything landed; quality_fail if the gate
        # dropped everything that survived ID/dup checks; extract_fail if
        # even the ID/dup step left us with nothing.
        if success_count > 0:
            log_status = STATUS_SUCCESS
        elif scored_count > 0:
            log_status = STATUS_QUALITY_FAIL
        else:
            log_status = STATUS_EXTRACT_FAIL

        _write_ingest_log(
            client,
            url=url,
            manufacturer=manufacturer,
            product_type=product_type,
            product_name_hint=product_name,
            product_family_hint=product_family,
            status=log_status,
            products_extracted=products_extracted_raw,
            products_written=success_count,
            fields_total=total_fields,
            fields_filled_avg=fields_filled_avg,
            fields_missing=missing_union,
            pages_detected=pages_detected,
            pages_used=pages_used,
            page_finder_method=page_finder_method,
            extracted_part_numbers=extracted_part_numbers,
            gemini_input_tokens=tokens["input"],
            gemini_output_tokens=tokens["output"],
        )

        if success_count == 0:
            _maybe_save_failure(log_status)
            return "failed"
        return "success"

    except Exception as e:
        logger.error(f"Error during document analysis: {e}")
        _write_ingest_log(
            client,
            url=url,
            manufacturer=manufacturer,
            product_type=product_type,
            product_name_hint=product_name,
            product_family_hint=product_family,
            status=STATUS_EXTRACT_FAIL,
            pages_detected=pages_detected,
            pages_used=pages_used,
            page_finder_method=page_finder_method,
            gemini_input_tokens=tokens["input"],
            gemini_output_tokens=tokens["output"],
            error_message=str(e)[:500],
        )
        _maybe_save_failure(STATUS_EXTRACT_FAIL, str(e)[:500])
        return "failed"


def _write_ingest_log(client: DynamoDBClient, **kwargs: Any) -> None:
    """Build and write an ingest-log record, swallowing any failure.

    Best-effort by design: a logging failure must not roll back a
    successful product write, so both build and write are wrapped.
    """
    try:
        record = build_record(**kwargs)
        client.write_ingest(record)
    except Exception as exc:
        logger.warning("ingest-log write failed: %s", exc)


if __name__ == "__main__":
    # AI-generated comment: This allows the module to be run directly as a script
    # in addition to being imported as a module, providing flexibility for
    # different execution methods.
    main()
