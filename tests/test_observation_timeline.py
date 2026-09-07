"""Proving Phase 4: one observation per action, built from evidence the recorder
already collects, without changing the shape of the recorded step itself.

The diff engine and the `Timeline` sidecar are proven first as pure functions —
no browser needed, since they only reshape data that already exists. The
acceptance tests at the bottom then drive the real recorder against the
fixture site, because the thing actually worth proving is that the worker
wires this up correctly: that `timeline.jsonl` really gets one entry per
emitted step, that a diff really finds what changed, and that doing all of
this never breaks `steps.jsonl`, which `RecordingManager._step` rebuilds a
`RecordingStep(**data)` from and will silently drop on any shape mismatch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from smartops.domain.models import RecordingStep
from smartops.recordings.timeline import (
    SUPPORTED_PROOF_TYPES,
    ActionObservation,
    Snapshot,
    Timeline,
    diff_visible_locators,
)
from tests.recorded_site import LocalSite


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


@pytest.fixture
def recorded(services, site):
    """A system pointed at the timeline fixture pages, signed in, ready to record."""
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
    from smartops.checks import ConnectionCheck
    from smartops.sessions import session_path

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    services.browser = PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )

    services.systems.save({
        "key": "timeline_portal",
        "name": "Timeline portal",
        "auth": {
            "mode": "session",
            "login_url": f"{site.base_url}/timeline/actions.html",
            "logged_in_selector": "#user-menu",
        },
        "reports": [{
            "key": "timeline_actions", "title": "Timeline actions",
            "url": f"{site.base_url}/timeline/actions.html",
            "download_selector": "#reveal-well-named",
        }],
    })
    services.connection_checks.record(
        "timeline_portal",
        ConnectionCheck(ok=True, reachable=True, signed_in=True, summary="ok"),
        at="2026-01-01T00:00:00Z",
    )
    path = session_path(services.settings.storage.sessions_dir, "timeline_portal")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cookies": [{"name": "sid", "value": "t", "domain": "127.0.0.1",
                     "path": "/", "expires": 4102444800}],
        "origins": [],
    }), encoding="utf-8")
    return services


def _capture(services, site, script, *, seconds: float = 6.0):
    from tests.recording_harness import capture_with_recorder

    return capture_with_recorder(
        services, start_url=f"{site.base_url}/timeline/actions.html", script=script,
        executable_path=_chromium_path(), seconds=seconds, system_key="timeline_portal",
    )


def _read_timeline(services) -> list[dict]:
    record = services.recordings.list(limit=1)[0]
    path = Path(record.artifact_dir) / "timeline.jsonl"
    assert path.exists(), "no timeline.jsonl sidecar was written for this recording"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- the diff engine, as a pure function ----------


def test_diff_reports_a_locator_that_appeared_and_one_that_disappeared():
    before = [{"value": '[id="a"]', "fallbacks": []}, {"value": '[id="b"]', "fallbacks": []}]
    after = [{"value": '[id="b"]', "fallbacks": []}, {"value": '[id="c"]', "fallbacks": ['[id="c2"]']}]

    changes = diff_visible_locators(before, after)

    assert changes["appeared"] == ['[id="c"]', '[id="c2"]']
    assert changes["disappeared"] == ['[id="a"]']


def test_diff_of_two_identical_snapshots_reports_no_changes():
    same = [{"value": '[id="a"]', "fallbacks": ['[name="a"]']}]

    assert diff_visible_locators(same, same) == {"appeared": [], "disappeared": []}


def test_diff_treats_missing_snapshots_as_empty_rather_than_raising():
    assert diff_visible_locators(None, None) == {"appeared": [], "disappeared": []}
    assert diff_visible_locators([], [{"value": '[id="x"]', "fallbacks": []}]) == {
        "appeared": ['[id="x"]'], "disappeared": [],
    }


# ---------- the Timeline sidecar ----------


def test_timeline_answers_what_happened_since_a_given_step(tmp_path):
    timeline = Timeline(tmp_path / "rec-1")
    for seq in (1, 2, 3):
        timeline.append(ActionObservation(
            seq=seq, monotonic_at=float(seq), occurred_at="2026-01-01T00:00:00Z",
            trigger={"action": "click"}, target={"page": "main", "frame": "", "locator": {}},
            before=Snapshot(), after=Snapshot(),
        ))

    assert [o.seq for o in timeline.since(1)] == [2, 3]
    assert timeline.since(3) == []
    assert timeline.at(2).seq == 2
    assert timeline.at(99) is None
    assert len(timeline) == 3


def test_timeline_persists_every_appended_observation_to_its_own_jsonl_file(tmp_path):
    artifact_dir = tmp_path / "rec-2"
    timeline = Timeline(artifact_dir)

    timeline.append(ActionObservation(
        seq=1, monotonic_at=1.0, occurred_at="2026-01-01T00:00:00Z",
        trigger={"action": "click"}, target={"page": "main", "frame": "", "locator": {}},
        before=Snapshot(), after=Snapshot(),
        changes={"appeared": ["#x"], "disappeared": []},
        proof_candidates=[{"type": "selector_visible", "value": "#x"}],
    ))

    lines = (artifact_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["seq"] == 1
    assert record["proof_candidates"] == [{"type": "selector_visible", "value": "#x"}]
    assert record["changes"] == {"appeared": ["#x"], "disappeared": []}


def test_timeline_record_builds_an_observation_straight_from_a_worker_step(tmp_path):
    """`Timeline.record` reads exactly the fields the worker already puts on a
    step — no new capture, just a comparison of what `_attach_observed_locators`
    already placed under `inputs`.
    """
    timeline = Timeline(tmp_path / "rec-3")
    step = {
        "action": "click",
        "target": {"page": "main", "frame": ""},
        "locator": {"strategy": "css", "value": '[id="prepare"]', "fallbacks": []},
        "inputs": {
            "_observed_visible_before": [{"value": '[id="prepare"]', "fallbacks": []}],
            "_observed_visible_after": [
                {"value": '[id="prepare"]', "fallbacks": []},
                {"value": '[id="ready"]', "fallbacks": []},
            ],
        },
        "before_image": "screenshots/000001.png",
        "after_image": "screenshots/000002.png",
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    }

    observation = timeline.record(step, quality_before={"blank": False}, quality_after={"blank": False})

    assert observation.seq == 1
    assert observation.changes == {"appeared": ['[id="ready"]'], "disappeared": []}
    assert {"type": "selector_visible", "value": '[id="ready"]'} in observation.proof_candidates
    assert observation.before.frame_path == "screenshots/000001.png"
    assert observation.after.visible_locators == ('[id="prepare"]', '[id="ready"]')
    assert all(c["type"] in SUPPORTED_PROOF_TYPES for c in observation.proof_candidates)


# ---------- wired into the real recorder ----------


def test_a_step_that_reveals_a_new_element_is_recorded_with_a_supported_proof(recorded, site) -> None:
    _capture(recorded, site, lambda page: page.click("#reveal-well-named"))
    observations = _read_timeline(recorded)

    clicks = [o for o in observations if o["trigger"]["action"] == "click"]
    assert clicks, f"no click observation was recorded on the timeline: {observations}"
    revealing = next((o for o in clicks if o["changes"]["appeared"]), None)
    assert revealing, f"no observation recorded the revealed element: {clicks}"
    assert '[id="result-revealed"]' in revealing["changes"]["appeared"]
    assert {"type": "selector_visible", "value": '[id="result-revealed"]'} in revealing["proof_candidates"]
    assert all(
        candidate["type"] in SUPPORTED_PROOF_TYPES
        for observation in observations
        for candidate in observation["proof_candidates"]
    )


def test_opening_a_new_tab_is_recorded_as_a_new_page_observation(recorded, site) -> None:
    _capture(recorded, site, lambda page: page.click("#open-second-tab"))
    observations = _read_timeline(recorded)

    opened = [o for o in observations if o["trigger"]["action"] == "switch_page"]
    assert opened, f"opening a second tab produced no timeline observation: {observations}"
    assert {"type": "new_page"} in opened[0]["proof_candidates"]


def test_the_timeline_sidecar_never_changes_the_recorded_step_contract(recorded, site) -> None:
    """`RecordingStep(**data)` raises on an unknown top-level key, and the worker's
    drain loop swallows that exception silently — so a broken contract shows up
    as a missing step, not a loud error. One click must produce exactly one.
    """
    steps = _capture(recorded, site, lambda page: page.click("#reveal-well-named"))

    clicks = [s for s in steps if s["action"] == "click"]
    assert len(clicks) == 1, f"the click step was silently dropped or duplicated: {steps}"
    # Rebuilding the dataclass from the exact dict the worker emitted must not
    # raise — this is the same construction RecordingManager._step performs.
    for step in steps:
        RecordingStep(recording_id="rec_contract_check", seq=1, kind=step["kind"], **{
            key: value for key, value in step.items() if key not in ("recording_id", "seq", "kind")
        })
