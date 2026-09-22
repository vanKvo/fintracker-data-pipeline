"""Unit tests for the Extractor service and handler.

Covers REQ-DP-01's "CSV Transaction Parsing" business rule and the
Extractor handler's routing (CSV via a confirmed column mapping vs.
PDF/Image via the Tier 1/Tier 2 waterfall) and error-handling contract.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.extractor.handler import ingestion_handler
from src.extractor.service import parse_csv_transactions
from src.shared.exceptions import InvalidCsvFormatError
from src.shared.schemas import PipelineStatus

_CONFIRMED_MAPPING = {"date": "date", "merchant": "merchant", "amount": "amount"}


@mock_aws
class TestParseCsvTransactions:
    def setup_method(self, method):
        os.environ["JOB_TRACKER_TABLE"] = "FinTracker_JobTracker_Test"
        self.bucket = "test-bucket"
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket=self.bucket)

    def test_valid_csv_produces_transactions(self):
        body = b"date,merchant,amount\n2026-04-01,Starbucks,5.25\n2026-04-02,Uber,14.00\n"
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            transactions = parse_csv_transactions(self.bucket, "stmt.csv", _CONFIRMED_MAPPING)

        assert len(transactions) == 2
        assert transactions[0].raw_merchant == "Starbucks"
        assert transactions[0].raw_amount == "5.25"
        assert transactions[1].raw_merchant == "Uber"

    def test_bad_row_is_skipped_not_fatal(self):
        body = b"date,merchant,amount\n2026-04-01,,5.25\n2026-04-02,Uber,14.00\n"
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            transactions = parse_csv_transactions(self.bucket, "stmt.csv", _CONFIRMED_MAPPING)

        assert len(transactions) == 1
        assert transactions[0].raw_merchant == "Uber"

    def test_confirmed_mapping_missing_canonical_field_raises(self):
        body = b"date,merchant,amount\n2026-04-01,Starbucks,5.25\n"
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            with pytest.raises(InvalidCsvFormatError):
                parse_csv_transactions(self.bucket, "stmt.csv", {"date": "date", "merchant": "merchant"})

    def test_confirmed_mapping_references_missing_column_raises(self):
        # The file changed between the mapping proposal and its confirmation — the confirmed
        # mapping's columns no longer exist in the file it's applied against.
        body = b"foo,bar\n1,2\n"
        self.s3.put_object(Bucket=self.bucket, Key="bad.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            with pytest.raises(InvalidCsvFormatError):
                parse_csv_transactions(self.bucket, "bad.csv", _CONFIRMED_MAPPING)


class TestIngestionHandlerCsvRouting:
    """CSV routes through the confirmed column mapping (REQ-DP-01 "User Mapping Confirmation
    Gate"), never through extract_transactions's Tier 1/Tier 2 waterfall — that path exists
    only for PDF/Image, which have no column mapping to confirm."""

    def _csv_event(self) -> dict:
        return {
            "job_id": "job-1",
            "key": "stmt.csv",
            "user_id": "user-1",
            "statement_id": "stmt-1",
            "account_id": "acct-1",
            "bucket": "test-bucket",
            "gatekeeperOutput": {"confirmed_mapping": _CONFIRMED_MAPPING},
        }

    def test_csv_job_produces_nonzero_transactions(self):
        with (
            patch("src.extractor.handler.parse_csv_transactions") as mock_parse,
            patch("src.extractor.handler.update_job_status") as mock_status,
        ):
            from src.extractor.schemas import RawTransaction

            mock_parse.return_value = [
                RawTransaction(raw_merchant="Starbucks", raw_amount="5.25", raw_date="2026-04-01")
            ]
            result = ingestion_handler(self._csv_event(), MagicMock())

        assert len(result["raw_transactions"]) == 1
        mock_parse.assert_called_once_with("test-bucket", "stmt.csv", _CONFIRMED_MAPPING)
        mock_status.assert_called_with("job-1", "user-1", PipelineStatus.INGESTING)

    def test_csv_job_never_calls_textract_waterfall(self):
        with (
            patch("src.extractor.handler.parse_csv_transactions", return_value=[]),
            patch("src.extractor.handler.update_job_status"),
            patch("src.extractor.handler.extract_transactions") as mock_extract,
        ):
            ingestion_handler(self._csv_event(), MagicMock())

        mock_extract.assert_not_called()

    def test_malformed_gatekeeper_output_fails_the_job(self):
        # Neither "confirmed_mapping" (CSV) nor a usable "statement_format" (PDF/Image) —
        # the handler can't route this event at all, and must still record FAILED rather than
        # raising past update_job_status uncaught.
        event = self._csv_event()
        event["gatekeeperOutput"] = {}

        with patch("src.extractor.handler.update_job_status") as mock_status:
            with pytest.raises(KeyError):
                ingestion_handler(event, MagicMock())

        args, kwargs = mock_status.call_args
        assert args[2] == PipelineStatus.FAILED


class TestIngestionHandlerErrorHandling:
    def test_extraction_failure_records_failed_status_and_reraises(self):
        event = {
            "job_id": "job-2",
            "key": "stmt.pdf",
            "user_id": "user-1",
            "statement_id": "stmt-2",
            "account_id": "acct-1",
            "bucket": "test-bucket",
            "gatekeeperOutput": {"statement_format": "PDF", "page_count": 1},
        }

        with (
            patch("src.extractor.handler.extract_transactions", side_effect=RuntimeError("Textract boom")),
            patch("src.extractor.handler.update_job_status") as mock_status,
        ):
            with pytest.raises(RuntimeError):
                ingestion_handler(event, MagicMock())

        mock_status.assert_called_once()
        args, kwargs = mock_status.call_args
        assert args[2] == PipelineStatus.FAILED
        assert "Textract boom" in kwargs.get("error", "")
