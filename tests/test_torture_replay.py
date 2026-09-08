"""Repeating the awkward gestures, not just recording them.

Capturing a hover, a right-click, a drag or a confirmation is only half the
job. A recording that holds a step the replay engine cannot perform is worse
than one that never captured it, because it looks complete right up until the
unattended run. These tests take each new action through the real replay engine
against the same pages, and check the page itself changed — not that the call
returned.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
def engine(services):
    from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter

    services.settings.browser.__dict__["executable_path"] = _chromium_path() or ""
    return PlaywrightBrowserAdapter(
        services.settings.browser, credential_store=services.credentials
    )


def _action(seq: int, action: str, **kwargs):
    step = {
        "seq": seq,
        "action": action,
        "target": {"page": "main", "frame": ""},
        "locator": {},
        "inputs": {},
        "success": {"type": "none"},
        "retry": {"max_attempts": 1, "safe_to_repeat": False},
    }
    step.update(kwargs)
    return step


def _performed(result):
    """Whether every step in the plan actually ran.

    A replay is only *successful* when it produces a validated file, which is
    the right contract for this product and the wrong question for these tests:
    what is under test here is whether the engine can perform each gesture at
    all, on pages that produce no report.
    """
    return [
        (step["seq"], step["action"], step["ok"], step["error"])
        for step in (result.step_results or [])
    ]


def _all_ran(result):
    steps = _performed(result)
    assert steps, f"no step ran at all: {result.message}"
    failed = [step for step in steps if not step[2]]
    assert not failed, f"a gesture the recorder captured could not be repeated: {failed}"


def _replay(services, engine, site, page: str, actions, *, expects: int = 0):
    from smartops.ports.browser import ReplayRequest
    from smartops.sessions import session_path

    destination = Path(services.settings.storage.raw_data_dir) / "replay"
    destination.mkdir(parents=True, exist_ok=True)
    return engine.replay(ReplayRequest(
        system="portal",
        report="daily",
        destination_dir=destination,
        plan={
            "plan_version": 2,
            "start_url": f"{site.base_url}/torture/{page}",
            "actions": actions,
            "expects_download": expects > 0,
            "expected_download_count": expects,
        },
        session_state_path=session_path(services.settings.storage.sessions_dir, "portal"),
    ))


def test_a_recorded_hover_reopens_the_menu_so_the_click_lands(services, engine, site) -> None:
    """Without the hover step this plan clicks a menu item that is not there."""
    result = _replay(services, engine, site, "hover_menu.html", [
        _action(1, "hover", locator={"strategy": "css", "value": '[id="reportsMenu"]'},
                retry={"max_attempts": 3, "safe_to_repeat": True}),
        _action(2, "click", locator={"strategy": "css", "value": '[id="dailyReport"]'},
                success={"type": "selector_visible", "value": '[id="outcome"]'}),
    ])

    _all_ran(result)


def test_the_same_plan_without_the_hover_fails_instead_of_pretending(
    services, engine, site
) -> None:
    """The control case: this is what a recording that lost the hover produces."""
    result = _replay(services, engine, site, "hover_menu.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="dailyReport"]'}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures, "clicking a closed menu must fail, not quietly pass"
    assert "no longer on the page" in failures[0][3]


def test_a_recorded_right_click_opens_what_a_left_click_never_would(
    services, engine, site
) -> None:
    result = _replay(services, engine, site, "context_menu.html", [
        _action(1, "context_click", locator={"strategy": "css", "value": '[id="resultRow"]'},
                success={"type": "selector_visible", "value": '[id="outcome"]'}),
    ])

    _all_ran(result)


def test_a_recorded_drag_moves_the_column(services, engine, site) -> None:
    result = _replay(services, engine, site, "drag_columns.html", [
        _action(1, "drag", locator={"strategy": "css", "value": '[id="colPlant"]'},
                inputs={"drop_selector": '[id="dropZone"]', "drop_fallbacks": []},
                success={"type": "selector_visible", "value": '[id="outcome"]'}),
    ])

    _all_ran(result)


def test_a_drag_with_no_destination_fails_rather_than_dropping_it_anywhere(
    services, engine, site
) -> None:
    result = _replay(services, engine, site, "drag_columns.html", [
        _action(1, "drag", locator={"strategy": "css", "value": '[id="colPlant"]'}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures and "nowhere safe to drop" in failures[0][3]


def test_a_confirmation_is_answered_during_replay_so_the_export_happens(
    services, engine, site
) -> None:
    """With nothing listening the browser cancels it and no file is ever made."""
    result = _replay(services, engine, site, "confirm_export.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnExport"]'}),
        _action(2, "dialog", inputs={"dialog_type": "confirm", "decision": "accept"}),
    ], expects=1)

    assert result.ok, result.message
    assert result.file_path and Path(result.file_path).exists()


def test_a_control_inside_a_web_component_is_reachable_at_replay(
    services, engine, site
) -> None:
    result = _replay(services, engine, site, "shadow.html", [
        _action(1, "fill", locator={"strategy": "css", "value": '[id="shadowReference"]'},
                inputs={"value": "INV-77"},
                success={"type": "value_equals", "value": "INV-77"},
                retry={"max_attempts": 2, "safe_to_repeat": True}),
        _action(2, "click", locator={"strategy": "css", "value": '[id="shadowInquiry"]'},
                success={"type": "selector_visible", "value": '[id="outcome"]'}),
    ])

    _all_ran(result)


def test_a_control_with_no_stable_id_is_found_by_what_it_is_called(
    services, engine, site
) -> None:
    """The identity a recording of a generated-id page has to fall back on."""
    result = _replay(services, engine, site, "rotating_ids.html", [
        _action(1, "click", locator={"strategy": "css", "value": 'role=button[name="Inquiry"]'},
                success={"type": "selector_visible", "value": '[id="outcome"]'}),
    ])

    _all_ran(result)


def test_a_replayed_multi_select_chooses_every_option_again(services, engine, site) -> None:
    result = _replay(services, engine, site, "multi_select.html", [
        _action(1, "select", locator={"strategy": "css", "value": '[id="plants"]'},
                inputs={"values": ["vd", "da", "ce"]},
                success={"type": "selected_values_are", "value": ["vd", "da", "ce"]},
                retry={"max_attempts": 2, "safe_to_repeat": True}),
    ])

    _all_ran(result)


def test_a_multi_select_that_loses_an_option_fails_its_own_proof(
    services, engine, site
) -> None:
    """The control case: this is what a recording that kept only one value does."""
    result = _replay(services, engine, site, "multi_select.html", [
        _action(1, "select", locator={"strategy": "css", "value": '[id="plants"]'},
                inputs={"values": ["vd"]},
                success={"type": "selected_values_are", "value": ["vd", "da", "ce"]},
                retry={"max_attempts": 1, "safe_to_repeat": True}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures, "a filter that lost two of its three choices must not pass"
    assert "every option the recording selected" in failures[0][3]


def test_a_dialog_the_recording_never_saw_is_refused_not_agreed_to(
    services, engine, site
) -> None:
    """Agreeing to a question nobody was asked during capture is a guess.

    The same native confirmation shape carries "export the report now" and
    "delete these records". A plan that demonstrated neither has no business
    answering yes to either, so a run without a recorded dialog step dismisses
    one — and writes down that it met it.
    """
    result = _replay(services, engine, site, "confirm_export.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnExport"]'}),
    ])

    # The click itself ran; what did not happen is the export behind the
    # confirmation, because nothing in this plan agreed to it.
    _all_ran(result)
    assert not result.file_paths, "an unrecorded confirmation must not produce a file"


def test_the_same_page_does_produce_the_file_once_the_plan_records_the_dialog(
    services, engine, site
) -> None:
    """The control case, so the rule above cannot be satisfied by refusing everything."""
    result = _replay(services, engine, site, "confirm_export.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnExport"]'}),
        _action(2, "dialog", inputs={"dialog_type": "confirm", "decision": "accept"}),
    ], expects=1)

    assert result.ok, result.message
    assert result.file_path and Path(result.file_path).exists()


def test_a_query_that_never_ran_fails_even_though_its_grid_is_full(
    services, engine, site
) -> None:
    """Presence proves nothing here: the grid is visible and full of stale rows."""
    result = _replay(services, engine, site, "dead_inquiry.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnInquiry"]'},
                success={"type": "content_changed", "value": '[id="resultGrid"]'}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures, "a query that did nothing must not pass on a grid that was already full"
    assert "did not change" in failures[0][3]


def test_the_same_proof_passes_once_the_query_really_refills_the_grid(
    services, engine, site
) -> None:
    """The control case, so the rule above cannot be satisfied by failing everything."""
    result = _replay(services, engine, site, "stale_grid.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnInquiry"]'},
                success={"type": "content_changed", "value": '[id="resultGrid"]'}),
    ])

    _all_ran(result)


def test_a_change_proof_whose_container_is_missing_fails_clearly(
    services, engine, site
) -> None:
    """Not silently: a proof that cannot be measured is not a proof that passed."""
    result = _replay(services, engine, site, "stale_grid.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnInquiry"]'},
                success={"type": "content_changed", "value": '[id="noSuchGrid"]'}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures and "was not on the page before it ran" in failures[0][3]


def test_a_field_is_reachable_at_replay_by_the_words_beside_it(
    services, engine, site
) -> None:
    """The anchor has to survive the ids being regenerated on this very load."""
    result = _replay(services, engine, site, "anchored_form.html", [
        _action(1, "fill",
                locator={"strategy": "css", "value": 'input:right-of(:text-is("From date"))'},
                inputs={"value": "2026-09-01"},
                success={"type": "value_equals", "value": "2026-09-01"},
                retry={"max_attempts": 2, "safe_to_repeat": True}),
        _action(2, "fill",
                locator={"strategy": "css", "value": 'input:right-of(:text-is("To date"))'},
                inputs={"value": "2026-09-30"},
                success={"type": "value_equals", "value": "2026-09-30"},
                retry={"max_attempts": 2, "safe_to_repeat": True}),
    ])

    _all_ran(result)


def test_a_renamed_control_is_named_in_the_failure_rather_than_guessed_at(
    services, engine, site
) -> None:
    """What happens to every automation after a release.

    The control still exists, in the same place, doing the same job — under a
    new name and a new id, so every recorded locator misses. The run must say
    what the page has now, and must still refuse to press it: deciding that
    'Search' does the job of 'Inquiry' is a business decision, not a lookup.
    """
    result = _replay(services, engine, site, "renamed_button.html", [
        _action(1, "click",
                locator={"strategy": "css", "value": '[id="btnInquiry"]',
                         "fallbacks": ['role=button[name="Inquiry"]']},
                inputs={"_fingerprint": {"tag": "button", "role": "button",
                                         "name": "Inquiry", "anchor": "", "x": 0.05, "y": 0.05}}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures, "a step whose element is gone must fail"
    reason = failures[0][3]
    assert "Inquiry" in reason and "Search" in reason, reason
    assert "Nothing was clicked" in reason


def test_a_step_with_no_description_still_fails_with_the_plain_message(
    services, engine, site
) -> None:
    """Recordings made before descriptions existed must not lose their error."""
    result = _replay(services, engine, site, "renamed_button.html", [
        _action(1, "click", locator={"strategy": "css", "value": '[id="btnInquiry"]'}),
    ])

    failures = [step for step in _performed(result) if not step[2]]
    assert failures and "no longer on the page" in failures[0][3]


def test_a_repair_in_the_shared_repository_reaches_a_plan_nobody_edited(
    services, engine, site, tmp_path
) -> None:
    """The whole reason the repository exists.

    The plan's own locator names a button that no longer exists. Nothing about
    the plan changes; the control is repaired once in the shared repository, and
    the step finds it.
    """
    from smartops.recordings.elements import ElementRepository

    path = tmp_path / "elements.json"
    repository = ElementRepository(path)
    element = repository.remember(
        url=f"{site.base_url}/torture/renamed_button.html",
        locators=['[id="btnInquiry"]'],
        fingerprint={"name": "Inquiry", "role": "button"},
    )
    repository.repair(element.reference, ['role=button[name="Search"]'])
    repository.save()

    from smartops.ports.browser import ReplayRequest
    from smartops.sessions import session_path

    destination = Path(services.settings.storage.raw_data_dir) / "replay"
    destination.mkdir(parents=True, exist_ok=True)
    result = engine.replay(ReplayRequest(
        system="portal", report="daily", destination_dir=destination,
        plan={
            "plan_version": 2,
            "start_url": f"{site.base_url}/torture/renamed_button.html",
            "actions": [_action(1, "click",
                                locator={"strategy": "css", "value": '[id="btnInquiry"]'},
                                inputs={"_element": element.reference})],
        },
        session_state_path=session_path(services.settings.storage.sessions_dir, "portal"),
        # The description is handed over already loaded. The browser adapter is
        # not allowed to fetch one itself: doing so made the executor depend on
        # the recorder that produced it, which is the two-way dependency the
        # project graph caught. See .project-eye/rules.yaml, ARCH-002.
        elements=ElementRepository(path).load(),
    ))

    _all_ran(result)


def test_the_same_plan_without_the_repository_still_fails_the_old_way(
    services, engine, site
) -> None:
    """A run given no repository must behave exactly as it did before one existed."""
    from smartops.ports.browser import ReplayRequest
    from smartops.sessions import session_path

    destination = Path(services.settings.storage.raw_data_dir) / "replay"
    destination.mkdir(parents=True, exist_ok=True)
    result = engine.replay(ReplayRequest(
        system="portal", report="daily", destination_dir=destination,
        plan={
            "plan_version": 2,
            "start_url": f"{site.base_url}/torture/renamed_button.html",
            "actions": [_action(1, "click",
                                locator={"strategy": "css", "value": '[id="btnInquiry"]'},
                                inputs={"_element": "some/screen#gone-00000000"})],
        },
        session_state_path=session_path(services.settings.storage.sessions_dir, "portal"),
    ))

    failures = [step for step in _performed(result) if not step[2]]
    assert failures, "with no repository there is nothing to repair the locator"
