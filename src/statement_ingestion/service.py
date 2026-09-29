"""Statement Ingestion Service — public contract for cross-feature consumers.

Other features should call methods on this service rather than importing
the repository directly, per Strict Feature Boundaries (Rule 2).

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Optional

from ..shared.schemas import PipelineStatus
from .repository import get_job as _get_job
from .repository import update_job_status as _update_job_status


def update_job_status(
    job_id: str,
    user_id: str,
    status: PipelineStatus,
    error: Optional[str] = None,
    task_token: Optional[str] = None,
    mapping_proposal: Optional[dict] = None,
) -> None:
    """Update the pipeline job status in the Job Tracker.

    This is the public service interface for other features to update
    job status without directly accessing the repository layer.

    Args:
        job_id: Step Function execution ID.
        user_id: Owner's Cognito sub.
        status: Current pipeline stage.
        error: Optional error message on failure.
        task_token: Step Functions task token to persist, if this update
            pauses the job for user input (see PENDING_CSV_COL_MAPPING_CONFIRMATION).
    """
    _update_job_status(job_id, user_id, status, error, task_token, mapping_proposal)


def get_job(job_id: str) -> Optional[dict]:
    """Fetch the current Job Tracker record for a job.

    Args:
        job_id: Step Function execution ID.

    Returns:
        The raw Job Tracker item (user_id, status, optional task_token/
        error), or None if the job doesn't exist or its 7-day TTL expired.
    """
    return _get_job(job_id)
