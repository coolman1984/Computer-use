"""Bind a successful test and approval to the automation that will actually run.

The digest deliberately contains no credential value and is stored only as a
hash.  It prevents an approval from silently surviving a changed plan, report
rule, authentication definition, or browser execution mode.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


ADMISSION_KEY = "_smartops_admission"
RUNTIME_CONTRACT_VERSION = 3


def current_execution_digest(services: Any, process: Any) -> str:
    """Digest the execution-relevant contract without retaining sensitive data."""
    try:
        auth = vars(services.systems.get(process.system_key).auth)
    except Exception:
        # A missing system must make the identity different, never accidentally
        # match a previously tested configuration.
        auth = {"system_profile": "unavailable"}
    browser = services.settings.browser
    contract = {
        "runtime_contract": RUNTIME_CONTRACT_VERSION,
        "process": {
            "id": process.id,
            "version": process.version,
            "plan": _plan_without_admission(process.plan),
            "validation_rules": process.validation_rules,
        },
        "authentication": auth,
        "browser": {
            "engine": browser.engine,
            "headless": browser.headless,
            "viewport": [browser.viewport_width, browser.viewport_height],
            "persistent_profile": bool(browser.user_data_dir.strip()),
            "profile_directory": browser.profile_directory,
            "extensions_enabled": browser.enable_extensions,
        },
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def admission_values(plan: dict[str, Any]) -> dict[str, str]:
    """Return the non-sensitive binding facts stored on a compiled plan."""
    value = (plan or {}).get(ADMISSION_KEY)
    if not isinstance(value, dict):
        return {}
    return {key: str(item) for key, item in value.items() if isinstance(item, str)}


def with_admission(plan: dict[str, Any], **values: str | None) -> dict[str, Any]:
    """Copy a plan and update only its private, non-executable admission facts."""
    result = deepcopy(plan or {})
    current = admission_values(result)
    for key, value in values.items():
        if value:
            current[key] = value
        else:
            current.pop(key, None)
    result[ADMISSION_KEY] = current
    return result


def _plan_without_admission(plan: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(plan or {})
    result.pop(ADMISSION_KEY, None)
    return result
