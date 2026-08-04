"""Statement Ingestion Orchestrator — coordinates the data ingestion workflow.

This module provides the S3 processor handler that triggers the Step Functions
state machine, which orchestrates: Gatekeeper -> Extractor -> Normalizer -> Data Dispatcher.

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

from ..core.observability import logger, tracer
from ..shared.schemas import PipelineStatus
from .service import update_job_status


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def s3_processor_handler(event: dict[str, Any], context: Any) -> dict:
    """S3 PutObject trigger — starts the Step Function execution.

    Reads S3 event metadata (bucket/key + object tags for userId, statementId)
    and triggers the State Machine with a structured input payload.

    Args:
        event: S3 event from Lambda trigger.
        context: Lambda context.

    Returns:
        Dict with executionArn.
    """
    sfn_client = boto3.client("stepfunctions")
    state_machine_arn = os.environ["STATE_MACHINE_ARN"]

    for record in event.get("Records", []):
        s3_info = record["s3"]
        bucket = s3_info["bucket"]["name"]
        key = s3_info["object"]["key"]

        head = boto3.client("s3").head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata", {})
        user_id = meta.get("user-id", "")
        statement_id = meta.get("statement-id", "")
        account_id = meta.get("account-id", "")

        payload = {
            "bucket": bucket,
            "key": key,
            "user_id": user_id,
            "statement_id": statement_id,
            "account_id": account_id,
        }

        response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps(payload),
        )
        job_id = response["executionArn"].split(":")[-1]
        update_job_status(job_id, user_id, PipelineStatus.STARTED)
        logger.info("Step Function started", job_id=job_id, key=key)

    return {"status": "triggered"}
