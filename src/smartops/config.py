"""تحميل إعدادات SmartOps من ملف YAML مع إمكانية التجاوز عبر متغيرات البيئة."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/system.yaml")
EXAMPLE_CONFIG_PATH = Path("config/system.example.yaml")


@dataclass(frozen=True)
class AppSettings:
    name: str = "SmartOps"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class StorageSettings:
    sqlite_path: Path = Path("data/smartops.db")
    raw_data_dir: Path = Path("data/raw")
    incidents_dir: Path = Path("incidents")
    logs_dir: Path = Path("logs")


@dataclass(frozen=True)
class BrowserSettings:
    engine: str = "playwright"
    headless: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900
    max_concurrency: int = 4


@dataclass(frozen=True)
class SafetySettings:
    allow_production_code_changes: bool = False
    allow_destructive_actions: bool = False
    require_approval_for_sensitive_actions: bool = True


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    storage: StorageSettings
    browser: BrowserSettings
    safety: SafetySettings
    source: Path | None = None


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _resolve_config_path(path: Path | str | None) -> Path | None:
    if path is not None:
        return Path(path)
    env_path = os.getenv("SMARTOPS_CONFIG")
    if env_path:
        return Path(env_path)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    if EXAMPLE_CONFIG_PATH.exists():
        return EXAMPLE_CONFIG_PATH
    return None


def load_settings(path: Path | str | None = None) -> Settings:
    """يقرأ الإعدادات. لو الملف غير موجود نستخدم القيم الافتراضية الآمنة."""
    config_path = _resolve_config_path(path)
    raw: dict[str, Any] = {}
    if config_path is not None and config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded

    app_raw = _section(raw, "app")
    storage_raw = _section(raw, "storage")
    browser_raw = _section(raw, "browser")
    viewport_raw = browser_raw.get("default_viewport") or {}
    safety_raw = _section(raw, "safety")

    app = AppSettings(
        name=app_raw.get("name", "SmartOps"),
        environment=os.getenv("SMARTOPS_ENVIRONMENT", app_raw.get("environment", "development")),
        host=app_raw.get("host", "127.0.0.1"),
        port=int(os.getenv("SMARTOPS_PORT", app_raw.get("port", 8765))),
    )
    storage = StorageSettings(
        sqlite_path=Path(os.getenv("SMARTOPS_SQLITE_PATH", storage_raw.get("sqlite_path", "data/smartops.db"))),
        raw_data_dir=Path(storage_raw.get("raw_data_dir", "data/raw")),
        incidents_dir=Path(storage_raw.get("incidents_dir", "incidents")),
        logs_dir=Path(storage_raw.get("logs_dir", "logs")),
    )
    browser = BrowserSettings(
        engine=browser_raw.get("engine", "playwright"),
        headless=bool(browser_raw.get("headless", True)),
        viewport_width=int(viewport_raw.get("width", 1440)),
        viewport_height=int(viewport_raw.get("height", 900)),
        max_concurrency=int(browser_raw.get("max_concurrency", 4)),
    )
    safety = SafetySettings(
        allow_production_code_changes=bool(safety_raw.get("allow_production_code_changes", False)),
        allow_destructive_actions=bool(safety_raw.get("allow_destructive_actions", False)),
        require_approval_for_sensitive_actions=bool(
            safety_raw.get("require_approval_for_sensitive_actions", True)
        ),
    )
    return Settings(app=app, storage=storage, browser=browser, safety=safety, source=config_path)


def ensure_directories(settings: Settings) -> None:
    """ينشئ المجلدات المطلوبة للتشغيل المحلي."""
    settings.storage.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    for directory in (
        settings.storage.raw_data_dir,
        settings.storage.incidents_dir,
        settings.storage.logs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
