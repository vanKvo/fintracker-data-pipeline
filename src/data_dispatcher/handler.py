"""Lambda handler for the Ledger Push Step Function task.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

from ..core.observability import logger, tracer
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .service import push_transactions_to_ledger


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def ledger_push_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — Push normalized transactions to Ledger Service API.

    Args:
        event: Normalizer output dict.
        context: Lambda context.

    Returns:
        Status summary dict.
    """
    job_id: str = event["job_id"]
    user_id: str = event["user_id"]
    statement_id: str = event["statement_id"]
    transactions: list[dict] = event.get("transactions", [])

    result = push_transactions_to_ledger(job_id, user_id, statement_id, transactions)

    if result.all_succeeded:
        status = PipelineStatus.COMPLETED
        error = None
    elif result.success_count > 0:
        status = PipelineStatus.PARTIALLY_COMPLETED
        error = f"{result.total_count - result.success_count} of {result.total_count} transactions failed to push"
    else:
        status = PipelineStatus.FAILED
        error = "All transactions failed to push to the Ledger" if result.total_count else None

    update_job_status(job_id, user_id, status, error=error)

    return result.model_dump()
