from __future__ import annotations
from pathlib import Path

# name.suffix -> Content-Type. ".json" here is only the sanitized network
# summary (already redacted by redact_url/safe_network_summary) — never raw
# step/session data, which lives outside these suffixes entirely.
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".json": "application/json"}
# "profile" is the live Chrome persistent-context profile: cookies, local
# storage, real login state. "session" is the saved storage_state.json — same
# category of secret. "trace" (trace.zip) can embed response bodies. All three
# stay server-side only. "network" is intentionally NOT blocked: the one file
# under it (sanitized-summary.json) is the redacted review artifact this
# preview endpoint exists for.
BLOCKED_PARTS = {"session", "trace", "profile"}

def preview_path(root: Path, name: str) -> Path | None:
    candidate = (root / name).resolve()
    try: candidate.relative_to(root.resolve())
    except ValueError: return None
    if candidate.suffix.lower() not in CONTENT_TYPES or any(part in BLOCKED_PARTS for part in candidate.parts): return None
    return candidate if candidate.is_file() else None
