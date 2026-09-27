"""Normalizer Service — standardizes raw transactions to a normalized schema.

__author__ = "Van Vo"
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from ..categorizer.service import categorize_merchant
from ..core.observability import logger
from ..extractor.schemas import REVIEW_CONFIDENCE_THRESHOLD, TextractOutput
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


def _row_fingerprint(statement_id: str, row_index: int, tx_date: str, merchant: str, amount: Decimal, tx_type: str) -> str:
    """REQ-STMT-02: the Ledger dedupes a retried push on (statement_id, row_fingerprint), so this
    must be the same value on every retry of the same row and distinct across genuinely different
    rows. date/merchant/amount/type alone can collide for two legitimately identical-looking
    transactions on the same statement (e.g. two $5 coffees same day) — row_index (the row's
    position in this statement's extraction, stable across a retry of the same deterministic
    parse) disambiguates those.
    """
    raw = f"{statement_id}|{row_index}|{tx_date}|{merchant}|{amount}|{tx_type}"
    return hashlib.sha256(raw.encode()).hexdigest()


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

    for row_index, raw in enumerate(textract_output.raw_transactions):
        merchant = _clean_merchant(raw.raw_merchant)
        amount = _parse_amount(raw.raw_amount)

        if amount is None:
            # REQ-DP-08: never log raw transaction content — raw.raw_amount is the statement's
            # actual dollar value, not just an opaque parse failure. job_id/extraction_tier are
            # enough to find and debug the offending row without it.
            logger.warning(
                "Unparsable amount, skipping",
                job_id=textract_output.job_id,
                extraction_tier=str(raw.extraction_tier),
            )
            continue

        cat_result = categorize_merchant(merchant, bank_category=raw.raw_category)
        tx_type = "RETURN" if amount < 0 else "SALE"

        # REQ-DP-01 "Manual Review Routing": a row below the confidence
        # threshold is flagged for manual review rather than posted at
        # face value. No new Ledger status is introduced (B. Constraints)
        # — needs_review rides alongside the existing PENDING_APPROVAL
        # status as a UI prioritization signal.
        needs_review = raw.confidence < REVIEW_CONFIDENCE_THRESHOLD

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
                confidence=raw.confidence,
                needs_review=needs_review,
                row_fingerprint=_row_fingerprint(
                    textract_output.statement_id, row_index, raw.raw_date, merchant, abs(amount), tx_type
                ),
            )
        )

    logger.info(
        "Normalization complete",
        job_id=textract_output.job_id,
        count=len(normalized),
    )
    return normalized
