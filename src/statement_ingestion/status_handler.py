"""Lambda handler for the job status polling endpoint.

Invoked via API Gateway (GET /jobs/{jobId}) so the UI can poll pipeline
progress using the statement_id it already has from the Ledger's
initiate-upload response (job_id == statement_id — see orchestrator.py).

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
from typing import Any

from ..core.observability import logger, tracer
from .service import get_job


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def job_status_handler(event: dict[str, Any], context: Any) -> dict:
    """API Gateway handler — GET /jobs/{jobId}.

    Args:
        event: API Gateway proxy event. Path parameter `jobId`. The
            requesting user's identity must be present as the
            `X-Internal-User-Id` header — set by the API Gateway JWT
            authorizer's claims mapping, never trusted from anywhere else
            (REQ-DP-05).
        context: Lambda context.

    Returns:
        API Gateway proxy response — 200 with {status, error} on success,
        404 if the job doesn't exist OR belongs to another tenant (the two
        cases are deliberately indistinguishable, matching the Ledger's own
        not-found-vs-not-yours convention).
    """
    job_id = event.get("pathParameters", {}).get("jobId")
    requesting_user_id = (event.get("headers") or {}).get("X-Internal-User-Id") or (event.get("headers") or {}).get(
        "x-internal-user-id"
    )

    if not job_id or not requesting_user_id:
        return _response(400, {"type": "INVALID_REQUEST", "title": "Invalid Request", "status": 400})

    job = get_job(job_id)
    if not job or job.get("user_id") != requesting_user_id:
        return _response(404, {"type": "JOB_NOT_FOUND", "title": "Job Not Found", "status": 404})

    body = {"jobId": job_id, "status": job.get("status"), "error": job.get("error")}
    if job.get("mapping_proposal"):
        body["mappingProposal"] = job["mapping_proposal"]
    return _response(200, body)


def _response(status: int, body: dict) -> dict:
    content_type = "application/problem+json" if status >= 400 else "application/json"
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": json.dumps(body)}
