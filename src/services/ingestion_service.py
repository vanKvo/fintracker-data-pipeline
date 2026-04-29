"""Textract Ingestion Service — orchestrator-agnostic document extraction.

Calls AWS Textract to extract raw table data from valid transaction page images.

__author__ = "Van Vo"
"""

from __future__ import annotations

import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.observability import logger
from ..schemas.pipeline import RawTransaction, TextractOutput

_textract = boto3.client("textract")


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

    # Skip header row (row 1) and parse from row 2 onward
    for row in range(2, max_row + 1):
        date = cells.get((row, 1), "").strip()
        merchant = cells.get((row, 2), "").strip()
        amount = cells.get((row, 3), "").strip()

        if merchant and amount:
            transactions.append(
                RawTransaction(
                    raw_merchant=merchant,
                    raw_amount=amount,
                    raw_date=date,
                )
            )

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

    This function is orchestrator-agnostic — callable from Step Function task
    or local test runner.

    Args:
        bucket: S3 bucket.
        page_keys: List of valid page S3 keys from Gatekeeper.
        job_id: Step Function execution ID.
        user_id: Cognito user sub.
        statement_id: Statement metadata UUID.
        account_id: Account UUID.

    Returns:
        TextractOutput containing all raw extracted transactions.
    """
    all_transactions: list[RawTransaction] = []

    for key in page_keys:
        logger.info("Extracting page", job_id=job_id, key=key)
        blocks = _call_textract(bucket, key)
        page_txs = _blocks_to_raw_transactions(blocks)
        all_transactions.extend(page_txs)
        logger.info("Page extracted", key=key, count=len(page_txs))

    logger.info("Textract complete", job_id=job_id, total=len(all_transactions))

    return TextractOutput(
        job_id=job_id,
        user_id=user_id,
        statement_id=statement_id,
        account_id=account_id,
        raw_transactions=all_transactions,
    )
