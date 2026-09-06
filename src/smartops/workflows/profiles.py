"""System and report definitions: load config/systems/*.yaml and turn them
into ready-to-run parameters for the built-in collect.report workflow.

Each system is described once instead of repeating the same setup on every
run: name, reports, validation rules, normal duration, and alert rules (see
the schema in MASTER_PLAN.md section 7), plus the authentication mode and
schedule (F-05). An incomplete definition raises ConfigurationError with a
clear message immediately, instead of failing obscurely later at runtime.

Note: normal_duration_seconds and alert are now actually consumed by
extract.download_report (F-07) to raise a slowness alert against fixed
thresholds. Trend/baseline detection stays deferred to the full early-warning
phase (P5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.errors import ConfigurationError

DEFAULT_SYSTEMS_DIR = Path("config/systems")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class AlertRule:
    """A simple lateness alert rule: warning and critical thresholds in seconds."""

    warn_after_seconds: float | None = None
    critical_after_seconds: float | None = None


@dataclass(frozen=True)
class AuthProfile:
    """Authentication mode and selectors; credentials never belong in YAML."""

    mode: str = "none"
    login_url: str = ""
    logged_in_selector: str = ""
    login_selector: str = ""
    credential_ref: str = ""
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    language_selector: str = ""
    popup_trigger_selector: str = ""
    notice_close_selector: str = ""


@dataclass(frozen=True)
class ScheduleProfile:
    """Report schedule: either a fixed daily time or an interval in seconds, never both."""

    daily_at: str = ""
    every_seconds: float | None = None
    enabled: bool = True

    @property
    def is_active(self) -> bool:
        return self.enabled and bool(self.daily_at or self.every_seconds)


@dataclass(frozen=True)
class ReportProfile:
    key: str
    title: str
    url: str
    download_selector: str = ""
    direct_download_url: str = ""
    wait_selector: str = ""
    period: str = ""
    normal_duration_seconds: float = 60.0
    validation_rules: dict[str, Any] = field(default_factory=dict)
    alert: AlertRule = field(default_factory=AlertRule)
    schedule: ScheduleProfile = field(default_factory=ScheduleProfile)

    def to_run_params(self, auth: "AuthProfile | None" = None) -> dict[str, Any]:
        """Build params ready for runner.create_run("collect.report", params=...).

        The "system" key is not set here; SystemProfile.to_run_params adds it.
        """
        filters: dict[str, Any] = {}
        if self.url:
            filters["url"] = self.url
        if self.download_selector:
            filters["download_selector"] = self.download_selector
        if self.direct_download_url:
            filters["direct_download_url"] = self.direct_download_url
        if self.wait_selector:
            filters["wait_selector"] = self.wait_selector
        if auth is not None:
            if auth.logged_in_selector:
                filters["logged_in_selector"] = auth.logged_in_selector
            if auth.login_selector:
                filters["login_selector"] = auth.login_selector
            if auth.mode == "unattended":
                filters["login_url"] = auth.login_url
                filters["credential_ref"] = auth.credential_ref
                filters["username_selector"] = auth.username_selector
                filters["password_selector"] = auth.password_selector
                filters["submit_selector"] = auth.submit_selector
                if auth.language_selector:
                    filters["language_selector"] = auth.language_selector
                if auth.popup_trigger_selector:
                    filters["popup_trigger_selector"] = auth.popup_trigger_selector
                if auth.notice_close_selector:
                    filters["notice_close_selector"] = auth.notice_close_selector
        params: dict[str, Any] = {
            "report": self.key,
            "period": self.period,
            "filters": filters,
            "rules": dict(self.validation_rules),
            "normal_duration_seconds": self.normal_duration_seconds,
        }
        if self.alert.warn_after_seconds is not None:
            params["warn_after_seconds"] = self.alert.warn_after_seconds
        if self.alert.critical_after_seconds is not None:
            params["critical_after_seconds"] = self.alert.critical_after_seconds
        return params


@dataclass(frozen=True)
class SystemProfile:
    key: str
    name: str
    reports: tuple[ReportProfile, ...]
    auth: AuthProfile = field(default_factory=AuthProfile)
    source: Path | None = None

    def report(self, report_key: str) -> ReportProfile:
        for candidate in self.reports:
            if candidate.key == report_key:
                return candidate
        raise ConfigurationError(
            f"Report is not defined on system {self.key}: {report_key}",
            details={
                "system": self.key,
                "report": report_key,
                "available": [r.key for r in self.reports],
            },
        )

    def to_run_params(self, report_key: str) -> dict[str, Any]:
        params = self.report(report_key).to_run_params(self.auth)
        params["system"] = self.key
        return params


def _require(data: dict[str, Any], field_name: str, *, context: str) -> Any:
    value = data.get(field_name)
    if value in (None, ""):
        raise ConfigurationError(
            f"Required field missing ({field_name}) in {context}",
            details={"field": field_name, "context": context},
        )
    return value


def _parse_alert(raw: dict[str, Any]) -> AlertRule:
    warn = raw.get("warn_after_seconds")
    critical = raw.get("critical_after_seconds")
    return AlertRule(
        warn_after_seconds=float(warn) if warn is not None else None,
        critical_after_seconds=float(critical) if critical is not None else None,
    )


def _parse_auth(raw: dict[str, Any], *, system_key: str) -> AuthProfile:
    mode = raw.get("mode", "none") or "none"
    if mode not in ("none", "session", "unattended"):
        raise ConfigurationError(
            f"Unknown authentication mode on system {system_key}: {mode}",
            details={"system": system_key, "mode": mode, "allowed": ["none", "session", "unattended"]},
        )
    login_url = raw.get("login_url", "") or ""
    if mode in ("session", "unattended") and not login_url:
        raise ConfigurationError(
            f"login_url is required for any system using auth mode {mode} ({system_key})",
            details={"system": system_key},
        )
    credential_ref = raw.get("credential_ref", system_key) or system_key
    username_selector = raw.get("username_selector", "") or ""
    password_selector = raw.get("password_selector", "") or ""
    submit_selector = raw.get("submit_selector", "") or ""
    language_selector = raw.get("language_selector", "") or ""
    popup_trigger_selector = raw.get("popup_trigger_selector", "") or ""
    notice_close_selector = raw.get("notice_close_selector", "") or ""
    logged_in_selector = raw.get("logged_in_selector", "") or ""
    login_selector = raw.get("login_selector", "") or ""
    if mode == "unattended" and not all((username_selector, password_selector, submit_selector)):
        raise ConfigurationError(
            f"unattended auth needs username_selector, password_selector, and submit_selector ({system_key})",
            details={"system": system_key},
        )
    if mode == "unattended" and not (
        logged_in_selector or login_selector or popup_trigger_selector
    ):
        raise ConfigurationError(
            f"unattended auth needs a login success check ({system_key})",
            details={"system": system_key},
        )
    return AuthProfile(
        mode=mode,
        login_url=login_url,
        logged_in_selector=logged_in_selector,
        login_selector=login_selector,
        credential_ref=credential_ref,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        language_selector=language_selector,
        popup_trigger_selector=popup_trigger_selector,
        notice_close_selector=notice_close_selector,
    )


def validate_schedule(
    *, daily_at: str = "", every_seconds: float | None = None, context: str
) -> None:
    """One shared rule set for every schedule in the platform.

    Reports (defined in YAML) and automations (created in the UI) are scheduled
    by the same scheduler, so they must agree on what a legal schedule is —
    otherwise the UI would accept something the loader later rejects.
    """
    if daily_at and every_seconds is not None:
        raise ConfigurationError(
            f"Choose either a daily time or an interval, not both, in {context}",
            details={"context": context},
        )
    if daily_at and not _TIME_RE.match(daily_at):
        raise ConfigurationError(
            f"The daily time must look like HH:MM (24-hour) in {context}: {daily_at}",
            details={"context": context, "daily_at": daily_at},
        )
    if every_seconds is not None and float(every_seconds) <= 0:
        raise ConfigurationError(
            f"The interval must be greater than zero in {context}",
            details={"context": context, "every_seconds": every_seconds},
        )


def _parse_schedule(raw: dict[str, Any], *, context: str) -> ScheduleProfile:
    daily_at = raw.get("daily_at", "") or ""
    every_seconds = raw.get("every_seconds")
    validate_schedule(daily_at=daily_at, every_seconds=every_seconds, context=context)
    return ScheduleProfile(
        daily_at=daily_at,
        every_seconds=float(every_seconds) if every_seconds is not None else None,
        enabled=bool(raw.get("enabled", True)),
    )


def _parse_report(raw: Any, *, system_key: str) -> ReportProfile:
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Invalid report definition (must be a mapping) on system {system_key}",
            details={"system": system_key},
        )
    key = _require(raw, "key", context=f"a report inside system {system_key}")
    context = f"report {system_key}.{key}"
    url = _require(raw, "url", context=context)
    download_selector = raw.get("download_selector", "") or ""
    direct_download_url = raw.get("direct_download_url", "") or ""
    # A report page may be registered before its browser task is recorded.
    # Direct-download details are an optional fast path for legacy/development
    # collection; the normal production journey learns the download action from
    # a reviewed recording. Requiring a CSS selector here forced non-technical
    # users to understand implementation details before they could teach a task.
    return ReportProfile(
        key=key,
        title=raw.get("title", key),
        url=url,
        download_selector=download_selector,
        direct_download_url=direct_download_url,
        wait_selector=raw.get("wait_selector", "") or "",
        period=raw.get("period", "") or "",
        normal_duration_seconds=float(raw.get("normal_duration_seconds", 60.0)),
        validation_rules=dict(raw.get("validation_rules") or {}),
        alert=_parse_alert(raw.get("alert") or {}),
        schedule=_parse_schedule(raw.get("schedule") or {}, context=context),
    )


def parse_system_profile(raw: Any, *, source: Path | None = None) -> SystemProfile:
    """Convert a dict loaded from YAML into a validated SystemProfile."""
    source_label = str(source) if source else "(no source)"
    if not isinstance(raw, dict):
        raise ConfigurationError(
            "A system definition must be a mapping", details={"source": source_label}
        )
    key = _require(raw, "key", context=f"system definition ({source_label})")
    reports_raw = raw.get("reports")
    if not reports_raw or not isinstance(reports_raw, list):
        raise ConfigurationError(
            f"System {key} needs a non-empty reports list", details={"system": key, "source": source_label}
        )
    reports = tuple(_parse_report(r, system_key=key) for r in reports_raw)
    auth = _parse_auth(raw.get("auth") or {}, system_key=key)
    return SystemProfile(key=key, name=raw.get("name", key), reports=reports, auth=auth, source=source)


def load_system_profiles(directory: Path | str | None = None) -> dict[str, SystemProfile]:
    """Load every *.yaml in the systems directory. A missing directory means no systems, not an error."""
    base = Path(directory) if directory is not None else DEFAULT_SYSTEMS_DIR
    profiles: dict[str, SystemProfile] = {}
    if not base.exists():
        return profiles
    for path in sorted(base.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            continue
        profile = parse_system_profile(loaded, source=path)
        if profile.key in profiles:
            raise ConfigurationError(
                f"Duplicate system key: {profile.key}",
                details={"first": str(profiles[profile.key].source), "second": str(path)},
            )
        profiles[profile.key] = profile
    return profiles


def system_to_yaml_dict(profile: SystemProfile) -> dict[str, Any]:
    """Serialise a system back to the same YAML shape the loader reads.

    Round-tripping through the loader's own format is what lets a system be
    created in the web app and still be a hand-editable file on disk — one
    source of truth, two ways in.
    """
    auth: dict[str, Any] = {"mode": profile.auth.mode}
    for field_name in (
        "login_url",
        "logged_in_selector",
        "login_selector",
        "credential_ref",
        "username_selector",
        "password_selector",
        "submit_selector",
        "language_selector",
        "popup_trigger_selector",
        "notice_close_selector",
    ):
        value = getattr(profile.auth, field_name)
        if value:
            auth[field_name] = value

    reports: list[dict[str, Any]] = []
    for report in profile.reports:
        entry: dict[str, Any] = {"key": report.key, "title": report.title, "url": report.url}
        for field_name in ("download_selector", "direct_download_url", "wait_selector", "period"):
            value = getattr(report, field_name)
            if value:
                entry[field_name] = value
        entry["normal_duration_seconds"] = report.normal_duration_seconds
        if report.validation_rules:
            entry["validation_rules"] = dict(report.validation_rules)
        alert: dict[str, Any] = {}
        if report.alert.warn_after_seconds is not None:
            alert["warn_after_seconds"] = report.alert.warn_after_seconds
        if report.alert.critical_after_seconds is not None:
            alert["critical_after_seconds"] = report.alert.critical_after_seconds
        if alert:
            entry["alert"] = alert
        schedule: dict[str, Any] = {"enabled": report.schedule.enabled}
        if report.schedule.daily_at:
            schedule["daily_at"] = report.schedule.daily_at
        if report.schedule.every_seconds is not None:
            schedule["every_seconds"] = report.schedule.every_seconds
        entry["schedule"] = schedule
        reports.append(entry)

    return {"key": profile.key, "name": profile.name, "auth": auth, "reports": reports}


def system_file_path(directory: Path | str, system_key: str) -> Path:
    """Where a system's definition lives. One file per system, named by its key."""
    return Path(directory) / f"{system_key}.yaml"


