"""Turn a finished recording into a plan the execution engine can actually replay.

This is the seam between "a human did it once" and "the platform does it every
night". The old version of this module produced a flat description of what was
captured and nothing consumed it, so a recording was a dead end. A plan built
here is executable input for the `process.replay` workflow — the same shape the
Playwright adapter walks action by action.

Layer order follows the golden rule (D004): a stable DOM selector first, then a
relative visual click (ratios, never absolute screen coordinates), and only
after that do we admit the step cannot be replayed unattended.

Nothing here reaches the network or the database; it is a pure transformation,
so a plan can be reviewed, diffed, and tested without launching anything.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..domain.models import RecordingStep

PLAN_VERSION = 2

# Confidence per layer, used only to explain the plan to a non-technical
# reviewer ("this step is solid" vs "this step is a guess").
_LAYER_CONFIDENCE = {"dom": "high", "visual": "low", "manual": "none"}

# Actions that do not act on an element and so need no locator.
_NO_ELEMENT = {"switch_page", "switch_frame", "navigate", "wait_for", "download"}
_NEXT_ACTIONABLE_ACTIONS = {"click", "fill", "select", "check"}
_OBSERVED_BEFORE = "_observed_visible_before"
_OBSERVED_AFTER = "_observed_visible_after"
_MODIFIER_KEYS = {"Control", "Shift", "Alt", "Meta"}

# These words identify a click which may submit, change, or publish business
# data.  The compiler is deliberately conservative: a false positive leaves a
# step for review; a false negative could make an irreversible action look
# successful without evidence.
_BUSINESS_IMPACT = re.compile(
    r"submit|save|delete|approve|send|confirm|post|export|download",
    re.IGNORECASE,
)


def _layer_for(step: RecordingStep) -> str:
    """Pick the most durable layer this captured step can be replayed on.

    A selector survives a layout change; a click ratio does not, but it still
    beats having no replay at all. A step with neither is honestly reported as
    manual rather than silently dropped, so review shows the real gap — except
    for the steps that act on no element at all, which need no locator to be
    perfectly repeatable.
    """
    if (step.action or step.kind) in _NO_ELEMENT:
        return "dom"
    if step.selector or (step.locator or {}).get("value"):
        return "dom"
    if step.x_ratio is not None and step.y_ratio is not None:
        return "visual"
    return "manual"


def _start_url(steps: list[RecordingStep], fallback: str = "") -> str:
    """The first real page URL seen during the recording — where a replay begins."""
    for step in steps:
        url = step.page_url_redacted
        if url and url != "about:blank" and urlsplit(url).scheme in ("http", "https"):
            return url
    return fallback


def build_plan(
    *,
    recording_id: str,
    system_key: str,
    report_key: str,
    steps: list[RecordingStep],
    start_url: str = "",
) -> dict[str, Any]:
    """Build the executable replay plan for one recording.

    The returned dict is stored on the Process and handed to the browser adapter
    as-is, so its shape is a contract. Each action carries the whole step: what
    it is, which tab and frame it happens in, how to find its element, what goes
    in, what proves it worked, where execution may resume, and whether repeating
    it is safe.

    A download is not an action to replay — the click before it caused it, and
    clicking again would fetch the file twice — but the number of downloads is
    kept, because a task that used to produce two files and now produces one has
    failed even though every step "worked".
    """
    actions: list[dict[str, Any]] = []
    downloads = [s for s in steps if (s.action or s.kind) == "download"]

    for step in steps:
        action_kind = step.action or step.kind
        if action_kind == "download":
            # The recording observed the file immediately after the preceding
            # action. That observation is legitimate success evidence for the
            # action which caused it; it is not a guessed selector or outcome.
            if actions and (actions[-1].get("success") or {}).get("type", "none") == "none":
                actions[-1]["success"] = {"type": "download_started"}
            continue
        if action_kind == "press":
            key = str((step.inputs or {}).get("key") or "")
            if key.split("+")[-1] in _MODIFIER_KEYS:
                # A modifier key being pressed is not a user action to replay.
                # Older recorder builds emitted Control+Control and
                # Control+Shift+Shift while a shortcut was being formed.
                continue
        actions.append(_action_from(step, seq=len(actions) + 1))

    _infer_observed_selector_proofs(actions)
    _infer_click_proofs(actions)
    _strip_observation_metadata(actions)

    # Where execution can safely pick up again. Only a step that has proved
    # itself can be a checkpoint: resuming after a step whose outcome was never
    # confirmed would skip work that may not have happened.
    for action in actions:
        if action["success"]["type"] != "none":
            action["checkpoint"] = f"after-step-{action['seq']}"

    return {
        "plan_version": PLAN_VERSION,
        "recording_id": recording_id,
        "system_key": system_key,
        "report_key": report_key,
        "start_url": start_url or _start_url(steps),
        "actions": actions,
        "expects_download": bool(downloads),
        "expected_download_count": len(downloads),
        "download_names": [d.inputs.get("file_name", "") for d in downloads if d.inputs],
        "download_hint": downloads[0].download_ref if downloads else "",
    }


def _infer_click_proofs(actions: list[dict[str, Any]]) -> None:
    """Use recorded, deterministic effects to prove ordinary navigation clicks.

    A next-step proof is only emitted for a DOM target in the same page/frame.
    Replay verifies that target was unavailable before the click and actionable
    afterwards, so merely having a later step cannot turn a dead click into a
    success.  Business-impact actions never receive this inferred proof.
    """
    for index, action in enumerate(actions[:-1]):
        following = actions[index + 1]
        if not _is_low_risk_unproven_click(action):
            continue

        if _opens_recorded_page(action, following):
            action["success"] = {"type": "new_page"}
            continue

        destination = _new_url_for_same_scope(action, following)
        if destination:
            action["success"] = {"type": "url_changed", "value": destination}
            continue

        if _can_be_next_step_proof(action, following):
            action["success"] = {
                "type": "next_step_actionable",
                "target": dict(following.get("target") or {"page": "main", "frame": ""}),
                "locator": dict(following.get("locator") or {}),
            }


def _infer_observed_selector_proofs(actions: list[dict[str, Any]]) -> None:
    """Prefer a result the recording actually observed over chained evidence.

    The recorder snapshots only visible, stable locators before an interaction
    and on its settled path. A selector that was absent before and visible after
    is direct proof for clicks and key presses, including business-impact
    actions. It is stronger than assuming a later step depended on the action.
    """
    for index, action in enumerate(actions):
        if (action.get("action") or action.get("kind")) not in {"click", "pointer_click", "press"}:
            continue
        if _proof_type(action.get("success")) != "none":
            continue
        before = _observed_locators(action, _OBSERVED_BEFORE)
        following = actions[index + 1] if index + 1 < len(actions) else None
        selector = ""
        if following is not None and _same_scope(action.get("target"), following.get("target")):
            # The next human action starts after the preceding interaction has
            # settled, so its pre-state is the most precise time boundary. It
            # cannot accidentally include effects from later unrelated steps.
            selector = _new_observed_selector(
                before, _observed_locators(following, _OBSERVED_BEFORE)
            )
        elif not _opens_recorded_page(action, following or {}):
            # There is no later sample in this page/frame (for example, a click
            # inside a frame followed by work in the parent). Then an immediate
            # post-action snapshot is the best available direct observation.
            selector = _new_observed_selector(
                before, _observed_locators(action, _OBSERVED_AFTER)
            )
        if selector:
            action["success"] = {"type": "selector_visible", "value": selector}


def _observed_locators(action: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = (action.get("inputs") or {}).get(key)
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict) and item.get("value")]


def _new_observed_selector(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> str:
    known = {
        str(selector)
        for locator in before
        for selector in [locator.get("value", ""), *(locator.get("fallbacks") or [])]
        if selector
    }
    for locator in after:
        selector = str(locator.get("value") or "")
        if selector and selector not in known and selector != "[redacted]":
            return selector
    return ""


def _strip_observation_metadata(actions: list[dict[str, Any]]) -> None:
    """Internal compiler evidence never becomes a replay input or review value."""
    for action in actions:
        inputs = action.get("inputs") or {}
        inputs.pop(_OBSERVED_BEFORE, None)
        inputs.pop(_OBSERVED_AFTER, None)


def _is_low_risk_unproven_click(action: dict[str, Any]) -> bool:
    if (action.get("action") or action.get("kind")) != "click":
        return False
    if _proof_type(action.get("success")) != "none":
        return False
    label = " ".join(
        str(action.get(name) or "") for name in ("label", "selector")
    )
    locator = action.get("locator") or {}
    label = f"{label} {locator.get('value') or ''}"
    return not _BUSINESS_IMPACT.search(label)


def _opens_recorded_page(action: dict[str, Any], following: dict[str, Any]) -> bool:
    target = following.get("target") or {}
    return (
        (following.get("action") or following.get("kind")) == "switch_page"
        and str(target.get("page") or "").startswith("page-")
    )


def _new_url_for_same_scope(action: dict[str, Any], following: dict[str, Any]) -> str:
    if not _same_scope(action.get("target"), following.get("target")):
        return ""
    before = str(action.get("url") or "")
    after = str(following.get("url") or "")
    if not before or not after or before == after:
        return ""
    parsed = urlsplit(after)
    if not parsed.scheme or not parsed.netloc:
        return ""
    # Query values can be redacted in recordings, so replay must prove the
    # stable route rather than compare a stored, possibly redacted query.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _can_be_next_step_proof(action: dict[str, Any], following: dict[str, Any]) -> bool:
    if not _same_scope(action.get("target"), following.get("target")):
        return False
    if (following.get("action") or following.get("kind")) not in _NEXT_ACTIONABLE_ACTIONS:
        return False
    locator = following.get("locator") or {}
    return (
        bool(locator.get("value") and locator.get("value") != "[redacted]")
        and not _locator_was_observed_before(action, locator)
    )


def _locator_was_observed_before(action: dict[str, Any], locator: dict[str, Any]) -> bool:
    wanted = {locator.get("value", ""), *(locator.get("fallbacks") or [])}
    wanted.discard("")
    return any(
        wanted.intersection(
            {item.get("value", ""), *(item.get("fallbacks") or [])}
        )
        for item in _observed_locators(action, _OBSERVED_BEFORE)
    )


def _same_scope(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    left = left or {}
    right = right or {}
    return (
        (left.get("page") or "main") == (right.get("page") or "main")
        and (left.get("frame") or "") == (right.get("frame") or "")
    )


def _proof_type(success: dict[str, Any] | None) -> str:
    return str((success or {}).get("type") or "none")


def _action_from(step: RecordingStep, *, seq: int) -> dict[str, Any]:
    """One recorded step as an executable action, upgrading an older one if needed."""
    action_kind = step.action or step.kind or "click"
    layer = _layer_for(step)
    locator = dict(step.locator or {})
    if not locator and step.selector:
        # A recording made before the contract existed: it has a selector and
        # nothing else. It still replays, on the layer it can support.
        locator = {"strategy": "css", "value": step.selector, "fallbacks": []}
    if step.x_ratio is not None and step.y_ratio is not None:
        # Kept as the last resort, used only when no selector matches.
        locator.setdefault("x_ratio", step.x_ratio)
        locator.setdefault("y_ratio", step.y_ratio)

    return {
        "seq": seq,
        "action": action_kind,
        "target": dict(step.target or {"page": "main", "frame": ""}),
        "locator": locator,
        "inputs": dict(step.inputs or {}),
        "success": dict(step.success or {"type": "none"}),
        "checkpoint": step.checkpoint,
        "retry": dict(step.retry or {"max_attempts": 1, "safe_to_repeat": False}),
        # Kept for the review screen and for older readers of the plan.
        "layer": layer,
        "kind": step.kind,
        "selector": step.selector,
        "x_ratio": step.x_ratio,
        "y_ratio": step.y_ratio,
        "url": step.page_url_redacted,
        "page_title": step.page_title,
        "label": step.target_text_redacted or step.selector or action_kind,
        "confidence": _LAYER_CONFIDENCE[layer],
    }


def review_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Judge whether a plan is fit to be tested, in words a non-technical reviewer can act on.

    This is the gate before a test run: a plan with no actions, no starting
    page, or no download would fail in the browser for reasons that look like
    a platform bug. Catching it here turns a confusing failure into a
    sentence that says what to do (record it again).
    """
    problems: list[str] = []
    warnings: list[str] = []

    actions = plan.get("actions") or []
    if not actions:
        problems.append(
            "The recording captured no steps, so there is nothing to replay. Record it again "
            "and make sure you click through the whole report inside the recording window."
        )
    if not plan.get("start_url"):
        problems.append(
            "The recording has no starting page. Record it again, beginning from the page "
            "where the report is opened."
        )
    if not plan.get("expects_download"):
        problems.append(
            "No file was downloaded during the recording. Record it again and click the "
            "export or download button, so the platform knows what a successful run produces."
        )

    weak = [a for a in actions if a.get("layer") == "visual"]
    manual = [a for a in actions if a.get("layer") == "manual"]
    if manual:
        problems.append(
            f"{len(manual)} step(s) could not be captured well enough to repeat. Record the "
            "workflow again, clicking buttons and links directly instead of dragging or "
            "using keyboard shortcuts."
        )
    if weak:
        problems.append(
            f"{len(weak)} step(s) can only be found by screen position. That is not reliable "
            "enough to approve. Add a real element selector in review, or record those steps again."
        )

    # A step with no evidence of success is repeated blind. It cannot pass the
    # review gate: the reviewer must add observable evidence or record the step
    # again. A final file is not proof that every earlier filter/navigation step
    # happened correctly.
    unproven = [a for a in actions if (a.get("success") or {}).get("type", "none") == "none"]
    if unproven:
        problems.append(
            f"{len(unproven)} step(s) have no proof of success. Add an observable result in "
            "the review screen, or record those steps again; they cannot be approved as guesses."
        )

    return {
        "ready": not problems,
        "problems": problems,
        "warnings": warnings,
        "action_count": len(actions),
        "weak_action_count": len(weak),
        "unproven_action_count": len(unproven),
        "download_count": int(plan.get("expected_download_count") or 0),
    }


