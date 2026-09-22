"""Lambda handler for the mapping confirmation endpoint.

Invoked via API Gateway when the user submits the confirmed/corrected
column mapping from the upload dialog (REQ-DP-01 "User Mapping
Confirmation Gate"). This is a synchronous, user-facing request/response
handler, not a Step Functions task — it's the thing that resumes the
paused Step Functions execution on the other side.

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
from typing import Any

from ..core.observability import logger, tracer
from ..shared.exceptions import InvalidCsvFormatError, MappingConfirmationTimeoutError, PipelineError
from .service import confirm_column_mapping


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def mapping_confirmation_handler(event: dict[str, Any], context: Any) -> dict:
    """API Gateway handler — POST /jobs/{jobId}/mapping-confirmation.

    Args:
        event: API Gateway proxy event. Path parameter `jobId` (== the
            statement_id the UI already has). Body must be JSON:
            {"bank_id": str, "confirmed_mapping": {"date": "...", "merchant": "...", "amount": "..."}}
        context: Lambda context.

    Returns:
        API Gateway proxy response dict — 204 on success, 400/404/409 on
        the corresponding PipelineError, matching this service's RFC 9457
        error-shape convention.
    """
    try:
        job_id = event["pathParameters"]["jobId"]
        body = json.loads(event.get("body") or "{}")
        bank_id = body["bank_id"]
        confirmed_mapping = body["confirmed_mapping"]
    except (KeyError, json.JSONDecodeError) as e:
        return _problem_response(400, "INVALID_REQUEST", f"Malformed request: {e}")

    try:
        confirm_column_mapping(job_id, bank_id, confirmed_mapping)
    except InvalidCsvFormatError as e:
        return _problem_response(400, e.reason, str(e))
    except MappingConfirmationTimeoutError as e:
        return _problem_response(409, e.reason, str(e))
    except PipelineError as e:
        logger.exception("Mapping confirmation failed", job_id=job_id, reason=e.reason)
        return _problem_response(500, e.reason, "Failed to confirm mapping")

    return {"statusCode": 204, "body": ""}


def _problem_response(status: int, reason: str, detail: str) -> dict:
    """RFC 9457 Problem Details response, matching the Ledger's error-shape
    convention (CLAUDE.md — "Error responses must follow RFC 9457")."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/problem+json"},
        "body": json.dumps({"type": reason, "title": reason.replace("_", " ").title(), "status": status, "detail": detail}),
    }
