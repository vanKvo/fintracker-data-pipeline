"""Textract Extraction Service — orchestrator-agnostic document extraction.

Calls AWS Textract to extract raw table data from valid transaction page images.
Also extracts and validates bank statement metadata (bank name, dates, totals).

__author__ = "Van Vo"
"""

from __future__ import annotations

import re
from typing import Optional

import boto3
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.observability import logger
from .schemas import RawTransaction, StatementMetadata, TextractOutput

_textract = boto3.client("textract")

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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _call_textract(bucket: str, key: str) -> list[dict]:
    """Call Textract AnalyzeDocument with retry/exponential backoff.

    Args:
        bucket: S3 bucket containing the page image.
        key: S3 key of the page image.

    Returns:
        List of raw Textract Block objects.
    """
    response = _textract.analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES"],
    )
    return response.get("Blocks", [])


def _extract_full_text(blocks: list[dict]) -> str:
    """Concatenate all LINE-type Textract blocks into a single text blob.

    Args:
        blocks: Raw Textract Blocks.

    Returns:
        Full-page text string.
    """
    lines = []
    for block in blocks:
        if block.get("BlockType") == "LINE":
            lines.append(block.get("Text", ""))
    return "\n".join(lines)


def _extract_metadata(full_text: str) -> Optional[StatementMetadata]:
    """Extract bank statement metadata from the full page text.

    Searches for bank name, opening/closing dates, total purchases/credits,
    and previous balance using regex patterns.

    Args:
        full_text: Concatenated text from all Textract LINE blocks.

    Returns:
        StatementMetadata if required fields are found, else None.
    """
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


def _blocks_to_raw_transactions(blocks: list[dict]) -> list[RawTransaction]:
    """Parse Textract TABLE blocks into RawTransaction objects.

    Table structure expected:
      Column 0: Date        | Column 1: Merchant     | Column 2: Amount

    Args:
        blocks: Raw Textract Blocks from AnalyzeDocument.

    Returns:
        List of parsed raw transactions.
    """
    cells: dict[tuple[int, int], str] = {}
    for block in blocks:
        if block.get("BlockType") == "CELL":
            row = block.get("RowIndex", 0)
            col = block.get("ColumnIndex", 0)
            text = " ".join(
                rel.get("Text", "")
                for rel in block.get("Relationships", [])
                if rel.get("Type") == "CHILD"
            ).strip()
            cells[(row, col)] = text

    if not cells:
        return []

    max_row = max(r for r, _ in cells.keys())
    transactions: list[RawTransaction] = []

    for row in range(2, max_row + 1):
        date = cells.get((row, 1), "").strip()
        merchant = cells.get((row, 2), "").strip()
        amount = cells.get((row, 3), "").strip()

        if merchant and amount:
            try:
                transactions.append(
                    RawTransaction(
                        raw_merchant=merchant,
                        raw_amount=amount,
                        raw_date=date,
                    )
                )
            except ValidationError as e:
                logger.warning("Invalid transaction row skipped", row=row, error=str(e))

    return transactions


def extract_transactions(
    bucket: str,
    page_keys: list[str],
    job_id: str,
    user_id: str,
    statement_id: str,
    account_id: str,
) -> TextractOutput:
    """Run Textract over all valid page images and aggregate raw transactions.

    Also extracts and validates bank statement metadata from the first page.

    Args:
        bucket: S3 bucket.
        page_keys: List of valid page S3 keys from Gatekeeper.
        job_id: Step Function execution ID.
        user_id: Cognito user sub.
        statement_id: Statement metadata UUID.
        account_id: Account UUID.

    Returns:
        TextractOutput containing all raw extracted transactions and metadata.
    """
    all_transactions: list[RawTransaction] = []
    metadata: Optional[StatementMetadata] = None

    for i, key in enumerate(page_keys):
        logger.info("Extracting page", job_id=job_id, key=key)
        blocks = _call_textract(bucket, key)

        if i == 0 and metadata is None:
            full_text = _extract_full_text(blocks)
            metadata = _extract_metadata(full_text)
            if metadata:
                logger.info(
                    "Statement metadata extracted",
                    job_id=job_id,
                    bank_name=metadata.bank_name,
                )
            else:
                logger.warning("Statement metadata not found on first page", job_id=job_id)

        page_txs = _blocks_to_raw_transactions(blocks)
        all_transactions.extend(page_txs)
        logger.info("Page extracted", key=key, count=len(page_txs))

    logger.info("Textract complete", job_id=job_id, total=len(all_transactions))

    return TextractOutput(
        job_id=job_id,
        user_id=user_id,
        statement_id=statement_id,
        account_id=account_id,
        metadata=metadata,
        raw_transactions=all_transactions,
    )
