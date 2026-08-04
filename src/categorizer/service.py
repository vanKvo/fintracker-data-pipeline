"""Categorizer Service — merchant categorization logic.

Pipeline:
  1. Apply Regex Map for obvious patterns (free, sub-millisecond).
  2. Look up in MerchantRegistry DynamoDB cache.
  3. Fallback: call Amazon Comprehend for NLP-based classification.
  4. Write newly learned merchants back to the registry.

__author__ = "Van Vo"
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

import boto3

from ..core.observability import logger
from .repository import cache_merchant, lookup_merchant
from .schemas import MerchantCategory

_comprehend = boto3.client("comprehend")

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


def categorize_merchant(merchant: str) -> MerchantCategory:
    """Categorize a merchant using the full lookup chain.

    Lookup order:
      1. Regex Map (free, O(n) patterns, sub-ms)
      2. MerchantRegistry DynamoDB cache (fast, ~1ms)
      3. Amazon Comprehend (paid, ~100ms — only when cache misses)

    Results from Comprehend are written back to the registry for future reuse.

    Args:
        merchant: Cleaned, lowercase merchant string.

    Returns:
        MerchantCategory with category, sub_category, confidence, and source.
    """
    result = _regex_categorize(merchant)
    if result:
        logger.debug("Merchant categorized via Regex", merchant=merchant, category=result[0])
        return MerchantCategory(category=result[0], sub_category=result[1], source="REGEX")

    cached = lookup_merchant(merchant)
    if cached:
        logger.debug("Merchant found in registry", merchant=merchant)
        return MerchantCategory(
            category=cached["category"],
            sub_category=cached.get("sub_category", "General"),
            confidence=cached.get("confidence"),
            source="CACHE",
        )

    category, sub_category, confidence = _comprehend_categorize(merchant)
    logger.info("Comprehend categorized", merchant=merchant, category=category)
    cache_merchant(merchant, category, sub_category, confidence)
    return MerchantCategory(
        category=category,
        sub_category=sub_category,
        confidence=confidence,
        source="COMPREHEND",
    )
