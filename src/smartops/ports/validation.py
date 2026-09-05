"""File validation contract: a download is not a success until the file proves valid."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ValidationRules:
    min_size_bytes: int = 1
    expected_extensions: tuple[str, ...] = ()
    required_columns: tuple[str, ...] = ()
    min_rows: int | None = None
    max_age_hours: float | None = None
    reject_duplicate_hash: bool = True
    # A portal that has lost your session answers a download request with an
    # HTML page. It arrives with the right filename, a healthy size and a unique
    # hash, so every other check passes and a login screen is filed as a valid
    # report. On by default because the failure is silent and common.
    reject_web_pages: bool = True
    # Text that must appear somewhere in the file. This is how a wrong period is
    # caught: a report exported for the wrong month is a perfectly valid file
    # that happens to be the wrong answer, and only its own content can tell.
    must_contain: tuple[str, ...] = ()


@dataclass
class ValidationReport:
    passed: bool
    sha256: str = ""
    size_bytes: int = 0
    row_count: int | None = None
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class FileValidatorPort(Protocol):
    def validate(self, path: Path, rules: ValidationRules) -> ValidationReport: ...
