"""One record per human action, instead of five subsystems that each remember a piece of it.

Today a step is described in the database, the frame that proves it lives on
disk under `screenshots/`, the network calls it caused sit in a summary file,
and which tab it happened in exists only in the worker's memory while the
recording is open. Answering "what happened after the last click" means
reassembling all four, and a step's proof gets chosen from whichever fragment
happened to be at hand rather than from a considered before/after comparison.

This module is the one place that comparison happens. It does not capture
anything new: the visible-locator snapshots it diffs are exactly the ones
`_CAPTURE_SCRIPT` already gathers before and after a click, a hover, a drag and
a key press (`observedVisibleLocators` / `observedVisibleLocatorsAfter` in
`worker.py`), and the frame paths and quality verdicts are exactly the ones
`vision.PageVision` already produces. What is new is putting them next to each
other, once per action, so a step's evidence is a considered diff instead of
whichever fragment happened to survive.

`timeline.jsonl` is a sidecar next to the recording's `steps.jsonl`, in the
same spirit as `screenshots/quality.jsonl`: additional evidence about a
recording, never a replacement for the contract `RecordingStep` and the replay
engine already rely on.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Every success type `adapters/browser/replay.py::ReplaySession._verify` knows
# how to check. A proof candidate this module offers must come from that set —
# inventing a type replay cannot verify would be a promise nobody can keep, and
# AGENTS.md is explicit that this platform fails rather than guesses.
SUPPORTED_PROOF_TYPES = {
    "selector_visible",
    "selector_hidden",
    "value_equals",
    "value_not_empty",
    "checked_is",
    "url_changed",
    "new_page",
    "page_available",
    "download_started",
    "network_response",
    "next_step_actionable",
}

# Of those, only these four are ones this module's diff engine can honestly
# derive from a before/after visible-locator comparison and the action kind. A
# typed value or a checkbox state is proved by reading the field itself, which
# `worker.py::_fill_contract` already does at the moment of capture; duplicating
# that here from a locator diff would be a guess, not evidence.
_DERIVABLE_PROOF_TYPES = {"selector_visible", "selector_hidden", "new_page", "download_started"}


@dataclass(frozen=True)
class Snapshot:
    """What the page looked like at one instant, from senses already running.

    ``frame_quality`` is `vision.FrameQuality.to_dict()` or ``None`` when no
    frame was measured for this instant (most steps only measure a frame every
    `PageVision.deep_interval` calls; see vision.py for why). ``visible_locators``
    is the same bounded, selector-only snapshot the recorder already redacts
    through `_redacted_observed_locators` before it reaches Python — never an
    element's text, value, or absolute position.
    """

    frame_path: str = ""
    frame_quality: dict[str, Any] | None = None
    visible_locators: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_path": self.frame_path,
            "frame_quality": self.frame_quality,
            "visible_locators": list(self.visible_locators),
        }


@dataclass
class ActionObservation:
    """One thing a person did, with everything every sense recorded about it in one place.

    Named ``ActionObservation`` rather than ``Observation`` because
    `vision.Observation` already names a different thing — one page described
    in facts, independent of any particular action — and the two should never
    be confused for each other.
    """

    seq: int
    monotonic_at: float
    occurred_at: str
    trigger: dict[str, Any]
    target: dict[str, Any]
    before: Snapshot
    after: Snapshot
    changes: dict[str, list[str]] = field(default_factory=lambda: {"appeared": [], "disappeared": []})
    proof_candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "monotonic_at": self.monotonic_at,
            "occurred_at": self.occurred_at,
            "trigger": dict(self.trigger),
            "target": dict(self.target),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "changes": {
                "appeared": list(self.changes.get("appeared", [])),
                "disappeared": list(self.changes.get("disappeared", [])),
            },
            "proof_candidates": [dict(candidate) for candidate in self.proof_candidates],
        }


def _flatten_locators(locators: Any) -> set[str]:
    """Every selector string a locator snapshot names — its value and its fallbacks.

    A control that gained a stable id but lost its aria-label is still the same
    control appearing; comparing whole fallback sets rather than only the
    primary value is what keeps the diff from missing that.
    """
    flat: set[str] = set()
    if not isinstance(locators, list):
        return flat
    for item in locators:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "")
        if value:
            flat.add(value)
        for fallback in item.get("fallbacks") or []:
            if fallback:
                flat.add(str(fallback))
    return flat


def diff_visible_locators(
    before: list[dict[str, Any]] | None, after: list[dict[str, Any]] | None
) -> dict[str, list[str]]:
    """What became visible and what stopped being visible between two snapshots.

    Both snapshots are the same bounded, selector-only lists the recorder
    already gathers before and after a click, a hover, a drag or a key press —
    see `observedVisibleLocators` / `observedVisibleLocatorsAfter` in
    `worker.py`'s `_CAPTURE_SCRIPT`. This is a comparison of what was already
    collected, not a new probe of the page.
    """
    before_set, after_set = _flatten_locators(before), _flatten_locators(after)
    return {
        "appeared": sorted(after_set - before_set),
        "disappeared": sorted(before_set - after_set),
    }


def _proof_candidates_for(step: dict[str, Any], changes: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Turn a locator diff and the action's own kind into checkable proof.

    A newly-visible locator is `selector_visible` evidence; one that vanished
    is `selector_hidden`; a `switch_page` step exists at all only when the
    worker just tracked a genuinely new tab (see `_track_page`); a `download`
    step exists only once the file has actually been saved to disk (see
    `_finish_download`). Nothing here is inferred from timing or from what the
    next step happens to need — that inference already belongs to
    `converter.py`, which reasons about the whole sequence at once.
    """
    candidates: list[dict[str, Any]] = [
        {"type": "selector_visible", "value": value} for value in changes.get("appeared", [])
    ]
    candidates += [
        {"type": "selector_hidden", "value": value} for value in changes.get("disappeared", [])
    ]
    action = str(step.get("action") or step.get("kind") or "")
    if action == "switch_page":
        candidates.append({"type": "new_page"})
    if action == "download":
        candidates.append({"type": "download_started"})
    return [candidate for candidate in candidates if candidate["type"] in _DERIVABLE_PROOF_TYPES]


