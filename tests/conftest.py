"""Shared pytest fixtures.

__author__ = "Van Vo"
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _configured_ledger_url():
    """The dispatcher refuses to push without LEDGER_API_URL (LedgerNotConfiguredError), so
    tests get a placeholder host. Tests of the unconfigured case patch it back to ""."""
    with patch("src.data_dispatcher.service._LEDGER_API_URL", "https://ledger.test"):
        yield
