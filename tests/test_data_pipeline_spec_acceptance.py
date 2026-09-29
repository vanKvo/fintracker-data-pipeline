"""Acceptance tests for requirements in docs/fintracker-data-pipelines/data-pipeline-spec-01.md,
spanning gatekeeper, extractor, normalizer, data_dispatcher, and ledger_client.

Each class was written F2P ("fail to pass") against its requirement before that
requirement's implementation landed. REQ-DP-03 is still red (not yet implemented);
the rest are now green — see data-pipeline-tests-01.md for the requirement each maps to.

__author__ = "Van Vo"
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.normalizer.schemas import NormalizedTransaction
from src.shared.schemas import PipelineStatus


class TestReqDp01BankSpecificColumnMapping:
    def test_bank_variant_headers_are_accepted_via_mapping(self):
        """A bank exporting `Transaction Date`/`Description`/`Debit` should
        map to the canonical fields instead of being rejected outright.

        This used to assert against _csv_has_valid_transactions (a plain structural
        True/False gate that predates the bank-specific mapping feature entirely — see
        tests/test_gatekeeper.py's TestCsvStructuralValidation for its current equivalent,
        _read_csv_headers_and_row_count). The actual REQ-DP-01 behavior this test names —
        resolving bank-variant headers to canonical fields — now lives in
        propose_column_mapping, so that's what this asserts against instead.
        """
        import os

        import boto3
        from moto import mock_aws

        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

        with mock_aws():
            from unittest.mock import patch

            from src.gatekeeper import mapping_repository
            from src.gatekeeper.service import propose_column_mapping

            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            mapping_repository._bank_mapping_table = dynamodb.create_table(
                TableName="BankMapping",
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

            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="test-bucket")
            s3.put_object(
                Bucket="test-bucket",
                Key="stmt.csv",
                Body=b"Transaction Date,Description,Amount\n2026-04-01,Starbucks,5.25\n",
            )

            with patch("src.gatekeeper.service._s3_client", s3):
                proposal = propose_column_mapping("test-bucket", "stmt.csv", "chase")

        # "chase" is seeded in the bundled baseline (bank_mapping_baseline.py) with exactly
        # these variants ("post date"/"posting date"/"transaction date" -> date, "description"
        # -> merchant, "amount" -> amount), so this resolves without any prior DynamoDB
        # correction.
        assert proposal.mapped == {"date": "Transaction Date", "merchant": "Description", "amount": "Amount"}
        assert proposal.unmapped_columns == []


class TestReqDp01CsvColMappingConfirmationDialog:
    def test_pending_csv_col_mapping_confirmation_status_exists(self):
        assert hasattr(PipelineStatus, "PENDING_CSV_COL_MAPPING_CONFIRMATION")

    def test_propose_column_mapping_function_exists(self):
        import src.gatekeeper.service as gk_service

        assert hasattr(gk_service, "propose_column_mapping")

    def test_confirm_column_mapping_function_exists(self):
        import src.gatekeeper.service as gk_service

        assert hasattr(gk_service, "confirm_column_mapping")


class TestReqDp01TieredPdfExtraction:
    def test_text_layer_extraction_function_exists(self):
        import src.extractor.service as extractor_service

        assert hasattr(extractor_service, "extract_from_text_layer")

    def test_textract_is_not_called_unconditionally_for_pdf(self):
        """Tier 1 (text layer) should be attempted before Tier 2 (Textract)
        — today Textract is the only path for PDF/Image, with no text-layer
        short-circuit."""
        import src.extractor.service as extractor_service

        source = inspect.getsource(extractor_service.extract_transactions)
        assert "fitz" in source or "text_layer" in source


class TestReqDp01ConfidenceScoring:
    def test_normalized_transaction_carries_confidence(self):
        tx = NormalizedTransaction(
            account_id="acct-1",
            statement_id="stmt-1",
            merchant="starbucks",
            amount=5.25,
            tx_date="2026-04-01",
            category="Food & Drink",
            row_fingerprint="a" * 64,
        )
        assert hasattr(tx, "confidence")


class TestReqDp02ParallelPageExtraction:
    def test_extraction_uses_concurrency_primitive(self):
        """Pages should be processed concurrently, not in a plain
        sequential for-loop over page_keys.

        extract_transactions itself delegates Tier 2 page processing to
        _run_tier2_concurrently (extractor/service.py) rather than looping inline — checking
        only extract_transactions's own source text for "concurrent.futures"/"asyncio" would
        always fail regardless of whether the underlying work is concurrent, since neither
        string appears in a function that only calls a helper. Checking the module's full
        source (module-level import plus every function reachable from it) reflects what's
        actually implemented instead of one function's literal text.
        """
        import inspect as _inspect

        import src.extractor.service as extractor_service

        module_source = _inspect.getsource(extractor_service)
        assert "ThreadPoolExecutor" in module_source


class TestReqDp02BatchedLedgerPush:
    def test_ledger_push_uses_batch_endpoint(self):
        """REQ-DP-02 offered two ways to satisfy "Parallel/Batched Ledger Push": concurrent
        individual calls, or one batch call. The real Ledger endpoint that got built
        (InternalTransactionController#bulkCreate) is the batch option — one POST covers a
        whole statement's transactions, which is what actually cuts HTTP overhead, so
        push_transactions_to_ledger no longer uses ThreadPoolExecutor at all (see
        tests/test_data_dispatcher_ledger_push.py::test_one_batch_call_covers_every_transaction
        for the behavioral proof this file's function-existence style can't express)."""
        import src.data_dispatcher.service as dispatcher_service

        assert hasattr(dispatcher_service, "_push_bulk")


class TestReqDp03LedgerPushIdempotency:
    def test_repeated_push_of_same_job_does_not_duplicate_ledger_calls(self):
        """Re-running the same job (e.g. a retried Step Functions task, or
        a duplicate S3 event triggering the whole pipeline twice) should
        not create duplicate Ledger transactions on the second attempt."""
        from src.data_dispatcher.service import push_transactions_to_ledger

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"insertedCount": 1, "skippedDuplicateCount": 0, "failedRows": []}
        tx = {
            "tx_date": "2026-04-01",
            "merchant": "starbucks",
            "amount": "5.25",
            "category": "Food & Drink",
            "sub_category": None,
            "type": "SALE",
            "row_fingerprint": "a" * 64,
        }

        with patch("src.data_dispatcher.service._requests.post", return_value=mock_resp) as mock_post:
            push_transactions_to_ledger("job-1", "user-1", "stmt-1", [tx])
            push_transactions_to_ledger("job-1", "user-1", "stmt-1", [tx])

        assert mock_post.call_count == 1


class TestReqDp05TenantIdentityVerification:
    def test_statement_owner_lookup_function_exists(self):
        """user_id/account_id should be resolved from a server-side record
        keyed by statement_id, not trusted directly from S3 object metadata
        set at upload time.

        Implemented as ledger_client.get_verified_statement_owner (calling the Ledger's new
        GET /api/v1/ledger/statements/internal/{id}/owner) rather than a
        verify_statement_owner function directly on the orchestrator module, as this class's
        original sketch guessed — same intent, real name.
        """
        import src.statement_ingestion.ledger_client as ledger_client

        assert hasattr(ledger_client, "get_verified_statement_owner")

    def test_s3_processor_does_not_trust_metadata_user_id_directly(self):
        import src.statement_ingestion.orchestrator as orchestrator

        source = inspect.getsource(orchestrator.s3_processor_handler)
        assert 'meta.get("user-id"' not in source
        assert 'meta.get("account-id"' not in source


@pytest.mark.skip(
    reason="REQ-DP-07 (least-privilege IAM) is an AWS CDK infrastructure "
    "concern — pipeline_stack.py IAM policy statements are not exercised "
    "by this Python unit-test suite. Verify via `cdk synth` + a "
    "cdk.assertions.Template snapshot test in the infrastructure package, "
    "or manual IAM policy review."
)
def test_iam_policies_are_least_privilege():
    pass
