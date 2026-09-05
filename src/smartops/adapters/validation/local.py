"""تنفيذ محلي لعقد FileValidatorPort: بمكتبات المعيار القياسي فقط.

نتجنّب اعتماديات جديدة (مثل openpyxl) لأن csv وzipfile وxml.etree
تكفي لقراءة عناوين الأعمدة وعدد الصفوف من CSV وExcel (.xlsx).
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
    """يحوّل مرجع خلية مثل C3 إلى رقم عمود بادئ من صفر."""
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
        raise ValueError("لا توجد أوراق داخل ملف Excel")
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
    """يفحص الوجود، الحجم، الامتداد، البصمة، الأعمدة، عدد الصفوف، العمر، والتكرار."""

    def __init__(self, files_repo: Any = None, *, now: Any = None) -> None:
        # files_repo اختياري: أي كائن له find_by_hash(sha256) -> list[FileArtifact]
        # (مثل services.files). بدونه يتم تخطي فحص التكرار.
        self._files_repo = files_repo
        self._now = now or time.time

    def validate(self, path: Path, rules: ValidationRules) -> ValidationReport:
        path = Path(path)
        failures: list[str] = []
        details: dict[str, Any] = {}

        if not path.exists():
            return ValidationReport(passed=False, failures=["الملف غير موجود"])

        size_bytes = path.stat().st_size
        if size_bytes < rules.min_size_bytes:
            failures.append(f"حجم الملف صغير جدًا ({size_bytes} بايت)")

        suffix = path.suffix.lower()
        if rules.expected_extensions:
            allowed = {
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in rules.expected_extensions
            }
            if suffix not in allowed:
                failures.append(f"امتداد غير متوقع: {suffix or '(بدون امتداد)'}")

        sha256 = _sha256_of(path)

        header: list[str] = []
        row_count: int | None = None
        needs_content = bool(rules.required_columns) or rules.min_rows is not None
        if suffix == ".csv":
            try:
                header, row_count = _read_csv_header_and_count(path)
            except (OSError, UnicodeDecodeError) as exc:
                failures.append(f"تعذر فتح الملف كـ CSV: {exc}")
        elif suffix == ".xlsx":
            try:
                header, row_count = _read_xlsx_header_and_count(path)
            except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
                failures.append(f"تعذر فتح ملف Excel: {exc}")
        elif needs_content:
            failures.append(f"لا يمكن التحقق من محتوى امتداد غير مدعوم: {suffix}")

        if rules.required_columns:
            missing = [c for c in rules.required_columns if c not in header]
            if missing:
                failures.append(f"أعمدة مفقودة: {', '.join(missing)}")

        if rules.min_rows is not None and row_count is not None and row_count < rules.min_rows:
            failures.append(f"عدد الصفوف ({row_count}) أقل من الحد الأدنى ({rules.min_rows})")

        if rules.max_age_hours is not None:
            age_hours = (self._now() - path.stat().st_mtime) / 3600
            if age_hours > rules.max_age_hours:
                failures.append(f"الملف قديم جدًا ({age_hours:.1f} ساعة)")

        if rules.reject_duplicate_hash and self._files_repo is not None and size_bytes > 0:
            duplicates = [f for f in self._files_repo.find_by_hash(sha256) if f.path != str(path)]
            if duplicates:
                failures.append("الملف مطابق لملف سابق (بصمة مكررة)")
                details["duplicate_of"] = duplicates[0].id

        return ValidationReport(
            passed=not failures,
            sha256=sha256,
            size_bytes=size_bytes,
            row_count=row_count,
            failures=failures,
            details=details,
        )
