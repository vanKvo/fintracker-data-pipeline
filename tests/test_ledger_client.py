"""Unit tests for the Ledger Client's statement-owner lookup (REQ-DP-05).

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.shared.exceptions import StatementOwnerNotFoundError
from src.statement_ingestion.ledger_client import StatementOwner, get_verified_statement_owner


def _mock_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400 and status_code != 404:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestGetVerifiedStatementOwner:
    def test_returns_owner_on_success(self):
        response = _mock_response(
            200, {"statementId": "stmt-1", "accountId": "acct-1", "userId": "user-1"}
        )
        with patch("src.statement_ingestion.ledger_client._requests.get", return_value=response) as mock_get:
            owner = get_verified_statement_owner("stmt-1")

        assert owner == StatementOwner(account_id="acct-1", user_id="user-1")
        args, kwargs = mock_get.call_args
        assert args[0].endswith("/api/v1/ledger/statements/internal/stmt-1/owner")

    def test_sends_a_sentinel_user_header_not_a_real_identity(self):
        # This call's whole purpose is to discover the real user — it must never assert one of
        # its own, but UserContextFilter (Ledger-side) still requires *a* syntactically valid
        # UUID on every internal route, this one included.
        response = _mock_response(200, {"statementId": "s", "accountId": "a", "userId": "u"})
        with patch("src.statement_ingestion.ledger_client._requests.get", return_value=response) as mock_get:
            get_verified_statement_owner("stmt-1")

        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["X-Internal-User-Id"] == "00000000-0000-0000-0000-000000000000"

    def test_404_raises_statement_owner_not_found(self):
        response = _mock_response(404)
        with patch("src.statement_ingestion.ledger_client._requests.get", return_value=response):
            try:
                get_verified_statement_owner("no-such-statement")
                assert False, "expected StatementOwnerNotFoundError"
            except StatementOwnerNotFoundError as e:
                assert e.reason == "STATEMENT_OWNER_NOT_FOUND"
