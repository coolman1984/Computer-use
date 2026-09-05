from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest

from smartops.config import SafetySettings
from smartops.core.errors import PermanentError
from smartops.domain.enums import RecordingStatus
from smartops.services import Services
from smartops.storage.db import Database


def test_backup_contains_sqlite_and_private_recording_artifacts(services, tmp_path) -> None:
    record = services.recording_manager.create("backup", "local")
    artifact = Path(record.artifact_dir)
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    archive = services.recording_recovery.backup(tmp_path / "private-backups")
    assert archive.exists()
    with ZipFile(archive) as contents:
        assert "smartops.db" in contents.namelist()
        assert f"recordings/{record.id}/manifest.json" in contents.namelist()


def test_startup_recovery_marks_stale_recording_interrupted(settings, clock, slept) -> None:
    first = Services(settings, db=Database(":memory:"), clock=clock, sleeper=slept.append)
    record = first.recording_manager.create("stale", "local")
    record.status = RecordingStatus.RECORDING
    first.recordings.save(record)
    # A fresh Services wired onto the same database simulates a restart.
    second = Services(settings, db=first.db, clock=clock, sleeper=slept.append)
    assert second.recordings.get(record.id).status is RecordingStatus.INTERRUPTED
    second.close()


def test_permanent_purge_requires_explicit_configuration(services) -> None:
    with pytest.raises(PermanentError):
        services.recording_recovery.purge_expired()
