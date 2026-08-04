"""Lambda handler entry point for the Statement Ingestion S3 processor.

Re-exports the orchestrator's s3_processor_handler for use as the Lambda entry point.

__author__ = "Van Vo"
"""

from .orchestrator import s3_processor_handler

__all__ = ["s3_processor_handler"]
