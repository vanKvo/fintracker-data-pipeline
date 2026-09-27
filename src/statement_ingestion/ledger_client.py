"""Ledger Client — verified statement ownership lookup (REQ-DP-05).

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests as _requests

from ..core.observability import logger
from ..shared.exceptions import StatementOwnerNotFoundError
from ..shared.sigv4 import sign_headers

_LEDGER_API_URL = os.environ.get("LEDGER_API_URL", "")

# UserContextFilter (Ledger-side) requires a syntactically valid X-Internal-User-Id on every
# /internal/* route, this one included — but this call's whole purpose is to discover the real
# owner, so there is no real identity to assert yet. This fixed sentinel satisfies that generic
# filter's format check without asserting a false identity; InternalStatementController#getOwner
# deliberately ignores it for this one route. The real access control is InternalCallerFilter's
# caller-ARN allow-list, same as every other internal route.
_UNKNOWN_CALLER_SENTINEL = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class StatementOwner:
    account_id: str
    user_id: str


def get_verified_statement_owner(statement_id: str) -> StatementOwner:
    """REQ-DP-05: looks up a statement's real owner from the Ledger, replacing the S3 object
    metadata tags (user-id/account-id) the pipeline previously trusted at face value — the
    same trust class as accepting a user-supplied id from a request body, which the project's
    architecture disallows for data scoping everywhere else. Called once, by the S3 Processor,
    before user_id/account_id enter the rest of the pipeline.

    Args:
        statement_id: The statement id from the S3 event — trusted only as "which statement to
            ask about", never as proof of who owns it.

    Returns:
        The verified owner.

    Raises:
        StatementOwnerNotFoundError: the Ledger has no statement with this id.
    """
    url = f"{_LEDGER_API_URL}/api/v1/ledger/statements/internal/{statement_id}/owner"
    headers = sign_headers("GET", url, {"X-Internal-User-Id": _UNKNOWN_CALLER_SENTINEL})
    response = _requests.get(url, headers=headers, timeout=10)
    if response.status_code == 404:
        logger.warning("Statement owner lookup found no such statement", statement_id=statement_id)
        raise StatementOwnerNotFoundError(f"No statement found for id {statement_id}")
    response.raise_for_status()

    body = response.json()
    return StatementOwner(account_id=body["accountId"], user_id=body["userId"])
