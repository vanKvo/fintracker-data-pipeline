"""Statement Ingestion Orchestrator — coordinates the data ingestion workflow.

This module provides the S3 processor handler that triggers the Step Functions
state machine, which orchestrates: Gatekeeper -> Extractor -> Normalizer -> Data Dispatcher.

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import boto3

from ..core.observability import logger, tracer
from ..shared.exceptions import StatementOwnerNotFoundError
from ..shared.schemas import PipelineStatus
from .ledger_client import get_verified_statement_owner
from .service import update_job_status


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def s3_processor_handler(event: dict[str, Any], context: Any) -> dict:
    """S3 PutObject trigger — starts the Step Function execution.

    Reads the S3 event (bucket/key + the statement-id/bank-id object tags) and triggers the
    State Machine with a structured input payload.

    REQ-DP-05: user_id and account_id are NOT read from the S3 object metadata tags — those
    tags are attacker- or bug-controlled the same way a request-body id is, the exact trust
    class CLAUDE.md's request-flow section disallows for data scoping. Instead, statement-id
    (itself not security-sensitive — it only says which record to ask about, and the Ledger's
    presigned URL is cryptographically bound to a specific statement-id's S3 key already) is
    used to look up the statement's real owner from the Ledger, the server-side record created
    by the authenticated initiate-upload request — see ledger_client.get_verified_statement_owner.
    That response is the only source of user_id/account_id for the rest of the pipeline.

    The job_id used for the Step Functions execution name IS the statement_id
    from the S3 event, not a freshly minted identifier. The Ledger
    already generates a statement_id when it creates the statement record
    and issues the presigned upload URL (see StatementController in
    fintracker-ledger) — the UI has that ID immediately, before the pipeline
    even starts, and needs a way to poll pipeline status using an ID it
    already holds. Reusing it as job_id avoids a second correlation table:
    GET /jobs/{statementId} on this service's status endpoint just works,
    and as a bonus, a duplicate S3 event for the same statement collides on
    execution name instead of silently double-processing (REQ-DP-03
    idempotency) — one statement can only ever have one execution.

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
        # bank-id is a processing parameter (which column-mapping baseline to try), not a
        # tenant-identity field — trusting it from the upload metadata carries no cross-tenant
        # risk, so REQ-DP-05 does not apply to it the way it does to user_id/account_id below.
        statement_id = meta.get("statement-id", "")
        bank_id = meta.get("bank-id", "")

        # See docstring: job_id == statement_id, not a fresh UUID, so the
        # UI can poll by an ID it already has and duplicate uploads for the
        # same statement can't double-run. statement_id is itself a
        # Ledger-generated UUID, so this still satisfies Step Functions'
        # execution-name format; the uuid4 fallback only covers a malformed
        # upload that's missing its statement-id tag entirely.
        job_id = statement_id or str(uuid.uuid4())

        try:
            owner = get_verified_statement_owner(statement_id)
        except StatementOwnerNotFoundError as e:
            # No verified identity to record this failure under (that is exactly what is
            # missing), so there is no update_job_status call to make here — this statement_id
            # was never legitimately issued a job to track in the first place.
            logger.exception("Refusing to start pipeline for an unverifiable statement", job_id=job_id, reason=str(e))
            continue

        payload = {
            "job_id": job_id,
            "bucket": bucket,
            "key": key,
            "user_id": owner.user_id,
            "statement_id": statement_id,
            "account_id": owner.account_id,
            "bank_id": bank_id,
        }

        sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=job_id,
            input=json.dumps(payload),
        )
        update_job_status(job_id, owner.user_id, PipelineStatus.STARTED)
        logger.info("Step Function started", job_id=job_id, key=key)

    return {"status": "triggered"}
