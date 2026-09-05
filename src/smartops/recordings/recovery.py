"""Maintenance operations for private recording data.

Backup archives are intentionally local and may contain sensitive private artifacts.
They are never exposed over HTTP or written into the repository.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..core.errors import PermanentError
from ..domain.enums import EventType, Severity


class RecordingRecovery:
    def __init__(self, services: Any) -> None:
        self.services = services

    def health(self) -> dict[str, object]:
        root = self.services.settings.storage.recordings_dir
        return {
            "status": "ok" if root.exists() and root.is_dir() else "unavailable",
            "recordings_dir": str(root),
            "active_workers": sum(1 for worker in self.services.recording_manager.workers.values() if worker.alive()),
            "recovered_interrupted": 0,
        }

    def backup(self, destination: Path | None = None) -> Path:
        """Create one private ZIP containing a consistent SQLite copy and recording artifacts."""
        target_dir = destination or self.services.settings.storage.recordings_backup_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.services.clock.now().strftime("%Y%m%dT%H%M%SZ")
        archive = target_dir / f"smartops-recordings-{stamp}.zip"
        db_copy = target_dir / f".smartops-recordings-{stamp}.db"
        try:
            snapshot = sqlite3.connect(db_copy)
            try:
                self.services.db.connection.backup(snapshot)
                snapshot.commit()
            finally:
                snapshot.close()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
                out.write(db_copy, "smartops.db")
                root = self.services.settings.storage.recordings_dir
                if root.exists():
                    for item in root.rglob("*"):
                        if item.is_file(): out.write(item, Path("recordings") / item.relative_to(root))
        finally:
            db_copy.unlink(missing_ok=True)
        return archive

    def purge_expired(self) -> int:
        settings = self.services.settings
        days = settings.storage.recordings_retention_days
        if not settings.safety.allow_recording_purge or days <= 0:
            raise PermanentError("Permanent delete is disabled; set a retention period and explicitly allow allow_recording_purge")
        threshold = self.services.clock.now() - timedelta(days=days)
        count = 0
        root = settings.storage.recordings_dir.resolve()
        for record in self.services.recordings.list(include_deleted=True, limit=10_000):
            if not record.deleted_at or record.deleted_at > threshold:
                continue
            artifact = Path(record.artifact_dir).resolve()
            try:
                artifact.relative_to(root)
            except ValueError as exc:
                raise PermanentError("Recording artifact path is outside the private recordings area") from exc
            if artifact.exists():
                shutil.rmtree(artifact)
            self.services.recordings.purge(record.id)
            self.services.events.emit(EventType.RECORDING_DELETED, severity=Severity.WARNING,
                message="Recording permanently deleted per the retention policy", payload={"recording_id": record.id, "permanent": True})
            count += 1
        return count
