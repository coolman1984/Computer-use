"""Recording and replaying a whole human task, not just its clicks.

Every test here drives a real browser against the fixture site in
`tests/recorded_site`. Two halves are proven separately and then together:

* **Capture** — the recorder must notice what the person actually did: typing,
  choosing from a list, pressing a key, opening a tab, working inside a frame,
  waiting, and downloading more than one file. A click log cannot describe any
  of that.
* **Replay** — each recorded step must be repeatable, and must prove it worked
  by a real consequence (an element appeared, the value changed, a page opened,
  a download started), never by the fact that a click was dispatched.

The acceptance test at the bottom records the entire task, closes the browser,
and runs the automation from a cold session — which is the only version of
"it works" that means anything for something meant to run overnight.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from smartops.domain.enums import RecordingStatus, RunStatus
from smartops.recordings.converter import build_plan, review_plan
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
    """A system pointed at the fixture site, signed in, ready to record against."""
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
    from smartops.checks import ConnectionCheck
    from smartops.sessions import session_path

    # This environment has no Google Chrome and no bundled headless shell, so
    # point every browser launch — recorder and replay alike — at the Chromium
    # that is here, exactly as browser.executable_path does in production.
    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    services.browser = PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )

    services.systems.save({
        "key": "portal",
        "name": "Reports portal",
        "auth": {
            "mode": "session",
            "login_url": f"{site.base_url}/index.html",
            "logged_in_selector": "#user-menu",
        },
        "reports": [{
            "key": "daily_sales", "title": "Daily sales",
            "url": f"{site.base_url}/report.html", "download_selector": "#download-summary",
        }],
    })
    services.connection_checks.record(
        "portal",
        ConnectionCheck(ok=True, reachable=True, signed_in=True, summary="ok"),
        at="2026-01-01T00:00:00Z",
    )
    path = session_path(services.settings.storage.sessions_dir, "portal")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cookies": [{"name": "sid", "value": "t", "domain": "127.0.0.1",
                     "path": "/", "expires": 4102444800}],
        "origins": [],
    }), encoding="utf-8")
    return services


# ---------- capture: what the recorder notices ----------


def _capture(services, site, script, *, seconds: float = 6.0):
    """Run the real recorder against the site while `script` drives the browser.

    The script receives the recorder's own page, so the actions it performs are
    exactly the ones a human would perform in the recording window.
    """
    from tests.recording_harness import capture_with_recorder

    return capture_with_recorder(
        services, start_url=f"{site.base_url}/report.html", script=script,
        executable_path=_chromium_path(), seconds=seconds,
    )


def test_typing_into_a_field_is_recorded_as_a_value(recorded, site) -> None:
    """The defect: only clicks were captured, so a form the person filled in came
    back as a couple of clicks with no idea what was typed. Replaying it produced
    an empty form and, usually, a report for the wrong thing.
    """
    steps = _capture(recorded, site, lambda page: page.fill("#reference", "INV-8842"))

    typed = [s for s in steps if s["action"] == "fill"]
    assert typed, "typing into a field must be recorded"
    assert typed[0]["inputs"]["value"] == "INV-8842"
    assert "reference" in typed[0]["locator"]["value"]


def test_a_password_is_recorded_as_a_reference_never_a_value(recorded, site) -> None:
    """A recording is stored in the database and shown on a review screen. A
    password captured into it would be a secret sitting in both.
    """
    steps = _capture(recorded, site, lambda page: page.fill("#secret", "hunter2-not-a-real-password"))

    serialised = json.dumps(steps)
    assert "hunter2" not in serialised, "the typed secret must never reach the recording"
    secret_steps = [s for s in steps if s["action"] == "fill" and s["inputs"].get("secret_ref")]
    assert secret_steps, "a password field must be recorded as a reference to fill at run time"
    assert secret_steps[0]["inputs"].get("value") in (None, "")


def test_choosing_from_a_dropdown_is_recorded_as_a_selection(recorded, site) -> None:
    steps = _capture(recorded, site, lambda page: page.select_option("#period", "monthly"))

    chosen = [s for s in steps if s["action"] == "select"]
    assert chosen, "choosing from a list must be recorded as a selection, not a click"
    assert chosen[0]["inputs"]["value"] == "monthly"


def test_pressing_a_key_is_recorded(recorded, site) -> None:
    def script(page):
        page.fill("#filter", "north")
        page.press("#filter", "Enter")

    steps = _capture(recorded, site, script)

    pressed = [s for s in steps if s["action"] == "press"]
    assert pressed, "a keyboard step must be recorded"
    assert pressed[0]["inputs"]["key"] == "Enter"


def test_opening_a_new_tab_is_recorded_as_a_step(recorded, site) -> None:
    steps = _capture(recorded, site, lambda page: page.click("#open-details"))

    opened = [s for s in steps if s["action"] == "switch_page"]
    assert opened, "opening a second tab must be recorded, so replay knows to follow it"


def test_working_inside_a_frame_records_the_frame(recorded, site) -> None:
    def script(page):
        page.frame_locator("#totals").locator("#confirm-total").click()

    steps = _capture(recorded, site, script)

    in_frame = [s for s in steps if s["target"].get("frame")]
    assert in_frame, "a step inside an iframe must record which frame it happened in"


def test_two_downloads_in_one_task_are_both_recorded(recorded, site) -> None:
    def script(page):
        page.click("#download-summary")
        page.wait_for_timeout(300)
        page.click("#download-detail")

    steps = _capture(recorded, site, script, seconds=8.0)

    downloads = [s for s in steps if s["action"] == "download"]
    assert len(downloads) == 2, f"both downloads must be recorded, saw {len(downloads)}"
    names = {d["inputs"]["file_name"] for d in downloads}
    assert names == {"summary.csv", "detail.csv"}


def test_every_recorded_step_carries_the_full_contract(recorded, site) -> None:
    """A step without success evidence is a step nobody can trust."""
    def script(page):
        page.fill("#reference", "INV-1")
        page.select_option("#period", "daily")
        page.click("#prepare")

    steps = _capture(recorded, site, script)
    assert steps, "the recorder captured nothing"

    for step in steps:
        assert step["action"], "every step needs an action type"
        assert "page" in step["target"], "every step needs the page it happened in"
        assert step["locator"] or step["action"] in ("switch_page", "download", "navigate"), \
            "every element step needs a way to find the element again"
        assert step["success"]["type"], "every step needs evidence of what success looks like"
        assert step["retry"]["max_attempts"] >= 1, "every step needs a retry policy"
        assert "safe_to_repeat" in step["retry"], "a step must say whether repeating it is safe"


# ---------- replay: each action repeated, and proven ----------


def _replay(services, site, actions, *, expects=2):
    """Replay a hand-built plan through the real engine, as a run would."""
    from smartops.ports.browser import ReplayRequest
    from smartops.sessions import session_path

    plan = {
        "plan_version": 2,
        "start_url": f"{site.base_url}/report.html",
        "actions": actions,
        "expects_download": expects > 0,
        "expected_download_count": expects,
    }
    destination = Path(services.settings.storage.raw_data_dir) / "replay"
    destination.mkdir(parents=True, exist_ok=True)
    return services.browser.replay(ReplayRequest(
        system="portal", report="daily_sales", destination_dir=destination, plan=plan,
        session_state_path=session_path(services.settings.storage.sessions_dir, "portal"),
    ))


def _action(seq, action, **kwargs):
    """A step in the contract's shape, with sane defaults for the parts a test does not care about."""
    step = {
        "seq": seq,
        "action": action,
        "target": {"page": "main", "frame": ""},
        "locator": {},
        "inputs": {},
        "success": {"type": "none"},
        "retry": {"max_attempts": 2, "safe_to_repeat": True},
    }
    step.update(kwargs)
    return step


