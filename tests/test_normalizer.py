"""Unit tests for the Normalizer and Gatekeeper services.

Categorization needs no AWS calls (REQ-DP-09), so no DynamoDB/Comprehend mocking.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal

from src.extractor.schemas import RawTransaction, TextractOutput
from src.categorizer.service import _regex_categorize
from src.normalizer.service import (
    _clean_merchant,
    _parse_amount,
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
        assert _clean_merchant("  SHELL   OIL  ") == "shell oil"


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
        assert result == ("Dining", "Delivery")

    def test_uber(self):
        result = _regex_categorize("uber trip")
        assert result == ("Transportation", "Rideshare")

    def test_amazon(self):
        assert _regex_categorize("amazon purchase") == ("Shopping", "General Retail")

    def test_amzn_mktp(self):
        assert _regex_categorize("amzn mktp us") == ("Shopping", "General Retail")

    def test_no_match(self):
        assert _regex_categorize("local deli corner") is None


class TestNormalizeAndCategorize:
    def test_regex_path(self):
        textract_output = TextractOutput(
            job_id="job-1",
            user_id="user-1",
            statement_id="stmt-1",
            account_id="acc-1",
            raw_transactions=[
                RawTransaction(raw_merchant="UBER TRIP", raw_amount="14.50", raw_date="2026-04-01")
            ],
        )
        result = normalize_and_categorize(textract_output)

        assert len(result) == 1
        assert result[0].category == "Transportation"

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
