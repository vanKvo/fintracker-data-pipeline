"""Pipeline-wide exception hierarchy.

Every exception carries a stable `reason` code so a Lambda handler can
record why a job failed (in the Job Tracker) without parsing a free-text
message, and so the same code can be asserted on in tests.

__author__ = "Van Vo"
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for every error raised inside the data pipeline.

    Attributes:
        reason: Stable machine-readable code, stored on the Job Tracker
            record and safe to assert on in tests.
    """

    reason: str = "PIPELINE_ERROR"

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class UnsupportedFormatError(PipelineError):
    reason = "UNSUPPORTED_FORMAT"


class NoValidPagesError(PipelineError):
    reason = "NO_VALID_PAGES"


class FileTooLargeError(PipelineError):
    reason = "FILE_TOO_LARGE"


class TooManyPagesError(PipelineError):
    reason = "TOO_MANY_PAGES"


class InvalidCsvFormatError(PipelineError):
    reason = "INVALID_CSV_FORMAT"


class LedgerPushFailedError(PipelineError):
    reason = "LEDGER_PUSH_FAILED"


class UnknownBankMappingError(PipelineError):
    """Raised only when the caller needs a hard failure. The Gatekeeper's
    normal path does NOT raise this — REQ-DP-01 F. Error Handling requires
    an unknown bank to still open the mapping dialog with every column
    unmapped, so the user can build the mapping from scratch."""

    reason = "UNKNOWN_BANK_MAPPING"


class MappingConfirmationTimeoutError(PipelineError):
    reason = "MAPPING_CONFIRMATION_TIMEOUT"


class CsvRowParseError(PipelineError):
    reason = "CSV_ROW_PARSE_ERROR"


class NoValidTransactionsError(PipelineError):
    """Every page (or the CSV) was processed but yielded zero transactions."""

    reason = "NO_VALID_TRANSACTIONS"


class StatementOwnerNotFoundError(PipelineError):
    """REQ-DP-05: the Ledger has no record of this statement_id. Never worth retrying — the
    upload's presigned URL was never actually issued for this id, or it was tampered with in
    transit; the file landing in S3 at all despite that is itself suspicious."""

    reason = "STATEMENT_OWNER_NOT_FOUND"