def test_replaying_a_typed_value_proves_the_field_holds_it(recorded, site) -> None:
    result = _replay(recorded, site, [
        _action(1, "fill",
                locator={"strategy": "css", "value": "#reference"},
                inputs={"value": "INV-8842"},
                success={"type": "value_equals", "value": "INV-8842"}),
        _action(2, "click",
                locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message
    assert result.file_paths and result.file_paths[0].name == "summary.csv"


def test_replaying_a_selection_proves_the_value_changed(recorded, site) -> None:
    result = _replay(recorded, site, [
        _action(1, "select",
                locator={"strategy": "css", "value": "#period"},
                inputs={"value": "monthly"},
                success={"type": "value_equals", "value": "monthly"}),
        _action(2, "click",
                locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


def test_replaying_a_key_press_proves_its_effect(recorded, site) -> None:
    result = _replay(recorded, site, [
        _action(1, "fill", locator={"strategy": "css", "value": "#filter"},
                inputs={"value": "north"}, success={"type": "value_equals", "value": "north"}),
        _action(2, "press", locator={"strategy": "css", "value": "#filter"},
                inputs={"key": "Enter"},
                success={"type": "selector_visible", "value": "#filter-applied"}),
        _action(3, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


def test_replaying_waits_for_an_element_that_arrives_late(recorded, site) -> None:
    """A click is not success. The report only becomes ready after a delay, and
    the step that follows must wait for that rather than race it.
    """
    result = _replay(recorded, site, [
        _action(1, "click", locator={"strategy": "css", "value": "#prepare"},
                success={"type": "selector_visible", "value": "#ready"}),
        _action(2, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


def test_replaying_follows_a_new_tab_and_comes_back(recorded, site) -> None:
    result = _replay(recorded, site, [
        _action(1, "click", locator={"strategy": "css", "value": "#open-details"},
                success={"type": "new_page"}),
        _action(2, "switch_page", target={"page": "latest", "frame": ""},
                success={"type": "selector_visible", "value": "#details-loaded"}),
        _action(3, "switch_page", target={"page": "main", "frame": ""},
                success={"type": "selector_visible", "value": "#download-summary"}),
        _action(4, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


def test_replaying_acts_inside_an_iframe(recorded, site) -> None:
    result = _replay(recorded, site, [
        _action(1, "click",
                target={"page": "main", "frame": "#totals"},
                locator={"strategy": "css", "value": "#confirm-total"},
                success={"type": "selector_visible", "value": "#total-confirmed"}),
        _action(2, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


def test_replaying_captures_two_downloads_in_one_run(recorded, site) -> None:
    """The defect: expect_download was armed only around the final action, so a
    task that produces two files could only ever bring back one — and the second,
    which often carries the detail, was silently lost.
    """
    result = _replay(recorded, site, [
        _action(1, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
        _action(2, "click", locator={"strategy": "css", "value": "#download-detail"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=2)

    assert result.ok, result.message
    names = sorted(p.name for p in result.file_paths)
    assert names == ["detail.csv", "summary.csv"]


def test_a_step_that_does_not_prove_itself_fails_the_run(recorded, site) -> None:
    """Clicking something and moving on is what let a broken automation look fine."""
    result = _replay(recorded, site, [
        _action(1, "click", locator={"strategy": "css", "value": "#prepare"},
                # A consequence that will never happen: the step must fail, not pass.
                success={"type": "selector_visible", "value": "#this-never-appears"},
                retry={"max_attempts": 1, "safe_to_repeat": True}),
    ], expects=0)

    assert not result.ok
    assert "step 1" in result.message.lower() or "prove" in result.message.lower()


def test_a_secret_is_filled_from_the_credential_store_at_run_time(recorded, site) -> None:
    """The recording holds a reference; the value only ever exists during the run."""
    recorded.credentials.put("portal", "tester", "hunter2-not-a-real-password")

    result = _replay(recorded, site, [
        _action(1, "fill",
                locator={"strategy": "css", "value": "#secret"},
                inputs={"secret_ref": "portal", "secret_field": "password"},
                success={"type": "value_not_empty"}),
        _action(2, "click", locator={"strategy": "css", "value": "#download-summary"},
                success={"type": "download_started"},
                retry={"max_attempts": 1, "safe_to_repeat": False}),
    ], expects=1)

    assert result.ok, result.message


# ---------- the whole task, from a cold browser ----------


def test_acceptance_the_full_task_records_and_replays_from_a_cold_session(recorded, site) -> None:
    """Record a complete human task, close the browser, and run it from scratch.

    Sign in with a saved session, open the report, type a value, choose from a
    list, press a key, open a second tab, act inside an iframe, wait for a late
    result, and download two files. Then everything the recorder produced is
    thrown at the execution engine in a brand-new browser, exactly as a
    scheduled run would.
    """
    def script(page):
        page.fill("#reference", "INV-8842")
        page.select_option("#period", "monthly")
        page.fill("#filter", "north")
        page.press("#filter", "Enter")
        page.click("#open-details")
        page.wait_for_timeout(400)
        page.frame_locator("#totals").locator("#confirm-total").click()
        page.click("#prepare")
        page.wait_for_selector("#ready", state="visible")
        page.click("#download-summary")
        page.wait_for_timeout(300)
        page.click("#download-detail")

    steps = _capture(recorded, site, script, seconds=12.0)

    # --- the recording describes the whole task, not a click log ---
    actions = [s["action"] for s in steps]
    for expected in ("fill", "select", "press", "switch_page", "click", "download"):
        assert expected in actions, f"the recorder missed {expected}: saw {actions}"
    assert actions.count("download") == 2
    assert any(s["target"].get("frame") for s in steps), "the iframe step was not recorded"

    # --- nothing sensitive was written down ---
    assert "hunter2" not in json.dumps(steps)

    # --- it becomes a plan the review gate accepts ---
    from smartops.domain.models import RecordingStep

    record = recorded.recordings.list(limit=1)[0]
    plan = build_plan(
        recording_id=record.id, system_key="portal", report_key="daily_sales",
        steps=recorded.recordings.steps(record.id), start_url=f"{site.base_url}/report.html",
    )
    verdict = review_plan(plan)
    assert verdict["ready"], verdict["problems"]
    assert plan["expected_download_count"] == 2

    # --- and runs to completion in a fresh browser, through the normal engine ---
    record.status = RecordingStatus.COMPLETED
    recorded.recordings.save(record)
    recorded.recording_manager.draft(record.id, "daily_sales")
    process = recorded.process_manager.create_from_recording(record.id)
    process, run = recorded.process_manager.test(process.id)

    assert run.status is RunStatus.SUCCEEDED, run.error_message
    files = recorded.files.list(run_id=run.id)
    assert sorted(f.original_name for f in files) == ["detail.csv", "summary.csv"]
    assert all(f.validation_status.value == "passed" for f in files), \
        [f.validation_details for f in files]

    # --- every step's outcome is on the record, not just the run's ---
    step_states = run.state.get("step_results") or []
    assert len(step_states) == len(plan["actions"])
    assert all(s["ok"] for s in step_states)
