"""Unit tests for SigV4 request signing on internal Ledger calls — replaces the retired
internal_api_key shared secret with the Lambda's own execution-role identity, matching what
the Ledger's InternalCallerFilter expects (signed, edge-verified caller identity).

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import pytest
from botocore.credentials import Credentials

from src.shared.sigv4 import sign_headers


def _fake_session(access_key="AKIAFAKEEXAMPLE", secret_key="fakesecretexample", region="us-east-1"):
    session = MagicMock()
    session.get_credentials.return_value = Credentials(access_key, secret_key)
    session.region_name = region
    return session


class TestSignHeaders:
    def test_adds_a_sigv4_authorization_header(self):
        with patch("src.shared.sigv4.boto3.Session", return_value=_fake_session()):
            headers = sign_headers("GET", "https://ledger.example.com/api/v1/ledger/statements/internal/s1/owner", {})

        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")
        assert "X-Amz-Date" in headers

    def test_preserves_caller_supplied_headers(self):
        with patch("src.shared.sigv4.boto3.Session", return_value=_fake_session()):
            headers = sign_headers(
                "GET",
                "https://ledger.example.com/api/v1/ledger/statements/internal/s1/owner",
                {"X-Internal-User-Id": "00000000-0000-0000-0000-000000000000"},
            )

        assert headers["X-Internal-User-Id"] == "00000000-0000-0000-0000-000000000000"

    def test_different_bodies_produce_different_signatures(self):
        with patch("src.shared.sigv4.boto3.Session", return_value=_fake_session()):
            url = "https://ledger.example.com/api/v1/ledger/transactions/internal/bulk"
            headers_a = sign_headers("POST", url, {}, b'{"statementId":"a"}')
            headers_b = sign_headers("POST", url, {}, b'{"statementId":"b"}')

        assert headers_a["Authorization"] != headers_b["Authorization"]

    def test_raises_when_no_credentials_are_available(self):
        session = _fake_session()
        session.get_credentials.return_value = None
        with patch("src.shared.sigv4.boto3.Session", return_value=session):
            with pytest.raises(RuntimeError):
                sign_headers("GET", "https://ledger.example.com/internal", {})
