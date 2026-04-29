"""DynamoDB CRUD for Job Tracker and Merchant Registry tables.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from ..core.observability import logger
from ..schemas.pipeline import JobTrackerItem, NormalizedTransaction, PipelineStatus

# Global DynamoDB client — reused across Lambda warm-starts
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
    ttl = int(time.time()) + 7 * 24 * 60 * 60  # 7 days

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


def lookup_merchant(merchant_key: str) -> Optional[dict]:
    """Look up a known merchant from the MerchantRegistry DynamoDB table.

    Args:
        merchant_key: Lowercased, Regex-cleaned merchant name.

    Returns:
        A dict with category/sub_category/confidence or None if not found.
    """
    try:
        response = _pipeline_table.get_item(
            Key={"PK": merchant_key, "SK": "DETAILS"}
        )
        return response.get("Item")
    except ClientError as e:
        logger.warning("Merchant lookup failed", error=str(e))
        return None


def cache_merchant(merchant_key: str, category: str, sub_category: str, confidence: Decimal) -> None:
    """Store a newly categorized merchant in the registry to avoid future Comprehend calls.

    Args:
        merchant_key: Lowercased merchant string.
        category: Standardized category name.
        sub_category: Sub-category label.
        confidence: Comprehend confidence score.
    """
    _pipeline_table.put_item(
        Item={
            "PK": merchant_key,
            "SK": "DETAILS",
            "category": category,
            "sub_category": sub_category,
            "confidence": confidence,
        }
    )
