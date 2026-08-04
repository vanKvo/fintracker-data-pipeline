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
    transactions: list[dict] = event.get("transactions", [])

    result = push_transactions_to_ledger(job_id, transactions)
    update_job_status(job_id, user_id, PipelineStatus.COMPLETED)

    return result.model_dump()
