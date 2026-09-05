"""Ready-made steps and workflows: a platform self-check + the standard report collection pattern.

The collection steps are fully written except for the real adapters (browser/validator), which are wired up later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters.notify.latency import evaluate_latency
from ..core.errors import AuthError, ConfigurationError, DataQualityError, TransientError
from ..core.ids import new_id
from ..domain.enums import AlertLevel, EventType, Severity, ValidationStatus
from ..domain.models import FileArtifact, StepDefinition, WorkflowDefinition
from ..engine.contracts import StepContext, StepResult
from ..ports.browser import ExtractionRequest
from ..ports.notify import Alert
from ..ports.validation import ValidationRules
from ..sessions import session_path
from ..storage.paths import ensure_raw_dir, slug


def echo(ctx: StepContext) -> StepResult:
    """A diagnostic step: passes values through into the run's shared state."""
    return StepResult.ok(**ctx.params)


def check_storage(ctx: StepContext) -> StepResult:
    """Confirms the storage directories exist and are writable."""
    settings = ctx.services.settings
    checked: dict[str, Any] = {}
    for label, directory in (
        ("raw_data_dir", settings.storage.raw_data_dir),
        ("incidents_dir", settings.storage.incidents_dir),
        ("logs_dir", settings.storage.logs_dir),
    ):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".smartops_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise TransientError(
                f"Directory is not writable: {path}", details={"error": str(exc)}
            ) from exc
        checked[label] = str(path)
    return StepResult.ok(storage_checked=checked)


def heartbeat(ctx: StepContext) -> StepResult:
    """A health pulse recorded in the event log so the watchdog knows the platform is alive."""
    ctx.emit(
        EventType.ALERT_RAISED,
        severity=Severity.DEBUG,
        message="Platform health pulse",
        payload={"component": "platform"},
    )
    return StepResult.ok(heartbeat_at=ctx.services.runner.clock.now().isoformat())


def download_report(ctx: StepContext) -> StepResult:
    """Downloads a report through the extraction engine and records the file in the raw data center."""
    browser = getattr(ctx.services, "browser", None)
    if browser is None:
        raise ConfigurationError("The browser engine is not wired up (services.browser)")

    system = ctx.get("system")
    report = ctx.get("report")
    if not system or not report:
        raise ConfigurationError("This step needs both system and report")

    now = ctx.services.runner.clock.now()
    destination = ensure_raw_dir(Path(ctx.services.settings.storage.raw_data_dir), system, report, now)
    settings = ctx.services.settings
    request = ExtractionRequest(
        system=system,
        report=report,
        destination_dir=destination,
        period=ctx.get("period", ""),
        filters=ctx.get("filters", {}) or {},
        run_id=ctx.run_id,
        session_state_path=session_path(settings.storage.sessions_dir, system),
        evidence_dir=Path(settings.storage.incidents_dir) / "evidence" / slug(ctx.run_id),
    )
    result = browser.extract(request)
    if result.auth_required:
        raise AuthError(
            result.message or f"Session expired for system {system}",
            details={"system": system, "needs_login": True, "no_retry": True, "command": f"python -m smartops login {system}"},
        )
    if not result.ok or result.file_path is None:
        raise TransientError(
            result.message or "Report extraction failed",
            details={"layer": result.layer_used.value, "evidence": result.evidence},
        )

    artifact = FileArtifact(
        id=new_id("file"),
        run_id=ctx.run_id,
        system=system,
        report=report,
        path=str(result.file_path),
        original_name=result.original_name,
        size_bytes=result.size_bytes,
        period=request.period,
        created_at=now,
    )
    ctx.services.files.save(artifact)
    ctx.emit(
        EventType.FILE_DOWNLOADED,
        message=f"Downloaded {report} from {system}",
        payload={
            "file_id": artifact.id,
            "path": artifact.path,
            "layer": result.layer_used.value,
            "size_bytes": artifact.size_bytes,
        },
    )
    _raise_latency_alert_if_needed(ctx, system=system, report=report, duration_seconds=result.duration_seconds)
    return StepResult.ok(
        file_id=artifact.id,
        file_path=artifact.path,
        layer_used=result.layer_used.value,
    )


