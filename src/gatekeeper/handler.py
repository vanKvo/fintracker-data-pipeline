"""Lambda handler for the Vision Gatekeeper Step Function task.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

from ..core.observability import logger, tracer
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .service import run_gatekeeper


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def gatekeeper_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — Vision Gatekeeper.

    Args:
        event: Step Function input with bucket/key/job_id/user_id.
        context: Lambda context.

    Returns:
        Serialized GatekeeperOutput as dict.
    """
    raw_job_id = event.get("job_id")
    job_id: str = str(raw_job_id) if raw_job_id else str(context.aws_request_id)
    output = run_gatekeeper(
        bucket=event["bucket"],
        s3_key=event["key"],
        job_id=job_id,
        user_id=event["user_id"],
        statement_id=event["statement_id"],
        account_id=event["account_id"],
    )
    update_job_status(job_id, event["user_id"], PipelineStatus.GATEKEEPER_PASSED)
    return output.model_dump()
