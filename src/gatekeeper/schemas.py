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


class GatekeeperOutput(BaseModel):
    """Output produced by the Vision Gatekeeper Lambda."""

    job_id: str
    s3_key: str
    user_id: str
    statement_id: str
    account_id: str
    statement_format: StatementFormat
    valid_page_keys: list[str] = Field(default_factory=list)
    junk_page_keys: list[str] = Field(default_factory=list)
    csv_s3_key: str | None = None
    has_valid_pages: bool
