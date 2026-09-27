"""Bank-provided category label -> FinTracker category label.

REQ-DP-09 "Bank Category First": some exports (Chase, Discover credit card CSVs) carry the
bank's own category. Targets are the Ledger's TransactionCategory labels, so imports resolve
there instead of collapsing to "Others". Labels with no clear equivalent are left out on purpose
and fall through to the regex list.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Optional

BANK_CATEGORY_MAP: dict[str, str] = {
    # Chase
    "shopping": "Shopping",
    "food & drink": "Dining",
    "groceries": "Groceries",
    "travel": "Travel",
    "bills & utilities": "Utilities",
    "gas": "Transportation",
    "automotive": "Transportation",
    "education": "Education",
    "health & wellness": "Healthcare",
    "personal": "Personal Care",
    "entertainment": "Entertainment",
    "home": "Housing",
    "fees & adjustments": "Fees",
    # Discover
    "merchandise": "Shopping",
    "department stores": "Shopping",
    "restaurants": "Dining",
    "supermarkets": "Groceries",
    "gasoline": "Transportation",
    "travel/ entertainment": "Travel",
    "home improvement": "Housing",
    "medical services": "Healthcare",
}


def translate_bank_category(bank_category: Optional[str]) -> Optional[str]:
    """Case/whitespace-insensitive lookup; None when blank or unknown."""
    if not bank_category:
        return None
    return BANK_CATEGORY_MAP.get(bank_category.strip().lower())