def _raise_latency_alert_if_needed(
    ctx: StepContext, *, system: str, report: str, duration_seconds: float
) -> None:
    """Raise an alert if the download was slower than the threshold defined on the report (F-07).

    A slow download is still a successful download; a failed alert channel must never take down the run.
    """
    level = evaluate_latency(
        duration_seconds,
        warn_after_seconds=ctx.get("warn_after_seconds"),
        critical_after_seconds=ctx.get("critical_after_seconds"),
    )
    if level is None:
        return

    normal = ctx.get("normal_duration_seconds")
    ctx.emit(
        EventType.ALERT_RAISED,
        severity=Severity.WARNING if level is AlertLevel.YELLOW else Severity.ERROR,
        message=f"Download was slower than expected ({duration_seconds:.1f} seconds)",
        payload={
            "level": level.value,
            "system": system,
            "report": report,
            "duration_seconds": duration_seconds,
            "normal_duration_seconds": normal,
        },
    )
    notifier = getattr(ctx.services, "notifier", None)
    if notifier is None:
        return
    try:
        notifier.send(
            Alert(
                level=level,
                title=f"Slowness in {system}/{report}",
                body=f"The download took {duration_seconds:.1f} seconds instead of the normal {normal or 'unknown'} seconds",
                run_id=ctx.run_id,
                payload={"system": system, "report": report, "duration_seconds": duration_seconds},
            )
        )
    except Exception:
        pass  # a failed alert channel must never take down a successful run


def validate_file(ctx: StepContext) -> StepResult:
    """Checks the downloaded file. A failure here is a data-quality error, not a silent success."""
    validator = getattr(ctx.services, "validator", None)
    if validator is None:
        raise ConfigurationError("The file validator is not wired up (services.validator)")

    file_path = ctx.get("file_path")
    file_id = ctx.get("file_id")
    if not file_path:
        raise ConfigurationError("No file_path in the run's shared state")

    raw_rules = ctx.get("rules", {}) or {}
    rules = ValidationRules(
        min_size_bytes=int(raw_rules.get("min_size_bytes", 1)),
        expected_extensions=tuple(raw_rules.get("expected_extensions", ())),
        required_columns=tuple(raw_rules.get("required_columns", ())),
        min_rows=raw_rules.get("min_rows"),
        max_age_hours=raw_rules.get("max_age_hours"),
        reject_duplicate_hash=bool(raw_rules.get("reject_duplicate_hash", True)),
    )
    report = validator.validate(Path(file_path), rules)

    artifacts = [f for f in ctx.services.files.list(run_id=ctx.run_id) if f.id == file_id]
    if artifacts:
        artifact = artifacts[0]
        artifact.validation_status = (
            ValidationStatus.PASSED if report.passed else ValidationStatus.FAILED
        )
        artifact.validation_details = {"failures": report.failures, **report.details}
        artifact.row_count = report.row_count
        artifact.sha256 = report.sha256
        ctx.services.files.save(artifact)

    if not report.passed:
        ctx.emit(
            EventType.FILE_REJECTED,
            severity=Severity.ERROR,
            message="The file failed validation",
            payload={"failures": report.failures, "file_id": file_id},
        )
        raise DataQualityError("The file failed validation", details={"failures": report.failures})

    ctx.emit(
        EventType.FILE_VALIDATED,
        message="The file is valid",
        payload={"file_id": file_id, "rows": report.row_count, "sha256": report.sha256},
    )
    return StepResult.ok(sha256=report.sha256, row_count=report.row_count)


SELF_CHECK = WorkflowDefinition(
    key="platform.selfcheck",
    title="Platform self-check",
    description="Checks storage and records a health pulse.",
    steps=(
        StepDefinition(name="storage", uses="core.check_storage", title="Check storage"),
        StepDefinition(name="heartbeat", uses="core.heartbeat", title="Health pulse"),
    ),
)

COLLECT_REPORT = WorkflowDefinition(
    key="collect.report",
    title="Collect a report from a system",
    description="Download a report, then validate it before considering it successful.",
    steps=(
        StepDefinition(name="download", uses="extract.download_report", title="Download the report"),
        StepDefinition(name="validate", uses="extract.validate_file", title="Validate the file"),
    ),
)


def register_builtins(services: Any) -> None:
    services.step_registry.add("core.echo", echo)
    services.step_registry.add("core.check_storage", check_storage)
    services.step_registry.add("core.heartbeat", heartbeat)
    services.step_registry.add("extract.download_report", download_report)
    services.step_registry.add("extract.validate_file", validate_file)
    services.workflows.register(SELF_CHECK)
    services.workflows.register(COLLECT_REPORT)
