"""Vision Gatekeeper Service — orchestrator-agnostic business logic.

Filters uploaded files to extract only valid transaction pages using
YOLOv8-Nano inference. Handles PDF-to-image splitting, CSV validation,
and raw image formats (screenshots).

__author__ = "Van Vo"
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any

import boto3
from PIL import Image

from ..core.observability import logger
from .schemas import GatekeeperOutput, StatementFormat

_YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "/opt/yolov8n.pt")
_s3_client = boto3.client("s3")

_CSV_REQUIRED_HEADERS = {"date", "merchant", "amount"}


def _load_yolo_model() -> Any:
    """Lazily load YOLOv8-Nano model from Lambda Layer path."""
    from ultralytics import YOLO  # type: ignore[import-untyped]
    return YOLO(_YOLO_MODEL_PATH)


def _is_pdf(data: bytes) -> bool:
    """Detect PDF via magic bytes.

    Args:
        data: Raw file bytes.

    Returns:
        True if the file is a PDF.
    """
    return data[:4] == b"%PDF"


def _is_csv(s3_key: str, data: bytes) -> bool:
    """Detect CSV by file extension and content sniffing.

    Args:
        s3_key: S3 object key.
        data: Raw file bytes.

    Returns:
        True if the file appears to be a valid CSV.
    """
    if s3_key.lower().endswith(".csv"):
        return True
    try:
        text = data[:4096].decode("utf-8", errors="replace")
        csv.Sniffer().sniff(text)
        return True
    except csv.Error:
        return False


def _csv_has_valid_transactions(data: bytes) -> bool:
    """Validate that a CSV file contains a recognizable transaction table.

    Checks that the CSV has required column headers (date, merchant, amount)
    and at least one data row.

    Args:
        data: Raw CSV bytes.

    Returns:
        True if the CSV has valid transaction data.
    """
    try:
        text = data.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            return False
        normalized_headers = {h.strip().lower() for h in reader.fieldnames}
        if not _CSV_REQUIRED_HEADERS.issubset(normalized_headers):
            return False
        first_row = next(reader, None)
        return first_row is not None
    except Exception:
        return False


def _pdf_to_images(pdf_bytes: bytes) -> list[Image.Image]:
    """Split a PDF into one PIL Image per page.

    Args:
        pdf_bytes: Raw PDF content.

    Returns:
        List of PIL images, one per page.
    """
    import fitz  # type: ignore[import-untyped]

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: list[Image.Image] = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
    return images


def _page_has_table(image: Image.Image, model: Any) -> bool:
    """Run YOLOv8-Nano inference to detect a transaction table on a page.

    Args:
        image: The page image.
        model: Pre-loaded YOLO model.

    Returns:
        True if a table-like structure is detected with confidence >= 0.5.
    """
    results = model(image, verbose=False)
    for result in results:
        for box in result.boxes:
            class_name = result.names[int(box.cls)]
            confidence = float(box.conf)
            if "table" in class_name.lower() and confidence >= 0.5:
                return True
    return False


def _detect_format(s3_key: str, file_bytes: bytes) -> StatementFormat:
    """Detect the format of the uploaded bank statement.

    Args:
        s3_key: S3 object key.
        file_bytes: Raw file bytes.

    Returns:
        The detected StatementFormat.
    """
    if _is_pdf(file_bytes):
        return StatementFormat.PDF
    if _is_csv(s3_key, file_bytes):
        return StatementFormat.CSV
    return StatementFormat.IMAGE


def run_gatekeeper(
    bucket: str,
    s3_key: str,
    job_id: str,
    user_id: str,
    statement_id: str,
    account_id: str,
) -> GatekeeperOutput:
    """Execute the Vision Gatekeeper pipeline step.

    Downloads the uploaded file from S3, detects the format (PDF, CSV, or image
    screenshot), and validates that the file contains valid transaction data.

    For PDFs: splits into page images and runs YOLOv8-Nano table detection.
    For CSVs: validates headers and row presence.
    For images: runs YOLOv8-Nano table detection on the single image.

    Args:
        bucket: S3 bucket name.
        s3_key: S3 object key of the uploaded file.
        job_id: Step Function execution ID.
        user_id: Cognito sub of the uploading user.
        statement_id: Pre-created statement metadata ID.
        account_id: Account this statement belongs to.

    Returns:
        A GatekeeperOutput with lists of valid and junk page S3 keys.
    """
    logger.info("Vision Gatekeeper starting", job_id=job_id, s3_key=s3_key)

    response = _s3_client.get_object(Bucket=bucket, Key=s3_key)
    file_bytes = response["Body"].read()

    statement_format = _detect_format(s3_key, file_bytes)
    logger.info("Statement format detected", job_id=job_id, format=str(statement_format))

    if statement_format == StatementFormat.CSV:
        has_valid = _csv_has_valid_transactions(file_bytes)
        logger.info(
            "CSV gatekeeper complete",
            job_id=job_id,
            has_valid=has_valid,
        )
        return GatekeeperOutput(
            job_id=job_id,
            s3_key=s3_key,
            user_id=user_id,
            statement_id=statement_id,
            account_id=account_id,
            statement_format=statement_format,
            valid_page_keys=[],
            junk_page_keys=[],
            csv_s3_key=s3_key if has_valid else None,
            has_valid_pages=has_valid,
        )

    images: list[Image.Image] = []
    if statement_format == StatementFormat.PDF:
        logger.info("PDF detected — splitting into page images", job_id=job_id)
        images = _pdf_to_images(file_bytes)
    else:
        images = [Image.open(io.BytesIO(file_bytes))]

    model = _load_yolo_model()
    valid_page_keys: list[str] = []
    junk_page_keys: list[str] = []

    base_key = s3_key.rsplit(".", 1)[0]

    for i, img in enumerate(images):
        page_key = f"{base_key}/page_{i}.jpg"
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        if _page_has_table(img, model):
            _s3_client.put_object(Bucket=bucket, Key=page_key, Body=buf.getvalue())
            valid_page_keys.append(page_key)
        else:
            junk_page_keys.append(page_key)

    has_valid = len(valid_page_keys) > 0
    logger.info(
        "Gatekeeper complete",
        job_id=job_id,
        valid=len(valid_page_keys),
        junk=len(junk_page_keys),
    )

    return GatekeeperOutput(
        job_id=job_id,
        s3_key=s3_key,
        user_id=user_id,
        statement_id=statement_id,
        account_id=account_id,
        statement_format=statement_format,
        valid_page_keys=valid_page_keys,
        junk_page_keys=junk_page_keys,
        has_valid_pages=has_valid,
    )
