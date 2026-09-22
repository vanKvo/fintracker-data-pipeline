"""Extractor Service — orchestrator-agnostic document extraction.

Three sources of transactions, tried in this order of preference:
  1. CSV: parsed directly from S3 using the user-confirmed column mapping.
  2. Tier 1 (PDF only): PyMuPDF reads the PDF's own text layer — no OCR,
     no per-page cost, fully deterministic (REQ-DP-01 "Text-Layer Fast Path").
  3. Tier 2 (PDF pages Tier 1 couldn't read, and all Image/screenshot
     uploads): Textract AnalyzeDocument with FeatureTypes=["TABLES"] — the
     same call classifies the page (does it have a TABLE block?) and
     extracts it, so there's no separate classify-then-extract round trip
     (REQ-DP-01 "Textract Fallback (Tier 2)").

Tier 2 pages are processed concurrently (REQ-DP-02 "Parallel Page
Extraction") since each Textract call is independent, I/O-bound network
work — a ThreadPoolExecutor gets real concurrency here despite the GIL,
because boto3's HTTP call releases it while waiting on the socket.

__author__ = "Van Vo"
"""

from __future__ import annotations

import csv
import io
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from decimal import Decimal
from typing import Optional

import boto3
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.observability import logger
from ..shared.exceptions import InvalidCsvFormatError, NoValidTransactionsError
from ..shared.schemas import ExtractionTier
from .schemas import (
    IMAGE_TEXTRACT_CONFIDENCE,
    TIER_CONFIDENCE,
    PageExtraction,
    RawTransaction,
    StatementMetadata,
    TextractOutput,
)

_textract = boto3.client("textract")
_s3_client = boto3.client("s3")

_CANONICAL_FIELD_TO_RAW_FIELD = {"date": "raw_date", "merchant": "raw_merchant", "amount": "raw_amount"}

# REQ-DP-02: bounded so a large statement can't exhaust the Lambda's own
# connection pool / the Ledger/Textract account-level concurrency limits —
# see data-pipeline-notes-01.md for the reasoning behind this specific cap.
_MAX_CONCURRENT_TEXTRACT_CALLS = 8

