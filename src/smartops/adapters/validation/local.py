"""Local implementation of the FileValidatorPort contract, standard library only.

We avoid new dependencies (such as openpyxl) because csv, zipfile, and
xml.etree are enough to read column headers and row counts from CSV and
Excel (.xlsx) files.
"""

from __future__ import annotations

import csv
import hashlib
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ...ports.validation import ValidationReport, ValidationRules

_CHUNK_SIZE = 1024 * 1024
_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_COL_LETTERS_RE = re.compile(r"[A-Z]+")

# How much of the file to read when deciding what it really is. A served error
# page declares itself in its first few hundred bytes.
_SNIFF_BYTES = 4096
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<!DOCTYPE HTML")
# Magic numbers for formats that are archives or documents underneath. Used to
# tell a real .xlsx from an HTML page wearing that extension.
_ZIP_MAGIC = b"PK\x03\x04"


def _looks_like_a_web_page(head: bytes) -> bool:
    """True when the bytes are an HTML document rather than data.

    Checked on content, never on the extension: the whole point is that the
    extension lies. A portal that has dropped your session answers the download
    with its login page, named exactly like the report you asked for.
    """
    lowered = head.lstrip()[:512].lower()
    return any(marker.lower() in lowered for marker in _HTML_MARKERS)


def _head_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(_SNIFF_BYTES)


def _decode(head: bytes) -> str:
    """Best-effort text for the "must contain" check; never raises on binary."""
    return head.decode("utf-8", errors="replace")


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv_header_and_count(path: Path) -> tuple[list[str], int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], 0
        row_count = sum(1 for _ in reader)
    return [cell.strip() for cell in header], row_count


def _col_index(cell_ref: str) -> int:
    """Convert a cell reference such as C3 into a zero-based column index."""
    match = _COL_LETTERS_RE.match(cell_ref)
    letters = match.group(0) if match else "A"
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    tree = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in tree.findall("m:si", _XLSX_NS):
        strings.append("".join(t.text or "" for t in si.findall(".//m:t", _XLSX_NS)))
    return strings


def _first_sheet_xml_name(archive: zipfile.ZipFile) -> str:
    sheets = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if not sheets:
        raise ValueError("no sheets inside the Excel file")
    return sheets[0]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "s":
        value_el = cell.find("m:v", _XLSX_NS)
        if value_el is None or value_el.text is None:
            return ""
        index = int(value_el.text)
        return shared[index] if index < len(shared) else ""
    if cell_type == "inlineStr":
        text_el = cell.find("m:is/m:t", _XLSX_NS)
        return (text_el.text or "") if text_el is not None else ""
    value_el = cell.find("m:v", _XLSX_NS)
    return value_el.text if value_el is not None and value_el.text is not None else ""


def _read_xlsx_header_and_count(path: Path) -> tuple[list[str], int]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        sheet_xml = archive.read(_first_sheet_xml_name(archive))
    tree = ET.fromstring(sheet_xml)
    rows = tree.findall(".//m:sheetData/m:row", _XLSX_NS)
    if not rows:
        return [], 0
    header: dict[int, str] = {}
    for cell in rows[0].findall("m:c", _XLSX_NS):
        ref = cell.get("r", "")
        header[_col_index(ref)] = _cell_value(cell, shared).strip()
    width = (max(header) + 1) if header else 0
    header_list = [header.get(i, "") for i in range(width)]
    return header_list, max(0, len(rows) - 1)


class LocalFileValidator:
    """Checks existence, size, extension, hash, columns, row count, age, and duplicates."""

    def __init__(self, files_repo: Any = None, *, now: Any = None) -> None:
        # files_repo is optional: any object with find_by_hash(sha256) ->
        # list[FileArtifact] (such as services.files). Without it the
        # duplicate check is skipped.
        self._files_repo = files_repo
        self._now = now or time.time

    def validate(self, path: Path, rules: ValidationRules) -> ValidationReport:
        path = Path(path)
        failures: list[str] = []
        details: dict[str, Any] = {}

        if not path.exists():
            return ValidationReport(passed=False, failures=["File does not exist"])

        size_bytes = path.stat().st_size
        if size_bytes < rules.min_size_bytes:
            failures.append(f"File is too small ({size_bytes} bytes)")
        if size_bytes == 0:
            # Nothing further can be true of an empty file, and every later check
            # would either pass vacuously or raise.
            return ValidationReport(
                passed=False, size_bytes=0, sha256=_sha256_of(path),
                failures=failures + ["The file is empty"],
            )

        head = _head_bytes(path)
        suffix = path.suffix.lower()

        # Identify the file by what is inside it, before trusting its name.
        is_web_page = _looks_like_a_web_page(head)
        if is_web_page and rules.reject_web_pages:
            failures.append(
                "The download returned a web page, not a report — this usually means the "
                "sign-in expired or the site showed an error instead of the file"
            )
        if suffix == ".xlsx" and not head.startswith(_ZIP_MAGIC):
            failures.append("The file is named as an Excel file but is not one")
        if rules.expected_extensions:
            allowed = {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in rules.expected_extensions
            }
            if suffix not in allowed:
                failures.append(f"Unexpected extension: {suffix or '(no extension)'}")

        sha256 = _sha256_of(path)

        header: list[str] = []
        row_count: int | None = None
        needs_content = bool(rules.required_columns) or rules.min_rows is not None
        # Reading an HTML page as a CSV "succeeds" and reports nonsense columns;
        # skip it so the failure above stands as the real reason.
        if is_web_page:
            pass
        elif suffix == ".csv":
            try:
                header, row_count = _read_csv_header_and_count(path)
            except (OSError, UnicodeDecodeError) as exc:
                failures.append(f"Could not open the file as CSV: {exc}")
        elif suffix == ".xlsx":
            try:
                header, row_count = _read_xlsx_header_and_count(path)
            except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
                failures.append(f"Could not open the Excel file: {exc}")
        elif needs_content:
            failures.append(f"Cannot validate the content of an unsupported extension: {suffix}")

        if rules.required_columns:
            missing = [c for c in rules.required_columns if c not in header]
            if missing:
                failures.append(f"Missing columns: {', '.join(missing)}")

        if rules.min_rows is not None and row_count is not None and row_count < rules.min_rows:
            failures.append(f"Row count ({row_count}) is below the minimum ({rules.min_rows})")

        if rules.must_contain and not is_web_page:
            text = _decode(head if size_bytes <= _SNIFF_BYTES else path.read_bytes())
            missing_text = [needle for needle in rules.must_contain if needle not in text]
            if missing_text:
                failures.append(
                    "The file does not mention: " + ", ".join(missing_text)
                    + " — it may be for the wrong period or the wrong report"
                )

        if rules.max_age_hours is not None:
            age_hours = (self._now() - path.stat().st_mtime) / 3600
            if age_hours > rules.max_age_hours:
                failures.append(f"File is too old ({age_hours:.1f} hours)")

        if rules.reject_duplicate_hash and self._files_repo is not None and size_bytes > 0:
            duplicates = [f for f in self._files_repo.find_by_hash(sha256) if f.path != str(path)]
            if duplicates:
                failures.append("File is identical to an earlier one (duplicate hash)")
                details["duplicate_of"] = duplicates[0].id

        return ValidationReport(
            passed=not failures,
            sha256=sha256,
            size_bytes=size_bytes,
            row_count=row_count,
            failures=failures,
            details=details,
        )
