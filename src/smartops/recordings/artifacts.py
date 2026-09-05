from __future__ import annotations
from pathlib import Path

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".json"}
BLOCKED_PARTS = {"session", "trace", "network"}

def preview_path(root: Path, name: str) -> Path | None:
    candidate = (root / name).resolve()
    try: candidate.relative_to(root.resolve())
    except ValueError: return None
    if candidate.suffix.lower() not in ALLOWED_SUFFIXES or any(part in BLOCKED_PARTS for part in candidate.parts): return None
    return candidate if candidate.is_file() else None
