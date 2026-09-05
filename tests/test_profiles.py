"""اختبارات S-03: تحميل تعريفات الأنظمة وتحويلها لمعطيات collect.report."""

from __future__ import annotations

from pathlib import Path

import pytest

from smartops.core.errors import ConfigurationError
from smartops.domain.enums import RunStatus
from smartops.workflows.profiles import (
    SystemRegistry,
    load_system_profiles,
    parse_system_profile,
)

VALID_YAML = """
key: erp_demo
name: نظام تجريبي
reports:
  - key: daily_sales
    title: تقرير المبيعات
    url: "https://intranet.example.local/reports/daily-sales"
    download_selector: "#export-csv"
    wait_selector: "#report-ready"
    period: daily
    normal_duration_seconds: 45
    validation_rules:
      min_size_bytes: 10
      expected_extensions: [".csv"]
      required_columns: ["a", "b"]
      min_rows: 1
    alert:
      warn_after_seconds: 90
      critical_after_seconds: 180
  - key: direct_report
    title: تقرير مباشر
    url: "https://intranet.example.local/reports/direct"
    direct_download_url: "https://intranet.example.local/exports/direct.csv"
"""


def test_parse_valid_system_profile() -> None:
    import yaml

    profile = parse_system_profile(yaml.safe_load(VALID_YAML))

    assert profile.key == "erp_demo"
    assert profile.name == "نظام تجريبي"
    assert [r.key for r in profile.reports] == ["daily_sales", "direct_report"]

    report = profile.report("daily_sales")
    assert report.url.endswith("daily-sales")
    assert report.alert.warn_after_seconds == 90
    assert report.alert.critical_after_seconds == 180
    assert report.validation_rules["required_columns"] == ["a", "b"]


def test_to_run_params_matches_collect_report_contract() -> None:
    import yaml

    profile = parse_system_profile(yaml.safe_load(VALID_YAML))
    params = profile.to_run_params("daily_sales")

    assert params["system"] == "erp_demo"
    assert params["report"] == "daily_sales"
    assert params["period"] == "daily"
    assert params["filters"]["url"].endswith("daily-sales")
    assert params["filters"]["download_selector"] == "#export-csv"
    assert params["filters"]["wait_selector"] == "#report-ready"
    assert params["rules"]["required_columns"] == ["a", "b"]


def test_direct_download_report_has_no_selector_requirement() -> None:
    import yaml

    profile = parse_system_profile(yaml.safe_load(VALID_YAML))
    params = profile.to_run_params("direct_report")

    assert "download_selector" not in params["filters"]
    assert params["filters"]["direct_download_url"].endswith("direct.csv")


def test_missing_system_key_raises_clear_error() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML)
    del raw["key"]

    with pytest.raises(ConfigurationError, match="key"):
        parse_system_profile(raw)


def test_report_without_selector_or_direct_url_raises_clear_error() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML)
    raw["reports"][0].pop("download_selector")

    with pytest.raises(ConfigurationError, match="download_selector"):
        parse_system_profile(raw)


def test_report_missing_url_raises_clear_error() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML)
    del raw["reports"][1]["url"]

    with pytest.raises(ConfigurationError, match="url"):
        parse_system_profile(raw)


def test_empty_reports_list_raises_clear_error() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML)
    raw["reports"] = []

    with pytest.raises(ConfigurationError, match="reports"):
        parse_system_profile(raw)


def test_load_system_profiles_from_directory(tmp_path: Path) -> None:
    (tmp_path / "erp.yaml").write_text(VALID_YAML, encoding="utf-8")
    profiles = load_system_profiles(tmp_path)

    assert set(profiles) == {"erp_demo"}
    assert profiles["erp_demo"].source == tmp_path / "erp.yaml"


def test_missing_directory_returns_empty(tmp_path: Path) -> None:
    profiles = load_system_profiles(tmp_path / "does_not_exist")
    assert profiles == {}