def _trigger_from_step(step: dict[str, Any]) -> dict[str, Any]:
    """What the person did, in the same redacted vocabulary the step itself uses.

    Everything read here has already passed through `redaction.py` on its way
    into the step dict; this only reshapes it, it never reaches back to the
    page for anything unredacted.
    """
    action = str(step.get("action") or step.get("kind") or "")
    trigger: dict[str, Any] = {"action": action}
    description = step.get("target_text_redacted") or ""
    if description and description != "[redacted]":
        trigger["description"] = description
    inputs = step.get("inputs") or {}
    if action == "select":
        trigger["value"] = inputs.get("value", "")
    elif action == "check":
        trigger["checked"] = bool(inputs.get("checked"))
    elif action == "press":
        trigger["key"] = inputs.get("key", "")
    elif action == "fill":
        trigger["field_kind"] = "secret" if inputs.get("secret_ref") else "text"
    return trigger


def _target_from_step(step: dict[str, Any]) -> dict[str, Any]:
    """Where the action happened: which tab, which frame, which control, and its geometry.

    ``bounds`` is deliberately never an absolute screen coordinate — only
    fractions of the viewport (``x_ratio``/``y_ratio``, recorded for every
    click) or fractions of the element itself (recorded only for a drawn
    surface too large to click through its centre; see ``relativePoint`` in
    `worker.py`). That is the same rule the recorder itself is bound by.
    """
    target = step.get("target") or {}
    locator = step.get("locator") or {}
    bounds: dict[str, float] = {}
    if step.get("x_ratio") is not None and step.get("y_ratio") is not None:
        bounds["x_ratio"] = step["x_ratio"]
        bounds["y_ratio"] = step["y_ratio"]
    if locator.get("position_mode") == "element_relative":
        bounds["element_x_ratio"] = locator.get("element_x_ratio")
        bounds["element_y_ratio"] = locator.get("element_y_ratio")
    return {
        "page": target.get("page", "main"),
        "frame": target.get("frame", ""),
        "locator": {
            "strategy": locator.get("strategy", "css"),
            "value": locator.get("value", ""),
            "fallbacks": list(locator.get("fallbacks") or []),
        },
        "bounds": bounds,
    }


class Timeline:
    """Every action's observation, in order, next to the recording it describes.

    A step's own contract (`RecordingStep`, `steps.jsonl`) does not change
    shape because of this class — it is read separately, from the step dict
    the worker was about to emit anyway, and appended here as a sidecar. See
    `screenshots/quality.jsonl` for the existing precedent this follows.
    """

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.path = self.artifact_dir / "timeline.jsonl"
        self._observations: list[ActionObservation] = []

    def __len__(self) -> int:
        return len(self._observations)

    def __iter__(self):
        return iter(self._observations)

    def record(
        self,
        step: dict[str, Any],
        *,
        quality_before: dict[str, Any] | None = None,
        quality_after: dict[str, Any] | None = None,
    ) -> ActionObservation:
        """Build this step's observation from data the recorder already collected, and keep it.

        ``quality_before``/``quality_after`` are `vision.FrameQuality.to_dict()`
        for the frames taken immediately before and after this step, when one
        was measured; the visible-locator snapshots come from
        ``step["inputs"]["_observed_visible_before"/"_observed_visible_after"]``,
        which `_attach_observed_locators` already places there for click, press,
        hover and drag steps.
        """
        inputs = step.get("inputs") or {}
        before_locators = inputs.get("_observed_visible_before") or []
        after_locators = inputs.get("_observed_visible_after") or []
        changes = diff_visible_locators(before_locators, after_locators)
        observation = ActionObservation(
            seq=len(self._observations) + 1,
            monotonic_at=time.monotonic(),
            occurred_at=datetime.now(timezone.utc).isoformat(),
            trigger=_trigger_from_step(step),
            target=_target_from_step(step),
            before=Snapshot(
                frame_path=str(step.get("before_image") or ""),
                frame_quality=quality_before,
                visible_locators=tuple(sorted(_flatten_locators(before_locators))),
            ),
            after=Snapshot(
                frame_path=str(step.get("after_image") or ""),
                frame_quality=quality_after,
                visible_locators=tuple(sorted(_flatten_locators(after_locators))),
            ),
            changes=changes,
            proof_candidates=_proof_candidates_for(step, changes),
        )
        self.append(observation)
        return observation

    def append(self, observation: ActionObservation) -> None:
        """Add an already-built observation and persist it immediately.

        Written one line at a time, like `steps.jsonl` and `quality.jsonl`
        already are, so a recording that ends abruptly still leaves every
        observation up to that point on disk rather than none of them.
        """
        self._observations.append(observation)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(observation.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # losing the sidecar record must never cost the step it describes

    def at(self, seq: int) -> ActionObservation | None:
        """What changed at one particular step, or ``None`` if it is not on this timeline."""
        for observation in self._observations:
            if observation.seq == seq:
                return observation
        return None

    def since(self, seq: int) -> list[ActionObservation]:
        """Every observation strictly after the given step — "what happened since step N"."""
        return [observation for observation in self._observations if observation.seq > seq]
