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
