"""REQ-DP-09: categorization without Comprehend or a shared merchant cache.

Order is bank category (translated) -> regex -> "Uncategorized".

__author__ = "Van Vo"
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from unittest.mock import patch

import boto3
from moto import mock_aws

import src.categorizer.service as categorizer_service
from src.categorizer.service import _REGEX_MAP, categorize_merchant
from src.extractor.schemas import RawTransaction, TextractOutput
from src.extractor.service import parse_csv_transactions
from src.normalizer.service import normalize_and_categorize

# Mirrors the Ledger's TransactionCategory labels — anything else collapses to "Others" there.
_LEDGER_LABELS = {
    "Groceries", "Dining", "Transportation", "Shopping", "Entertainment", "Utilities",
    "Housing", "Healthcare", "Insurance", "Subscriptions", "Travel", "Education",
    "Personal Care", "Income", "Transfer", "Fees", "Others",
}

_CONFIRMED_MAPPING = {"date": "Transaction Date", "merchant": "Description", "amount": "Amount"}


class TestReqDp09NoPaidClassifier:
    def test_categorizer_has_no_comprehend_client(self):
        assert "comprehend" not in inspect.getsource(categorizer_service).lower()

    def test_categorizer_does_not_use_a_shared_merchant_cache(self):
        assert not hasattr(categorizer_service, "lookup_merchant")
        assert not hasattr(categorizer_service, "cache_merchant")

    def test_unknown_merchant_is_uncategorized(self):
        result = categorize_merchant("local deli corner")
        assert result.category == "Uncategorized"
        assert result.source == "NONE"


class TestReqDp09BankCategory:
    def test_bank_label_is_translated(self):
        result = categorize_merchant("pho ga vang va", bank_category="Food & Drink")
        assert result.category == "Dining"
        assert result.source == "BANK"

    def test_bank_category_wins_over_regex(self):
        assert categorize_merchant("uber trip", bank_category="Travel").category == "Travel"

    def test_bank_label_match_ignores_case_and_whitespace(self):
        assert categorize_merchant("x", bank_category="  bills & utilities ").category == "Utilities"

    def test_unknown_bank_label_falls_back_to_regex(self):
        result = categorize_merchant("starbucks", bank_category="Professional Services")
        assert result.category == "Dining"
        assert result.source == "REGEX"

    def test_blank_bank_label_falls_back_to_uncategorized(self):
        assert categorize_merchant("local deli", bank_category="").category == "Uncategorized"


class TestReqDp09LedgerLabels:
    def test_every_regex_category_is_a_ledger_label(self):
        assert {category for _, category, _ in _REGEX_MAP} <= _LEDGER_LABELS

    def test_every_bank_translation_is_a_ledger_label(self):
        from src.categorizer.bank_categories import BANK_CATEGORY_MAP

        assert set(BANK_CATEGORY_MAP.values()) <= _LEDGER_LABELS


@mock_aws
class TestReqDp09CsvCategoryColumn:
    def setup_method(self, method):
        os.environ["JOB_TRACKER_TABLE"] = "FinTracker_JobTracker_Test"
        self.bucket = "test-bucket"
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket=self.bucket)

    def test_category_column_is_carried_on_each_row(self):
        body = (
            b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
            b"08/01/2026,08/02/2026,LIDL #1112,Groceries,Sale,-42.10,\n"
        )
        self.s3.put_object(Bucket=self.bucket, Key="chase.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            rows = parse_csv_transactions(self.bucket, "chase.csv", _CONFIRMED_MAPPING)

        assert rows[0].raw_category == "Groceries"

    def test_csv_without_category_column_has_no_bank_category(self):
        body = b"Transaction Date,Description,Amount\n08/01/2026,LIDL #1112,-42.10\n"
        self.s3.put_object(Bucket=self.bucket, Key="plain.csv", Body=body)

        with patch("src.extractor.service._s3_client", self.s3):
            rows = parse_csv_transactions(self.bucket, "plain.csv", _CONFIRMED_MAPPING)

        assert rows[0].raw_category is None


class TestReqDp09NormalizerUsesBankCategory:
    def test_row_bank_category_drives_the_category(self):
        output = TextractOutput(
            job_id="job-1", user_id="user-1", statement_id="stmt-1", account_id="acc-1",
            raw_transactions=[
                RawTransaction(raw_merchant="TARGET 00012345", raw_amount="25.00", raw_date="2026-08-01", raw_category="Groceries")
            ],
        )
        result = normalize_and_categorize(output)
        assert result[0].category == "Groceries"


class TestReqDp09ChaseBaselineMapping:
    def test_chase_category_column_is_category_not_type(self):
        mappings = json.loads((Path(__file__).parent.parent / "src/gatekeeper/bank_mappings.json").read_text())
        chase = mappings["banks"]["chase"]["field_mappings"]
        assert chase.get("Category") == "category"
        assert chase.get("Type") != "category"
