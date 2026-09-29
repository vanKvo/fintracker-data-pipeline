"""DynamoDB CRUD for Job Tracker table.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
import time
from typing import Optional

import boto3

from ..core.observability import logger
from ..shared.schemas import PipelineStatus

_dynamodb = boto3.resource("dynamodb")

_JOB_TRACKER_TABLE_NAME = os.environ.get("JOB_TRACKER_TABLE", "FinTracker_JobTracker")
_job_tracker_table = _dynamodb.Table(_JOB_TRACKER_TABLE_NAME)


def update_job_status(
    job_id: str,
    user_id: str,
    status: PipelineStatus,
    error: Optional[str] = None,
    task_token: Optional[str] = None,
    mapping_proposal: Optional[dict] = None,
) -> None:
    """Update the asynchronous pipeline job status in DynamoDB.

    Sets a 7-day TTL automatically. When status is PENDING_CSV_COL_MAPPING_CONFIRMATION,
    task_token must be supplied so a later confirm_column_mapping call can
    resume the paused Step Functions execution (the `.waitForTaskToken`
    callback pattern) — the token is only ever written here, never logged.

    Args:
        job_id: Step Function execution ID used as PK.
        user_id: Owner's Cognito sub.
        status: Current pipeline stage.
        error: Optional error message on failure.
        task_token: Step Functions task token to resume against, if this
            update is pausing the job for user input.
    """
    ttl = int(time.time()) + 7 * 24 * 60 * 60

    item: dict = {
        "PK": f"JOB#{job_id}",
        "SK": "METADATA",
        "user_id": user_id,
        "status": str(status),
        "ttl": ttl,
    }
    if error:
        item["error"] = error
    if task_token:
        item["task_token"] = task_token
    if mapping_proposal:
        item["mapping_proposal"] = mapping_proposal

    _job_tracker_table.put_item(Item=item)
    logger.info("Job status updated", job_id=job_id, status=str(status))


def get_job(job_id: str) -> Optional[dict]:
    """Fetch the current Job Tracker record for a job.

    Args:
        job_id: Step Function execution ID.

    Returns:
        The raw DynamoDB item, or None if the job doesn't exist (e.g. its
        7-day TTL has already expired).
    """
    response = _job_tracker_table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "METADATA"})
    return response.get("Item")
