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
    """Retrieve a single config value by its short key name.

    Checks a plain Lambda environment variable of the same name first — dev's Terraform
    environment (infrastructure/terraform/environments/dev) passes values like
    LEDGER_API_URL straight through as env vars rather than provisioning SSM parameters, so
    this is what makes that work without every caller needing to know which source it came
    from. Falls back to SSM SecureString otherwise, which is what a real (staging/prod)
    environment must still use (CLAUDE.md's Python Standards: "Secrets via AWS SSM Parameter
    Store, never hardcoded") — this fallback is what a hardened environment relies on.

    Args:
        key: The short parameter name (e.g. "MERCHANT_TABLE").
        default: Fallback value if the key is not found in either source.

    Returns:
        The parameter value string.
    """
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    return get_config().get(f"{_SSM_PATH}/{key}", default)
