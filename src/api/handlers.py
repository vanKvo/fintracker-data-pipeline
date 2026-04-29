"""Lambda handlers for the Data Pipeline Step Function tasks.

All handlers are orchestrator-agnostic wrappers around pure service functions.
They can be invoked by Step Functions, direct Lambda invocations, or test runners.

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

from ..core.observability import logger, tracer
from ..crud.pipeline_repository import update_job_status
from ..schemas.pipeline import GatekeeperOutput, PipelineStatus, TextractOutput
from ..services.gatekeeper_service import run_gatekeeper
from ..services.ingestion_service import extract_transactions
from ..services.normalizer_service import normalize_and_categorize

_LEDGER_API_URL = os.environ.get("LEDGER_API_URL", "")
_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

import requests as _requests


# ─── Step 1: S3 Processor Lambda — triggers Step Functions ───────────────────────────
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

        # userId and statementId are stored as S3 object metadata tags
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


# ─── Step 2: Vision Gatekeeper ────────────────────────────────────────────────
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


# ─── Step 3: Textract Ingestion ───────────────────────────────────────────────
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


# ─── Step 4: Normalizer + Categorizer ────────────────────────────────────────
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


# ─── Step 5: Push to Ledger ───────────────────────────────────────────────────
@tracer.capture_lambda_handler
@logger.inject_lambda_context
def ledger_push_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — Push normalized transactions to Ledger Service API.

    Calls the internal Ledger Service REST endpoint (service-to-service auth via API key).
    All transactions are created with status PENDING_APPROVAL.

    Args:
        event: Normalizer output dict.
        context: Lambda context.

    Returns:
        Status summary dict.
    """
    job_id: str = event["job_id"]
    user_id: str = event["user_id"]
    transactions: list[dict] = event.get("transactions", [])

    headers = {"x-internal-api-key": _INTERNAL_API_KEY, "Content-Type": "application/json"}

    success_count = 0
    for tx in transactions:
        try:
            resp = _requests.post(
                f"{_LEDGER_API_URL}/v1/transactions/internal",
                json=tx,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            success_count += 1
        except Exception as e:
            logger.error("Failed to push transaction to ledger", error=str(e), tx=tx)

    update_job_status(job_id, user_id, PipelineStatus.COMPLETED)
    logger.info("Ledger push complete", job_id=job_id, pushed=success_count, total=len(transactions))

    return {"job_id": job_id, "pushed": success_count, "total": len(transactions)}
