"""تعريفات الأنظمة والتقارير: تحميل config/systems/*.yaml وتحويلها إلى
معطيات جاهزة لتشغيل سير العمل الجاهز collect.report.

كل نظام يوصف مرة واحدة بدل تكرار نفس الإعداد في كل تشغيل: الاسم، التقارير،
قواعد التحقق، الزمن الطبيعي، وقواعد الإنذار (انظر المخطط في MASTER_PLAN.md
القسم 7). تعريف ناقص يرفع ConfigurationError برسالة واضحة فورًا بدل فشل
غامض أثناء التنفيذ لاحقًا.

ملاحظة: normal_duration_seconds وalert ما زالا بيانات وصفية غير مستهلكة
بعد من محرك التشغيل؛ ستُستخدم في مرحلة الإنذار المبكر (P5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.errors import ConfigurationError

DEFAULT_SYSTEMS_DIR = Path("config/systems")


@dataclass(frozen=True)
class AlertRule:
    """قاعدة إنذار تأخير بسيطة: عتبتا تحذير وحرجة بالثواني."""

    warn_after_seconds: float | None = None
    critical_after_seconds: float | None = None


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

    def to_run_params(self) -> dict[str, Any]:
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
        return {
            "report": self.key,
            "period": self.period,
            "filters": filters,
            "rules": dict(self.validation_rules),
        }


@dataclass(frozen=True)
class SystemProfile:
    key: str
    name: str
    reports: tuple[ReportProfile, ...]
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
        params = self.report(report_key).to_run_params()
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
    return SystemProfile(key=key, name=raw.get("name", key), reports=reports, source=source)


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
