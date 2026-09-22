"""Unit tests for the Gatekeeper service.

Covers format detection, REQ-DP-04 resource caps, CSV structural
validation, and REQ-DP-01's "User Mapping Confirmation Gate" — proposing
and confirming a CSV's column mapping.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.gatekeeper.schemas import GatekeeperOutput, StatementFormat
from src.gatekeeper.service import (
    _detect_format,
    _is_csv,
    _is_pdf,
    _read_csv_headers_and_row_count,
    confirm_column_mapping,
    propose_column_mapping,
    run_gatekeeper,
)
from src.shared.exceptions import (
    FileTooLargeError,
    InvalidCsvFormatError,
    MappingConfirmationTimeoutError,
    UnsupportedFormatError,
)
from src.shared.schemas import PipelineStatus


def _valid_csv_bytes() -> bytes:
    return b"date,merchant,amount\n2026-04-01,Starbucks,5.25\n"


def _mock_table(name: str) -> "boto3.resources.factory.dynamodb.Table":
    """Creates a fresh PK/SK table in the active moto mock and returns it — the module-level
    `_bank_mapping_table` / `_job_tracker_table` attributes are resolved once, at import time,
    from whatever BANK_MAPPING_TABLE/JOB_TRACKER_TABLE env var was set then (see each
    repository module), so setting the env var inside a test's setup_method is too late to
    affect them. Callers monkeypatch the module attribute directly onto this table instead —
    the same pattern test_normalizer.py uses."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    return dynamodb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


class TestFormatDetection:
    def test_pdf_magic_bytes(self):
        assert _is_pdf(b"%PDF-1.4 ...") is True

    def test_not_pdf(self):
        assert _is_pdf(b"not a pdf") is False

    def test_csv_by_extension(self):
        assert _is_csv("statement.csv", b"anything") is True

    def test_csv_by_content_sniff(self):
        assert _is_csv("upload.dat", _valid_csv_bytes()) is True

    def test_detect_format_pdf(self):
        assert _detect_format("statement.pdf", b"%PDF-1.4") == StatementFormat.PDF

    def test_detect_format_csv(self):
        assert _detect_format("statement.csv", _valid_csv_bytes()) == StatementFormat.CSV

    def test_detect_format_image_fallback(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 20
        assert _detect_format("photo.jpg", png_bytes) == StatementFormat.IMAGE

    @pytest.mark.xfail(reason="csv.Sniffer() false-positives on some binary content — REQ-DP-01 Format Detection gap, not yet fixed")
    def test_detect_format_binary_image_not_misclassified_as_csv(self):
        binary_jpeg_like = b"\xff\xd8\xff\xe0binarydata"
        assert _detect_format("photo.jpg", binary_jpeg_like) == StatementFormat.IMAGE


class TestCsvStructuralValidation:
    """_read_csv_headers_and_row_count is the structural gate before a column mapping is even
    proposed — it doesn't care what the headers are named, only that there's a usable header
    row and at least one data row (REQ-DP-01's per-bank mapping means exact header names
    aren't known yet at this stage)."""

    def test_valid_headers_and_row(self):
        headers, row_count = _read_csv_headers_and_row_count(_valid_csv_bytes())
        assert headers == ["date", "merchant", "amount"]
        assert row_count == 1

    def test_arbitrary_bank_specific_headers_are_still_structurally_valid(self):
        # Whether "Transaction Date"/"Description"/"Debit" MAP to canonical fields is
        # propose_column_mapping's job, not this structural gate's.
        data = b"Transaction Date,Description,Debit\n2026-04-01,Starbucks,5.25\n"
        headers, row_count = _read_csv_headers_and_row_count(data)
        assert headers == ["Transaction Date", "Description", "Debit"]
        assert row_count == 1

    def test_single_column_raises(self):
        with pytest.raises(InvalidCsvFormatError):
            _read_csv_headers_and_row_count(b"onlyonecolumn\nvalue\n")

    def test_headers_only_no_rows_raises(self):
        with pytest.raises(InvalidCsvFormatError):
            _read_csv_headers_and_row_count(b"date,merchant,amount\n")


@mock_aws
class TestProposeColumnMapping:
    def setup_method(self, method):
        from src.gatekeeper import mapping_repository

        mapping_repository._bank_mapping_table = _mock_table("BankMapping")

        self.bucket = "test-bucket"
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket=self.bucket)

    def test_implicit_match_for_unknown_bank(self):
        # No DynamoDB row and no baseline entry for this bank_id — headers that already match
        # a canonical field name literally (date/merchant/amount) must still resolve via
        # _IMPLICIT_VARIANTS, so a plain CSV isn't rejected just for being from an unseeded bank.
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=_valid_csv_bytes())

        with patch("src.gatekeeper.service._s3_client", self.s3):
            proposal = propose_column_mapping(self.bucket, "stmt.csv", "unknown_bank_xyz")

        assert proposal.mapped == {"date": "date", "merchant": "merchant", "amount": "amount"}
        assert proposal.unmapped_columns == []
        assert proposal.is_known_bank is False

    def test_unmatched_headers_are_reported_unmapped(self):
        body = b"Transaction Date,Description,Debit\n2026-04-01,Starbucks,5.25\n"
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=body)

        with patch("src.gatekeeper.service._s3_client", self.s3):
            proposal = propose_column_mapping(self.bucket, "stmt.csv", "unknown_bank_xyz")

        assert proposal.mapped == {}
        assert set(proposal.unmapped_columns) == {"Transaction Date", "Description", "Debit"}

    def test_stored_dynamo_correction_is_applied(self):
        # A prior confirm_column_mapping call for this bank already taught the system that
        # "Transaction Date" means "date" — a fresh upload from the same bank should resolve it
        # automatically instead of asking the user again. Deliberately a bank_id NOT in the
        # bundled baseline (bank_mappings.json has no "chase"-style entry for it), so a match
        # here can only come from the DynamoDB write this test makes, not baseline luck.
        from src.gatekeeper.mapping_repository import save_bank_mapping_correction

        save_bank_mapping_correction("unseeded_test_bank", "date", "transaction date")

        body = b"Transaction Date,merchant,amount\n2026-04-01,Starbucks,5.25\n"
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=body)

        with patch("src.gatekeeper.service._s3_client", self.s3):
            proposal = propose_column_mapping(self.bucket, "stmt.csv", "unseeded_test_bank")

        assert proposal.mapped["date"] == "Transaction Date"
        assert proposal.is_known_bank is True


