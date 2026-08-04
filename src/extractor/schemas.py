"""Extractor feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, model_validator


class RawTransaction(BaseModel):
    """A raw row extracted from Textract before normalization."""

    raw_merchant: str
    raw_amount: str
    raw_date: str
    raw_type: Optional[str] = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "RawTransaction":
        if not self.raw_merchant.strip():
            raise ValueError("raw_merchant must not be empty")
        if not self.raw_amount.strip():
            raise ValueError("raw_amount must not be empty")
        return self


class StatementMetadata(BaseModel):
    """Metadata extracted from the bank statement header/footer.

    Includes bank name, date range, and summary totals used to validate
    that the extracted transactions are complete and consistent.
    """

    bank_name: str
    opening_date: str
    closing_date: str
    total_purchases: Optional[str] = None
    total_credits: Optional[str] = None
    previous_balance: Optional[str] = None

    @model_validator(mode="after")
    def validate_metadata(self) -> "StatementMetadata":
        if not self.bank_name.strip():
            raise ValueError("bank_name must not be empty")
        if not self.opening_date.strip():
            raise ValueError("opening_date must not be empty")
        if not self.closing_date.strip():
            raise ValueError("closing_date must not be empty")
        return self


class TextractOutput(BaseModel):
    """Output produced by the Textract Ingestion Lambda."""

    job_id: str
    user_id: str
    statement_id: str
    account_id: str
    metadata: Optional[StatementMetadata] = None
    raw_transactions: list[RawTransaction]
