"""مثال مرجعي: تركيب محوّلات وهمية على عقود ports وتشغيل سير عمل الجمع."""

from __future__ import annotations

from pathlib import Path

from smartops.domain.enums import ExtractionLayer, RunStatus, ValidationStatus
from smartops.ports.browser import ExtractionRequest, ExtractionResult
from smartops.ports.validation import ValidationReport, ValidationRules


class FakeBrowser:
    def __init__(self, content: bytes = b"col_a,col_b\n1,2\n") -> None:
        self.content = content
        self.calls: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls.append(request)
        target = Path(request.destination_dir) / f"{request.report}.csv"
        target.write_bytes(self.content)
        return ExtractionResult(
            ok=True,
            layer_used=ExtractionLayer.NETWORK,
            file_path=target,
            original_name=target.name,
            size_bytes=len(self.content),
        )

    def capture_evidence(self, run_id: str) -> dict:
        return {"run_id": run_id}


class FakeValidator:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed
        self.calls = 0

    def validate(self, path: Path, rules: ValidationRules) -> ValidationReport:
        self.calls += 1
        return ValidationReport(
            passed=self.passed,
            sha256="abc123",
            size_bytes=path.stat().st_size,
            row_count=1,
            failures=[] if self.passed else ["عدد الصفوف أقل من المتوقع"],
        )


def test_collect_report_happy_path(services) -> None:
    services.browser = FakeBrowser()
    services.validator = FakeValidator()

    run = services.runner.create_run(
        "collect.report", params={"system": "erp", "report": "daily_sales"}
    )
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.SUCCEEDED
    files = services.files.list(run_id=run.id)
    assert len(files) == 1
    assert files[0].validation_status is ValidationStatus.PASSED
    assert files[0].sha256 == "abc123"
    assert Path(files[0].path).exists()
    assert "erp" in files[0].path and "daily_sales" in files[0].path


def test_bad_file_fails_run_and_records_rejection(services) -> None:
    services.browser = FakeBrowser()
    services.validator = FakeValidator(passed=False)

    run = services.runner.create_run(
        "collect.report", params={"system": "erp", "report": "daily_sales"}
    )
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.FAILED
    assert run.error_class == "data_quality"
    files = services.files.list(run_id=run.id)
    assert files[0].validation_status is ValidationStatus.FAILED
    types = [e.type.value for e in services.events.timeline(run.id)]
    assert "file_rejected" in types and "incident_opened" in types


def test_missing_adapter_is_configuration_error(services) -> None:
    run = services.runner.create_run(
        "collect.report", params={"system": "erp", "report": "daily_sales"}
    )
    run = services.runner.execute(run.id)
    assert run.status is RunStatus.FAILED
    assert run.error_class == "permanent"
