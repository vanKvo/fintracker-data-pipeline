"""Unit tests for REQ-DP-06 (tenant scoping) and REQ-DP-08 (PII/log hygiene)
as implemented in the Data Dispatcher.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.data_dispatcher.service import push_transactions_to_ledger

_TX = {
    "tx_date": "2026-04-01",
    "merchant": "Starbucks",
    "amount": "5.25",
    "category": "Food & Drink",
    "sub_category": None,
    "type": "SALE",
    "row_fingerprint": "a" * 64,
}


def _mock_bulk_response(inserted=1, skipped=0, failed=None) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "insertedCount": inserted,
        "skippedDuplicateCount": skipped,
        "failedRows": failed or [],
    }
    return resp


class TestTenantScopingHeader:
    def test_internal_user_id_header_forwarded_on_push(self):
        with patch("src.data_dispatcher.service._requests.post", return_value=_mock_bulk_response()) as mock_post:
            push_transactions_to_ledger("job-1", "user-42", "stmt-1", [_TX])

        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Internal-User-Id"] == "user-42"

    def test_internal_api_key_still_present_alongside_tenant_header(self):
        with patch("src.data_dispatcher.service._requests.post", return_value=_mock_bulk_response()) as mock_post:
            push_transactions_to_ledger("job-1", "user-42", "stmt-1", [_TX])

        _, kwargs = mock_post.call_args
        assert "x-internal-api-key" in kwargs["headers"]

    def test_posts_to_the_real_bulk_endpoint_with_the_statement_id(self):
        with patch("src.data_dispatcher.service._requests.post", return_value=_mock_bulk_response()) as mock_post:
            push_transactions_to_ledger("job-1", "user-42", "stmt-1", [_TX])

        args, kwargs = mock_post.call_args
        assert args[0].endswith("/api/v1/ledger/transactions/internal/bulk")
        assert kwargs["json"]["statementId"] == "stmt-1"


class TestPiiLogHygiene:
    def test_failed_push_does_not_log_raw_transaction_body(self):
        with (
            patch(
                "src.data_dispatcher.service._requests.post",
                side_effect=Exception("network error"),
            ),
            patch("src.data_dispatcher.service.logger") as mock_logger,
        ):
            push_transactions_to_ledger(
                "job-1",
                "user-42",
                "stmt-1",
                [{**_TX, "merchant": "Starbucks", "amount": "5.25", "account_number": "1234567890"}],
            )

        mock_logger.error.assert_called_once()
        _, kwargs = mock_logger.error.call_args
        logged_text = str(kwargs) + str(mock_logger.error.call_args.args)
        assert "Starbucks" not in logged_text
        assert "1234567890" not in logged_text
        assert "tx" not in kwargs
