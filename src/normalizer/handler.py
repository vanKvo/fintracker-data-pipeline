"""Lambda handler for the Normalizer + Categorizer Step Function task.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

from ..core.observability import logger, tracer
from ..extractor.schemas import TextractOutput
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .service import normalize_and_categorize


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def normalizer_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — Normalize + Categorize raw transactions.

    Args:
        event: TextractOutput dict from previous step.
        context: Lambda context.

    Returns:
        Dict with list of serialized NormalizedTransaction objects.
    """
    tx_output = TextractOutput(**event)
    normalized = normalize_and_categorize(tx_output)
    update_job_status(tx_output.job_id, tx_output.user_id, PipelineStatus.NORMALIZING)
    return {
        "job_id": tx_output.job_id,
        "user_id": tx_output.user_id,
        "statement_id": tx_output.statement_id,
        "account_id": tx_output.account_id,
        "transactions": [t.model_dump(mode="json") for t in normalized],
    }
