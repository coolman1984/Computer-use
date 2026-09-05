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

from typing import Any
from urllib.parse import urlsplit

from ..domain.models import RecordingStep

PLAN_VERSION = 1

# Confidence per layer, used only to explain the plan to a non-technical
# reviewer ("this step is solid" vs "this step is a guess").
_LAYER_CONFIDENCE = {"dom": "high", "visual": "low", "manual": "none"}


def _layer_for(step: RecordingStep) -> str:
    """Pick the most durable layer this captured step can be replayed on.

    A selector survives a layout change; a click ratio does not, but it still
    beats having no replay at all. A step with neither is honestly reported as
    manual rather than silently dropped, so review shows the real gap.
    """
    if step.selector:
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

    The returned dict is stored on the Process and handed to the browser
    adapter as-is, so its shape is a contract: `actions` is an ordered list of
    {seq, layer, kind, selector, x_ratio, y_ratio, label}, and `expects_download`
    says whether a successful replay must produce a file.
    """
    actions: list[dict[str, Any]] = []
    for step in steps:
        if step.kind == "download":
            # A download is an outcome of the click before it, not a separate
            # action to replay: clicking it again would download twice.
            continue
        layer = _layer_for(step)
        actions.append(
            {
                "seq": len(actions) + 1,
                "layer": layer,
                "kind": step.kind,
                "selector": step.selector,
                "x_ratio": step.x_ratio,
                "y_ratio": step.y_ratio,
                "url": step.page_url_redacted,
                "label": step.target_text_redacted or step.selector or "click",
                "confidence": _LAYER_CONFIDENCE[layer],
            }
        )

    downloads = [s for s in steps if s.kind == "download"]
    return {
        "plan_version": PLAN_VERSION,
        "recording_id": recording_id,
        "system_key": system_key,
        "report_key": report_key,
        "start_url": start_url or _start_url(steps),
        "actions": actions,
        "expects_download": bool(downloads),
        "download_hint": downloads[0].download_ref if downloads else "",
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
        warnings.append(
            f"{len(weak)} step(s) will be repeated by screen position because the page gave no "
            "stable name for the element. They still work, but they are the first thing to "
            "break if the site's layout changes."
        )

    return {
        "ready": not problems,
        "problems": problems,
        "warnings": warnings,
        "action_count": len(actions),
        "weak_action_count": len(weak),
    }


def describe_plan(plan: dict[str, Any]) -> list[str]:
    """One plain sentence per action, for the review screen."""
    lines: list[str] = []
    for action in plan.get("actions") or []:
        label = action.get("label") or "an element"
        if action.get("layer") == "dom":
            lines.append(f"Click {label} (matched by name on the page).")
        elif action.get("layer") == "visual":
            lines.append(f"Click {label} (matched by its position on the page).")
        else:
            lines.append(f"Step {action.get('seq')} cannot be repeated automatically.")
    if plan.get("expects_download"):
        lines.append("Wait for the file to download, then check it is valid.")
    return lines
