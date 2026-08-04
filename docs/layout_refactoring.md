# Feature-Based Layout Refactoring

## Overview

Refactored `src/` from a tech-layer layout to a feature-based (vertical slice) layout following `rules/python-feature-layout-rules.md`. This document details every issue identified against the testing criteria in `problem.txt` and the corresponding implementation changes.

---

## Final Directory Structure

```
src/
  __init__.py
  core/
    __init__.py
    config.py
    observability.py
  shared/
    __init__.py
    schemas.py
  gatekeeper/
    __init__.py
    schemas.py          # GatekeeperOutput, StatementFormat
    handler.py
    service.py          # PDF, CSV, and image (screenshot) support
  extractor/
    __init__.py
    schemas.py          # RawTransaction, StatementMetadata, TextractOutput
    handler.py
    service.py          # Textract extraction + metadata extraction/validation
  normalizer/
    __init__.py
    schemas.py          # NormalizedTransaction
    handler.py
    service.py
  categorizer/
    __init__.py
    schemas.py          # MerchantCategory, MerchantRegistryItem (NEW)
    repository.py
    service.py
  data_dispatcher/
    __init__.py
    schemas.py          # LedgerPushResult (NEW)
    handler.py
    service.py
  statement_ingestion/
    __init__.py
    handler.py
    orchestrator.py
    repository.py
    service.py          # Public contract for cross-feature access (NEW)
```

---

## Testing Criteria Audit

### Criterion 1 -- Feature folders exist

**Status:** PASS (no changes needed)

All required feature folders were already present: `gatekeeper/`, `extractor/`, `normalizer/`, `categorizer/`, `data_dispatcher/`, `statement_ingestion/`.

---

### Criterion 2 -- Gatekeeper: PDF, CSV, and screenshot support

**Status:** FAILED -> FIXED

**Issue:** The gatekeeper only handled PDF (via `_is_pdf` magic-byte check) and images (fallback). CSV bank statements were not supported.

**Changes:**

- **`gatekeeper/schemas.py`** -- Added `StatementFormat` enum (`PDF`, `CSV`, `IMAGE`) and new fields `statement_format` and `csv_s3_key` on `GatekeeperOutput`.
- **`gatekeeper/service.py`** -- Added:
  - `_is_csv(s3_key, data)` -- detects CSV by extension and content sniffing via `csv.Sniffer`.
  - `_csv_has_valid_transactions(data)` -- validates that a CSV has required headers (`date`, `merchant`, `amount`) and at least one data row.
  - `_detect_format(s3_key, file_bytes)` -- returns the `StatementFormat` for the uploaded file.
  - Updated `run_gatekeeper()` to branch on format: CSV files are validated via header/row checks; PDF and image files go through YOLOv8-Nano table detection as before.

---

### Criterion 3 -- Extractor: Textract extraction + metadata validation

**Status:** FAILED -> FIXED

**Issue:** The extractor invoked Textract and parsed transaction rows, but did not extract or validate bank statement metadata (bank name, opening date, closing date, total purchases, total credits, previous balance).

**Changes:**

- **`extractor/schemas.py`** -- Added:
  - `StatementMetadata` model with fields: `bank_name`, `opening_date`, `closing_date`, `total_purchases`, `total_credits`, `previous_balance`. Includes a `model_validator` that rejects empty required fields.
  - `RawTransaction` now has a `model_validator` that rejects empty `raw_merchant` or `raw_amount`.
  - `TextractOutput` gained an optional `metadata: StatementMetadata` field.
- **`extractor/service.py`** -- Added:
  - `_METADATA_PATTERNS` -- regex patterns for each metadata field (bank names, date ranges, totals, previous balance).
  - `_extract_full_text(blocks)` -- concatenates all Textract `LINE` blocks into a text blob.
  - `_extract_metadata(full_text)` -- applies regex patterns and returns a validated `StatementMetadata` or `None`.
  - `extract_transactions()` now extracts metadata from the first page and includes it in the output. Transaction row parsing also catches `ValidationError` for invalid rows.

---

### Criterion 4 -- Normalizer: Standardizes to NormalizedTransaction schema

**Status:** PASS (no changes needed)

The `normalizer/schemas.py` already defines `NormalizedTransaction` and `normalizer/service.py` standardizes raw transactions (merchant cleaning, amount parsing, type detection).

Minor update: `normalize_and_categorize()` now consumes the updated `MerchantCategory` return type from the categorizer service (accessing `.category` and `.sub_category` attributes instead of tuple unpacking).

---

### Criterion 5 -- Categorization logic under categorizer/

**Status:** FAILED -> FIXED

**Issue:** The categorizer feature had no `schemas.py`. Per criterion 9, all schemas related to a feature should be in its own `schemas.py`.

**Changes:**

