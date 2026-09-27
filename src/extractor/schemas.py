"""Extractor feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, model_validator

from ..shared.schemas import ExtractionTier

# REQ-DP-01 "Confidence Scoring": CSV (user-confirmed) is fully trusted;
# TEXT_LAYER is deterministic (no OCR uncertainty); TEXTRACT is OCR-derived
# and probabilistic. IMAGE_TEXTRACT (screenshots) gets a stricter score
# than a PDF page run through the same Tier 2 path, since a single-page
# photo has no multi-page context and is far more likely to be blurry,
# cropped, or mis-rotated (REQ-DP-01 "Screenshot Stricter Gate").
TIER_CONFIDENCE: dict[str, Decimal] = {
    ExtractionTier.CSV: Decimal("1.00"),
    ExtractionTier.TEXT_LAYER: Decimal("0.95"),
    ExtractionTier.TEXTRACT: Decimal("0.75"),
}
IMAGE_TEXTRACT_CONFIDENCE = Decimal("0.55")

# A row below this confidence is routed to manual review rather than
# accepted at face value (REQ-DP-01 "Manual Review Routing").
REVIEW_CONFIDENCE_THRESHOLD = Decimal("0.70")


class RawTransaction(BaseModel):
    """A raw row extracted before normalization."""

    raw_merchant: str
    raw_amount: str
    raw_date: str
    raw_type: Optional[str] = None
    # REQ-DP-09: the bank's own category, when its CSV export has one.
    raw_category: Optional[str] = None
    extraction_tier: ExtractionTier = ExtractionTier.TEXTRACT
    confidence: Decimal = Decimal("0.75")

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


class PageExtraction(BaseModel):
    """Result of running Tier 2 (Textract) on a single page — the same
    AnalyzeDocument call both classifies the page (has_table) and, if
    classified positive, extracts it in one round trip."""

    page_key: str
    has_table: bool
    raw_transactions: list[RawTransaction] = []
    raw_text: str = ""


class TextractOutput(BaseModel):
    """Output produced by the Extractor Lambda (Textract for PDF/Image,
    direct parse for CSV)."""

    job_id: str
    user_id: str
    statement_id: str
    account_id: str
    metadata: Optional[StatementMetadata] = None
    raw_transactions: list[RawTransaction]
