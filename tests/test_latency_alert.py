"""F-07 tests: evaluating download slowness, and that a slow run still succeeds while raising an alert."""

from __future__ import annotations

import json
from pathlib import Path

from smartops.adapters.notify.latency import evaluate_latency
from smartops.domain.enums import AlertLevel, ExtractionLayer, RunStatus
from smartops.ports.browser import ExtractionRequest, ExtractionResult
from smartops.ports.validation import ValidationReport


def test_within_normal_range_returns_none() -> None:
    assert evaluate_latency(10.0, warn_after_seconds=90, critical_after_seconds=180) is None


def test_over_warn_returns_yellow() -> None:
    assert (
        evaluate_latency(100.0, warn_after_seconds=90, critical_after_seconds=180)
        is AlertLevel.YELLOW
    )


def test_over_critical_returns_red() -> None:
    assert (
        evaluate_latency(200.0, warn_after_seconds=90, critical_after_seconds=180) is AlertLevel.RED
    )


def test_critical_wins_when_both_exceeded() -> None:
    assert evaluate_latency(500.0, warn_after_seconds=10, critical_after_seconds=20) is AlertLevel.RED


def test_none_thresholds_are_ignored() -> None:
    assert evaluate_latency(999.0, warn_after_seconds=None, critical_after_seconds=None) is None


def test_boundary_equals_threshold_triggers() -> None:
    assert evaluate_latency(90.0, warn_after_seconds=90, critical_after_seconds=None) is AlertLevel.YELLOW


# ---------- Integration: a slow run raises an alert and still succeeds ----------


class SlowFakeBrowser:
    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        target = Path(request.destination_dir) / f"{request.report}.csv"
        target.write_bytes(b"col_a,col_b\n1,2\n")
        return ExtractionResult(
            ok=True,
            layer_used=ExtractionLayer.NETWORK,
            file_path=target,
            original_name=target.name,
            size_bytes=target.stat().st_size,
            duration_seconds=250.0,
        )

    def capture_evidence(self, run_id: str) -> dict:
        return {}


class OkFakeValidator:
    def validate(self, path: Path, rules) -> ValidationReport:
        return ValidationReport(passed=True, sha256="x", row_count=1)


def test_slow_download_raises_alert_but_run_still_succeeds(services) -> None:
    services.browser = SlowFakeBrowser()
    services.validator = OkFakeValidator()

    run = services.runner.create_run(
        "collect.report",
        params={
            "system": "erp",
            "report": "daily_sales",
            "warn_after_seconds": 90,
            "critical_after_seconds": 180,
        },
    )
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.SUCCEEDED
    types = [e.type.value for e in services.events.timeline(run.id)]
    assert "alert_raised" in types

    alerts_log = services.settings.storage.logs_dir / "alerts.jsonl"
    assert alerts_log.exists()
    records = [json.loads(line) for line in alerts_log.read_text(encoding="utf-8").splitlines()]
    assert any(r["level"] == "red" for r in records)


def test_no_thresholds_means_no_alert(services) -> None:
    services.browser = SlowFakeBrowser()
    services.validator = OkFakeValidator()

    run = services.runner.create_run(
        "collect.report", params={"system": "erp", "report": "daily_sales"}
    )
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.SUCCEEDED
    types = [e.type.value for e in services.events.timeline(run.id)]
    assert "alert_raised" not in types
