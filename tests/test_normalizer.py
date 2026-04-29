"""Unit tests for the Normalizer and Gatekeeper services.

Uses moto to mock DynamoDB and avoids actual Comprehend / YOLO calls.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.schemas.pipeline import RawTransaction, TextractOutput
from src.services.normalizer_service import (
    _clean_merchant,
    _parse_amount,
    _regex_categorize,
    normalize_and_categorize,
)


class TestCleanMerchant:
    def test_strips_hash_reference(self):
        assert _clean_merchant("STARBUCKS #12345") == "starbucks"

    def test_strips_star_reference(self):
        assert _clean_merchant("AMAZON *MARKETPLACE") == "amazon *marketplace"

    def test_lowercases(self):
        assert _clean_merchant("UBER EATS") == "uber eats"

    def test_collapses_whitespace(self):
        assert _clean_merchant("  SHELL   OIL  ") == "shell   oil"


class TestParseAmount:
    def test_standard_positive(self):
        assert _parse_amount("$1,234.56") == Decimal("1234.56")

    def test_credit_parenthesis(self):
        result = _parse_amount("(50.00)")
        assert result == Decimal("-50.00")

    def test_invalid_returns_none(self):
        assert _parse_amount("N/A") is None


class TestRegexCategorize:
    def test_uber_eats_before_uber(self):
        result = _regex_categorize("uber eats order")
        assert result == ("Food & Drink", "Delivery")

    def test_uber(self):
        result = _regex_categorize("uber trip")
        assert result == ("Transportation", "Rideshare")

    def test_amazon(self):
        assert _regex_categorize("amazon purchase") == ("Shopping", "General Retail")

    def test_amzn_mktp(self):
        assert _regex_categorize("amzn mktp us") == ("Shopping", "General Retail")

    def test_no_match(self):
        assert _regex_categorize("local deli corner") is None


@mock_aws
class TestNormalizeAndCategorize:
    def setup_method(self):
        """Seed MerchantRegistry DynamoDB for registry lookup tests."""
        import os
        os.environ["PIPELINE_TABLE"] = "FinTracker_DataPipeline_Test"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="FinTracker_DataPipeline_Test",
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
        table.put_item(Item={
            "PK": "petco",
            "SK": "DETAILS",
            "category": "Services",
            "sub_category": "Pets",
            "confidence": Decimal("0.95"),
        })

    def test_regex_path_no_dynamo_call(self):
        """Uber should resolve via Regex — DynamoDB should NOT be queried."""
        textract_output = TextractOutput(
            job_id="job-1",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acc-1",
            raw_transactions=[
                RawTransaction(raw_merchant="UBER TRIP", raw_amount="14.50", raw_date="2026-04-01")
            ],
        )
        with patch("src.services.normalizer_service.lookup_merchant") as mock_lookup:
            result = normalize_and_categorize(textract_output)

        mock_lookup.assert_not_called()
        assert len(result) == 1
        assert result[0].category == "Transportation"

    def test_registry_path(self):
        """Petco in registry, should NOT call Comprehend."""
        textract_output = TextractOutput(
            job_id="job-2",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acc-1",
            raw_transactions=[
                RawTransaction(raw_merchant="petco", raw_amount="32.00", raw_date="2026-04-05")
            ],
        )
        with patch("src.services.normalizer_service._comprehend_categorize") as mock_comprehend:
            result = normalize_and_categorize(textract_output)

        mock_comprehend.assert_not_called()
        assert result[0].category == "Services"

    def test_skips_unparsable_amounts(self):
        textract_output = TextractOutput(
            job_id="job-3",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acc-1",
            raw_transactions=[
                RawTransaction(raw_merchant="STARBUCKS", raw_amount="VOID", raw_date="2026-04-01")
            ],
        )
        result = normalize_and_categorize(textract_output)
        assert result == []
