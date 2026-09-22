"""Unit tests for the Data Dispatcher's bulk Ledger push (REQ-STMT-02, REQ-DP-02).

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.data_dispatcher.schemas import LedgerPushResult
from src.data_dispatcher.service import _to_ledger_line, push_transactions_to_ledger

_TX = {
    "tx_date": "2026-04-01",
    "merchant": "starbucks",
    "amount": "5.25",
    "category": "Food & Drink",
    "sub_category": "Coffee Shops",
    "type": "SALE",
    "row_fingerprint": "a" * 64,
}


def _mock_response(inserted=0, skipped=0, failed=None) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "insertedCount": inserted,
        "skippedDuplicateCount": skipped,
        "failedRows": failed or [],
    }
    return resp


class TestToLedgerLine:
    def test_maps_snake_case_fields_to_the_ledger_camelcase_contract(self):
        line = _to_ledger_line(_TX)
        assert line == {
            "date": "2026-04-01",
            "merchant": "starbucks",
            "amount": "5.25",
            "category": "Food & Drink",
            "subCategory": "Coffee Shops",
            "type": "PURCHASE",
            "rowFingerprint": "a" * 64,
        }

    def test_return_type_maps_to_credit(self):
        line = _to_ledger_line({**_TX, "type": "RETURN"})
        assert line["type"] == "CREDIT"


class TestPushTransactionsToLedger:
    def test_empty_transactions_is_a_no_op_success(self):
        with patch("src.data_dispatcher.service._requests.post") as mock_post:
            result = push_transactions_to_ledger("job-1", "user-1", "stmt-1", [])

        mock_post.assert_not_called()
        assert result == LedgerPushResult(job_id="job-1", success_count=0, total_count=0, all_succeeded=True)

    def test_one_batch_call_covers_every_transaction(self):
        with patch(
            "src.data_dispatcher.service._requests.post", return_value=_mock_response(inserted=3)
        ) as mock_post:
            push_transactions_to_ledger("job-1", "user-1", "stmt-1", [_TX, _TX, _TX])

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert len(kwargs["json"]["transactions"]) == 3

    def test_inserted_and_skipped_duplicates_both_count_as_success(self):
        with patch(
            "src.data_dispatcher.service._requests.post",
            return_value=_mock_response(inserted=1, skipped=1),
        ):
            result = push_transactions_to_ledger("job-1", "user-1", "stmt-1", [_TX, _TX])

        assert result.success_count == 2
        assert result.all_succeeded is True

    def test_failed_rows_mark_the_push_not_fully_succeeded(self):
        with patch(
            "src.data_dispatcher.service._requests.post",
            return_value=_mock_response(inserted=1, failed=[{"index": 1, "reason": "bad row"}]),
        ):
            result = push_transactions_to_ledger("job-1", "user-1", "stmt-1", [_TX, _TX])

        assert result.success_count == 1
        assert result.total_count == 2
        assert result.all_succeeded is False

    def test_request_failure_counts_every_row_as_failed_not_silently_dropped(self):
        with patch(
            "src.data_dispatcher.service._requests.post", side_effect=Exception("network error")
        ):
            result = push_transactions_to_ledger("job-1", "user-1", "stmt-1", [_TX, _TX])

        assert result.success_count == 0
        assert result.total_count == 2
        assert result.all_succeeded is False
