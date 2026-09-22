"""Lambda handler for the Gatekeeper Step Function task.

This task is invoked via the Step Functions `.waitForTaskToken` integration
pattern for every statement format, not just CSV — Step Functions ties the
integration pattern to the state definition, not to the payload, so one
Task can't sometimes be request/response and sometimes be callback-based.
Consequently this handler must always resolve the task explicitly:

  - CSV needing confirmation: store the task token and return without
    resolving. The execution stays paused until confirm_column_mapping
    (invoked out of band, when the user submits the mapping dialog) calls
    send_task_success — see gatekeeper/service.py.
  - Everything else (PDF/Image, or any failure): resolve the task token
    directly, here, via send_task_success/send_task_failure. Simply
    returning a value would leave the execution hanging until Step
    Functions' task timeout fires, which is a correctness bug, not a
    graceful failure.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

import boto3

from ..core.observability import logger, tracer
from ..shared.exceptions import PipelineError
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .service import run_gatekeeper

_sfn_client = boto3.client("stepfunctions")


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def gatekeeper_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — Gatekeeper (waitForTaskToken integration).

    Args:
        event: Step Function input with bucket/key/job_id/user_id, plus
            bank_id for CSV and a TaskToken (always present under
            waitForTaskToken).
        context: Lambda context.

    Returns:
        Serialized GatekeeperOutput as dict — useful for local/direct
        invocation and tests; the Step Functions execution itself only
        advances based on the send_task_success/send_task_failure calls
        made here, not this return value.
    """
    raw_job_id = event.get("job_id")
    job_id: str = str(raw_job_id) if raw_job_id else str(context.aws_request_id)
    user_id: str = event["user_id"]
    task_token: str | None = event.get("TaskToken")

    try:
        output = run_gatekeeper(
            bucket=event["bucket"],
            s3_key=event["key"],
            job_id=job_id,
            user_id=user_id,
            statement_id=event["statement_id"],
            account_id=event["account_id"],
            bank_id=event.get("bank_id"),
        )
    except Exception as e:
        reason = e.reason if isinstance(e, PipelineError) else "GATEKEEPER_FAILED"
        logger.exception("Gatekeeper failed", job_id=job_id, reason=reason)
        update_job_status(job_id, user_id, PipelineStatus.FAILED, error=str(e))
        if task_token:
            _sfn_client.send_task_failure(taskToken=task_token, error=reason, cause=str(e))
        raise

    if output.requires_mapping_confirmation:
        # Pause: store the token AND the proposed mapping (mapped/unmapped
        # columns — the UI needs this to render the confirmation dialog,
        # not just to know a confirmation is pending), do NOT resolve the
        # task. The execution resumes later via confirm_column_mapping's
        # send_task_success.
        update_job_status(
            job_id,
            user_id,
            PipelineStatus.PENDING_MAPPING_CONFIRMATION,
            task_token=task_token,
            mapping_proposal=output.mapping_proposal.model_dump() if output.mapping_proposal else None,
        )
        logger.info("Job paused for mapping confirmation", job_id=job_id)
    else:
        update_job_status(job_id, user_id, PipelineStatus.GATEKEEPER_PASSED)
        if task_token:
            _sfn_client.send_task_success(taskToken=task_token, output=output.model_dump_json())

    return output.model_dump()
