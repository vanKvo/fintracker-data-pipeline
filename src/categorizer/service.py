"""Categorizer Service — merchant categorization logic.

REQ-DP-09 lookup order (no paid classifier, no shared cache):
  1. Bank-provided category, translated to a FinTracker label (CSV only).
  2. Regex Map for well-known merchants.
  3. "Uncategorized" — left for the user to categorize.

Per-user merchant rules are applied later, in the Ledger, and take priority over both.

__author__ = "Van Vo"
"""

from __future__ import annotations

import re
from typing import Optional

from ..core.observability import logger
from .bank_categories import translate_bank_category
from .schemas import MerchantCategory

UNCATEGORIZED = "Uncategorized"

# Targets are the Ledger's TransactionCategory labels.
_REGEX_MAP: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^uber\s*eats", re.I), "Dining", "Delivery"),
    (re.compile(r"^uber", re.I), "Transportation", "Rideshare"),
    (re.compile(r"^lyft", re.I), "Transportation", "Rideshare"),
    (re.compile(r"^amzn\s*mktp|^amazon", re.I), "Shopping", "General Retail"),
    (re.compile(r"^apple\s*\.com|^itunes", re.I), "Subscriptions", "Digital Services"),
    (re.compile(r"^netflix", re.I), "Subscriptions", "Streaming"),
    (re.compile(r"^spotify", re.I), "Subscriptions", "Streaming"),
    (re.compile(r"^hulu|^disney\s*plus", re.I), "Subscriptions", "Streaming"),
    (re.compile(r"^starbucks", re.I), "Dining", "Coffee Shops"),
    (re.compile(r"^mcdonald", re.I), "Dining", "Fast Food"),
    (re.compile(r"^walmart", re.I), "Groceries", "General"),
    (re.compile(r"^costco", re.I), "Groceries", "General"),
    (re.compile(r"^whole\s*fds|^wholefds", re.I), "Groceries", "General"),
    (re.compile(r"^door\s*dash|^doordash", re.I), "Dining", "Delivery"),
    (re.compile(r"^shell|^exxon|^chevron|^bp\s", re.I), "Transportation", "Gas"),
    (re.compile(r"^staples|^office\s*depot", re.I), "Shopping", "Office Supplies"),
    (re.compile(r"^cvs|^walgreens", re.I), "Healthcare", "Pharmacy"),
    (re.compile(r"^verizon|^at&t|^t-mobile|^xfinity|^spectrum", re.I), "Utilities", "Communication"),
    (re.compile(r"^geico|^progressive|^state\s*farm|^allstate", re.I), "Insurance", "Auto & Home"),
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


def categorize_merchant(merchant: str, bank_category: Optional[str] = None) -> MerchantCategory:
    """Categorize a merchant: bank category -> Regex Map -> Uncategorized.

    Args:
        merchant: Cleaned, lowercase merchant string.
        bank_category: The bank's own category for this row, if its export has one.

    Returns:
        MerchantCategory with source "BANK", "REGEX", or "NONE".
    """
    # REQ-DP-08: never log the merchant string itself — it's raw statement content.
    translated = translate_bank_category(bank_category)
    if translated:
        logger.debug("Merchant categorized via bank category", category=translated)
        return MerchantCategory(category=translated, sub_category="General", source="BANK")

    result = _regex_categorize(merchant)
    if result:
        logger.debug("Merchant categorized via Regex", category=result[0])
        return MerchantCategory(category=result[0], sub_category=result[1], source="REGEX")

    return MerchantCategory(category=UNCATEGORIZED, sub_category="General", source="NONE")
