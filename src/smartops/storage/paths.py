"""Raw data directory layout: data/raw/YYYY/MM/DD/<system>/<report>/"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(value: str) -> str:
    cleaned = _SAFE.sub("-", (value or "unknown").strip())
    return cleaned.strip("-").lower() or "unknown"


def raw_dir(root: Path, system: str, report: str, when: datetime) -> Path:
    return root / f"{when:%Y}" / f"{when:%m}" / f"{when:%d}" / slug(system) / slug(report)


def ensure_raw_dir(root: Path, system: str, report: str, when: datetime) -> Path:
    target = raw_dir(root, system, report, when)
    target.mkdir(parents=True, exist_ok=True)
    return target
