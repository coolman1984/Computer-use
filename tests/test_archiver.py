"""اختبارات S-06: أرشفة تحليلية (Parquet مقسّم + استعلام DuckDB لمقارنة فترتين)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from smartops.adapters.history.archiver import HistoryArchiver
from smartops.core.ids import new_id
from smartops.domain.enums import ValidationStatus
from smartops.domain.models import FileArtifact


def _artifact(**overrides) -> FileArtifact:
    defaults = dict(
        id=new_id("file"),
        run_id="run_demo",
        system="erp_demo",
        report="daily_sales",
        path="/data/raw/x.csv",
        original_name="x.csv",
        size_bytes=1000,
        sha256="abc123",
        row_count=50,
        period="2026-01-01",
        validation_status=ValidationStatus.PASSED,
        created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return FileArtifact(**defaults)


def test_archive_writes_parquet_under_expected_partition(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    artifact = _artifact()

    target = archiver.archive(artifact)

    expected_dir = tmp_path / "2026" / "01" / "01" / "erp_demo" / "daily_sales"
    assert target.parent == expected_dir
    assert target.name == f"{artifact.id}.parquet"
    assert target.exists()


def test_query_returns_archived_record(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    artifact = _artifact()
    archiver.archive(artifact)

    records = archiver.query()

    assert len(records) == 1
    record = records[0]
    assert record["file_id"] == artifact.id
    assert record["system"] == "erp_demo"
    assert record["report"] == "daily_sales"
    assert record["size_bytes"] == 1000
    assert record["row_count"] == 50
    assert record["validation_status"] == "passed"


def test_query_with_where_filters_records(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    archiver.archive(_artifact(system="erp_a"))
    archiver.archive(_artifact(system="erp_b"))

    records = archiver.query("system = ?", ["erp_a"])

    assert len(records) == 1
    assert records[0]["system"] == "erp_a"


def test_query_on_empty_archive_returns_empty_list(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    assert archiver.query() == []


def test_compare_periods_on_empty_archive_returns_zeros(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    comparison = archiver.compare_periods("erp_demo", "daily_sales", "2026-01-01", "2026-01-02")

    assert comparison.count_a == 0
    assert comparison.count_b == 0
    assert comparison.avg_size_bytes_a is None
    assert comparison.failed_a == 0


def test_compare_periods_computes_stats_correctly(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)

    # فترة 1: يومين، حجم وصفوف صغيرة نسبيًا، وملف واحد مرفوض
    archiver.archive(_artifact(size_bytes=1000, row_count=50, period="p1"))
    archiver.archive(
        _artifact(size_bytes=2000, row_count=100, period="p1", validation_status=ValidationStatus.FAILED)
    )

    # فترة 2: حجم وصفوف أكبر بوضوح، كلها سليمة
    archiver.archive(
        _artifact(
            size_bytes=4000,
            row_count=300,
            period="p2",
            created_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        )
    )
    archiver.archive(
        _artifact(
            size_bytes=6000,
            row_count=500,
            period="p2",
            created_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        )
    )

    comparison = archiver.compare_periods("erp_demo", "daily_sales", "p1", "p2")

    assert comparison.count_a == 2
    assert comparison.count_b == 2
    assert comparison.avg_size_bytes_a == 1500
    assert comparison.avg_size_bytes_b == 5000
    assert comparison.avg_row_count_a == 75
    assert comparison.avg_row_count_b == 400
    assert comparison.failed_a == 1
    assert comparison.failed_b == 0


def test_compare_periods_ignores_other_systems_and_reports(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    archiver.archive(_artifact(system="other_system", period="p1"))
    archiver.archive(_artifact(report="other_report", period="p1"))

    comparison = archiver.compare_periods("erp_demo", "daily_sales", "p1", "p2")

    assert comparison.count_a == 0
    assert comparison.count_b == 0


def test_archive_uses_slugged_system_and_report_names(tmp_path: Path) -> None:
    archiver = HistoryArchiver(tmp_path)
    artifact = _artifact(system="ERP System!", report="Daily / Sales Report")

    target = archiver.archive(artifact)

    assert "erp-system" in str(target)
    assert "daily-sales-report" in str(target)
