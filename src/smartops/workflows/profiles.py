"""تعريفات الأنظمة والتقارير: تحميل config/systems/*.yaml وتحويلها إلى
معطيات جاهزة لتشغيل سير العمل الجاهز collect.report.

كل نظام يوصف مرة واحدة بدل تكرار نفس الإعداد في كل تشغيل: الاسم، التقارير،
قواعد التحقق، الزمن الطبيعي، وقواعد الإنذار (انظر المخطط في MASTER_PLAN.md
القسم 7)، بالإضافة لطريقة المصادقة والجدولة (F-05). تعريف ناقص يرفع
ConfigurationError برسالة واضحة فورًا بدل فشل غامض أثناء التنفيذ لاحقًا.

ملاحظة: normal_duration_seconds وalert أصبحا يُستهلكان فعليًا من
extract.download_report (F-07) لإطلاق إنذار بطء بعتبات ثابتة. اكتشاف
الاتجاه/الخط الأساسي (baseline) يظل مؤجلًا لمرحلة الإنذار المبكر الكاملة (P5).
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
    """قاعدة إنذار تأخير بسيطة: عتبتا تحذير وحرجة بالثواني."""

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


@dataclass(frozen=True)
class ScheduleProfile:
    """جدولة تقرير: إما وقت يومي ثابت، أو كل عدد ثوانٍ. مش الاثنين معًا."""

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
        """يبني params جاهزة لـ runner.create_run("collect.report", params=...).

        لا يضع مفتاح "system" هنا؛ يُضاف من SystemProfile.to_run_params.
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
            f"التقرير غير معرّف في النظام {self.key}: {report_key}",
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
            f"حقل مطلوب مفقود ({field_name}) في {context}",
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
            f"وضع مصادقة غير معروف في النظام {system_key}: {mode}",
            details={"system": system_key, "mode": mode, "allowed": ["none", "session", "unattended"]},
        )
    login_url = raw.get("login_url", "") or ""
    if mode in ("session", "unattended") and not login_url:
        raise ConfigurationError(
            f"لازم login_url لأي نظام وضع مصادقته {mode} ({system_key})",
            details={"system": system_key},
        )
    credential_ref = raw.get("credential_ref", system_key) or system_key
    username_selector = raw.get("username_selector", "") or ""
    password_selector = raw.get("password_selector", "") or ""
    submit_selector = raw.get("submit_selector", "") or ""
    logged_in_selector = raw.get("logged_in_selector", "") or ""
    login_selector = raw.get("login_selector", "") or ""
    if mode == "unattended" and not all((username_selector, password_selector, submit_selector)):
        raise ConfigurationError(
            f"unattended auth needs username_selector, password_selector, and submit_selector ({system_key})",
            details={"system": system_key},
        )
    if mode == "unattended" and not (logged_in_selector or login_selector):
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
    )


def _parse_schedule(raw: dict[str, Any], *, context: str) -> ScheduleProfile:
    daily_at = raw.get("daily_at", "") or ""
    every_seconds = raw.get("every_seconds")
    if daily_at and every_seconds is not None:
        raise ConfigurationError(
            f"اختر daily_at أو every_seconds، مش الاثنين معًا، في {context}",
            details={"context": context},
        )
    if daily_at and not _TIME_RE.match(daily_at):
        raise ConfigurationError(
            f"صيغة daily_at غير صحيحة (المطلوب HH:MM) في {context}: {daily_at}",
            details={"context": context, "daily_at": daily_at},
        )
    if every_seconds is not None and float(every_seconds) <= 0:
        raise ConfigurationError(
            f"every_seconds لازم يكون أكبر من صفر في {context}",
            details={"context": context, "every_seconds": every_seconds},
        )
    return ScheduleProfile(
        daily_at=daily_at,
        every_seconds=float(every_seconds) if every_seconds is not None else None,
        enabled=bool(raw.get("enabled", True)),
    )


def _parse_report(raw: Any, *, system_key: str) -> ReportProfile:
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"تعريف تقرير غير صحيح (يجب أن يكون كائنًا) في النظام {system_key}",
            details={"system": system_key},
        )
    key = _require(raw, "key", context=f"تقرير داخل النظام {system_key}")
    context = f"التقرير {system_key}.{key}"
    url = _require(raw, "url", context=context)
    download_selector = raw.get("download_selector", "") or ""
    direct_download_url = raw.get("direct_download_url", "") or ""
    if not download_selector and not direct_download_url:
        raise ConfigurationError(
            f"لازم download_selector أو direct_download_url في {context}",
            details={"system": system_key, "report": key},
        )
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
    """يحوّل قاموسًا محمّلًا من YAML إلى SystemProfile متحقق منه."""
    source_label = str(source) if source else "(بلا مصدر)"
    if not isinstance(raw, dict):
        raise ConfigurationError(
            "تعريف النظام يجب أن يكون كائنًا (mapping)", details={"source": source_label}
        )
    key = _require(raw, "key", context=f"تعريف نظام ({source_label})")
    reports_raw = raw.get("reports")
    if not reports_raw or not isinstance(reports_raw, list):
        raise ConfigurationError(
            f"النظام {key} يحتاج قائمة reports غير فارغة", details={"system": key, "source": source_label}
        )
    reports = tuple(_parse_report(r, system_key=key) for r in reports_raw)
    auth = _parse_auth(raw.get("auth") or {}, system_key=key)
    return SystemProfile(key=key, name=raw.get("name", key), reports=reports, auth=auth, source=source)


def load_system_profiles(directory: Path | str | None = None) -> dict[str, SystemProfile]:
    """يحمّل كل ملفات *.yaml من مجلد الأنظمة. مجلد غير موجود = لا أنظمة، بلا خطأ."""
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
                f"مفتاح نظام مكرر: {profile.key}",
                details={"first": str(profiles[profile.key].source), "second": str(path)},
            )
        profiles[profile.key] = profile
    return profiles


class SystemRegistry:
    """سجل الأنظمة المحمّلة: نقطة دخول واحدة لبناء معطيات تشغيل collect.report."""

    def __init__(self, profiles: dict[str, SystemProfile] | None = None) -> None:
        self._profiles = dict(profiles or {})

    @classmethod
    def load(cls, directory: Path | str | None = None) -> "SystemRegistry":
        return cls(load_system_profiles(directory))

    def get(self, system_key: str) -> SystemProfile:
        if system_key not in self._profiles:
            raise ConfigurationError(
                f"نظام غير معرّف: {system_key}",
                details={"system": system_key, "available": sorted(self._profiles)},
            )
        return self._profiles[system_key]

    def list(self) -> list[SystemProfile]:
        return [self._profiles[key] for key in sorted(self._profiles)]

    def run_params(self, system_key: str, report_key: str) -> dict[str, Any]:
        return self.get(system_key).to_run_params(report_key)

    def iter_scheduled(self) -> list[tuple[SystemProfile, ReportProfile]]:
        """كل أزواج (نظام, تقرير) اللي جدولتها مفعّلة — مصدر بيانات الجدولة (F-08)."""
        pairs: list[tuple[SystemProfile, ReportProfile]] = []
        for system in self.list():
            for report in system.reports:
                if report.schedule.is_active:
                    pairs.append((system, report))
        return pairs