class SystemRegistry:
    """Registry of loaded systems: one entry point for building collect.report run params."""

    def __init__(
        self, profiles: dict[str, SystemProfile] | None = None, *, directory: Path | None = None
    ) -> None:
        self._profiles = dict(profiles or {})
        self.directory = Path(directory) if directory is not None else DEFAULT_SYSTEMS_DIR

    @classmethod
    def load(cls, directory: Path | str | None = None) -> "SystemRegistry":
        base = Path(directory) if directory is not None else DEFAULT_SYSTEMS_DIR
        return cls(load_system_profiles(base), directory=base)

    def reload(self) -> "SystemRegistry":
        """Re-read the systems directory in place.

        Called after any change so a system added in the web app is usable
        immediately. Requiring a server restart for this was the single biggest
        reason the first step of the journey lived outside the product.
        """
        self._profiles = load_system_profiles(self.directory)
        return self

    def save(self, raw: dict[str, Any]) -> SystemProfile:
        """Validate a system definition and write it to disk, then reload.

        Validation runs first and on the same code path the loader uses, so a
        definition can never be written in a shape the platform would later
        refuse to start with.
        """
        profile = parse_system_profile(raw)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = system_file_path(self.directory, profile.key)
        existing = self._profiles.get(profile.key)
        if existing is not None and existing.source and Path(existing.source) != path:
            # The system already lives in a differently named file (someone
            # hand-wrote it). Update that file rather than creating a second
            # definition of the same key, which the loader would reject.
            path = Path(existing.source)
        path.write_text(
            yaml.safe_dump(system_to_yaml_dict(profile), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.reload()
        return self.get(profile.key)

    def delete(self, system_key: str) -> bool:
        """Remove a system definition file and reload. Missing is not an error."""
        profile = self._profiles.get(system_key)
        path = (
            Path(profile.source)
            if profile is not None and profile.source
            else system_file_path(self.directory, system_key)
        )
        removed = path.exists()
        if removed:
            path.unlink()
        self.reload()
        return removed

    def get(self, system_key: str) -> SystemProfile:
        if system_key not in self._profiles:
            raise ConfigurationError(
                f"System is not defined: {system_key}",
                details={"system": system_key, "available": sorted(self._profiles)},
            )
        return self._profiles[system_key]

    def list(self) -> list[SystemProfile]:
        return [self._profiles[key] for key in sorted(self._profiles)]

    def run_params(self, system_key: str, report_key: str) -> dict[str, Any]:
        return self.get(system_key).to_run_params(report_key)

    def iter_scheduled(self) -> list[tuple[SystemProfile, ReportProfile]]:
        """Every (system, report) pair with an active schedule — the scheduler's data source (F-08)."""
        pairs: list[tuple[SystemProfile, ReportProfile]] = []
        for system in self.list():
            for report in system.reports:
                if report.schedule.is_active:
                    pairs.append((system, report))
        return pairs
