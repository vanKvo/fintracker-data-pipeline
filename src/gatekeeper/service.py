"""Gatekeeper Service — orchestrator-agnostic business logic.

Detects the uploaded statement's format (PDF, CSV, or image screenshot),
enforces resource caps (REQ-DP-04) before any paid inference can run, and
for CSV proposes a bank-specific column mapping for user confirmation
(REQ-DP-01).

Per-page table classification (previously done here via YOLOv8-Nano) has
been removed: Textract's AnalyzeDocument already classifies a page as it
extracts it (has a TABLE block, or it doesn't), so a separate classify-then
-extract round trip added a model, a Lambda layer, and cold-start cost with
no accuracy benefit. See extractor/service.py::classify_and_extract_page
for the Tier 1 (text layer) -> Tier 2 (Textract) waterfall that replaced it.

__author__ = "Van Vo"
"""

from __future__ import annotations

import csv
import io
import json
import os

import boto3
from botocore.exceptions import ClientError

from ..core.observability import logger
from ..shared.exceptions import (
    FileTooLargeError,
    InvalidCsvFormatError,
    MappingConfirmationTimeoutError,
    TooManyPagesError,
    UnsupportedFormatError,
)
from ..shared.schemas import PipelineStatus
from ..statement_ingestion.service import get_job, update_job_status
from .mapping_repository import CANONICAL_FIELDS, get_bank_mapping, save_bank_mapping_correction
from .schemas import ColumnMappingProposal, GatekeeperOutput, StatementFormat

_s3_client = boto3.client("s3")
_sfn_client = boto3.client("stepfunctions")

# REQ-DP-04: cap resource consumption per upload before any paid inference runs.
_MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_STATEMENT_FILE_SIZE_BYTES", 25 * 1024 * 1024))
_MAX_PDF_PAGES = int(os.environ.get("MAX_STATEMENT_PDF_PAGES", 50))

# A column matching its canonical field's own name is always a known match,
# even for a bank with no stored mapping yet — this keeps the common case
# (a CSV that already uses plain "date"/"merchant"/"amount" headers) working
# without requiring every bank to be pre-seeded in the mapping table.
_IMPLICIT_VARIANTS: dict[str, set[str]] = {field: {field} for field in CANONICAL_FIELDS}


def _is_pdf(data: bytes) -> bool:
    """Detect PDF via magic bytes."""
    return data[:4] == b"%PDF"


def _is_csv(s3_key: str, data: bytes) -> bool:
    """Detect CSV by file extension and content sniffing."""
    if s3_key.lower().endswith(".csv"):
        return True
    try:
        text = data[:4096].decode("utf-8", errors="replace")
        csv.Sniffer().sniff(text)
        return True
    except csv.Error:
        return False


def _detect_format(s3_key: str, file_bytes: bytes) -> StatementFormat:
    """Detect the format of the uploaded bank statement."""
    if _is_pdf(file_bytes):
        return StatementFormat.PDF
    if _is_csv(s3_key, file_bytes):
        return StatementFormat.CSV
    return StatementFormat.IMAGE


