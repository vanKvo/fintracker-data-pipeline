"""Core configuration loaded from AWS SSM Parameter Store.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from functools import lru_cache

from aws_lambda_powertools.utilities.parameters import SSMProvider

# Global SSM provider — initialised once per Lambda cold-start
_ssm_provider = SSMProvider()
_ENV = os.environ.get("APP_ENV", "Dev")
_SSM_PATH = f"/FinTracker/{_ENV}/DataPipeline"


@lru_cache(maxsize=1)
def get_config() -> dict[str, str]:
    """Fetch all pipeline parameters from SSM using GetParametersByPath.

    Results are cached in Lambda memory for 5 minutes via Powertools TTL.

    Returns:
        A dict mapping parameter names to their string values.
    """
    params: dict[str, str] = _ssm_provider.get_multiple(  # type: ignore[assignment]
        _SSM_PATH,
        max_age=300,
        decrypt=True,
    )
    return params


def get_param(key: str, default: str = "") -> str:
    """Retrieve a single SSM parameter value by its short key name.

    Args:
        key: The short parameter name (e.g. "MERCHANT_TABLE").
        default: Fallback value if the key is not found.

    Returns:
        The parameter value string.
    """
    return get_config().get(f"{_SSM_PATH}/{key}", default)
