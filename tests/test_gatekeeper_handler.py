"""Unit tests for the Gatekeeper Lambda handler's error-handling contract.

Covers REQ-DP-03's "Failure Handling" business rule applied at the
Gatekeeper stage: a failed/rejected job must update status instead of
dying silently.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.gatekeeper.handler import gatekeeper_handler
from src.gatekeeper.schemas import ColumnMappingProposal, GatekeeperOutput, StatementFormat
from src.shared.exceptions import UnsupportedFormatError
from src.shared.schemas import PipelineStatus


def _event(bank_id: str | None = None, task_token: str | None = None) -> dict:
    # A TaskToken is always present under the real waitForTaskToken integration pattern (see
    # gatekeeper_handler's own docstring) — tests that exercise task resolution need one present,
    # same as production traffic always has.
    event = {
        "job_id": "job-1",
        "bucket": "test-bucket",
        "key": "stmt.pdf",
        "user_id": "user-1",
        "statement_id": "stmt-1",
        "account_id": "acct-1",
    }
    if bank_id:
        event["bank_id"] = bank_id
    if task_token:
        event["TaskToken"] = task_token
    return event


class TestGatekeeperHandlerStatus:
    def test_pdf_marks_gatekeeper_passed_and_resolves_task_success(self):
        # PDF/Image no longer carry a page-validity verdict here at all — YOLO gating was
        # removed, and the Extractor's Tier 1/Tier 2 waterfall decides per-page usability
        # instead (see GatekeeperOutput's own docstring). Gatekeeper either succeeds outright
        # or raises; there's no third "ran fine but rejected the file" outcome any more.
        output = GatekeeperOutput(
            job_id="job-1",
            s3_key="stmt.pdf",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acct-1",
            statement_format=StatementFormat.PDF,
            page_count=3,
        )
        with (
            patch("src.gatekeeper.handler.run_gatekeeper", return_value=output),
            patch("src.gatekeeper.handler.update_job_status") as mock_status,
            patch("src.gatekeeper.handler._sfn_client") as mock_sfn,
        ):
            gatekeeper_handler(_event(task_token="token-1"), MagicMock())

        mock_status.assert_called_once_with("job-1", "user-1", PipelineStatus.GATEKEEPER_PASSED)
        mock_sfn.send_task_success.assert_called_once()

    def test_csv_needing_confirmation_marks_pending_and_does_not_resolve_task(self):
        # REQ-DP-01 "User Mapping Confirmation Gate": the execution must stay paused — no
        # send_task_success/send_task_failure — until confirm_column_mapping resumes it out of
        # band, once the user confirms the mapping.
        proposal = ColumnMappingProposal(
            bank_id="chase",
            csv_s3_key="stmt.csv",
            mapped={"date": "Transaction Date"},
            unmapped_columns=["Description", "Debit"],
            is_known_bank=True,
        )
        output = GatekeeperOutput(
            job_id="job-1",
            s3_key="stmt.csv",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acct-1",
            statement_format=StatementFormat.CSV,
            csv_s3_key="stmt.csv",
            mapping_proposal=proposal,
            requires_csv_col_mapping_confirmation=True,
        )
        with (
            patch("src.gatekeeper.handler.run_gatekeeper", return_value=output),
            patch("src.gatekeeper.handler.update_job_status") as mock_status,
            patch("src.gatekeeper.handler._sfn_client") as mock_sfn,
        ):
            gatekeeper_handler(_event(bank_id="chase", task_token="token-1"), MagicMock())

        mock_status.assert_called_once_with(
            "job-1",
            "user-1",
            PipelineStatus.PENDING_CSV_COL_MAPPING_CONFIRMATION,
            task_token="token-1",
            mapping_proposal=proposal.model_dump(),
        )
        mock_sfn.send_task_success.assert_not_called()
        mock_sfn.send_task_failure.assert_not_called()

    def test_gatekeeper_exception_records_failed_status_and_reraises(self):
        with (
            patch("src.gatekeeper.handler.run_gatekeeper", side_effect=UnsupportedFormatError("bad file")),
            patch("src.gatekeeper.handler.update_job_status") as mock_status,
        ):
            with pytest.raises(UnsupportedFormatError):
                gatekeeper_handler(_event(), MagicMock())

        args, kwargs = mock_status.call_args
        assert args[2] == PipelineStatus.FAILED
        assert "bad file" in kwargs.get("error", "")
