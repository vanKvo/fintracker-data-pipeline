"""Normalizer Service — standardizes raw transactions to a normalized schema.

__author__ = "Van Vo"
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..categorizer.service import categorize_merchant
from ..core.observability import logger
from ..extractor.schemas import TextractOutput
from .schemas import NormalizedTransaction


def _clean_merchant(raw: str) -> str:
    """Normalize a raw merchant string to lowercase, stripped form.

    Removes trailing reference numbers (e.g., "STARBUCKS #12345" -> "starbucks").

    Args:
        raw: The raw merchant string from Textract.

    Returns:
        A cleaned, lowercase merchant string.
    """
    cleaned = re.sub(r"[#\*]\d+", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _parse_amount(raw_amount: str) -> Optional[Decimal]:
    """Parse a raw currency string to Decimal.

    Supports formats like: "$1,234.56", "1234.56", "(123.45)" for credits.

    Args:
        raw_amount: Raw amount string from Textract.

    Returns:
        Decimal value, negative for credits/returns, or None if unparsable.
    """
    is_credit = raw_amount.strip().startswith("(") or raw_amount.strip().startswith("-")
    cleaned = re.sub(r"[^\d.]", "", raw_amount)
    try:
        amount = Decimal(cleaned)
        return -amount if is_credit else amount
    except InvalidOperation:
        return None


def normalize_and_categorize(
    textract_output: TextractOutput,
) -> list[NormalizedTransaction]:
    """Run the full normalize + categorize pipeline for all raw transactions.

    Args:
        textract_output: Output from the extraction step.

    Returns:
        List of NormalizedTransaction objects ready for the Ledger.
    """
    normalized: list[NormalizedTransaction] = []

    for raw in textract_output.raw_transactions:
        merchant = _clean_merchant(raw.raw_merchant)
        amount = _parse_amount(raw.raw_amount)

        if amount is None:
            logger.warning("Unparsable amount, skipping", raw_amount=raw.raw_amount)
            continue

        cat_result = categorize_merchant(merchant)
        tx_type = "RETURN" if amount < 0 else "SALE"

        normalized.append(
            NormalizedTransaction(
                account_id=textract_output.account_id,
                statement_id=textract_output.statement_id,
                merchant=merchant,
                amount=abs(amount),
                tx_date=raw.raw_date,
                category=cat_result.category,
                sub_category=cat_result.sub_category,
                type=tx_type,
            )
        )

    logger.info(
        "Normalization complete",
        job_id=textract_output.job_id,
        count=len(normalized),
    )
    return normalized
