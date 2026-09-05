"""F-01 tests: the sessions directory and the external systems directory in settings."""

from __future__ import annotations

import os
from pathlib import Path

from smartops.config import StorageSettings, ensure_directories, load_settings


def test_storage_settings_defaults() -> None:
    storage = StorageSettings()
    assert storage.sessions_dir == Path("data/sessions")
    assert storage.systems_dir == Path("config/systems")


def test_load_settings_reads_storage_overrides_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "system.yaml"
    config_path.write_text(
        "storage:\n"
        "  sessions_dir: custom/sessions\n"
        "  systems_dir: custom/systems\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.storage.sessions_dir == Path("custom/sessions")
    assert settings.storage.systems_dir == Path("custom/systems")


def test_env_overrides_win_over_yaml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "system.yaml"
    config_path.write_text(
        "storage:\n  sessions_dir: yaml/sessions\n  systems_dir: yaml/systems\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTOPS_SESSIONS_DIR", "env/sessions")
    monkeypatch.setenv("SMARTOPS_SYSTEMS_DIR", "env/systems")
    settings = load_settings(config_path)
    assert settings.storage.sessions_dir == Path("env/sessions")
    assert settings.storage.systems_dir == Path("env/systems")


def test_ensure_directories_creates_sessions_dir(tmp_path: Path) -> None:
    storage = StorageSettings(
        sqlite_path=tmp_path / "db" / "smartops.db",
        raw_data_dir=tmp_path / "raw",
        incidents_dir=tmp_path / "incidents",
        logs_dir=tmp_path / "logs",
        history_dir=tmp_path / "history",
        sessions_dir=tmp_path / "sessions",
        systems_dir=tmp_path / "systems",
    )
    from smartops.config import AppSettings, BrowserSettings, SafetySettings, Settings

    settings = Settings(
        app=AppSettings(), storage=storage, browser=BrowserSettings(), safety=SafetySettings()
    )
    ensure_directories(settings)
    assert storage.sessions_dir.is_dir()
