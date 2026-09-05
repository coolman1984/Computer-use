"""Raw data layout: data/raw/YYYY/MM/DD/<system>/<report>/<run_id>/

The run id is part of the path, not decoration. Addressing output by date alone
meant the second run of a day wrote over the first one's file — same folder,
same server-suggested filename — while both database rows still pointed at that
one path. A day's earlier result was gone with nothing to show it had ever
existed, and the duplicate-hash check could not fire either, because it excludes
the file's own path and both runs shared it.

One run, one folder. That makes a result impossible to overwrite, makes "which
run produced this file" answerable from the path alone, and lets the duplicate
check actually compare two distinct files.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(value: str) -> str:
    cleaned = _SAFE.sub("-", (value or "unknown").strip())
    return cleaned.strip("-").lower() or "unknown"


def safe_segment(value: str) -> str:
    """Sanitise a value for use as one path segment, preserving its case.

    Run ids are already filesystem-safe and are matched against by eye and by
    search; lowercasing them (as slug does, correctly, for human-typed system and
    report names) makes "which run wrote this file" harder to answer from the
    path than it needs to be.
    """
    cleaned = _SAFE.sub("-", (value or "").strip()).strip("-")
    return cleaned or "unknown"


def raw_dir(root: Path, system: str, report: str, when: datetime, run_id: str = "") -> Path:
    """Where one run's files live.

    The date prefix keeps the tree browsable by a human; the run id underneath it
    keeps runs from colliding. A missing run id falls back to the date folder so
    older callers and already-collected files still resolve.
    """
    base = root / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}" / slug(system) / slug(report)
    return base / safe_segment(run_id) if run_id else base


def ensure_raw_dir(
    root: Path, system: str, report: str, when: datetime, run_id: str = ""
) -> Path:
    target = raw_dir(root, system, report, when, run_id)
    target.mkdir(parents=True, exist_ok=True)
    return target