_METADATA_PATTERNS: dict[str, re.Pattern] = {
    "bank_name": re.compile(
        r"(chase|bank of america|wells fargo|citi|capital one|discover|amex|american express|usaa|pnc|td bank|us bank)",
        re.IGNORECASE,
    ),
    "opening_date": re.compile(
        r"(?:opening|start|begin|from)\s*(?:date)?[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.IGNORECASE,
    ),
    "closing_date": re.compile(
        r"(?:closing|end|through|to)\s*(?:date)?[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.IGNORECASE,
    ),
    "total_purchases": re.compile(
        r"(?:total\s+purchases|total\s+debits)[:\s]*\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
    "total_credits": re.compile(
        r"(?:total\s+credits|total\s+payments)[:\s]*\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
    "previous_balance": re.compile(
        r"(?:previous\s+balance|prior\s+balance|beginning\s+balance)[:\s]*\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
}

# Tier 1 heuristic: a line is transaction-shaped if it has a date-like token
# and a currency-amount-like token. Deliberately permissive (over-matching a
# few non-transaction lines is fine — the Normalizer already skips rows with
# unparsable amounts); under-matching would silently drop real rows.
_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_AMOUNT_PATTERN = re.compile(r"\(?-?\$?\d[\d,]*\.\d{2}\)?")


# ---------------------------------------------------------------------------
# CSV path
# ---------------------------------------------------------------------------


def parse_csv_transactions(bucket: str, csv_s3_key: str, confirmed_mapping: dict[str, str]) -> list[RawTransaction]:
    """Parse a CSV into raw transactions using the user-confirmed column mapping.

    Args:
        bucket: S3 bucket.
        csv_s3_key: S3 key of the CSV file.
        confirmed_mapping: canonical field ("date"/"merchant"/"amount") ->
            the CSV's actual source column name, as confirmed by the user
            in the mapping dialog (REQ-DP-01 "User Mapping Confirmation Gate").

    Returns:
        List of parsed raw transactions, tagged as ExtractionTier.CSV with
        full confidence — a row that fails to parse is skipped and logged
        (REQ-DP-01 F. Error Handling — CSV_ROW_PARSE_ERROR), never fatal
        to the rest of the file.

    Raises:
        InvalidCsvFormatError: If the confirmed mapping references a column
            that doesn't actually exist in this file (the file changed
            between proposal and confirmation).
    """
    missing_fields = _CANONICAL_FIELD_TO_RAW_FIELD.keys() - confirmed_mapping.keys()
    if missing_fields:
        raise InvalidCsvFormatError(f"Confirmed mapping is missing required fields: {sorted(missing_fields)}")

    response = _s3_client.get_object(Bucket=bucket, Key=csv_s3_key)
    text = response["Body"].read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise InvalidCsvFormatError("CSV has no header row")

    actual_headers = set(reader.fieldnames)
    missing_columns = set(confirmed_mapping.values()) - actual_headers
    if missing_columns:
        raise InvalidCsvFormatError(f"Confirmed mapping references columns not present in the file: {sorted(missing_columns)}")

    transactions: list[RawTransaction] = []
    for row_num, row in enumerate(reader, start=2):
        try:
            transactions.append(
                RawTransaction(
                    raw_date=(row.get(confirmed_mapping["date"]) or "").strip(),
                    raw_merchant=(row.get(confirmed_mapping["merchant"]) or "").strip(),
                    raw_amount=(row.get(confirmed_mapping["amount"]) or "").strip(),
                    extraction_tier=ExtractionTier.CSV,
                    confidence=TIER_CONFIDENCE[ExtractionTier.CSV],
                )
            )
        except ValidationError as e:
            logger.warning("CSV_ROW_PARSE_ERROR — invalid row skipped", row=row_num, error=str(e))

    return transactions


# ---------------------------------------------------------------------------
# Tier 1 — PDF text layer
# ---------------------------------------------------------------------------


def _parse_text_layer_line(line: str) -> Optional[RawTransaction]:
    """Parse a single text-layer line into a raw transaction, if it looks
    like one. Returns None for lines that don't match the heuristic."""
    date_match = _DATE_PATTERN.search(line)
    amount_matches = list(_AMOUNT_PATTERN.finditer(line))
    if not date_match or not amount_matches:
        return None

    amount_match = amount_matches[-1]  # the trailing amount is the transaction total
    merchant = (line[: amount_match.start()] + line[amount_match.end() :]).replace(date_match.group(), "", 1).strip()
    if not merchant:
        return None

    try:
        return RawTransaction(
            raw_date=date_match.group(),
            raw_merchant=merchant,
            raw_amount=amount_match.group(),
            extraction_tier=ExtractionTier.TEXT_LAYER,
            confidence=TIER_CONFIDENCE[ExtractionTier.TEXT_LAYER],
        )
    except ValidationError:
        return None


def extract_from_text_layer(bucket: str, s3_key: str) -> dict[int, list[RawTransaction]]:
    """Tier 1: read a PDF's own text layer directly, no OCR involved.

    Args:
        bucket: S3 bucket.
        s3_key: S3 key of the original PDF.

    Returns:
        Mapping of page index (0-based) to the transaction-shaped rows
        found on that page. A page absent from the dict, or mapped to an
        empty list, has no usable text layer and needs Tier 2
        (REQ-DP-01 F. Error Handling — TEXT_LAYER_EMPTY).
    """
    import fitz  # type: ignore[import-untyped]

    response = _s3_client.get_object(Bucket=bucket, Key=s3_key)
    pdf_bytes = response["Body"].read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    MINIMUM_EXTRACTABLE_CHARS = 150
    FINANCIAL_KEYWORDS = {"balance", "account", "statement", "deposit", "withdrawal", "date", "amount"}

    pages: dict[int, list[RawTransaction]] = {}
    for page_num, page in enumerate(doc):
        page_text = (page.get_text() or "").lower()
        # Remove all non-alphanumeric characters (spaces, punctuation, symbols)
        clean_text = "".join(char for char in (page_text or "") if char.isalnum()) 
        # Check if text length is low OR if it lacks basic bank statement terminology
        has_keywords = any(kw in page_text for kw in FINANCIAL_KEYWORDS)

        if len(clean_text) < MINIMUM_EXTRACTABLE_CHARS or not has_keywords:
            continue  # skip scanned page with no text layer/noisy chars

        rows = [row for line in page_text.splitlines() if (row := _parse_text_layer_line(line))]
        if rows:
            pages[page_num] = rows

    logger.info("Text-layer extraction complete", s3_key=s3_key, pages_with_rows=len(pages), total_pages=doc.page_count)
    return pages


# ---------------------------------------------------------------------------
# Tier 2 — Textract
# ---------------------------------------------------------------------------


def _rasterize_pdf_page(bucket: str, s3_key: str, page_num: int) -> bytes:
    """Render a single PDF page to JPEG bytes, only called for pages Tier 1
    couldn't handle — most digitally-generated PDFs never reach this."""
    import fitz  # type: ignore[import-untyped]
    from PIL import Image

    response = _s3_client.get_object(Bucket=bucket, Key=s3_key)
    pdf_bytes = response["Body"].read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[page_num].get_pixmap(dpi=150)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_textract(bucket: str, key: str) -> list[dict]:
    """Call Textract AnalyzeDocument with retry/exponential backoff
    (REQ-DP-01 F. Error Handling — TEXTRACT_TRANSIENT_FAILURE)."""
    response = _textract.analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES"],
    )
    return response.get("Blocks", [])


def _extract_full_text(blocks: list[dict]) -> str:
    return "\n".join(block.get("Text", "") for block in blocks if block.get("BlockType") == "LINE")


def _blocks_to_raw_transactions(blocks: list[dict], tier_confidence: Decimal) -> list[RawTransaction]:
    """Parse Textract TABLE blocks into RawTransaction objects.

    Table structure expected: Column 1=Date, Column 2=Merchant, Column 3=Amount.
    """
    cells: dict[tuple[int, int], str] = {}
    for block in blocks:
        if block.get("BlockType") == "CELL":
            row, col = block.get("RowIndex", 0), block.get("ColumnIndex", 0)
            text = " ".join(
                rel.get("Text", "") for rel in block.get("Relationships", []) if rel.get("Type") == "CHILD"
            ).strip()
            cells[(row, col)] = text

    if not cells:
        return []

    max_row = max(r for r, _ in cells.keys())
    transactions: list[RawTransaction] = []
    for row in range(2, max_row + 1):
        date, merchant, amount = cells.get((row, 1), "").strip(), cells.get((row, 2), "").strip(), cells.get((row, 3), "").strip()
        if merchant and amount:
            try:
                transactions.append(
                    RawTransaction(
                        raw_merchant=merchant,
                        raw_amount=amount,
                        raw_date=date,
                        extraction_tier=ExtractionTier.TEXTRACT,
                        confidence=tier_confidence,
                    )
                )
            except ValidationError as e:
                logger.warning("Invalid transaction row skipped", row=row, error=str(e))
    return transactions


def classify_and_extract_page(bucket: str, s3_key: str, page_num: int, is_pdf: bool, is_screenshot: bool) -> PageExtraction:
    """Tier 2: a single AnalyzeDocument call both classifies (has a TABLE
    block?) and extracts a page — no separate classification round trip.

    Args:
        bucket: S3 bucket.
        s3_key: S3 key of the original file (PDF or image).
        page_num: 0-based page index. Ignored for non-PDF (single image).
        is_pdf: Whether s3_key is a PDF needing on-demand rasterization,
            or already a directly-readable image.
        is_screenshot: Whether this is a single-image upload (REQ-DP-01
            "Screenshot Stricter Gate" — a lower confidence score).

    Returns:
        PageExtraction with has_table, any raw transactions found, and the
        page's full text (for statement metadata extraction).
    """
    if is_pdf:
        image_bytes = _rasterize_pdf_page(bucket, s3_key, page_num)
        page_key = f"{s3_key.rsplit('.', 1)[0]}/page_{page_num}.jpg"
        _s3_client.put_object(Bucket=bucket, Key=page_key, Body=image_bytes)
    else:
        page_key = s3_key

    try:
        blocks = _call_textract(bucket, page_key)
    except Exception as e:
        logger.warning("TEXTRACT_TRANSIENT_FAILURE — page extraction failed after retries", page_key=page_key, error=str(e))
        return PageExtraction(page_key=page_key, has_table=False)

    has_table = any(block.get("BlockType") == "TABLE" for block in blocks)
    confidence = IMAGE_TEXTRACT_CONFIDENCE if is_screenshot else TIER_CONFIDENCE[ExtractionTier.TEXTRACT]
    raw_transactions = _blocks_to_raw_transactions(blocks, confidence) if has_table else []

    return PageExtraction(
        page_key=page_key,
        has_table=has_table,
        raw_transactions=raw_transactions,
        raw_text=_extract_full_text(blocks),
    )


def _run_tier2_concurrently(bucket: str, s3_key: str, page_nums: list[int], is_pdf: bool, is_screenshot: bool) -> list[PageExtraction]:
    """Run classify_and_extract_page across pages concurrently
    (REQ-DP-02 "Parallel Page Extraction"). One page's failure never blocks
    the others (REQ-DP-02 F. Error Handling — PARTIAL_EXTRACTION_FAILURE)."""
    results: list[PageExtraction] = []
    max_workers = min(_MAX_CONCURRENT_TEXTRACT_CALLS, len(page_nums)) or 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(classify_and_extract_page, bucket, s3_key, page_num, is_pdf, is_screenshot): page_num
            for page_num in page_nums
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                page_num = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning("PARTIAL_EXTRACTION_FAILURE — page raised unexpectedly", page_num=page_num, error=str(e))

    return results


# ---------------------------------------------------------------------------
# Metadata + orchestration
# ---------------------------------------------------------------------------


def _extract_metadata(full_text: str) -> Optional[StatementMetadata]:
    """Extract bank statement metadata (bank name, dates, totals) from a
    page's full text, whichever tier produced that text."""
    extracted: dict[str, Optional[str]] = {}
    for field, pattern in _METADATA_PATTERNS.items():
        match = pattern.search(full_text)
        if match:
            extracted[field] = match.group(1) if match.lastindex else match.group(0)

    if not all(extracted.get(f) for f in ("bank_name", "opening_date", "closing_date")):
        return None

    try:
        return StatementMetadata(
            bank_name=extracted["bank_name"] or "",
            opening_date=extracted["opening_date"] or "",
            closing_date=extracted["closing_date"] or "",
            total_purchases=extracted.get("total_purchases"),
            total_credits=extracted.get("total_credits"),
            previous_balance=extracted.get("previous_balance"),
        )
    except ValidationError as e:
        logger.warning("Statement metadata validation failed", error=str(e))
        return None


def extract_transactions(
    bucket: str,
    s3_key: str,
    is_pdf: bool,
    page_count: int,
    job_id: str,
    user_id: str,
    statement_id: str,
    account_id: str,
) -> TextractOutput:
    """Run the full PDF/Image extraction waterfall: Tier 1 (text layer, PDF
    only) first, Tier 2 (Textract) only for pages Tier 1 couldn't cover —
    this is what caps Textract spend as upload volume grows (REQ-DP-02
    "Tier Skipping at Scale").

    Args:
        bucket: S3 bucket.
        s3_key: S3 key of the original PDF or image.
        is_pdf: Whether this is a multi-page PDF or a single image.
        page_count: Number of pages (1 for a single image).
        job_id, user_id, statement_id, account_id: Job context, carried
            through to the output.

    Returns:
        TextractOutput with all raw transactions found across every tier.

    Raises:
        NoValidTransactionsError: Every page was processed but not one
            transaction was found (REQ-DP-01 F. Error Handling — NO_VALID_PAGES).
    """
    all_transactions: list[RawTransaction] = []
    metadata: Optional[StatementMetadata] = None
    pages_needing_tier2: list[int] = list(range(page_count))

    if is_pdf:
        tier1_pages = extract_from_text_layer(bucket, s3_key)
        pages_needing_tier2 = [p for p in range(page_count) if not tier1_pages.get(p)]
        for page_rows in tier1_pages.values():
            all_transactions.extend(page_rows)

    if pages_needing_tier2:
        logger.info(
            "Falling through to Tier 2 (Textract)",
            job_id=job_id,
            pages_needing_tier2=len(pages_needing_tier2),
            of_total=page_count,
        )
        tier2_results = _run_tier2_concurrently(bucket, s3_key, pages_needing_tier2, is_pdf, is_screenshot=not is_pdf)
        for page_extraction in sorted(tier2_results, key=lambda r: r.page_key):
            all_transactions.extend(page_extraction.raw_transactions)
            if metadata is None and page_extraction.raw_text:
                metadata = _extract_metadata(page_extraction.raw_text)

    if metadata is None and is_pdf:
        # Tier 1 covered the whole document — fall back to the first page's
        # own text layer for metadata, so a fully-digital PDF doesn't need
        # a wasted Textract call just to find the bank name / date range.
        import fitz  # type: ignore[import-untyped]

        response = _s3_client.get_object(Bucket=bucket, Key=s3_key)
        doc = fitz.open(stream=response["Body"].read(), filetype="pdf")
        if doc.page_count:
            metadata = _extract_metadata(doc[0].get_text())

    if not all_transactions:
        raise NoValidTransactionsError(f"No transactions found across {page_count} page(s) in any tier")

    logger.info("Extraction complete", job_id=job_id, total_transactions=len(all_transactions))

    return TextractOutput(
        job_id=job_id,
        user_id=user_id,
        statement_id=statement_id,
        account_id=account_id,
        metadata=metadata,
        raw_transactions=all_transactions,
    )
