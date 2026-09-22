"""Data Dispatcher Service — sends processed transactions to the Ledger API.

REQ-STMT-02: the Ledger's internal endpoint is one bulk call per statement
(POST /api/v1/ledger/transactions/internal/bulk, body {statementId,
transactions}), not one call per transaction — the endpoint itself batches
and reports per-row outcomes (insertedCount/skippedDuplicateCount/failedRows)
in a single response. This satisfies REQ-DP-02 "Parallel/Batched Ledger
Push" via the batch option its own spec called out, rather than concurrent
individual calls: a batch call is strictly less HTTP overhead for the same
data, and it is the shape the Ledger team actually built
(InternalTransactionController#bulkCreate).

__author__ = "Van Vo"
"""

from __future__ import annotations

import os

import requests as _requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..core.config import get_param
from ..core.observability import logger
from .schemas import LedgerPushResult

_LEDGER_API_URL = os.environ.get("LEDGER_API_URL", "")

# REQ-DP-06: PURCHASE/CREDIT is the Ledger's own vocabulary (BulkCreateTransactionsRequest.
# TransactionLine.type); SALE/RETURN is this pipeline's internal one (NormalizedTransaction) —
# translated only at this wire boundary, not renamed pipeline-wide.
_TYPE_TO_LEDGER = {"SALE": "PURCHASE", "RETURN": "CREDIT"}


# Secret, not config — SSM SecureString via core.config (CLAUDE.md Python
# Standards: "Secrets via AWS SSM Parameter Store, never hardcoded"), not a
# plain Lambda environment variable, which is visible to anyone with
# lambda:GetFunctionConfiguration and isn't rotated independently of a deploy.
def _internal_api_key() -> str:
    return get_param("INTERNAL_API_KEY")


def _is_throttled(exception: BaseException) -> bool:
    """True for a 429/503 the Ledger returned, or a transient connection
    error — anything worth retrying with backoff rather than failing the
    push outright (REQ-DP-02 F. Error Handling — LEDGER_THROTTLED)."""
    response = getattr(exception, "response", None)
    if response is not None:
        return response.status_code in (429, 503)
    return isinstance(exception, (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout))


def _to_ledger_line(tx: dict) -> dict:
    """Maps a serialized NormalizedTransaction dict (snake_case, this pipeline's own field
    names) to a BulkCreateTransactionsRequest.TransactionLine (camelCase, the Ledger's wire
    contract)."""
    return {
        "date": tx["tx_date"],
        "merchant": tx["merchant"],
        "amount": str(tx["amount"]),
        "category": tx["category"],
        "subCategory": tx.get("sub_category"),
        "type": _TYPE_TO_LEDGER.get(tx["type"], tx["type"]),
        "rowFingerprint": tx["row_fingerprint"],
    }


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_throttled),
    reraise=True,
)
def _push_bulk(body: dict, headers: dict) -> dict:
    resp = _requests.post(
        f"{_LEDGER_API_URL}/api/v1/ledger/transactions/internal/bulk",
        json=body,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def push_transactions_to_ledger(
    job_id: str,
    user_id: str,
    statement_id: str,
    transactions: list[dict],
) -> LedgerPushResult:
    """Push one statement's normalized transactions to the Ledger in a single bulk call.

    All transactions are created with status PENDING_APPROVAL via
    service-to-service auth (internal API key), scoped to the owning
    tenant via the X-Internal-User-Id header — the Ledger's
    UserContextFilter enforces scoping on this header, not on any
    user_id/account_id field embedded in the transaction body
    (REQ-DP-06 "Tenant Scoping on the Ledger Push").

    Args:
        job_id: Step Function execution ID for logging.
        user_id: Verified owner of the statement this job is processing.
        statement_id: The statement every one of these transactions belongs to — one bulk
            call covers exactly one statement (REQ-STMT-02).
        transactions: List of serialized NormalizedTransaction dicts.

    Returns:
        LedgerPushResult with success/total counts. success_count counts both newly inserted
        rows and rows already present from a prior attempt (skipped as duplicates) — both are
        a "this row is accounted for" outcome, not a failure. A total request failure (e.g.
        the Ledger unreachable, or throttled past retry exhaustion) counts every row as failed
        rather than silently dropping the statement.
    """
    if not transactions:
        return LedgerPushResult(job_id=job_id, success_count=0, total_count=0, all_succeeded=True)

    headers = {
        "x-internal-api-key": _internal_api_key(),
        "X-Internal-User-Id": user_id,
        "Content-Type": "application/json",
    }
    body = {
        "statementId": statement_id,
        "transactions": [_to_ledger_line(tx) for tx in transactions],
    }

    try:
        result = _push_bulk(body, headers)
    except Exception as e:
        # Never log the request body — merchant/amount/account details are sensitive
        # statement content (REQ-DP-08 PII/log hygiene).
        logger.error("Failed to push statement's transactions to ledger", error=str(e), job_id=job_id)
        return LedgerPushResult(
            job_id=job_id, success_count=0, total_count=len(transactions), all_succeeded=False
        )

    inserted = result.get("insertedCount", 0)
    skipped_duplicates = result.get("skippedDuplicateCount", 0)
    failed_rows = result.get("failedRows", [])
    success_count = inserted + skipped_duplicates

    logger.info(
        "Ledger push complete",
        job_id=job_id,
        inserted=inserted,
        skipped_duplicates=skipped_duplicates,
        failed=len(failed_rows),
        total=len(transactions),
    )
    return LedgerPushResult(
        job_id=job_id,
        success_count=success_count,
        total_count=len(transactions),
        all_succeeded=not failed_rows,
    )
