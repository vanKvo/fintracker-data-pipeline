"""Categorizer feature schemas.

__author__ = "Van Vo"
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class MerchantCategory(BaseModel):
    """Result of merchant categorization."""

    category: str
    sub_category: str
    confidence: Optional[Decimal] = None
    source: str = "REGEX"
