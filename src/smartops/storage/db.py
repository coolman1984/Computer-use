"""The SQLite layer: a safe connection plus migrations that are harmless to re-run."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workflow_key TEXT NOT NULL,
    workflow_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    resume_at TEXT,
    error_class TEXT,
    error_message TEXT,
    lock_token TEXT,
    lock_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, resume_at);
CREATE INDEX IF NOT EXISTS idx_runs_workflow ON runs(workflow_key, created_at DESC);

CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    input TEXT NOT NULL DEFAULT '{}',
    output TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    error_class TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, name)
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT,
    step_name TEXT,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, created_at DESC);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    system TEXT NOT NULL,
    report TEXT NOT NULL,
    path TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    row_count INTEGER,
    period TEXT NOT NULL DEFAULT '',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_source ON files(system, report, created_at DESC);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    root_cause TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    pack_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_signature ON incidents(signature);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    run_id TEXT,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    thinking_level TEXT NOT NULL DEFAULT 'medium',
    reason TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    escalated_to TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    title TEXT NOT NULL,
    root_cause TEXT NOT NULL DEFAULT '',
    fix TEXT NOT NULL DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_signature ON knowledge(signature);
"""

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    system_key TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    parent_recording_id TEXT REFERENCES recordings(id),
    status TEXT NOT NULL,
    artifact_dir TEXT NOT NULL DEFAULT '',
    worker_pid INTEGER,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    error_message TEXT,
    step_count INTEGER NOT NULL DEFAULT 0,
    download_count INTEGER NOT NULL DEFAULT 0,
    automation_draft TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_recordings_system_active ON recordings(system_key, status, deleted_at);
CREATE INDEX IF NOT EXISTS idx_recordings_created ON recordings(created_at DESC);

CREATE TABLE IF NOT EXISTS recording_steps (
    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT,
    page_url_redacted TEXT NOT NULL DEFAULT '',
    page_title TEXT NOT NULL DEFAULT '',
    selector TEXT NOT NULL DEFAULT '',
    target_text_redacted TEXT NOT NULL DEFAULT '',
    x_ratio REAL,
    y_ratio REAL,
    changed_ratio REAL,
    request_ref TEXT NOT NULL DEFAULT '',
    download_ref TEXT NOT NULL DEFAULT '',
    before_image TEXT NOT NULL DEFAULT '',
    after_image TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(recording_id, seq)
);
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, SCHEMA_V1), (2, SCHEMA_V2))


class Database:
    """One SQLite connection per thread, in WAL mode to reduce contention."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._shared: sqlite3.Connection | None = None

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        if str(self.path) == ":memory:":
            if self._shared is None:
                self._shared = self._new_connection()
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        with self._write_lock:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def migrate(self) -> int:
        """Apply only the missing migrations and return the current schema version."""
        conn = self.connection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for version, script in MIGRATIONS:
            if version in applied:
                continue
            with self.transaction() as tx:
                tx.executescript(script)
                tx.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        if self._shared is not None:
            self._shared.close()
            self._shared = None
