"""Static baseline bank column mappings, loaded from bank_mappings.json.

This is the deploy-time seed referenced in REQ-DP-01 C. Data Impacts: a
curated starting mapping for common US banks, bundled with the Lambda so a
first-time upload from a known bank works without waiting on a prior
DynamoDB write. mapping_repository.get_bank_mapping layers this baseline
under any DynamoDB corrections — DynamoDB always wins, since a user's
confirmed correction is more trustworthy than the static seed.

A proper production seeding path would load this file into DynamoDB once
via a CDK custom resource or a one-off migration script at deploy time,
rather than re-parsing it on every cold start; that's flagged as a
follow-up in data-pipeline-notes-01.md rather than implemented here, to
keep this change scoped to the pipeline's own request-time behavior.

__author__ = "Van Vo"
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..core.observability import logger
from .mapping_repository import CANONICAL_FIELDS

_BASELINE_FILE = Path(__file__).parent / "bank_mappings.json"

# bank_mappings.json's own field taxonomy is richer than our 3-field
# canonical scheme (it also tracks running_balance, check_number, category,
# etc.) — this is the subset we can actually use today.
_SOURCE_TO_CANONICAL = {
    "transaction_date": "date",
    "description": "description",  # translated to "merchant" below
    "amount": "amount",
}


@lru_cache(maxsize=1)
def load_baseline_mappings() -> dict[str, dict[str, list[str]]]:
    """Parse bank_mappings.json into this service's canonical field shape.

    Returns:
        bank_id -> {canonical_field: [known header variants, lowercased]}.
        A bank using split debit/credit columns instead of a single amount
        column (e.g. Citibank, Capital One) is intentionally excluded —
        REQ-DP-01's `amount` field assumes one signed column; reconciling
        split columns is a distinct feature this baseline doesn't attempt.
    """
    try:
        raw = json.loads(_BASELINE_FILE.read_text())
    except FileNotFoundError:
        logger.warning("Bank mapping baseline file not found", path=str(_BASELINE_FILE))
        return {}

    baseline: dict[str, dict[str, list[str]]] = {}
    for bank_id, bank_config in raw.get("banks", {}).items():
        field_mappings: dict[str, str] = bank_config.get("field_mappings", {})
        variants: dict[str, set[str]] = {field: set() for field in CANONICAL_FIELDS}

        for header, source_field in field_mappings.items():
            canonical = _SOURCE_TO_CANONICAL.get(source_field)
            if canonical == "description":
                variants["merchant"].add(header.strip().lower())
            elif canonical:
                variants[canonical].add(header.strip().lower())

        if all(variants[field] for field in CANONICAL_FIELDS):
            baseline[bank_id] = {field: sorted(values) for field, values in variants.items()}
        else:
            logger.info(
                "Bank excluded from baseline — uses a column shape not yet supported",
                bank_id=bank_id,
                missing_canonical_fields=sorted(f for f in CANONICAL_FIELDS if not variants[f]),
            )

    logger.info("Bank mapping baseline loaded", bank_count=len(baseline))
    return baseline
