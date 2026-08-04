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

_PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE", "FinTracker_DataPipeline")
_pipeline_table = _dynamodb.Table(_PIPELINE_TABLE_NAME)


def update_job_status(job_id: str, user_id: str, status: PipelineStatus, error: Optional[str] = None) -> None:
    """Update the asynchronous pipeline job status in DynamoDB.

    Sets a 7-day TTL automatically.

    Args:
        job_id: Step Function execution ID used as PK.
        user_id: Owner's Cognito sub.
        status: Current pipeline stage.
        error: Optional error message on failure.
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

    _pipeline_table.put_item(Item=item)
    logger.info("Job status updated", job_id=job_id, status=str(status))
