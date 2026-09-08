"""Safe, collision-free destinations for browser downloads.

The server controls ``suggested_filename``. It is useful display metadata, not
an authority to choose a path on the operator's disk. Every SmartOps download
therefore receives a reserved, run-local destination before Playwright writes
any bytes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_WINDOWS_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})
_MAX_FILENAME_LENGTH = 120


def safe_download_name(suggested: str | None, *, fallback: str = "download") -> str:
    """Return a safe basename that works on Windows and cannot escape a run folder."""
    raw = str(suggested or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1]
    name = _WINDOWS_FORBIDDEN.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = fallback
    stem, suffix = Path(name).stem, Path(name).suffix
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    # Keep the suffix where possible: report contracts often care about it.
    available = max(1, _MAX_FILENAME_LENGTH - len(suffix))
    name = f"{stem[:available]}{suffix[:20]}".strip(" .")
    return name or fallback


def reserve_download_path(
    destination: Path | str,
    suggested: str | None,
    *,
    fallback: str = "download",
) -> Path:
    """Atomically reserve an unused path under *destination*.

    The zero-byte reservation is intentional. It is collision protection, not
    evidence that the download succeeded; callers must still save, check size,
    validate, and register the resulting file.
    """
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    base = safe_download_name(suggested, fallback=fallback)
    stem, suffix = Path(base).stem, Path(base).suffix
    for sequence in range(1, 10_001):
        name = base if sequence == 1 else f"{stem}-{sequence}{suffix}"
        target = directory / name
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        else:
            os.close(descriptor)
            return target
    raise OSError("could not reserve a unique download filename")


def discard_empty_reservation(path: Path | str) -> None:
    """Remove only the known empty placeholder after a save failed.

    A reservation prevents a collision, but a failed browser save must not
    leave that placeholder looking like a received artifact. Never remove a
    non-empty path: partial data is evidence and needs explicit recovery.
    """
    target = Path(path)
    try:
        if target.is_file() and target.stat().st_size == 0:
            target.unlink()
    except OSError:
        pass
