"""اختبارات S-01: مدقق الملفات المحلي (CSV/Excel) بلا اعتماديات جديدة."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


from smartops.adapters.validation.local import LocalFileValidator
from smartops.ports.validation import ValidationRules

_XLSX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_XLSX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="xl/workbook.xml"/>
</Relationships>"""

_XLSX_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>"""

_XLSX_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _make_xlsx(path: Path, rows: list[list[str]]) -> None:
    """يبني ملف xlsx صالح يدويًا (بلا openpyxl) بخلايا نصية مضمّنة (inlineStr)."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    row_xml_parts = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row):
            col_letter = chr(ord("A") + c_idx)
            ref = f"{col_letter}{r_idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        row_xml_parts.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_xml_parts)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", _XLSX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _XLSX_ROOT_RELS)
        archive.writestr("xl/workbook.xml", _XLSX_WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _XLSX_WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def test_valid_csv_passes(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    path.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")

    report = LocalFileValidator().validate(
        path, ValidationRules(required_columns=("col_a", "col_b"), min_rows=2)
    )

    assert report.passed
    assert report.row_count == 2
    assert report.size_bytes == path.stat().st_size
    assert len(report.sha256) == 64
    assert report.failures == []


def test_valid_xlsx_passes(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _make_xlsx(path, [["col_a", "col_b"], ["1", "2"], ["3", "4"]])

    report = LocalFileValidator().validate(
        path, ValidationRules(required_columns=("col_a", "col_b"), min_rows=2)
    )

    assert report.passed
    assert report.row_count == 2
    assert report.failures == []


def test_empty_file_fails_min_size(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")

    report = LocalFileValidator().validate(path, ValidationRules(min_size_bytes=1))

    assert not report.passed
    assert any("صغير" in failure for failure in report.failures)


def test_wrong_extension_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_text("col_a,col_b\n1,2\n", encoding="utf-8")

    report = LocalFileValidator().validate(
        path, ValidationRules(expected_extensions=(".csv", ".xlsx"))
    )

    assert not report.passed
    assert any("امتداد" in failure for failure in report.failures)


def test_missing_required_column_fails(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    path.write_text("col_a,col_c\n1,2\n", encoding="utf-8")

    report = LocalFileValidator().validate(
        path, ValidationRules(required_columns=("col_a", "col_b"))
    )

    assert not report.passed
    assert any("col_b" in failure for failure in report.failures)


def test_below_min_rows_fails(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    path.write_text("col_a\n1\n", encoding="utf-8")

    report = LocalFileValidator().validate(path, ValidationRules(min_rows=5))

    assert not report.passed
    assert any("عدد الصفوف" in failure for failure in report.failures)


def test_stale_file_fails_max_age(tmp_path: Path) -> None:
    path = tmp_path / "report.csv"
    path.write_text("col_a\n1\n", encoding="utf-8")

    fixed_now = path.stat().st_mtime + 3 * 3600  # الآن بعد 3 ساعات من وقت التعديل
    validator = LocalFileValidator(now=lambda: fixed_now)
    report = validator.validate(path, ValidationRules(max_age_hours=1.0))

    assert not report.passed
    assert any("قديم" in failure for failure in report.failures)


class _FakeFilesRepo:
    """يحاكي RunRepository.find_by_hash دون الحاجة لقاعدة بيانات حقيقية."""

    def __init__(self, existing: list) -> None:
        self._existing = existing

    def find_by_hash(self, sha256: str) -> list:
        return [f for f in self._existing if f.sha256 == sha256]


class _Existing:
    def __init__(self, id_: str, sha256: str, path: str) -> None:
        self.id = id_
        self.sha256 = sha256
        self.path = path


def test_duplicate_hash_is_rejected(tmp_path: Path) -> None:
    content = "col_a,col_b\n1,2\n"
    path = tmp_path / "new_report.csv"
    path.write_text(content, encoding="utf-8")

    import hashlib

    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    repo = _FakeFilesRepo([_Existing("file_old", sha256, str(tmp_path / "old_report.csv"))])

    report = LocalFileValidator(files_repo=repo).validate(path, ValidationRules())

    assert not report.passed
    assert any("مكرر" in failure for failure in report.failures)
    assert report.details["duplicate_of"] == "file_old"


def test_same_file_is_not_flagged_as_its_own_duplicate(tmp_path: Path) -> None:
    """لو الملف نفسه (بنفس المسار) موجود في المستودع، ده مش تكرار."""
    content = "col_a\n1\n"
    path = tmp_path / "report.csv"
    path.write_text(content, encoding="utf-8")

    import hashlib

    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    repo = _FakeFilesRepo([_Existing("file_self", sha256, str(path))])

    report = LocalFileValidator(files_repo=repo).validate(path, ValidationRules())

    assert report.passed


def test_missing_file_fails_clearly(tmp_path: Path) -> None:
    report = LocalFileValidator().validate(tmp_path / "ghost.csv", ValidationRules())
    assert not report.passed
    assert report.failures == ["الملف غير موجود"]


def test_bad_xlsx_reports_clear_failure(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a real zip file")

    report = LocalFileValidator().validate(path, ValidationRules(required_columns=("col_a",)))

    assert not report.passed
    assert any("Excel" in failure for failure in report.failures)
