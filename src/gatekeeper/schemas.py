"""Gatekeeper feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class StatementFormat(StrEnum):
    PDF = "PDF"
    CSV = "CSV"
    IMAGE = "IMAGE"


class ColumnMappingProposal(BaseModel):
    """The Gatekeeper's proposed CSV column mapping, for the user-facing
    confirmation dialog (REQ-DP-01 "Mapping Transparency").

    `mapped` and `unmapped_columns` together must account for every column
    in the CSV — the dialog needs the full picture, not just the gaps.
    """

    bank_id: str
    csv_s3_key: str
    mapped: dict[str, str] = Field(default_factory=dict, description="canonical field -> source column name")
    unmapped_columns: list[str] = Field(default_factory=list)
    is_known_bank: bool = True


class GatekeeperOutput(BaseModel):
    """Output produced by the Gatekeeper Lambda.

    For CSV, mapping_proposal is always populated and requires_mapping_confirmation
    is True — the Extractor never runs on a CSV until a human confirms the
    mapping (REQ-DP-01 "User Mapping Confirmation Gate").

    For PDF/Image, per-page tier classification and validity are no longer
    decided here — YOLO gating was removed; the Extractor's Tier 1/Tier 2
    waterfall (classify_and_extract_page) now makes that call per page, since
    Tier 2 (Textract AnalyzeDocument+TABLES) already classifies a page as it
    extracts it, and Tier 1 needs the original PDF bytes rather than a
    rasterized image. page_count is informational only, from a cheap PDF
    metadata read with no rasterization.
    """

    job_id: str
    s3_key: str
    user_id: str
    statement_id: str
    account_id: str
    statement_format: StatementFormat
    page_count: int = 1
    csv_s3_key: str | None = None
    mapping_proposal: ColumnMappingProposal | None = None
    requires_mapping_confirmation: bool = False
