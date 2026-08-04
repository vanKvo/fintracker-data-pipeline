"""Statement Ingestion Service — public contract for cross-feature consumers.

Other features should call methods on this service rather than importing
the repository directly, per Strict Feature Boundaries (Rule 2).

__author__ = "Van Vo"
"""

from __future__ import annotations

from typing import Optional

from ..shared.schemas import PipelineStatus
from .repository import update_job_status as _update_job_status


def update_job_status(
    job_id: str,
    user_id: str,
    status: PipelineStatus,
    error: Optional[str] = None,
) -> None:
    """Update the pipeline job status in the Job Tracker.

    This is the public service interface for other features to update
    job status without directly accessing the repository layer.

    Args:
        job_id: Step Function execution ID.
        user_id: Owner's Cognito sub.
        status: Current pipeline stage.
        error: Optional error message on failure.
    """
    _update_job_status(job_id, user_id, status, error)
