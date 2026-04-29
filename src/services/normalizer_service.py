"""Normalizer & Categorizer Service — orchestrator-agnostic.

Pipeline:
  1. Clean the raw merchant string.
  2. Apply Regex Map for obvious patterns (free, sub-millisecond).
  3. Look up in MerchantRegistry DynamoDB cache.
  4. Fallback: call Amazon Comprehend for NLP-based classification.
  5. Write newly learned merchants back to the registry.

__author__ = "Van Vo"
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import boto3

from ..core.observability import logger
from ..crud.pipeline_repository import cache_merchant, lookup_merchant
from ..schemas.pipeline import NormalizedTransaction, RawTransaction, TextractOutput

# ─── Global Comprehend client ─────────────────────────────────────────────────
_comprehend = boto3.client("comprehend")

# ─── Regex Map — checked FIRST before DynamoDB ────────────────────────────────
# Key: compiled regex pattern. Value: (category, sub_category)
_REGEX_MAP: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^uber\s*eats", re.I), "Food & Drink", "Delivery"),
    (re.compile(r"^uber", re.I), "Transportation", "Rideshare"),
    (re.compile(r"^lyft", re.I), "Transportation", "Rideshare"),
    (re.compile(r"^amzn\s*mktp|^amazon", re.I), "Shopping", "General Retail"),
    (re.compile(r"^apple\s*\.com|^itunes", re.I), "Entertainment", "Digital Services"),
    (re.compile(r"^netflix", re.I), "Entertainment", "Subscriptions"),
    (re.compile(r"^spotify", re.I), "Entertainment", "Subscriptions"),
    (re.compile(r"^hulu|^disney\s*plus", re.I), "Entertainment", "Subscriptions"),
    (re.compile(r"^starbucks", re.I), "Food & Drink", "Coffee Shops"),
    (re.compile(r"^mcdonald", re.I), "Food & Drink", "Fast Food"),
    (re.compile(r"^walmart", re.I), "Shopping", "Groceries"),
    (re.compile(r"^costco", re.I), "Shopping", "Groceries"),
    (re.compile(r"^whole\s*fds|^wholefds", re.I), "Shopping", "Groceries"),
    (re.compile(r"^door\s*dash|^doordash", re.I), "Food & Drink", "Delivery"),
    (re.compile(r"^shell|^exxon|^chevron|^bp\s", re.I), "Transportation", "Gas"),
    (re.compile(r"^staples|^office\s*depot", re.I), "Shopping", "Office Supplies"),
    (re.compile(r"^cvs|^walgreens", re.I), "Health", "Pharmacy"),
    (re.compile(r"^verizon|^at&t|^t-mobile|^xfinity|^spectrum", re.I), "Utilities", "Communication"),
    (re.compile(r"^geico|^progressive|^state\s*farm|^allstate", re.I), "Services", "Insurance"),
]


def _clean_merchant(raw: str) -> str:
    """Normalize a raw merchant string to lowercase, stripped form.

    Removes trailing reference numbers (e.g., "STARBUCKS #12345" -> "starbucks").

    Args:
        raw: The raw merchant string from Textract.

    Returns:
        A cleaned, lowercase merchant string.
    """
    cleaned = re.sub(r"[#\*]\d+", "", raw)  # strip reference numbers
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


def _regex_categorize(merchant: str) -> Optional[tuple[str, str]]:
    """Apply the static Regex Map to classify obvious merchant patterns instantly.

    Args:
        merchant: Cleaned merchant string.

    Returns:
        (category, sub_category) tuple or None if no pattern matches.
    """
    for pattern, category, sub_category in _REGEX_MAP:
        if pattern.match(merchant):
            return category, sub_category
    return None


def _comprehend_categorize(merchant: str) -> tuple[str, str, Decimal]:
    """Use Amazon Comprehend to classify an unknown merchant.

    Falls back to "Uncategorized" if classification confidence is too low.

    Args:
        merchant: Cleaned merchant string.

    Returns:
        (category, sub_category, confidence_score) tuple.
    """
    try:
        response = _comprehend.classify_document(
            Text=f"Purchase at: {merchant}",
            EndpointArn=f"arn:aws:comprehend:us-east-1:*:document-classifier-endpoint/fintracker-merchant-classifier",
        )
        classes = response.get("Classes", [])
        if classes:
            top = classes[0]
            return top["Name"], "General", Decimal(str(top["Score"]))
    except Exception as e:
        logger.warning("Comprehend categorization failed", error=str(e))

    return "Uncategorized", "General", Decimal("0.0")


def normalize_and_categorize(
    textract_output: TextractOutput,
) -> list[NormalizedTransaction]:
    """Run the full normalize → categorize pipeline for all raw transactions.

    Lookup order:
      1. Regex Map (free, O(n) patterns, sub-ms)
      2. MerchantRegistry DynamoDB cache (fast, ~1ms)
      3. Amazon Comprehend (paid, ~100ms — only when cache misses)

    Results from Comprehend are written back to the registry for future reuse.

    This function is orchestrator-agnostic.

    Args:
        textract_output: Output from the ingestion step.

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

        # Step 1: Regex Map
        result = _regex_categorize(merchant)
        category, sub_category, confidence = "Uncategorized", "General", Decimal("1.0")

        if result:
            category, sub_category = result
            logger.debug("Merchant categorized via Regex", merchant=merchant, category=category)
        else:
            # Step 2: DynamoDB Registry
            cached = lookup_merchant(merchant)
            if cached:
                category = cached["category"]
                sub_category = cached.get("sub_category", "General")
                logger.debug("Merchant found in registry", merchant=merchant)
            else:
                # Step 3: Comprehend fallback
                category, sub_category, confidence = _comprehend_categorize(merchant)
                logger.info("Comprehend categorized", merchant=merchant, category=category)
                # Write back to registry for future reuse
                cache_merchant(merchant, category, sub_category, confidence)

        tx_type = "RETURN" if amount < 0 else "SALE"

        normalized.append(
            NormalizedTransaction(
                account_id=textract_output.account_id,
                statement_id=textract_output.statement_id,
                merchant=merchant,
                amount=abs(amount),
                tx_date=raw.raw_date,
                category=category,
                sub_category=sub_category,
                type=tx_type,
            )
        )

    logger.info(
        "Normalization complete",
        job_id=textract_output.job_id,
        count=len(normalized),
    )
    return normalized
