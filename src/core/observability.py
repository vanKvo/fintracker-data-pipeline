"""Lambda Powertools observability singletons for Data Pipeline Service.

__author__ = "Van Vo"
"""

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

logger = Logger(service="data-pipeline-service")
tracer = Tracer(service="data-pipeline-service")
metrics = Metrics(namespace="FinTracker", service="data-pipeline-service")

__all__ = ["logger", "tracer", "metrics", "MetricUnit"]
