"""Analytical archiving: turns validated FileArtifact data into a Parquet
record, partitioned by date, system, and report, with a simple DuckDB query
to compare two periods.

This layer archives metadata about the file (size, row count, validation
result, hash...), not the report's content itself; every report has a
completely different data schema, so analyzing its content is a separate
concern outside the scope of this package (see D006: SQLite for operations,
DuckDB/Parquet for analytics).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from ...domain.models import FileArtifact
from ...storage.paths import slug


@dataclass(frozen=True)
class PeriodComparison:
    """A simple statistical comparison summary between two periods for the same system and report."""

    system: str
    report: str
    period_a: str
    period_b: str
    count_a: int
    count_b: int
    avg_size_bytes_a: float | None
    avg_size_bytes_b: float | None
    avg_row_count_a: float | None
    avg_row_count_b: float | None
    failed_a: int
    failed_b: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HistoryArchiver:
    """Writes each FileArtifact record as a Parquet file inside its partition, queried via DuckDB."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir = Path(base_dir)

    def _partition_dir(self, artifact: FileArtifact, when: datetime) -> Path:
        return (
            self._base_dir
            / f"{when:%Y}"
            / f"{when:%m}"
            / f"{when:%d}"
            / slug(artifact.system)
            / slug(artifact.report)
        )

    def archive(self, artifact: FileArtifact) -> Path:
        """Write one file's record as Parquet inside its partition (date/system/report).

        Each record is written to its own file named <file_id>.parquet, so
        two concurrent archiving operations never contend over the same file.
        """
        when = artifact.created_at or _utcnow()
        partition = self._partition_dir(artifact, when)
        partition.mkdir(parents=True, exist_ok=True)
        target = partition / f"{artifact.id}.parquet"

        row: dict[str, Any] = {
            "file_id": artifact.id,
            "run_id": artifact.run_id,
            "system": artifact.system,
            "report": artifact.report,
            "period": artifact.period,
            "size_bytes": artifact.size_bytes,
            "row_count": artifact.row_count,
            "sha256": artifact.sha256,
            "validation_status": artifact.validation_status.value,
            "created_at": when.isoformat(),
        }
        placeholders = ", ".join(["?"] * len(row))
        columns = ", ".join(row.keys())
        con = duckdb.connect()
        try:
            con.execute(
                f"COPY (SELECT * FROM (VALUES ({placeholders})) AS t({columns}))"
                f" TO '{target.as_posix()}' (FORMAT PARQUET)",
                list(row.values()),
            )
        finally:
            con.close()
        return target

    def _has_any_records(self) -> bool:
        return next(self._base_dir.rglob("*.parquet"), None) is not None

    def _dataset_glob(self) -> str:
        return (self._base_dir / "**" / "*.parquet").as_posix()

    def query(
        self, where_sql: str = "", params: list[Any] | None = None
    ) -> list[dict[str, Any]]:
        """A general query over every archived record. Returns [] if the archive is empty."""
        if not self._has_any_records():
            return []
        glob = self._dataset_glob()
        sql = f"SELECT * FROM read_parquet('{glob}')"
        if where_sql:
            sql += f" WHERE {where_sql}"
        con = duckdb.connect()
        try:
            result = con.execute(sql, params or [])
            columns = [d[0] for d in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]
        finally:
            con.close()

    def compare_periods(
        self, system: str, report: str, period_a: str, period_b: str
    ) -> PeriodComparison:
        """Compare the count, average size, average row count, and rejection
        count between two periods for the same system and report. An empty
        archive means every value is zero/None."""
        if not self._has_any_records():
            return PeriodComparison(
                system=system,
                report=report,
                period_a=period_a,
                period_b=period_b,
                count_a=0,
                count_b=0,
                avg_size_bytes_a=None,
                avg_size_bytes_b=None,
                avg_row_count_a=None,
                avg_row_count_b=None,
                failed_a=0,
                failed_b=0,
            )

        glob = self._dataset_glob()
        sql = (
            "SELECT count(*) AS n, avg(size_bytes) AS avg_size, avg(row_count) AS avg_rows,"
            " sum(CASE WHEN validation_status = 'failed' THEN 1 ELSE 0 END) AS failed"
            f" FROM read_parquet('{glob}') WHERE system = ? AND report = ? AND period = ?"
        )
        con = duckdb.connect()
        try:
            row_a = con.execute(sql, [system, report, period_a]).fetchone()
            row_b = con.execute(sql, [system, report, period_b]).fetchone()
        finally:
            con.close()

        return PeriodComparison(
            system=system,
            report=report,
            period_a=period_a,
            period_b=period_b,
            count_a=row_a[0],
            count_b=row_b[0],
            avg_size_bytes_a=row_a[1],
            avg_size_bytes_b=row_b[1],
            avg_row_count_a=row_a[2],
            avg_row_count_b=row_b[2],
            failed_a=row_a[3] or 0,
            failed_b=row_b[3] or 0,
        )
