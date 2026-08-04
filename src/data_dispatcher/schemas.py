"""Data Dispatcher feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from pydantic import BaseModel


class LedgerPushResult(BaseModel):
    """Result of pushing transactions to the Ledger API."""

    job_id: str
    success_count: int
    total_count: int
    all_succeeded: bool