def describe_plan(plan: dict[str, Any]) -> list[str]:
    """One plain sentence per action, for the review screen.

    The reviewer is deciding whether this is really the task they performed, so
    the sentences say what will happen in their words — including where it
    happens and how the platform will know it worked.
    """
    lines: list[str] = []
    for action in plan.get("actions") or []:
        lines.append(_describe_action(action))
    count = int(plan.get("expected_download_count") or 0)
    if count == 1:
        lines.append("Wait for the file to download, then check it is valid.")
    elif count > 1:
        lines.append(f"Wait for all {count} files to download, then check each one is valid.")
    return lines


def _describe_action(action: dict[str, Any]) -> str:
    kind = action.get("action") or action.get("kind") or "click"
    label = action.get("label") or "an element"
    inputs = action.get("inputs") or {}
    where = _describe_where(action)

    if kind == "fill":
        if inputs.get("secret_ref"):
            credential_field = inputs.get("secret_field") or "credential"
            body = f"Type the saved {credential_field} into {label}"
        else:
            body = f"Type \"{inputs.get('value', '')}\" into {label}"
    elif kind == "select":
        body = f"Choose \"{inputs.get('value', '')}\" from {label}"
    elif kind == "check":
        body = f"{'Tick' if inputs.get('checked', True) else 'Untick'} {label}"
    elif kind == "press":
        body = f"Press {inputs.get('key', 'Enter')}"
    elif kind == "pointer_click":
        body = f"Press and release {label}"
    elif kind == "switch_page":
        body = "Move to the tab the task opened"
    elif kind == "switch_frame":
        body = "Move into the panel on the page"
    elif kind == "navigate":
        body = f"Open {inputs.get('url', 'the next page')}"
    elif kind == "wait_for":
        body = "Wait for the page to catch up"
    elif action.get("layer") == "visual":
        body = f"Click {label} (matched by its position on the page)"
    elif action.get("layer") == "manual":
        return f"Step {action.get('seq')} cannot be repeated automatically."
    else:
        body = f"Click {label}"

    return f"{body}{where}{_describe_proof(action)}."


def _describe_where(action: dict[str, Any]) -> str:
    target = action.get("target") or {}
    if target.get("frame"):
        return ", inside the panel on the page"
    page = target.get("page") or "main"
    return "" if page in ("", "main") else ", in the other tab"


def _describe_proof(action: dict[str, Any]) -> str:
    """How the platform will know the step worked — the part that used to be missing."""
    success = action.get("success") or {}
    kind = success.get("type") or "none"
    return {
        "selector_visible": ", then wait until the result appears",
        "selector_hidden": ", then wait until it disappears",
        "value_equals": ", and check the value took",
        "value_not_empty": ", and check the field was filled",
        "checked_is": ", and check the box changed",
        "url_changed": ", and check the page moved",
        "new_page": ", and check a new tab opened",
        "page_available": "",
        "next_step_actionable": ", then check the next recorded control becomes available",
        "download_started": ", and check a file starts downloading",
        "network_response": ", and check the report service responds successfully",
        "none": "",
    }.get(kind, "")
