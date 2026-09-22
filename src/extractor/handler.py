"""Lambda handler for the Extractor Step Function task.

The Gatekeeper task uses result_path (not the default full-state
replacement), so this handler always receives the ORIGINAL Step Functions
input (job_id/bucket/key/user_id/statement_id/account_id) at the top level,
plus the Gatekeeper's resolved output nested under `gatekeeperOutput` —
whether that's a full GatekeeperOutput (PDF/Image, or a CSV that skipped
confirmation because every column was already mapped) or just
{"job_id", "confirmed_mapping"} (a CSV job resumed via confirm_column_mapping).
This is what keeps bucket/user_id/etc. from being lost across the pause.

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Any

from ..core.observability import logger, tracer
from ..gatekeeper.schemas import StatementFormat
from ..shared.exceptions import PipelineError
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import update_job_status
from .schemas import TextractOutput
from .service import extract_transactions, parse_csv_transactions


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def ingestion_handler(event: dict[str, Any], context: Any) -> dict:
    """Step Function Task — document ingestion (Textract/text-layer for PDF/Image, direct parse for CSV).

    Args:
        event: {job_id, bucket, key, user_id, statement_id, account_id,
            gatekeeperOutput: {...}} — see module docstring.
        context: Lambda context.

    Returns:
        Serialized TextractOutput as dict.

    Raises:
        Exception: Re-raised after recording job status FAILED, so the Step
            Functions execution still fails and can be caught/retried at
            the state-machine level.
    """
    job_id = event["job_id"]
    user_id = event["user_id"]
    statement_id = event["statement_id"]
    account_id = event["account_id"]
    bucket = event["bucket"]
    s3_key = event["key"]
    gatekeeper_output: dict = event.get("gatekeeperOutput", {})

    try:
        if "confirmed_mapping" in gatekeeper_output:
            raw_transactions = parse_csv_transactions(bucket, s3_key, gatekeeper_output["confirmed_mapping"])
            output = TextractOutput(
                job_id=job_id,
                user_id=user_id,
                statement_id=statement_id,
                account_id=account_id,
                raw_transactions=raw_transactions,
            )
        else:
            statement_format = StatementFormat(gatekeeper_output["statement_format"])
            output = extract_transactions(
                bucket=bucket,
                s3_key=s3_key,
                is_pdf=statement_format == StatementFormat.PDF,
                page_count=gatekeeper_output.get("page_count", 1),
                job_id=job_id,
                user_id=user_id,
                statement_id=statement_id,
                account_id=account_id,
            )
    except Exception as e:
        reason = e.reason if isinstance(e, PipelineError) else "EXTRACTION_FAILED"
        logger.exception("Extraction failed", job_id=job_id, reason=reason)
        update_job_status(job_id, user_id, PipelineStatus.FAILED, error=str(e))
        raise

    update_job_status(job_id, user_id, PipelineStatus.INGESTING)
    return output.model_dump()