def _read_csv_headers_and_row_count(data: bytes) -> tuple[list[str], int]:
    """Read a CSV's header row and count of data rows, without assuming any
    particular column names — REQ-DP-01's per-bank mapping means the exact
    headers aren't known yet at this stage.

    Returns:
        (headers, data_row_count).

    Raises:
        InvalidCsvFormatError: If there's no header row, or fewer than 2
            columns (not enough structure to be a transaction table), or
            zero data rows.
    """
    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or len(reader.fieldnames) < 2:
        raise InvalidCsvFormatError("CSV has no usable header row")

    row_count = sum(1 for _ in reader)
    if row_count == 0:
        raise InvalidCsvFormatError("CSV has a header row but no data rows")

    return list(reader.fieldnames), row_count


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Read a PDF's page count without rasterizing any page — cheap metadata
    read via PyMuPDF, used only for the REQ-DP-04 page-count cap.
    """
    import fitz  # type: ignore[import-untyped]

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise UnsupportedFormatError(f"File could not be parsed as a PDF: {e}") from e

    if doc.page_count > _MAX_PDF_PAGES:
        raise TooManyPagesError(
            f"PDF has {doc.page_count} pages, exceeding the {_MAX_PDF_PAGES} page limit"
        )
    return doc.page_count


def propose_column_mapping(bucket: str, csv_s3_key: str, bank_id: str) -> ColumnMappingProposal:
    """Match a CSV's actual headers against a bank's known column-name
    variants, for the user-facing mapping confirmation dialog.

    Never parses transaction rows — that only happens after the user
    confirms the mapping (REQ-DP-01 "User Mapping Confirmation Gate").

    Args:
        bucket: S3 bucket.
        csv_s3_key: S3 key of the uploaded CSV.
        bank_id: Bank institution the user selected at upload time.

    Returns:
        A ColumnMappingProposal with every column found, each either
        matched to a canonical field or left unmapped.
    """
    response = _s3_client.get_object(Bucket=bucket, Key=csv_s3_key)
    file_bytes = response["Body"].read()
    headers, _ = _read_csv_headers_and_row_count(file_bytes)

    stored_variants = get_bank_mapping(bank_id)
    is_known_bank = stored_variants is not None

    variants_by_field: dict[str, set[str]] = {field: set(_IMPLICIT_VARIANTS[field]) for field in CANONICAL_FIELDS}
    if stored_variants:
        for field in CANONICAL_FIELDS:
            variants_by_field[field] |= set(stored_variants.get(field, []))

    mapped: dict[str, str] = {}
    unmapped_columns: list[str] = []
    for header in headers:
        normalized = header.strip().lower()
        matched_field = next(
            (field for field, variants in variants_by_field.items() if normalized in variants),
            None,
        )
        if matched_field and matched_field not in mapped:
            mapped[matched_field] = header
        else:
            unmapped_columns.append(header)

    logger.info(
        "Column mapping proposed",
        bank_id=bank_id,
        is_known_bank=is_known_bank,
        mapped_count=len(mapped),
        unmapped_count=len(unmapped_columns),
    )

    return ColumnMappingProposal(
        bank_id=bank_id,
        csv_s3_key=csv_s3_key,
        mapped=mapped,
        unmapped_columns=unmapped_columns,
        is_known_bank=is_known_bank,
    )


def confirm_column_mapping(job_id: str, bank_id: str, confirmed_mapping: dict[str, str]) -> None:
    """Persist the user-confirmed mapping and resume the paused pipeline.

    Writes each confirmed (canonical_field -> header) pair back to the bank
    mapping table (REQ-DP-01 B. Constraints — a runtime-writable store, so
    the correction improves every future upload for this bank), then sends
    a Step Functions task success to resume the execution that's been
    waiting at PENDING_MAPPING_CONFIRMATION.

    Args:
        job_id: Step Function execution ID, used to look up the stored
            task token.
        bank_id: Bank institution this mapping applies to.
        confirmed_mapping: canonical field -> source column name, as
            confirmed (or corrected) by the user in the mapping dialog.

    Raises:
        InvalidCsvFormatError: If the confirmed mapping is missing any
            required canonical field.
        MappingConfirmationTimeoutError: If the job has no stored task
            token — it was never paused, already resumed, or its Job
            Tracker record has expired.
    """
    missing = set(CANONICAL_FIELDS) - confirmed_mapping.keys()
    if missing:
        raise InvalidCsvFormatError(f"Confirmed mapping is missing required fields: {sorted(missing)}")

    job = get_job(job_id)
    if not job or not job.get("task_token"):
        raise MappingConfirmationTimeoutError(
            f"Job {job_id} has no pending mapping confirmation to resume"
        )

    for canonical_field, header in confirmed_mapping.items():
        save_bank_mapping_correction(bank_id, canonical_field, header)

    try:
        _sfn_client.send_task_success(
            taskToken=job["task_token"],
            output=json.dumps({"job_id": job_id, "confirmed_mapping": confirmed_mapping}),
        )
    except ClientError as e:
        logger.warning("Failed to resume paused Step Functions execution", job_id=job_id, error=str(e))
        raise

    update_job_status(job_id, job["user_id"], PipelineStatus.GATEKEEPER_PASSED)
    logger.info("Column mapping confirmed, pipeline resumed", job_id=job_id, bank_id=bank_id)


def run_gatekeeper(
    bucket: str,
    s3_key: str,
    job_id: str,
    user_id: str,
    statement_id: str,
    account_id: str,
    bank_id: str | None = None,
) -> GatekeeperOutput:
    """Execute the Gatekeeper pipeline step: format detection, resource
    caps, and — for CSV — column mapping proposal.

    Args:
        bucket: S3 bucket name.
        s3_key: S3 object key of the uploaded file.
        job_id: Step Function execution ID.
        user_id: Cognito sub of the uploading user.
        statement_id: Pre-created statement metadata ID.
        account_id: Account this statement belongs to.
        bank_id: Bank institution the user selected at upload time.
            Required for CSV; ignored for PDF/Image.

    Returns:
        A GatekeeperOutput. For CSV, requires_mapping_confirmation is True
        and mapping_proposal is populated. For PDF/Image, the file is
        ready for the Extractor's Tier 1/Tier 2 waterfall.

    Raises:
        FileTooLargeError, TooManyPagesError: REQ-DP-04 resource caps.
        UnsupportedFormatError: The file isn't a readable PDF/image.
        InvalidCsvFormatError: The CSV has no usable header/data rows.
    """
    logger.info("Gatekeeper starting", job_id=job_id, s3_key=s3_key)

    response = _s3_client.get_object(Bucket=bucket, Key=s3_key)
    file_bytes = response["Body"].read()

    if len(file_bytes) > _MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            f"File is {len(file_bytes)} bytes, exceeding the {_MAX_FILE_SIZE_BYTES} byte limit"
        )

    statement_format = _detect_format(s3_key, file_bytes)
    logger.info("Statement format detected", job_id=job_id, format=str(statement_format))

    if statement_format == StatementFormat.CSV:
        if not bank_id:
            raise InvalidCsvFormatError("A bank institution must be selected for CSV import")

        _read_csv_headers_and_row_count(file_bytes)  # raises on structural gate failure
        mapping_proposal = propose_column_mapping(bucket, s3_key, bank_id)

        return GatekeeperOutput(
            job_id=job_id,
            s3_key=s3_key,
            user_id=user_id,
            statement_id=statement_id,
            account_id=account_id,
            statement_format=statement_format,
            csv_s3_key=s3_key,
            mapping_proposal=mapping_proposal,
            requires_mapping_confirmation=True,
        )

    page_count = 1
    if statement_format == StatementFormat.PDF:
        page_count = _pdf_page_count(file_bytes)
    else:
        # Cheap validity check that the bytes are actually a readable image,
        # without holding a full PIL.Image open past this function.
        import io as _io

        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(_io.BytesIO(file_bytes)) as img:
                img.verify()
        except UnidentifiedImageError as e:
            raise UnsupportedFormatError(f"File is not a readable image: {e}") from e

    logger.info("Gatekeeper complete", job_id=job_id, format=str(statement_format), page_count=page_count)

    return GatekeeperOutput(
        job_id=job_id,
        s3_key=s3_key,
        user_id=user_id,
        statement_id=statement_id,
        account_id=account_id,
        statement_format=statement_format,
        page_count=page_count,
    )
