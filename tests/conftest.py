from __future__ import annotations

import pytest

from smartops.config import AppSettings, BrowserSettings, SafetySettings, Settings, StorageSettings
from smartops.credentials import InMemoryCredentialStore
from smartops.core.clock import FrozenClock
from smartops.services import Services
from smartops.storage.db import Database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app=AppSettings(environment="test"),
        storage=StorageSettings(
            sqlite_path=tmp_path / "smartops.db",
            raw_data_dir=tmp_path / "raw",
            incidents_dir=tmp_path / "incidents",
            logs_dir=tmp_path / "logs",
            history_dir=tmp_path / "history",
            sessions_dir=tmp_path / "sessions",
            systems_dir=tmp_path / "systems",
        ),
        browser=BrowserSettings(),
        safety=SafetySettings(),
    )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def services(settings, clock, slept) -> Services:
    svc = Services(
        settings,
        db=Database(":memory:"),
        clock=clock,
        sleeper=slept.append,
        credential_store=InMemoryCredentialStore(),
    )
    yield svc
    svc.close()
