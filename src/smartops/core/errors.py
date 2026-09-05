"""Error classification: the basis for retry, escalation, and incident decisions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorClass(StrEnum):
    TRANSIENT = "transient"        # temporary fault: network, slowness, dropout
    RATE_LIMIT = "rate_limit"      # the site is asking us to slow down
    AUTH = "auth"                  # expired session or missing permission
    TARGET_NOT_FOUND = "target_not_found"  # element/page/report does not exist
    DATA_QUALITY = "data_quality"  # the file arrived but is not valid
    PERMANENT = "permanent"        # definition or logic error, retrying is pointless
    INTERNAL = "internal"          # unexpected error inside the platform


RETRYABLE_CLASSES = frozenset(
    {ErrorClass.TRANSIENT, ErrorClass.RATE_LIMIT, ErrorClass.AUTH, ErrorClass.DATA_QUALITY}
)


class SmartOpsError(Exception):
    """An error with a known classification inside the platform."""

    error_class: ErrorClass = ErrorClass.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        error_class: ErrorClass | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_class is not None:
            self.error_class = error_class
        self.code = code or self.error_class.value
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class.value,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class TransientError(SmartOpsError):
    error_class = ErrorClass.TRANSIENT


class RateLimitError(SmartOpsError):
    error_class = ErrorClass.RATE_LIMIT


class AuthError(SmartOpsError):
    error_class = ErrorClass.AUTH


class TargetNotFoundError(SmartOpsError):
    error_class = ErrorClass.TARGET_NOT_FOUND


class DataQualityError(SmartOpsError):
    error_class = ErrorClass.DATA_QUALITY


class PermanentError(SmartOpsError):
    error_class = ErrorClass.PERMANENT


class ConfigurationError(PermanentError):
    """Missing or invalid definition: do not retry, fix the definition."""


class ConcurrencyError(TransientError):
    """The run is locked by another worker."""


_BUILTIN_MAP: dict[type[BaseException], ErrorClass] = {
    TimeoutError: ErrorClass.TRANSIENT,
    ConnectionError: ErrorClass.TRANSIENT,
    OSError: ErrorClass.TRANSIENT,
    KeyError: ErrorClass.PERMANENT,
    ValueError: ErrorClass.PERMANENT,
    TypeError: ErrorClass.PERMANENT,
}


def wrap_error(exc: BaseException) -> SmartOpsError:
    """Convert any exception into a classified error so the cause is never lost."""
    if isinstance(exc, SmartOpsError):
        return exc
    error_class = ErrorClass.INTERNAL
    for exc_type, mapped in _BUILTIN_MAP.items():
        if isinstance(exc, exc_type):
            error_class = mapped
            break
    return SmartOpsError(
        str(exc) or exc.__class__.__name__,
        error_class=error_class,
        code=exc.__class__.__name__,
    )
