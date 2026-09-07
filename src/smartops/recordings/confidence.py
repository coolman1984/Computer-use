"""Say which recorded steps are weak, while they can still be redone.

A recording can look complete — every action has a selector, every action has
a proof — and still be built on guesses: a selector that only happens to work
because the page has not reloaded since it was assigned, or a click nobody
ever saw do anything. Discovering that forty minutes into an overnight replay
is exactly the failure mode `docs/RECORDER_ROADMAP.md` calls out. This module
scores a step the moment it exists, in words an operator who has never seen a
selector can act on: redo the step, or trust it.

Three dimensions, each answering a different question a reviewer would
otherwise have to work out by hand:

* ``target_identity`` — if this step ran again tomorrow, would it find the
  same control? A name, a test id, or an accessible role survives a reload; a
  framework's own throwaway id does not, and screen position survives nothing.
* ``effect_observability`` — did anything on the page prove this step did
  something, or is it a dispatched action with no witness?
* ``replay_safety`` — if this step's proof never arrives, can the platform
  just try it again, or would that risk doing it twice?

Nothing here invents a proof the replay engine cannot check; it only judges the
proof and the identity the recorder already produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Actions that never touch an element at all — a tab switch, a wait, a file
# that already arrived, a browser dialog already answered. Mirrors
# `converter.py::_NO_ELEMENT` exactly, for the same reason: nothing here needs
# a locator to be perfectly repeatable, so scoring it on how well it was
# identified would be scoring the wrong thing. (`manager.py::_NO_ELEMENT_ACTIONS`
# is a different, smaller set for a different check — it has no "download" or
# "dialog" — and is not what this mirrors.)
_NO_ELEMENT_ACTIONS = {"switch_page", "switch_frame", "navigate", "wait_for", "download", "dialog"}

# Mirrors the client-side GENERATED_ID pattern in `worker.py`'s capture script.
# The recorder already ranks a name, a test id, an aria-label, a role-plus-label
# and an href ahead of an element's raw id, and only falls back to the id when
# none of those exist. So a selector that still matches this pattern is one the
# recorder could find no better way to address — keep the two patterns in sync
# if either one changes.
GENERATED_ID_PATTERN = re.compile(
    r"(ext-gen|gwt-uid|yui_|__bvid__|:r[0-9a-z]+:|[0-9a-f]{8}-[0-9a-f]{4}-|\d{7,})",
    re.IGNORECASE,
)

# A step is called weak exactly when its worst dimension falls below this line.
# 1.0 marks a dimension with a real, defensible answer; 0.5 marks the one
# outcome that is a normal fact about the *action* rather than a flaw in how it
# was *captured* (a step that is simply unsafe to repeat, such as a submit);
# 0.35 and 0.0 mark the two outcomes that are actual guesses (a generated-looking
# id, or no observed evidence at all). 0.5 sits strictly between "unsafe but
# otherwise solid" and "a guess", so an unsafe-to-repeat step never gets called
# weak on that fact alone, while a generated id or an unproven effect always does.
WEAK_THRESHOLD = 0.5


@dataclass(frozen=True)
class DimensionScore:
    """One dimension's verdict: a number to sort by, a sentence to read."""

    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 2), "reason": self.reason}


@dataclass(frozen=True)
class StepConfidence:
    """The combined verdict on one step, and which dimension is responsible for it."""

    overall: float
    weak: bool
    reason: str
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": round(self.overall, 2),
            "weak": self.weak,
            "reason": self.reason,
            "dimensions": {name: dimension.to_dict() for name, dimension in self.dimensions.items()},
        }


def _locator_candidates(step: dict[str, Any]) -> list[str]:
    locator = step.get("locator") or {}
    candidates = [str(locator.get("value") or "")]
    candidates += [str(item) for item in (locator.get("fallbacks") or [])]
    return [value for value in candidates if value and value != "[redacted]"]


