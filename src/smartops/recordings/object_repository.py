"""A small, deterministic UI object repository for recorded browser plans.

The repository is intentionally embedded in a reviewed plan for now.  That
gives every application revision a reusable catalogue of descriptors without a
database migration or a hidden global selector store.  Actions carry an object
reference while retaining their inline locator for old readers; the current
runtime treats the repository as the authority whenever that reference exists.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


OBJECT_REPOSITORY_VERSION = 1


def build_object_repository(actions: list[dict[str, Any]], application: str) -> dict[str, Any]:
    """Attach stable object references to actionable steps and return the catalogue."""
    repository: dict[str, Any] = {
        "version": OBJECT_REPOSITORY_VERSION,
        "application": application,
        "application_version": "recorded",
        "screens": {},
        "objects": {},
    }
    for action in actions:
        register_action_object(repository, action)
    return repository


def register_action_object(repository: dict[str, Any], action: dict[str, Any]) -> str:
    """Create/reuse the descriptor for one action and return its object id.

    No object is made for navigation, waiting, downloads, or an action without
    an element descriptor.  This avoids giving non-elements a misleading UI
    identity.
    """
    locator = action.get("locator") or {}
    if not _has_descriptor(locator):
        return ""
    target = action.get("target") or {}
    screen = {"page": str(target.get("page") or "main"), "frame": str(target.get("frame") or "")}
    descriptor = {"screen": screen, "locator": _clean_locator(locator)}
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    object_id = f"ui-{sha256(encoded.encode('utf-8')).hexdigest()[:16]}"
    objects = repository.setdefault("objects", {})
    objects.setdefault(object_id, {"id": object_id, **deepcopy(descriptor)})
    screen_key = _screen_key(screen)
    screens = repository.setdefault("screens", {})
    screens.setdefault(screen_key, {"page": screen["page"], "frame": screen["frame"], "object_ids": []})
    if object_id not in screens[screen_key]["object_ids"]:
        screens[screen_key]["object_ids"].append(object_id)
    action["object_ref"] = object_id
    return object_id


def resolve_locator(action: dict[str, Any], repository: dict[str, Any] | None) -> dict[str, Any]:
    """Return the authoritative descriptor for an action or raise a safe error.

    Old plans have no reference and continue using their inline locator.  A new
    plan with a dangling reference must fail clearly; falling back to an old
    duplicated selector would conceal repository drift.
    """
    ref = str(action.get("object_ref") or "")
    inline = action.get("locator") or {}
    if not ref:
        return dict(inline)
    objects = (repository or {}).get("objects") or {}
    entry = objects.get(ref)
    if not isinstance(entry, dict) or not isinstance(entry.get("locator"), dict):
        raise KeyError(ref)
    return deepcopy(entry["locator"])


def _has_descriptor(locator: dict[str, Any]) -> bool:
    anchor = locator.get("anchor") or {}
    semantic = locator.get("semantic") or {}
    return bool(
        locator.get("value")
        or locator.get("fallbacks")
        or (isinstance(anchor, dict) and anchor.get("container") and anchor.get("target"))
        or (isinstance(semantic, dict) and semantic.get("role"))
    )


def _clean_locator(locator: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(locator)
    result.pop("object_ref", None)
    return result


def _screen_key(screen: dict[str, str]) -> str:
    encoded = json.dumps(screen, sort_keys=True, separators=(",", ":"))
    return f"screen-{sha256(encoded.encode('utf-8')).hexdigest()[:12]}"
