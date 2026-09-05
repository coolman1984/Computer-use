"""Ready-made steps and workflows: a platform self-check + the standard report collection pattern.

The collection steps are fully written except for the real adapters (browser/validator), which are wired up later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters.notify.latency import evaluate_latency
from ..core.errors import (
    AuthError,
    ConfigurationError,
    DataQualityError,
    SmartOpsError,
    TransientError,
)
from ..core.ids import new_id
from ..domain.enums import AlertLevel, EventType, Severity, ValidationStatus
from ..domain.models import FileArtifact, StepDefinition, WorkflowDefinition
from ..engine.contracts import StepContext, StepResult
from ..ports.browser import ExtractionRequest, ReplayRequest
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
    destination = ensure_raw_dir(
        Path(ctx.services.settings.storage.raw_data_dir), system, report, now, ctx.run_id
    )
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


def replay_recording(ctx: StepContext) -> StepResult:
    """Replay a recorded plan and register the file it produced.

    This is the step that makes a recording an automation. It ends in exactly
    the same place as extract.download_report — a FileArtifact saved in the raw
    data centre — so the validate step, the incident opener, and the archiver
    treat both origins identically.
    """
    browser = getattr(ctx.services, "browser", None)
    if browser is None:
        raise ConfigurationError("The browser engine is not wired up (services.browser)")

    system = ctx.get("system")
    report = ctx.get("report")
    plan = ctx.get("plan") or {}
    if not system or not report:
        raise ConfigurationError("This step needs both system and report")
    if not plan.get("actions"):
        raise ConfigurationError(
            "This automation has no recorded steps to repeat. Record the workflow again."
        )

    now = ctx.services.runner.clock.now()
    settings = ctx.services.settings
    destination = ensure_raw_dir(
        Path(settings.storage.raw_data_dir), system, report, now, ctx.run_id
    )
    request = ReplayRequest(
        system=system,
        report=report,
        destination_dir=destination,
        plan=plan,
        period=ctx.get("period", ""),
        filters=_auth_filters(ctx.services, system),
        run_id=ctx.run_id,
        session_state_path=session_path(settings.storage.sessions_dir, system),
        evidence_dir=Path(settings.storage.incidents_dir) / "evidence" / slug(ctx.run_id),
    )
    result = browser.replay(request)
    if result.auth_required:
        raise AuthError(
            result.message or f"Session expired for system {system}",
            details={"system": system, "needs_login": True, "no_retry": True},
        )
    if not result.ok or not result.file_paths:
        raise TransientError(
            result.message or "Repeating the recording failed",
            details={
                "layer": result.layer_used.value,
                "evidence": result.evidence,
                # Which step stopped it, and how far the task got, belongs on the
                # failure: "it failed" is not something anyone can act on.
                "step_results": result.step_results,
            },
        )

    # One task can produce several files. Each is registered and validated in its
    # own right, so a summary that arrives and a detail that does not is a
    # failure rather than a success with something missing.
    file_ids: list[str] = []
    file_paths: list[str] = []
    for produced in result.file_paths:
        artifact = FileArtifact(
            id=new_id("file"),
            run_id=ctx.run_id,
            system=system,
            report=report,
            path=str(produced),
            original_name=produced.name,
            size_bytes=produced.stat().st_size if produced.exists() else 0,
            period=request.period,
            created_at=now,
        )
        ctx.services.files.save(artifact)
        file_ids.append(artifact.id)
        file_paths.append(artifact.path)
        ctx.emit(
            EventType.FILE_DOWNLOADED,
            message=f"Downloaded {produced.name} from {system} by repeating the recording",
            payload={
                "file_id": artifact.id,
                "path": artifact.path,
                "layer": result.layer_used.value,
                "size_bytes": artifact.size_bytes,
                "process_id": ctx.get("process_id"),
            },
        )

    _raise_latency_alert_if_needed(
        ctx, system=system, report=report, duration_seconds=result.duration_seconds
    )
    return StepResult.ok(
        file_ids=file_ids,
        file_paths=file_paths,
        # Kept so anything reading a single file still works.
        file_id=file_ids[0],
        file_path=file_paths[0],
        layer_used=result.layer_used.value,
        step_results=result.step_results,
    )


def _auth_filters(services: Any, system_key: str) -> dict[str, Any]:
    """Authentication filters for a system, or an empty dict when it needs no sign-in.

    A recorded automation carries no auth details of its own on purpose: they
    belong to the system definition, so changing how a system is signed into
    never means re-recording every automation that uses it.
    """
    systems = getattr(services, "systems", None)
    if systems is None:
        return {}
    try:
        system = systems.get(system_key)
    except SmartOpsError:
        return {}
    auth = system.auth
    filters: dict[str, Any] = {}
    if auth.logged_in_selector:
        filters["logged_in_selector"] = auth.logged_in_selector
    if auth.login_selector:
        filters["login_selector"] = auth.login_selector
    if auth.mode == "unattended":
        filters.update(
            {
                "login_url": auth.login_url,
                "credential_ref": auth.credential_ref,
                "username_selector": auth.username_selector,
                "password_selector": auth.password_selector,
                "submit_selector": auth.submit_selector,
            }
        )
    return filters


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
    """Checks every downloaded file. A failure here is a data-quality error, not a silent success."""
    validator = getattr(ctx.services, "validator", None)
    if validator is None:
        raise ConfigurationError("The file validator is not wired up (services.validator)")

    # A task can produce several files; all of them are checked. Falling back to
    # the singular keys keeps runs started before this change working.
    paths = ctx.get("file_paths") or ([ctx.get("file_path")] if ctx.get("file_path") else [])
    ids = ctx.get("file_ids") or ([ctx.get("file_id")] if ctx.get("file_id") else [])
    if not paths:
        raise ConfigurationError("No downloaded file in the run's shared state")

    raw_rules = ctx.get("rules", {}) or {}
    rules = ValidationRules(
        min_size_bytes=int(raw_rules.get("min_size_bytes", 1)),
        expected_extensions=tuple(raw_rules.get("expected_extensions", ())),
        required_columns=tuple(raw_rules.get("required_columns", ())),
        min_rows=raw_rules.get("min_rows"),
        max_age_hours=raw_rules.get("max_age_hours"),
        reject_duplicate_hash=bool(raw_rules.get("reject_duplicate_hash", True)),
    )
    by_id = {f.id: f for f in ctx.services.files.list(run_id=ctx.run_id)}
    all_failures: list[str] = []
    checksums: list[str] = []
    rows: list[int | None] = []

    for index, path in enumerate(paths):
        file_id = ids[index] if index < len(ids) else ""
        report = validator.validate(Path(path), rules)
        artifact = by_id.get(file_id)
        if artifact is not None:
            artifact.validation_status = (
                ValidationStatus.PASSED if report.passed else ValidationStatus.FAILED
            )
            artifact.validation_details = {"failures": report.failures, **report.details}
            artifact.row_count = report.row_count
            artifact.sha256 = report.sha256
            ctx.services.files.save(artifact)

        if report.passed:
            checksums.append(report.sha256)
            rows.append(report.row_count)
            ctx.emit(
                EventType.FILE_VALIDATED,
                message=f"{Path(path).name} is valid",
                payload={"file_id": file_id, "rows": report.row_count, "sha256": report.sha256},
            )
        else:
            named = [f"{Path(path).name}: {failure}" for failure in report.failures]
            all_failures.extend(named)
            ctx.emit(
                EventType.FILE_REJECTED,
                severity=Severity.ERROR,
                message=f"{Path(path).name} failed its checks",
                payload={"failures": report.failures, "file_id": file_id},
            )

    if all_failures:
        # The specific failure belongs in the run's own error message: the run
        # page and the incident both show that string, and "the file failed
        # validation" tells the user nothing they can act on.
        raise DataQualityError(
            "The file did not pass its checks: " + "; ".join(all_failures),
            details={"failures": all_failures},
        )

    return StepResult.ok(
        sha256=checksums[0] if checksums else "",
        sha256_list=checksums,
        row_count=rows[0] if rows else None,
        validated_count=len(paths),
    )


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


REPLAY_PROCESS = WorkflowDefinition(
    key="process.replay",
    title="Run a recorded automation",
    description="Repeat a reviewed recording, then validate the file it produced.",
    steps=(
        StepDefinition(name="replay", uses="automation.replay_recording", title="Repeat the recorded steps"),
        StepDefinition(name="validate", uses="extract.validate_file", title="Validate the file"),
    ),
)


def register_builtins(services: Any) -> None:
    services.step_registry.add("core.echo", echo)
    services.step_registry.add("core.check_storage", check_storage)
    services.step_registry.add("core.heartbeat", heartbeat)
    services.step_registry.add("extract.download_report", download_report)
    services.step_registry.add("extract.validate_file", validate_file)
    services.step_registry.add("automation.replay_recording", replay_recording)
    services.workflows.register(SELF_CHECK)
    services.workflows.register(COLLECT_REPORT)
    services.workflows.register(REPLAY_PROCESS)
