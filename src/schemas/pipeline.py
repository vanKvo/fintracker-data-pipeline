"""Pydantic v2 models for Data Pipeline event contracts.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class PipelineStatus(StrEnum):
    STARTED = "STARTED"
    GATEKEEPER_PASSED = "GATEKEEPER_PASSED"
    GATEKEEPER_FAILED = "GATEKEEPER_FAILED"
    INGESTING = "INGESTING"
    NORMALIZING = "NORMALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class S3EventRecord(BaseModel):
    """Typed representation of an S3 PutObject event record."""

    bucket: str
    key: str
    user_id: str  # injected via S3 object metadata at upload time
    statement_id: str
    account_id: str


class GatekeeperOutput(BaseModel):
    """Output produced by the Vision Gatekeeper Lambda."""

    job_id: str
    s3_key: str
    user_id: str
    statement_id: str
    account_id: str
    valid_page_keys: list[str] = Field(default_factory=list)
    junk_page_keys: list[str] = Field(default_factory=list)
    has_valid_pages: bool


class TextractOutput(BaseModel):
    """Output produced by the Textract Ingestion Lambda."""

    job_id: str
    user_id: str
    statement_id: str
    account_id: str
    raw_transactions: list[RawTransaction]


class RawTransaction(BaseModel):
    """A raw row extracted from Textract before normalization."""

    raw_merchant: str
    raw_amount: str
    raw_date: str
    raw_type: Optional[str] = None


class NormalizedTransaction(BaseModel):
    """A fully categorized transaction ready for the Ledger."""

    account_id: str
    statement_id: str
    merchant: str
    amount: Decimal
    tx_date: str  # ISO date "YYYY-MM-DD"
    category: str
    sub_category: Optional[str] = None
    source: str = "STATEMENT_UPLOAD"
    type: str = "SALE"
    status: str = "PENDING_APPROVAL"


class JobTrackerItem(BaseModel):
    """DynamoDB Job Tracker record."""

    job_id: str
    user_id: str
    status: PipelineStatus
    error: Optional[str] = None
