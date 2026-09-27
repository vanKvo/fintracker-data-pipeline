"""DynamoDB CRUD for the Bank Column Mapping table.

Runtime-writable by design (REQ-DP-01 B. Constraints): a per-bank mapping
committed as a bundled JSON file could not incorporate a user's manual
correction without a redeploy. Seeded with a baseline mapping, then updated
in place whenever a user confirms a correction in the mapping dialog.

__author__ = "Van Vo"
"""

from __future__ import annotations

import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..core.observability import logger

_dynamodb = boto3.resource("dynamodb")

_BANK_MAPPING_TABLE_NAME = os.environ.get("BANK_MAPPING_TABLE", "FinTracker_BankMapping")
_bank_mapping_table = _dynamodb.Table(_BANK_MAPPING_TABLE_NAME)

# Canonical fields every bank mapping must eventually resolve.
CANONICAL_FIELDS = ("date", "merchant", "amount")


def get_bank_mapping(bank_id: str) -> Optional[dict[str, list[str]]]:
    """Look up a bank's known column-name variants.

    Layers the DynamoDB-stored corrections (from confirmed user mappings)
    on top of the bundled static baseline (bank_mapping_baseline.py,
    seeded from bank_mappings.json) — DynamoDB always wins for a field
    both sources define, since a user's confirmed correction is more
    trustworthy than the static seed.

    Args:
        bank_id: Stable identifier for the bank institution (e.g. "chase").

    Returns:
        A dict of canonical field -> known header variants (lowercased),
        or None if this bank is in neither source (REQ-DP-01 F. Error
        Handling — UNKNOWN_BANK_MAPPING is not fatal, the caller still
        opens the dialog with everything unmapped).
    """
    # Imported lazily to avoid a circular import (bank_mapping_baseline
    # imports CANONICAL_FIELDS from this module).
    from .bank_mapping_baseline import load_baseline_mappings

    baseline = load_baseline_mappings().get(bank_id)

    try:
        response = _bank_mapping_table.get_item(Key={"PK": f"BANKMAP#{bank_id}", "SK": "DETAILS"})
        stored = response.get("Item", {}).get("variants")
    except ClientError as e:
        logger.warning("Bank mapping lookup failed, using baseline only", bank_id=bank_id, error=str(e))
        stored = None

    if not baseline and not stored:
        return None

    merged: dict[str, list[str]] = {field: [] for field in CANONICAL_FIELDS}
    for source in (baseline, stored):
        if not source:
            continue
        for field in CANONICAL_FIELDS:
            merged[field] = sorted(set(merged[field]) | set(source.get(field, [])))
    return merged


def save_bank_mapping_correction(bank_id: str, canonical_field: str, header_variant: str) -> None:
    """Persist a user-confirmed column mapping so future uploads for this
    bank resolve it automatically.

    Idempotent — adding a variant that's already recorded is a no-op.

    Args:
        bank_id: Stable identifier for the bank institution.
        canonical_field: One of CANONICAL_FIELDS.
        header_variant: The actual column header the bank used, lowercased
            and stripped before storage.
    """
    if canonical_field not in CANONICAL_FIELDS:
        raise ValueError(f"Unknown canonical field: {canonical_field}")

    normalized_variant = header_variant.strip().lower()
    existing = get_bank_mapping(bank_id) or {field: [] for field in CANONICAL_FIELDS}
    known_variants = set(existing.get(canonical_field, []))

    if normalized_variant in known_variants:
        return

    known_variants.add(normalized_variant)
    existing[canonical_field] = sorted(known_variants)

    _bank_mapping_table.put_item(
        Item={
            "PK": f"BANKMAP#{bank_id}",
            "SK": "DETAILS",
            "bank_id": bank_id,
            "variants": existing,
        }
    )
    logger.info(
        "Bank column mapping updated",
        bank_id=bank_id,
        canonical_field=canonical_field,
        header_variant=normalized_variant,
    )
