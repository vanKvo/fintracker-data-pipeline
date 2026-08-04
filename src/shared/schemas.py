"""Shared Pydantic v2 models used across multiple features.

__author__ = "Van Vo"
"""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel


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
    user_id: str
    statement_id: str
    account_id: str


class JobTrackerItem(BaseModel):
    """DynamoDB Job Tracker record."""

    job_id: str
    user_id: str
    status: PipelineStatus
    error: Optional[str] = None
