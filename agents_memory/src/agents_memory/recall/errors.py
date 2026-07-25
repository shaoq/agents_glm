"""Recall domain errors.

These represent fatal, non-degradable failures. Recoverable failures use
``DegradationCode`` on ``RecallResult.metadata`` instead of raising.

SQLite unavailability, authorization validation failure and output contract
violation are always fatal: Recall never fabricates a normal or empty result
when it could not actually verify candidates.
"""

from agents_memory.recall.models import RecallErrorCode


class RecallError(Exception):
    """Base class for fatal Recall domain errors."""

    code: RecallErrorCode = RecallErrorCode.CONTRACT_VIOLATION

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message or self.code.value)

    def __str__(self) -> str:
        return self.message or self.code.value


class RecallRequestError(RecallError):
    code = RecallErrorCode.REQUEST_INVALID


class RecallStorageUnavailable(RecallError):
    code = RecallErrorCode.STORAGE_UNAVAILABLE


class RecallAuthorizationUnavailable(RecallError):
    code = RecallErrorCode.AUTHORIZATION_UNAVAILABLE


class RecallRecordLoadFailed(RecallError):
    code = RecallErrorCode.RECORD_LOAD_FAILED


class RecallContractViolation(RecallError):
    code = RecallErrorCode.CONTRACT_VIOLATION


class RecallOutputSchemaInvalid(RecallError):
    code = RecallErrorCode.OUTPUT_SCHEMA_INVALID


class RecallConcurrentModification(RecallError):
    code = RecallErrorCode.CONCURRENT_MODIFICATION