def _has_position_fallback(step: dict[str, Any]) -> bool:
    """Whether a screen-fraction click point was recorded for this step.

    Checked at both the raw step's top level (`x_ratio`/`y_ratio`, set for
    every click by `worker.py`) and inside a compiled plan action's locator
    (`converter.py::_action_from` moves it there) — the two shapes this
    function is asked to score.
    """
    locator = step.get("locator") or {}
    return (step.get("x_ratio") is not None and step.get("y_ratio") is not None) or (
        locator.get("x_ratio") is not None and locator.get("y_ratio") is not None
    )


def score_target_identity(step: dict[str, Any]) -> DimensionScore:
    """How likely this step's locator is to still mean the same thing tomorrow."""
    action = str(step.get("action") or step.get("kind") or "click")
    if action in _NO_ELEMENT_ACTIONS:
        return DimensionScore(1.0, "This step does not need to find a control on the page.")

    candidates = _locator_candidates(step)
    if candidates:
        if GENERATED_ID_PATTERN.search(candidates[0]):
            return DimensionScore(
                0.35,
                "This step's only identity is an id the page made up when it was loaded, "
                "like a ticket number — it may not exist, or may mean something else, the "
                "next time the page runs.",
            )
        return DimensionScore(
            1.0,
            "This step is identified by a name the page itself gives this control, which "
            "should stay the same the next time the page runs.",
        )

    if _has_position_fallback(step):
        return DimensionScore(
            0.0,
            "This step was found only by its position on screen, so it will break the next "
            "time the page changes.",
        )
    return DimensionScore(
        0.0,
        "There is no recorded way to find this element again at all — not a name, not an "
        "id, not even where it was on screen.",
    )


def _has_observed_evidence(observation: Any) -> bool:
    if observation is None:
        return False
    candidates = (
        observation.proof_candidates
        if hasattr(observation, "proof_candidates")
        else observation.get("proof_candidates")
    )
    return bool(candidates)


def score_effect_observability(step: dict[str, Any], observation: Any = None) -> DimensionScore:
    """Whether anything on the page was seen to change because of this step.

    ``observation`` is the step's `timeline.ActionObservation`, when one is
    available. It carries evidence a freshly-captured step's own ``success``
    field usually does not yet have — `_fill_contract` leaves most clicks at
    ``{"type": "none"}`` and only `converter.py` infers a real proof afterwards
    — so a live recording can only judge this dimension honestly by asking the
    timeline what it actually saw change.
    """
    success_type = str((step.get("success") or {}).get("type") or "none")
    if success_type != "none" or _has_observed_evidence(observation):
        return DimensionScore(
            1.0,
            "Something on the page was seen to change right after this step, which is real "
            "evidence it worked.",
        )
    return DimensionScore(
        0.0,
        "Nothing on the screen was seen to change after this step, so there is no evidence "
        "yet that it actually did anything.",
    )


def score_replay_safety(step: dict[str, Any]) -> DimensionScore:
    """Whether a failed attempt at this step can just be tried again."""
    retry = step.get("retry") or {}
    if bool(retry.get("safe_to_repeat")):
        return DimensionScore(
            1.0, "This step can be safely tried again if it does not work the first time."
        )
    # An unsafe-to-repeat step is often a normal fact about the action itself —
    # a submit, a download — not a flaw in how it was captured, so this pulls
    # the overall score down without on its own condemning a step that is
    # otherwise well identified and already proven to work.
    return DimensionScore(
        0.5,
        "If this step fails partway through, it cannot simply be tried again — repeating it "
        "could submit, download, or change something a second time.",
    )


def score_step(step: dict[str, Any], observation: Any = None) -> StepConfidence:
    """Score one step on every dimension, and report the one that would sink it.

    The overall score is the *worst* dimension, not an average: a step scored
    well on two dimensions and badly on the third is exactly as replayable as
    its worst dimension, and averaging would let that one real weakness hide
    behind two unrelated strengths.
    """
    dimensions = {
        "target_identity": score_target_identity(step),
        "effect_observability": score_effect_observability(step, observation),
        "replay_safety": score_replay_safety(step),
    }
    _, worst = min(dimensions.items(), key=lambda item: item[1].score)
    return StepConfidence(
        overall=worst.score,
        weak=worst.score < WEAK_THRESHOLD,
        reason=worst.reason,
        dimensions=dimensions,
    )
