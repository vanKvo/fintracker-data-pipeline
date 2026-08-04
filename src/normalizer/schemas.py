"""Normalizer feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class NormalizedTransaction(BaseModel):
    """A fully categorized transaction ready for the Ledger."""

    account_id: str
    statement_id: str
    merchant: str
    amount: Decimal
    tx_date: str
    category: str
    sub_category: Optional[str] = None
    source: str = "STATEMENT_UPLOAD"
    type: str = "SALE"
    status: str = "PENDING_APPROVAL"