def test_duplicate_system_key_across_files_raises(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(VALID_YAML, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(VALID_YAML, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="مكرر"):
        load_system_profiles(tmp_path)


def test_example_config_in_repo_is_valid() -> None:
    """ملف config/systems/example.yaml في المستودع لازم يفضل صالحًا دايمًا."""
    profiles = load_system_profiles("config/systems")
    assert "erp_demo" in profiles
    assert len(profiles["erp_demo"].reports) >= 1


def test_registry_unknown_system_raises_with_available_list(tmp_path: Path) -> None:
    (tmp_path / "erp.yaml").write_text(VALID_YAML, encoding="utf-8")
    registry = SystemRegistry.load(tmp_path)

    with pytest.raises(ConfigurationError) as exc_info:
        registry.get("nope")
    assert exc_info.value.details["available"] == ["erp_demo"]


def test_registry_unknown_report_raises(tmp_path: Path) -> None:
    (tmp_path / "erp.yaml").write_text(VALID_YAML, encoding="utf-8")
    registry = SystemRegistry.load(tmp_path)

    with pytest.raises(ConfigurationError) as exc_info:
        registry.run_params("erp_demo", "nope")
    assert "daily_sales" in exc_info.value.details["available"]


def test_registry_list_is_sorted(tmp_path: Path) -> None:
    (tmp_path / "z.yaml").write_text(VALID_YAML.replace("erp_demo", "zzz_system"), encoding="utf-8")
    (tmp_path / "a.yaml").write_text(VALID_YAML.replace("erp_demo", "aaa_system"), encoding="utf-8")
    registry = SystemRegistry.load(tmp_path)

    assert [p.key for p in registry.list()] == ["aaa_system", "zzz_system"]


def test_profile_params_feed_collect_report_end_to_end(services, tmp_path: Path) -> None:
    """اختبار تكاملي: params من ملف تعريف حقيقي تشتغل مباشرة مع collect.report."""
    from smartops.domain.enums import ExtractionLayer
    from smartops.ports.browser import ExtractionRequest, ExtractionResult
    from smartops.ports.validation import ValidationReport, ValidationRules

    class FakeBrowser:
        def extract(self, request: ExtractionRequest) -> ExtractionResult:
            assert request.filters["download_selector"] == "#export-csv"
            target = Path(request.destination_dir) / "daily_sales.csv"
            target.write_bytes(b"a,b\n1,2\n")
            return ExtractionResult(
                ok=True,
                layer_used=ExtractionLayer.NETWORK,
                file_path=target,
                original_name=target.name,
                size_bytes=target.stat().st_size,
            )

        def capture_evidence(self, run_id: str) -> dict:
            return {}

    class FakeValidator:
        def validate(self, path: Path, rules: ValidationRules) -> ValidationReport:
            assert rules.required_columns == ("a", "b")
            return ValidationReport(passed=True, sha256="x", row_count=1)

    (tmp_path / "erp.yaml").write_text(VALID_YAML, encoding="utf-8")
    registry = SystemRegistry.load(tmp_path)
    params = registry.run_params("erp_demo", "daily_sales")
    params["rules"] = {**params["rules"], "required_columns": ("a", "b")}

    services.browser = FakeBrowser()
    services.validator = FakeValidator()

    run = services.runner.create_run("collect.report", params=params)
    run = services.runner.execute(run.id)

    assert run.status is RunStatus.SUCCEEDED


# ---------- اختبارات F-05: المصادقة والجدولة ----------

VALID_YAML_WITH_AUTH_AND_SCHEDULE = """
key: erp_demo
name: نظام تجريبي
auth:
  mode: session
  login_url: "https://intranet.example.local/login"
  logged_in_selector: "#user-menu"
  login_selector: "#login-form"
reports:
  - key: daily_sales
    title: تقرير المبيعات
    url: "https://intranet.example.local/reports/daily-sales"
    download_selector: "#export-csv"
    period: daily
    alert:
      warn_after_seconds: 90
      critical_after_seconds: 180
    schedule:
      daily_at: "08:00"
  - key: hourly_report
    title: تقرير كل ساعة
    url: "https://intranet.example.local/reports/hourly"
    download_selector: "#export-csv"
    schedule:
      every_seconds: 3600
"""


def test_auth_profile_parses() -> None:
    import yaml

    profile = parse_system_profile(yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE))
    assert profile.auth.mode == "session"
    assert profile.auth.login_url.endswith("/login")
    assert profile.auth.logged_in_selector == "#user-menu"
    assert profile.auth.login_selector == "#login-form"


def test_session_mode_without_login_url_raises() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE)
    del raw["auth"]["login_url"]

    with pytest.raises(ConfigurationError, match="login_url"):
        parse_system_profile(raw)


def test_unknown_auth_mode_raises() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE)
    raw["auth"]["mode"] = "password"

    with pytest.raises(ConfigurationError):
        parse_system_profile(raw)


def test_bad_daily_at_format_raises() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE)
    raw["reports"][0]["schedule"]["daily_at"] = "8am"

    with pytest.raises(ConfigurationError, match="daily_at"):
        parse_system_profile(raw)


def test_both_schedule_kinds_together_raises() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE)
    raw["reports"][0]["schedule"]["every_seconds"] = 60

    with pytest.raises(ConfigurationError):
        parse_system_profile(raw)


def test_zero_every_seconds_raises() -> None:
    import yaml

    raw = yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE)
    raw["reports"][1]["schedule"]["every_seconds"] = 0

    with pytest.raises(ConfigurationError):
        parse_system_profile(raw)


def test_to_run_params_carries_auth_selectors_and_thresholds() -> None:
    import yaml

    profile = parse_system_profile(yaml.safe_load(VALID_YAML_WITH_AUTH_AND_SCHEDULE))
    params = profile.to_run_params("daily_sales")

    assert params["filters"]["logged_in_selector"] == "#user-menu"
    assert params["filters"]["login_selector"] == "#login-form"
    assert params["warn_after_seconds"] == 90
    assert params["critical_after_seconds"] == 180
    assert "normal_duration_seconds" in params


def test_iter_scheduled_returns_only_active_pairs(tmp_path: Path) -> None:
    (tmp_path / "erp.yaml").write_text(VALID_YAML_WITH_AUTH_AND_SCHEDULE, encoding="utf-8")
    (tmp_path / "unscheduled.yaml").write_text(
        VALID_YAML.replace("erp_demo", "no_schedule_system"), encoding="utf-8"
    )
    registry = SystemRegistry.load(tmp_path)

    pairs = registry.iter_scheduled()
    keys = {(system.key, report.key) for system, report in pairs}

    assert ("erp_demo", "daily_sales") in keys
    assert ("erp_demo", "hourly_report") in keys
    assert not any(system.key == "no_schedule_system" for system, _ in pairs)
