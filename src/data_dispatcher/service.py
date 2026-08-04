"""Data Dispatcher Service — sends processed transactions to the Ledger API.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os

import requests as _requests

from ..core.observability import logger
from .schemas import LedgerPushResult

_LEDGER_API_URL = os.environ.get("LEDGER_API_URL", "")
_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")


def push_transactions_to_ledger(
    job_id: str,
    transactions: list[dict],
) -> LedgerPushResult:
    """Push normalized transactions to the Ledger Service REST API.

    All transactions are created with status PENDING_APPROVAL via
    service-to-service auth (internal API key).

    Args:
        job_id: Step Function execution ID for logging.
        transactions: List of serialized NormalizedTransaction dicts.

    Returns:
        LedgerPushResult with success/total counts.
    """
    headers = {"x-internal-api-key": _INTERNAL_API_KEY, "Content-Type": "application/json"}

    success_count = 0
    for tx in transactions:
        try:
            resp = _requests.post(
                f"{_LEDGER_API_URL}/v1/transactions/internal",
                json=tx,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            success_count += 1
        except Exception as e:
            logger.error("Failed to push transaction to ledger", error=str(e), tx=tx)

    logger.info("Ledger push complete", job_id=job_id, pushed=success_count, total=len(transactions))
    return LedgerPushResult(
        job_id=job_id,
        success_count=success_count,
        total_count=len(transactions),
        all_succeeded=success_count == len(transactions),
    )
