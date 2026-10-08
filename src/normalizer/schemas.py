"""Normalizer feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel


# TXT-01: the Ledger's own vocabulary, used end to end — no translation at the Ledger boundary.
TransactionType = Literal["EXPENSE", "INCOME", "REFUND", "TRANSFER", "ADJUSTMENT"]
TransactionDirection = Literal["DEBIT", "CREDIT"]


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
    type: TransactionType
    direction: TransactionDirection
    status: str = "PENDING_APPROVAL"
    confidence: Decimal = Decimal("1.00")
    needs_review: bool = False
    # REQ-STMT-02: the Ledger's bulk-create endpoint dedupes on (statement_id, row_fingerprint)
    # — a 64-char lowercase-hex digest, required on every line. See normalizer/service.py for
    # how it's computed.
    row_fingerprint: str
