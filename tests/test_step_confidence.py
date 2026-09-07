"""Proving Phase 8: a step is judged weak or not from named, independently
meaningful dimensions, combined by an explicit rule, in words an operator who
has never seen a selector can act on.

Most of this is proven as pure functions against hand-built step dicts —
`confidence.py` only judges data the recorder and the plan compiler already
produce, so it needs no browser to prove its rules are right. The last section
proves the two places that data actually comes from: the worker flags a weak
step while the browser is still open, and `converter.review_plan` refuses a
plan that contains one converter's older checks would have let through.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from smartops.recordings.confidence import (
    score_effect_observability,
    score_replay_safety,
    score_step,
    score_target_identity,
)
from smartops.recordings.converter import review_plan
from smartops.recordings.timeline import ActionObservation, Snapshot
from tests.recorded_site import LocalSite


def _step(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "click",
        "target": {"page": "main", "frame": ""},
        "locator": {"strategy": "css", "value": "", "fallbacks": []},
        "inputs": {},
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    }
    base.update(overrides)
    return base


# ---------- target_identity ----------


def test_a_step_with_no_locator_and_no_screen_position_scores_the_weakest_identity():
    score = score_target_identity(_step())

    assert score.score == 0.0
    assert "no recorded way to find" in score.reason.lower()


def test_a_step_found_only_by_its_screen_position_scores_weak_identity():
    step = _step(locator={"x_ratio": 0.4, "y_ratio": 0.6}, x_ratio=0.4, y_ratio=0.6)

    score = score_target_identity(step)

    assert score.score < 0.5
    assert "position on screen" in score.reason


def test_a_generated_looking_id_scores_weak_identity():
    """Mirrors worker.py's GENERATED_ID: an id the page invented at load time,
    kept only because the recorder found nothing better to address it by.
    """
    step = _step(locator={"strategy": "css", "value": '[id="ext-gen-58203917"]', "fallbacks": []})

    score = score_target_identity(step)

    assert score.score < 0.5
    assert "made up when it was loaded" in score.reason


def test_a_stable_named_locator_scores_full_identity_confidence():
    step = _step(locator={"strategy": "css", "value": '[data-testid="reveal-button"]', "fallbacks": []})

    score = score_target_identity(step)

    assert score.score == 1.0


def test_a_step_that_needs_no_element_scores_full_identity_confidence():
    step = _step(action="switch_page", locator={})

    score = score_target_identity(step)

    assert score.score == 1.0
    assert "does not need to find" in score.reason


# ---------- effect_observability ----------


def test_a_step_with_no_success_type_and_no_observation_scores_weak_effect_observability():
    score = score_effect_observability(_step(success={"type": "none"}))

    assert score.score == 0.0
    assert "nothing on the screen was seen to change" in score.reason.lower()


def test_a_step_with_a_recorded_success_type_scores_full_effect_observability():
    score = score_effect_observability(_step(success={"type": "selector_visible", "value": "#x"}))

    assert score.score == 1.0


def test_a_live_observations_proof_candidate_counts_even_before_a_success_type_is_inferred():
    """At capture time most clicks still carry success={"type": "none"} — the
    real proof is only inferred later, in converter.py. A live recording has to
    judge this dimension from the timeline's own diff instead.
    """
    observation = ActionObservation(
        seq=1, monotonic_at=0.0, occurred_at="2026-01-01T00:00:00Z",
        trigger={"action": "click"}, target={}, before=Snapshot(), after=Snapshot(),
        proof_candidates=[{"type": "selector_visible", "value": "#x"}],
    )

    score = score_effect_observability(_step(success={"type": "none"}), observation)

    assert score.score == 1.0


# ---------- replay_safety ----------


def test_a_safe_to_repeat_step_scores_full_replay_safety():
    score = score_replay_safety(_step(retry={"max_attempts": 3, "safe_to_repeat": True}))

    assert score.score == 1.0


def test_an_unsafe_to_repeat_step_scores_lower_but_not_the_lowest_possible():
    score = score_replay_safety(_step(retry={"max_attempts": 1, "safe_to_repeat": False}))

    assert 0.0 < score.score < 1.0


# ---------- combining the three into one verdict ----------


def test_being_unsafe_to_repeat_alone_does_not_make_an_otherwise_solid_step_weak():
    """Being unsafe to repeat is a normal fact about some actions (a submit),
    not a flaw in how they were captured, so it must not sink an otherwise
    well-identified, already-proven step on its own.
    """
    step = _step(
        locator={"strategy": "css", "value": '[data-testid="reveal-button"]', "fallbacks": []},
        success={"type": "selector_visible", "value": "#x"},
        retry={"max_attempts": 1, "safe_to_repeat": False},
    )

    confidence = score_step(step)

    assert confidence.weak is False


def test_overall_confidence_is_the_weakest_dimension_not_an_average():
    """A step scored well on two dimensions and badly on the third is exactly as
    replayable as its worst dimension; averaging would let one real weakness
    hide behind two unrelated strengths.
    """
    step = _step(
        locator={"strategy": "css", "value": '[data-testid="reveal-button"]', "fallbacks": []},
        success={"type": "none"},
        retry={"max_attempts": 3, "safe_to_repeat": True},
    )

    confidence = score_step(step)

    assert confidence.overall == 0.0
    assert confidence.weak is True
    assert confidence.reason == confidence.dimensions["effect_observability"].reason


def test_a_step_failing_two_dimensions_names_both_problems_in_its_reason():
    """A made-up id *and* no observed effect is a more serious, differently-fixed
    problem than either alone, so the reader must be told about both rather
    than only whichever one happens to score lowest.
    """
    step = _step(
        locator={"strategy": "css", "value": '[id="ext-gen-58203917"]', "fallbacks": []},
        success={"type": "none"},
        retry={"max_attempts": 3, "safe_to_repeat": True},  # kept strong on purpose
    )

    confidence = score_step(step)

    assert confidence.weak
    assert "made up when it was loaded" in confidence.reason
    assert "nothing on the screen was seen to change" in confidence.reason.lower()
    # Worst dimension first: effect_observability (0.0) scores lower than
    # target_identity (0.35), so its sentence must lead.
    assert confidence.reason.lower().index("nothing on the screen") < confidence.reason.lower().index(
        "made up when it was loaded"
    )


def test_a_weak_steps_reason_is_written_for_a_non_technical_reader():
    # Effect and safety are both made deliberately strong here so the one real
    # weakness -- the generated id -- is unambiguously what the verdict is
    # about, rather than being masked by a second, unrelated weak dimension.
    step = _step(
        locator={"strategy": "css", "value": '[id="ext-gen-1234567"]', "fallbacks": []},
        success={"type": "selector_visible", "value": "#x"},
        retry={"max_attempts": 3, "safe_to_repeat": True},
    )

    confidence = score_step(step)

    assert confidence.weak
    for jargon in ("target_identity", "effect_observability", "replay_safety", "dimension"):
        assert jargon not in confidence.reason
    assert "made up when it was loaded" in confidence.reason


# ---------- converter.review_plan, extended rather than duplicated ----------


def _plan(*actions: dict[str, Any], download: bool = False) -> dict[str, Any]:
    return {
        "actions": list(actions),
        "start_url": "https://example.test/report",
        "expects_download": download,
        "expected_download_count": 1 if download else 0,
    }


def test_review_plan_refuses_a_step_whose_only_identity_is_a_generated_id() -> None:
    """The defect this closes: a step with *some* selector and *some* recorded
    success sailed through the existing weak/unproven checks even when that
    selector was nothing but a framework's throwaway id.
    """
    plan = _plan({
        "seq": 1, "action": "click",
        "target": {"page": "main", "frame": ""},
        "locator": {"strategy": "css", "value": '[id="ext-gen-58203917"]', "fallbacks": []},
        "inputs": {}, "layer": "dom",
        "success": {"type": "url_changed", "value": "https://example.test/next"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    })

    verdict = review_plan(plan)

    assert verdict["ready"] is False
    assert verdict["low_confidence_action_count"] == 1
    assert any("made up when it loaded" in problem for problem in verdict["problems"])
    by_seq = {entry["seq"]: entry for entry in verdict["step_confidence"]}
    assert by_seq[1]["weak"] is True


def test_review_plan_accepts_a_plan_whose_steps_are_identified_by_stable_names() -> None:
    plan = _plan(
        {
            "seq": 1, "action": "click",
            "target": {"page": "main", "frame": ""},
            "locator": {"strategy": "css", "value": '[data-testid="reveal-button"]', "fallbacks": []},
            "inputs": {}, "layer": "dom",
            "success": {"type": "selector_visible", "value": '[id="result-revealed"]'},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        },
        {
            "seq": 2, "action": "click",
            "target": {"page": "main", "frame": ""},
            "locator": {"strategy": "css", "value": "#download-summary", "fallbacks": []},
            "inputs": {}, "layer": "dom",
            "success": {"type": "download_started"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        },
        download=True,
    )

    verdict = review_plan(plan)

    assert verdict["ready"] is True
    assert verdict["low_confidence_action_count"] == 0


def test_review_plan_does_not_report_a_positional_step_twice() -> None:
    """A pure-position step is already reported by the existing weak-action
    check; confidence scoring must extend that check, not restate it.
    """
    plan = _plan({
        "seq": 1, "action": "click",
        "target": {"page": "main", "frame": ""},
        "locator": {"x_ratio": 0.4, "y_ratio": 0.6},
        "inputs": {}, "layer": "visual", "x_ratio": 0.4, "y_ratio": 0.6,
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    })

    verdict = review_plan(plan)

    assert verdict["weak_action_count"] == 1
    assert verdict["low_confidence_action_count"] == 0
    assert not any("made up when it loaded" in problem for problem in verdict["problems"])


# ---------- wired into the real recorder ----------


def _chromium_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


@pytest.fixture
def site():
    with LocalSite() as running:
        yield running


def _record_one_click(site, tmp_path, selector: str):
    """Run the real worker against the fixture site, click one thing, and hand
    back the worker itself so its live weak-step signal can be inspected.
    """
    from smartops.recordings.worker import PlaywrightRecordingWorker

    finished = threading.Event()
    failures: list[str] = []

    def drive(page: Any) -> None:
        try:
            page.click(selector)
            page.wait_for_timeout(300)
        except Exception as exc:  # surfaced as a test failure, not swallowed
            failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            worker.stop()

    def finish(error: str | None) -> None:
        if error:
            failures.append(error)
        finished.set()

    worker = PlaywrightRecordingWorker(
        "confidence-test",
        tmp_path / "confidence-test",
        f"{site.base_url}/timeline/actions.html",
        lambda item: None,
        lambda: None,
        finish,
        _chromium_path() or "",
        None,
        True,
        drive,
    )
    worker.start()
    assert finished.wait(15), "the recording did not finish"
    assert not failures, failures
    return worker


def test_the_recorder_flags_a_step_on_an_unnamed_generated_id_element_as_weak(site, tmp_path) -> None:
    """The failure case: a control with no name, no aria-label, no observable
    effect — only a framework-invented id — must be caught live, not discovered
    forty minutes into a replay.
    """
    worker = _record_one_click(site, tmp_path, "#ext-gen-58203917")

    assert worker._weak_steps, "clicking the generated-id-only element was never flagged as weak"
    flagged = worker._weak_steps[-1]
    assert flagged["action"] == "click"
    assert flagged["reason"]


def test_the_recorder_does_not_flag_a_well_identified_observed_step_as_weak(site, tmp_path) -> None:
    worker = _record_one_click(site, tmp_path, "#reveal-well-named")

    assert not worker._weak_steps, f"a well-identified, observed click was flagged weak: {worker._weak_steps}"