- **`categorizer/schemas.py`** (NEW) -- Created with:
  - `MerchantCategory` -- result model with `category`, `sub_category`, `confidence`, and `source` fields.
  - `MerchantRegistryItem` -- typed representation of a DynamoDB merchant registry record.
- **`categorizer/service.py`** -- `categorize_merchant()` now returns a `MerchantCategory` object instead of a raw `tuple[str, str]`, providing structured data with provenance tracking (`source` field: `REGEX`, `CACHE`, or `COMPREHEND`).

---

### Criterion 6 -- Data dispatcher sends to REST API

**Status:** FAILED -> FIXED

**Issue:** The data dispatcher feature had no `schemas.py`.

**Changes:**

- **`data_dispatcher/schemas.py`** (NEW) -- Created with `LedgerPushResult` model (`job_id`, `success_count`, `total_count`, `all_succeeded`).
- **`data_dispatcher/service.py`** -- `push_transactions_to_ledger()` now returns a `LedgerPushResult` instead of a raw tuple.
- **`data_dispatcher/handler.py`** -- Updated to call `result.model_dump()` on the new return type.

---

### Criterion 7 -- Statement ingestion orchestrates workflows

**Status:** PASS (no changes needed)

`statement_ingestion/orchestrator.py` already handles S3 events and starts the Step Functions state machine.

---

### Criterion 8 -- statement_ingestion/orchestrator coordinates workflow

**Status:** PASS (no changes needed)

The orchestrator reads S3 event metadata, constructs the payload, calls `sfn_client.start_execution()`, and tracks job status.

---

### Criterion 9 -- Schemas/models/routes under feature folders

**Status:** FAILED -> FIXED

**Issue:** `categorizer/` and `data_dispatcher/` had no `schemas.py`. Additionally, all feature handlers (`gatekeeper`, `extractor`, `normalizer`, `data_dispatcher`) imported `statement_ingestion.repository.update_job_status` directly -- a cross-feature repository import that violates Rule 2 (Strict Feature Boundaries) of `python-feature-layout-rules.md`.

**Changes:**

- Created `categorizer/schemas.py` and `data_dispatcher/schemas.py` (detailed above).
- **`statement_ingestion/service.py`** (NEW) -- Public service contract that wraps `repository.update_job_status`. This is the only module other features should import.
- Updated all four feature handlers plus `statement_ingestion/orchestrator.py` to import from `statement_ingestion.service` instead of `statement_ingestion.repository`.

---

## Rule Compliance Summary

| Rule | Description | Status |
|------|-------------|--------|
| 1. High Cohesion, Low Coupling | Each feature is self-contained in its own folder | PASS |
| 2. Strict Feature Boundaries | Cross-feature access goes through services, not repositories | FIXED |
| 3. Core is for Plumbing | `core/` contains only `config.py` and `observability.py` | PASS |
| 4. Protect API Contract with DTOs | All features use Pydantic schemas for input/output | FIXED |
| 5. Conscious use of shared/ | `shared/schemas.py` contains only cross-cutting primitives (`PipelineStatus`, `S3EventRecord`, `JobTrackerItem`) | PASS |
| 6. Dependency Injection | Services receive dependencies via parameters | PASS |
| 7. Model Segregation | Each feature owns its own schemas/models | FIXED |

---

## Files Modified

| File | Change |
|------|--------|
| `src/gatekeeper/schemas.py` | Added `StatementFormat` enum, `statement_format` and `csv_s3_key` fields |
| `src/gatekeeper/service.py` | Added CSV detection, CSV validation, format detection, updated `run_gatekeeper()` |
| `src/extractor/schemas.py` | Added `StatementMetadata`, `RawTransaction` validator, `metadata` field on `TextractOutput` |
| `src/extractor/service.py` | Added metadata extraction/validation, transaction row validation |
| `src/extractor/handler.py` | Import changed from `.repository` to `.service` |
| `src/normalizer/service.py` | Updated to consume `MerchantCategory` object |
| `src/normalizer/handler.py` | Import changed from `.repository` to `.service` |
| `src/categorizer/service.py` | Returns `MerchantCategory` instead of tuple |
| `src/data_dispatcher/service.py` | Returns `LedgerPushResult` instead of tuple |
| `src/data_dispatcher/handler.py` | Updated to use `result.model_dump()`, import fix |
| `src/gatekeeper/handler.py` | Import changed from `.repository` to `.service` |
| `src/statement_ingestion/orchestrator.py` | Import changed from `.repository` to `.service` |

## Files Created

| File | Purpose |
|------|---------|
| `src/categorizer/schemas.py` | `MerchantCategory`, `MerchantRegistryItem` models |
| `src/data_dispatcher/schemas.py` | `LedgerPushResult` model |
| `src/statement_ingestion/service.py` | Public service contract exposing `update_job_status` |
