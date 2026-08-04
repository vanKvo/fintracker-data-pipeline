"""Lambda handler for the Textract Extraction Step Function task.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

from ..core.observability import logger, tracer
from ..gatekeeper.schemas import GatekeeperOutput
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .service import extract_transactions


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def ingestion_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — AWS Textract document ingestion.

    Args:
        event: GatekeeperOutput dict from previous step.
        context: Lambda context.

    Returns:
        Serialized TextractOutput as dict.
    """
    gk_output = GatekeeperOutput(**event)
    output = extract_transactions(
        bucket=event["bucket"] if "bucket" in event else gk_output.s3_key.split("/")[0],
        page_keys=gk_output.valid_page_keys,
        job_id=gk_output.job_id,
        user_id=gk_output.user_id,
        statement_id=gk_output.statement_id,
        account_id=gk_output.account_id,
    )
    update_job_status(gk_output.job_id, gk_output.user_id, PipelineStatus.INGESTING)
    return output.model_dump()