@mock_aws
class TestConfirmColumnMapping:
    def setup_method(self, method):
        from src.gatekeeper import mapping_repository
        from src.statement_ingestion import repository as ingestion_repo

        mapping_repository._bank_mapping_table = _mock_table("BankMapping")
        ingestion_repo._job_tracker_table = _mock_table("JobTracker")

    def test_confirming_resumes_the_paused_execution(self):
        from src.statement_ingestion.service import get_job, update_job_status

        update_job_status("job-1", "user-1", PipelineStatus.PENDING_MAPPING_CONFIRMATION, task_token="token-1")

        with patch("src.gatekeeper.service._sfn_client") as mock_sfn:
            confirm_column_mapping("job-1", "chase", {"date": "Transaction Date", "merchant": "Description", "amount": "Debit"})

        mock_sfn.send_task_success.assert_called_once()
        _, kwargs = mock_sfn.send_task_success.call_args
        assert kwargs["taskToken"] == "token-1"

        job = get_job("job-1")
        assert job["status"] == str(PipelineStatus.GATEKEEPER_PASSED)

    def test_missing_canonical_field_raises(self):
        with pytest.raises(InvalidCsvFormatError):
            confirm_column_mapping("job-1", "chase", {"date": "Transaction Date"})

    def test_job_with_no_pending_confirmation_raises_timeout(self):
        # No update_job_status call at all for this job_id — get_job returns None.
        with pytest.raises(MappingConfirmationTimeoutError):
            confirm_column_mapping(
                "never-paused-job", "chase", {"date": "d", "merchant": "m", "amount": "a"}
            )


@mock_aws
class TestRunGatekeeperCsv:
    def setup_method(self, method):
        os.environ["BANK_MAPPING_TABLE"] = "FinTracker_BankMapping_Test"
        self.bucket = "test-bucket"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=self.bucket)
        self.s3 = s3

    def test_valid_csv_requires_mapping_confirmation(self):
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=_valid_csv_bytes())
        with patch("src.gatekeeper.service._s3_client", self.s3):
            output = run_gatekeeper(
                bucket=self.bucket,
                s3_key="stmt.csv",
                job_id="job-1",
                user_id="user-1",
                statement_id="stmt-1",
                account_id="acct-1",
                bank_id="chase",
            )
        assert output.requires_mapping_confirmation is True
        assert output.mapping_proposal is not None
        assert output.csv_s3_key == "stmt.csv"
        assert output.statement_format == StatementFormat.CSV

    def test_csv_without_bank_id_raises(self):
        self.s3.put_object(Bucket=self.bucket, Key="stmt.csv", Body=_valid_csv_bytes())
        with patch("src.gatekeeper.service._s3_client", self.s3):
            with pytest.raises(InvalidCsvFormatError):
                run_gatekeeper(
                    bucket=self.bucket,
                    s3_key="stmt.csv",
                    job_id="job-1",
                    user_id="user-1",
                    statement_id="stmt-1",
                    account_id="acct-1",
                    bank_id=None,
                )

    def test_structurally_invalid_csv_raises(self):
        self.s3.put_object(Bucket=self.bucket, Key="bad.csv", Body=b"onlyonecolumn\nvalue\n")
        with patch("src.gatekeeper.service._s3_client", self.s3):
            with pytest.raises(InvalidCsvFormatError):
                run_gatekeeper(
                    bucket=self.bucket,
                    s3_key="bad.csv",
                    job_id="job-2",
                    user_id="user-1",
                    statement_id="stmt-2",
                    account_id="acct-1",
                    bank_id="chase",
                )


@mock_aws
class TestRunGatekeeperResourceCaps:
    def setup_method(self, method):
        os.environ["BANK_MAPPING_TABLE"] = "FinTracker_BankMapping_Test"
        self.bucket = "test-bucket"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=self.bucket)
        self.s3 = s3

    def test_oversized_file_rejected_before_processing(self):
        oversized = b"%PDF-1.4" + b"0" * (26 * 1024 * 1024)
        self.s3.put_object(Bucket=self.bucket, Key="huge.pdf", Body=oversized)
        with patch("src.gatekeeper.service._s3_client", self.s3):
            with pytest.raises(FileTooLargeError):
                run_gatekeeper(
                    bucket=self.bucket,
                    s3_key="huge.pdf",
                    job_id="job-3",
                    user_id="user-1",
                    statement_id="stmt-3",
                    account_id="acct-1",
                )

    def test_corrupt_image_rejected_as_unsupported_format(self):
        garbage = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 20
        self.s3.put_object(Bucket=self.bucket, Key="bad.jpg", Body=garbage)
        with patch("src.gatekeeper.service._s3_client", self.s3):
            with pytest.raises(UnsupportedFormatError):
                run_gatekeeper(
                    bucket=self.bucket,
                    s3_key="bad.jpg",
                    job_id="job-4",
                    user_id="user-1",
                    statement_id="stmt-4",
                    account_id="acct-1",
                )
