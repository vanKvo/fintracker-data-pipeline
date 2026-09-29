"""Unit tests for the Data Dispatcher handler.

Covers the confirmed REQ-DP-02/REQ-DP-03-adjacent bug: job status was
unconditionally set to COMPLETED even when the Ledger push partially or
fully failed.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.data_dispatcher.handler import ledger_push_handler
from src.data_dispatcher.schemas import LedgerPushResult
from src.shared.schemas import PipelineStatus


def _event() -> dict:
    return {
        "job_id": "job-1",
        "user_id": "user-1",
        "statement_id": "stmt-1",
        "transactions": [{"merchant": "Starbucks"}, {"merchant": "Uber"}],
    }


class TestLedgerPushJobStatus:
    def test_all_succeeded_marks_completed(self):
        result = LedgerPushResult(job_id="job-1", success_count=2, total_count=2, all_succeeded=True)
        with (
            patch("src.data_dispatcher.handler.push_transactions_to_ledger", return_value=result),
            patch("src.data_dispatcher.handler.update_job_status") as mock_status,
        ):
            ledger_push_handler(_event(), MagicMock())

        mock_status.assert_called_once_with("job-1", "user-1", PipelineStatus.COMPLETED, error=None)

    def test_partial_failure_marks_partially_completed_not_completed(self):
        result = LedgerPushResult(job_id="job-1", success_count=1, total_count=2, all_succeeded=False)
        with (
            patch("src.data_dispatcher.handler.push_transactions_to_ledger", return_value=result),
            patch("src.data_dispatcher.handler.update_job_status") as mock_status,
        ):
            ledger_push_handler(_event(), MagicMock())

        args, kwargs = mock_status.call_args
        assert args[2] == PipelineStatus.PARTIALLY_COMPLETED
        assert args[2] != PipelineStatus.COMPLETED

    def test_total_failure_marks_failed_not_completed(self):
        result = LedgerPushResult(job_id="job-1", success_count=0, total_count=2, all_succeeded=False)
        with (
            patch("src.data_dispatcher.handler.push_transactions_to_ledger", return_value=result),
            patch("src.data_dispatcher.handler.update_job_status") as mock_status,
        ):
            ledger_push_handler(_event(), MagicMock())

        args, kwargs = mock_status.call_args
        assert args[2] == PipelineStatus.FAILED


class TestLedgerNotConfigured:
    def test_missing_ledger_url_marks_job_failed_with_reason_code(self):
        from src.shared.exceptions import LedgerNotConfiguredError

        with (
            patch(
                "src.data_dispatcher.handler.push_transactions_to_ledger",
                side_effect=LedgerNotConfiguredError("LEDGER_API_URL is not set"),
            ),
            patch("src.data_dispatcher.handler.update_job_status") as mock_status,
        ):
            with pytest.raises(LedgerNotConfiguredError):
                ledger_push_handler(_event(), MagicMock())

        mock_status.assert_called_once_with("job-1", "user-1", PipelineStatus.FAILED, error="LEDGER_NOT_CONFIGURED")
