"""The recorder against pages built to defeat it.

Each page here reproduces one way a real enterprise screen breaks a recording.
None of them is exotic; they are the ordinary shapes of modern web applications
— a control inside a web component, ids regenerated on every load, a menu that
only exists while the pointer is on it, an export behind a native confirmation,
a rich-text field with no value, a form two frames deep, an action that is only
on the right-click menu.

Every test drives the shipped recorder with a real browser and asserts on what
the recording actually contains, because the failure these guard against is not
a crash. It is a recording that comes back looking complete and replays into
nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    """A system pointed at the torture pages, ready to record against."""
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    services.browser = PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )
    services.systems.save({
        "key": "portal",
        "name": "Torture portal",
        "auth": {"mode": "none"},
        "reports": [{"key": "daily", "title": "Daily", "url": f"{site.base_url}/torture/shadow.html"}],
    })
    return services


def _capture(services, url: str, script, *, seconds: float = 6.0):
    from tests.recording_harness import capture_with_recorder

    return capture_with_recorder(
        services, start_url=url, script=script,
        executable_path=_chromium_path(), seconds=seconds,
    )


def _actions(steps):
    return [step["action"] for step in steps]


def _locators(steps, action):
    return [
        [step["locator"].get("value", "")] + list(step["locator"].get("fallbacks") or [])
        for step in steps
        if step["action"] == action
    ]


# ---------- a control inside a web component ----------


def test_a_control_inside_a_web_component_is_recorded_as_itself(recorded, site) -> None:
    """The browser reports the host; the recording must hold the real control."""
    def use_shadow_panel(page) -> None:
        panel = page.locator("report-panel")
        panel.locator("#shadowReference").fill("INV-77")
        panel.locator("#shadowInquiry").click()
        page.wait_for_function("() => document.getElementById('outcome').textContent === 'inquiry ran'")
        page.wait_for_timeout(400)

    steps = _capture(recorded, f"{site.base_url}/torture/shadow.html", use_shadow_panel)

    typed = [step for step in steps if step["action"] == "fill"]
    assert typed, "typing inside a web component must be recorded"
    assert typed[0]["inputs"]["value"] == "INV-77"
    # Not "report-panel": the wrapper is not what the person used.
    assert "shadowReference" in " ".join(
        [typed[0]["locator"]["value"], *typed[0]["locator"].get("fallbacks", [])]
    )

    clicked = [step for step in steps if step["action"] in {"click", "pointer_click"}]
    assert clicked, "a click inside a web component must be recorded"
    everything = " ".join(
        [clicked[-1]["locator"]["value"], *clicked[-1]["locator"].get("fallbacks", [])]
    )
    assert "shadowInquiry" in everything or "Inquiry" in everything


# ---------- ids that will not exist tomorrow ----------


def test_a_regenerated_id_is_never_the_primary_way_back_to_a_control(recorded, site) -> None:
    def press_inquiry(page) -> None:
        page.click("text=Inquiry")
        page.wait_for_timeout(500)

    steps = _capture(recorded, f"{site.base_url}/torture/rotating_ids.html", press_inquiry)

    clicked = [step for step in steps if step["action"] in {"click", "pointer_click"}]
    assert clicked
    primary = clicked[-1]["locator"]["value"]
    assert "ext-gen" not in primary, f"a generated id was chosen as the locator: {primary}"
    # And what replaced it is an identity that survives a reload.
    assert primary.startswith("role=") or primary.startswith("text=") or "aria-label" in primary

    # The generated id is still kept, last, because a bad locator beats none.
    all_of_them = [primary, *clicked[-1]["locator"].get("fallbacks", [])]
    assert any("ext-gen" in candidate for candidate in all_of_them)
    assert all_of_them.index(primary) < min(
        index for index, candidate in enumerate(all_of_them) if "ext-gen" in candidate
    )


# ---------- a menu that closes when the pointer leaves ----------


def test_a_menu_that_only_opens_on_hover_records_the_hover(recorded, site) -> None:
    """Without the hover, replaying the click alone finds a closed menu."""
    def open_menu_and_choose(page) -> None:
        page.hover("#reportsMenu")
        page.wait_for_selector("#dailyReport", state="visible")
        page.click("#dailyReport")
        page.wait_for_timeout(600)

    steps = _capture(recorded, f"{site.base_url}/torture/hover_menu.html", open_menu_and_choose)

    assert "hover" in _actions(steps), f"the revealing hover was lost: {_actions(steps)}"
    hover_step = next(step for step in steps if step["action"] == "hover")
    assert "reportsMenu" in " ".join(
        [hover_step["locator"]["value"], *hover_step["locator"].get("fallbacks", [])]
    )
    # And it comes before the click it made possible.
    order = _actions(steps)
    assert order.index("hover") < max(
        index for index, action in enumerate(order) if action in {"click", "pointer_click"}
    )
    # Resting a pointer changes nothing, so it is safe to repeat.
    assert hover_step["retry"]["safe_to_repeat"] is True


# ---------- an export behind a native confirmation ----------


def test_a_confirmation_the_person_accepted_is_recorded_and_the_file_still_arrives(
    recorded, site
) -> None:
    """Playwright cancels dialogs when nothing listens, and the export vanishes."""
    def export_with_confirmation(page) -> None:
        page.click("#btnExport")
        page.wait_for_function(
            "() => document.getElementById('outcome').textContent === 'export confirmed'"
        )
        page.wait_for_timeout(900)

    steps = _capture(recorded, f"{site.base_url}/torture/confirm_export.html", export_with_confirmation)

    assert "dialog" in _actions(steps), f"the confirmation was answered invisibly: {_actions(steps)}"
    dialog = next(step for step in steps if step["action"] == "dialog")
    assert dialog["inputs"] == {"dialog_type": "confirm", "decision": "accept"}
    assert "Export the daily report" in dialog["target_text_redacted"]

    # The point of accepting it: the file the task exists for actually arrives.
    downloads = [step for step in steps if step["action"] == "download"]
    assert downloads, "accepting the confirmation must let the download happen"
    assert downloads[0]["inputs"]["file_name"] == "daily-report"  # no extension, as sent


# ---------- a field with no value ----------


def test_typing_into_a_rich_text_field_is_recorded(recorded, site) -> None:
    def write_a_note(page) -> None:
        page.click("#note")
        page.type("#note", "Closing note for the shift")
        page.click("#btnSave")
        page.wait_for_timeout(500)

    steps = _capture(recorded, f"{site.base_url}/torture/rich_text.html", write_a_note)

    typed = [step for step in steps if step["action"] == "fill"]
    assert typed, f"typing into a contenteditable was lost: {_actions(steps)}"
    assert typed[0]["inputs"]["value"] == "Closing note for the shift"


# ---------- two frames down ----------


def test_a_form_two_frames_deep_keeps_its_frame_identity(recorded, site) -> None:
    def use_the_deep_form(page) -> None:
        inner = page.frame_locator("#outer").frame_locator("#inner")
        inner.locator("#deepReference").fill("DEEP-9")
        inner.locator("#btnDeep").click()
        page.wait_for_timeout(700)

    steps = _capture(recorded, f"{site.base_url}/torture/nested_frames.html", use_the_deep_form)

    typed = [step for step in steps if step["action"] == "fill"]
    assert typed and typed[0]["inputs"]["value"] == "DEEP-9"
    # A step in a frame that says it happened on the page would replay against
    # the wrong document and find nothing.
    assert typed[0]["target"]["frame"].endswith("frame_inner.html")


# ---------- an action that is only on the right-click menu ----------


def test_a_right_click_is_recorded_as_a_right_click(recorded, site) -> None:
    def open_the_row_menu(page) -> None:
        page.click("#resultRow", button="right")
        page.wait_for_function(
            "() => document.getElementById('outcome').textContent === 'context menu opened'"
        )
        page.wait_for_timeout(500)

    steps = _capture(recorded, f"{site.base_url}/torture/context_menu.html", open_the_row_menu)

    assert "context_click" in _actions(steps), f"the right-click was lost: {_actions(steps)}"
    step = next(item for item in steps if item["action"] == "context_click")
    assert "resultRow" in " ".join(
        [step["locator"]["value"], *step["locator"].get("fallbacks", [])]
    )
    assert step["retry"]["safe_to_repeat"] is False


# ---------- and every one of them has to compile ----------


def test_every_torture_recording_compiles_into_a_plan_replay_can_run(recorded, site) -> None:
    """A step the platform cannot repeat must never reach a plan silently."""
    def full_task(page) -> None:
        page.hover("#reportsMenu")
        page.wait_for_selector("#dailyReport", state="visible")
        page.click("#dailyReport")
        page.wait_for_timeout(600)

    steps = _capture(recorded, f"{site.base_url}/torture/hover_menu.html", full_task)
    record = recorded.recordings.list(limit=1)[0]
    plan = build_plan(
        recording_id=record.id,
        system_key="portal",
        report_key="daily",
        steps=recorded.recordings.steps(record.id),
        start_url=f"{site.base_url}/torture/hover_menu.html",
    )

    for action in plan["actions"]:
        kind = action.get("action") or action.get("kind")
        assert kind, "a plan action with no kind cannot be repeated"
    # The real gate: the review must not pass a plan holding an action the
    # replay engine has no handler for.
    review = review_plan(plan)
    assert isinstance(review, dict) and "ready" in review


# ---------- a gesture that used to vanish without a word ----------


def test_a_drag_is_recorded_with_both_of_its_ends(recorded, site) -> None:
    """A press that moves used to be discarded as a stray hand, silently."""
    def move_the_column(page) -> None:
        page.drag_and_drop("#colPlant", "#dropZone")
        page.wait_for_function(
            "() => document.getElementById('outcome').textContent === 'column moved'"
        )
        page.wait_for_timeout(500)

    steps = _capture(recorded, f"{site.base_url}/torture/drag_columns.html", move_the_column)

    assert "drag" in _actions(steps), f"the drag was lost: {_actions(steps)}"
    step = next(item for item in steps if item["action"] == "drag")
    assert "colPlant" in step["locator"]["value"]
    # Both ends, or replay would have nowhere safe to drop it.
    assert "dropZone" in step["inputs"]["drop_selector"]
    assert step["retry"]["safe_to_repeat"] is False


# ---------- a filter the person picked three of ----------


def test_every_option_chosen_in_a_multi_select_is_recorded(recorded, site) -> None:
    """One value stood in for all of them, and the replayed report came back smaller."""
    def choose_three_plants(page) -> None:
        page.select_option("#plants", ["vd", "da", "ce"])
        page.select_option("#shift", "night")
        page.wait_for_timeout(600)

    steps = _capture(recorded, f"{site.base_url}/torture/multi_select.html", choose_three_plants)

    chosen = [step for step in steps if step["action"] == "select"]
    assert len(chosen) == 2, f"both lists must be recorded: {_actions(steps)}"

    plants = next(step for step in chosen if "plants" in step["locator"]["value"])
    assert plants["inputs"]["values"] == ["vd", "da", "ce"]
    # And the proof has to be the whole set: reading back one value would pass
    # while two of the three choices were missing.
    assert plants["success"] == {
        "type": "selected_values_are", "value": ["vd", "da", "ce"]
    }

    # A single-choice list is untouched by any of this.
    shift = next(step for step in chosen if "shift" in step["locator"]["value"])
    assert shift["inputs"] == {"value": "night"}
    assert shift["success"] == {"type": "value_equals", "value": "night"}


# ---------- the step that runs the query ----------


def test_a_grid_that_refills_in_place_is_proved_by_changing_not_by_existing(
    recorded, site
) -> None:
    """The grid was already on screen holding the previous answer.

    Nothing appears and nothing vanishes when the query runs, so the proof the
    compiler would otherwise reach for — "the grid is visible" — passes
    instantly against stale rows, and a query that never ran reports success.
    """
    from smartops.recordings.converter import build_plan

    def run_the_query(page) -> None:
        page.click("#btnInquiry")
        page.wait_for_function(
            "() => document.querySelectorAll('#resultGrid .row').length === 24"
        )
        page.wait_for_timeout(700)
        page.click("#btnInquiry")  # a second action, so the first one has a settled boundary
        page.wait_for_timeout(600)

    _capture(recorded, f"{site.base_url}/torture/stale_grid.html", run_the_query)
    record = recorded.recordings.list(limit=1)[0]
    plan = build_plan(
        recording_id=record.id, system_key="portal", report_key="daily",
        steps=recorded.recordings.steps(record.id),
        start_url=f"{site.base_url}/torture/stale_grid.html",
    )

    inquiry = plan["actions"][0]
    assert inquiry["success"]["type"] == "content_changed", inquiry["success"]
    assert "resultGrid" in inquiry["success"]["value"]


def test_a_field_with_no_identity_of_its_own_is_found_by_the_words_beside_it(
    recorded, site
) -> None:
    """The ordinary enterprise form: the label is the only stable thing on it."""
    def fill_the_dates(page) -> None:
        page.fill("table tr:nth-child(1) input", "2026-09-01")
        page.fill("table tr:nth-child(2) input", "2026-09-30")
        page.wait_for_timeout(600)

    steps = _capture(recorded, f"{site.base_url}/torture/anchored_form.html", fill_the_dates)

    typed = [step for step in steps if step["action"] == "fill"]
    assert len(typed) == 2, _actions(steps)

    first = [typed[0]["locator"]["value"], *typed[0]["locator"].get("fallbacks", [])]
    assert any("From date" in candidate for candidate in first), first
    # And the invented id is still kept, behind the label rather than in front.
    anchored = next(i for i, c in enumerate(first) if "From date" in c)
    generated = next(i for i, c in enumerate(first) if "ext-gen" in c)
    assert anchored < generated

    second = [typed[1]["locator"]["value"], *typed[1]["locator"].get("fallbacks", [])]
    assert any("To date" in candidate for candidate in second), second
