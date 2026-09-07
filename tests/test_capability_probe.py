"""Asking a page how it can be automated, before spending a recording on it.

Every failed G-MES attempt so far was a recording built on the one sensor that
happened to be tried first. These tests hold the probe to the opposite
discipline: it reports what each sensor can actually see on a given screen, and
picks the strongest identity and the strongest proof that screen supports —
including on a screen whose markup is deliberately useless.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from smartops.recordings.probe import (
    accessibility_sensor,
    addressability_sensor,
    nexacro_sensor,
    probe_page,
)
from smartops.recordings.vision import PageVision
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


@contextmanager
def _page(url: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=_chromium_path())
        try:
            context = browser.new_context(viewport={"width": 900, "height": 600})
            page = context.new_page()
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def test_a_nexacro_screen_is_read_through_its_own_object_model(site) -> None:
    with _page(f"{site.base_url}/nexacro_like_app.html") as page:
        sensor = nexacro_sensor(page)

    assert sensor.status == "strong"
    assert sensor.facts["version"] == "17.1.2.500"
    assert sensor.facts["runtime"] == "web"
    assert sensor.facts["form_count"] == 1

    components = {c["id"]: c for c in sensor.facts["forms"][0]["components"]}
    assert components["btnInquiry"]["type"] == "Button"
    assert components["btnInquiry"]["text"] == "Inquiry"
    assert components["grdResult"]["type"] == "Grid"

    # The grid is the point: a named dataset with a countable shape is proof
    # material, and it exists whether or not anything can see the screen.
    grid = sensor.facts["grids"][0]
    assert grid == {"id": "grdResult", "dataset": "dsResult", "rows": 0, "columns": 19}
    assert sensor.facts["excel_export_available"] is True


def test_a_dataset_row_count_is_what_proves_a_query_ran(site) -> None:
    with _page(f"{site.base_url}/nexacro_like_app.html") as page:
        before = nexacro_sensor(page).facts["grids"][0]["rows"]
        page.evaluate("() => window.__runInquiry()")
        after = nexacro_sensor(page).facts["grids"][0]["rows"]

    assert (before, after) == (0, 758)


def test_useless_markup_does_not_stop_a_nexacro_screen_being_automatable(site) -> None:
    """The screen whose ids are all generated is still fully addressable."""
    with _page(f"{site.base_url}/nexacro_like_app.html") as page:
        markup = addressability_sensor(page)
        report = probe_page(page, PageVision(Path(os.environ.get("TMPDIR", "/tmp"))))

    assert markup.status == "absent"  # not one stable id in the whole page
    assert markup.facts["stable_id"] == 0
    assert report["recommended_identity"] == "nexacro_component"
    assert report["recommended_evidence"] == "dataset_row_count"
    assert "nexacro component" in report["verdict"]


def test_an_ordinary_page_is_reported_as_an_ordinary_page(site) -> None:
    with _page(f"{site.base_url}/plain_form.html") as page:
        report = probe_page(page, PageVision(Path(os.environ.get("TMPDIR", "/tmp"))))

    sensors = {sensor["sensor"]: sensor for sensor in report["sensors"]}
    assert sensors["nexacro"]["status"] == "absent"
    assert sensors["dom"]["status"] == "strong"
    assert report["recommended_identity"] == "accessible_name"
    assert report["recommended_evidence"] == "control_state"


def test_chrome_can_name_the_controls_of_an_ordinary_page(site) -> None:
    with _page(f"{site.base_url}/plain_form.html") as page:
        sensor = accessibility_sensor(page)

    assert sensor.status in {"strong", "partial"}
    assert sensor.facts["named_total"] > 0


def test_a_screen_nobody_can_see_still_reports_how_to_prove_a_step(site) -> None:
    """The whole point: a withheld picture is not a dead end, it is one dead sensor."""
    with _page(f"{site.base_url}/withheld_frame.html") as page:
        report = probe_page(page, PageVision(Path(os.environ.get("TMPDIR", "/tmp"))))

    sensors = {sensor["sensor"]: sensor for sensor in report["sensors"]}
    assert sensors["visual"]["status"] == "blocked"
    assert "not available on this machine" in report["verdict"]


def test_a_screen_with_nothing_addressable_says_do_not_record_it(site) -> None:
    with _page(f"{site.base_url}/withheld_frame.html") as page:
        report = probe_page(page, PageVision(Path(os.environ.get("TMPDIR", "/tmp"))))

    assert report["recommended_identity"] == ""
    assert "Recording it would produce" in report["verdict"]


def test_the_probe_never_returns_a_field_value(site) -> None:
    with _page(f"{site.base_url}/plain_form.html") as page:
        report = probe_page(page, PageVision(Path(os.environ.get("TMPDIR", "/tmp"))))

    serialised = str(report)
    assert "never-reported" not in serialised  # the password on that page
    assert "A-1024" not in serialised  # an ordinary field's value
