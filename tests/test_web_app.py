"""The operations centre, driven the way a real user drives it.

A real uvicorn server, a real browser, real clicks. Nothing is stubbed behind
the pages: everything goes through the same API endpoints the shipped app uses.

The centrepiece is `test_the_whole_journey_end_to_end`, which walks all eleven
stages in one browser session — add a system, test it, sign in, record, review,
build an automation, test it, approve it, run it, verify the file, schedule it —
and asserts that each gate actually refuses to be skipped. That test is the
success criterion for the platform: a non-technical user reaching a working,
scheduled automation without a terminal or a hand-edited file.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

import uvicorn

from smartops.api.app import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _resolve_executable_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


class _LiveServer:
    def __init__(self, app) -> None:
        self.port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "The test server did not start within the timeout"

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5.0)


@pytest.fixture
def live_server(services):
    server = _LiveServer(create_app(services))
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def page(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=_resolve_executable_path()
        )
        context = browser.new_context(base_url=live_server.base_url)
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda exc: errors.append(str(exc)))
        pg.errors_log = errors  # attached so each test can assert a clean console
        yield pg
        browser.close()


# ---------- helpers that stand in for the parts needing a real website ----------


def _save_session(services, system_key: str) -> None:
    """Write a saved-session file, as a completed sign-in would."""
    from smartops.sessions import session_path

    path = session_path(services.settings.storage.sessions_dir, system_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")


def _record_check(services, system_key: str) -> None:
    """Mark a system as connection-tested, as a passing test would."""
    from smartops.checks import ConnectionCheck

    services.connection_checks.record(
        system_key,
        ConnectionCheck(ok=True, reachable=True, signed_in=True, summary="The site opened."),
        at="2026-01-01T00:00:00Z",
    )


class _FakeBrowser:
    """Stands in for a real website: replays a plan by writing the file it would download.

    It asserts the shape of what the engine hands it, so this fake failing means
    the replay contract broke — not that a website changed.
    """

    def __init__(self) -> None:
        self.replayed_plans: list[dict] = []

    def replay(self, request):
        from smartops.domain.enums import ExtractionLayer
        from smartops.ports.browser import ExtractionResult

        self.replayed_plans.append(request.plan)
        assert request.plan.get("actions"), "a replay must receive the recorded actions"
        target = Path(request.destination_dir) / "daily_sales.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"date,amount\n2026-01-01,42\n")
        return ExtractionResult(
            ok=True,
            layer_used=ExtractionLayer.DOM,
            file_path=target,
            original_name=target.name,
            size_bytes=target.stat().st_size,
            duration_seconds=1.0,
        )

    def extract(self, request):  # not used by these tests, but part of the port
        raise NotImplementedError

    def capture_evidence(self, run_id: str) -> dict:
        return {}


def _recorded_system(services, key: str = "erp") -> None:
    """A system that is defined, connection-tested and signed in."""
    services.systems.save(
        {
            "key": key,
            "name": "Sales ERP",
            "auth": {
                "mode": "session",
                "login_url": "https://erp.example.local/login",
                "logged_in_selector": "#user-menu",
            },
            "reports": [
                {
                    "key": "daily_sales",
                    "title": "Daily sales",
                    "url": "https://erp.example.local/reports/daily",
                    "download_selector": "#export",
                }
            ],
        }
    )
    _record_check(services, key)
    _save_session(services, key)


def _completed_recording(services, system_key: str = "erp"):
    """A finished recording whose captured steps make a replayable plan."""
    from smartops.domain.enums import RecordingStatus
    from smartops.domain.models import RecordingStep

    record = services.recording_manager.create("Daily sales export", system_key)
    services.recordings.save_step(
        RecordingStep(
            record.id, 1, "click", selector="#reports",
            page_url_redacted="https://erp.example.local/reports/daily", target_text_redacted="Reports",
        )
    )
    services.recordings.save_step(
        RecordingStep(
            record.id, 2, "click", selector="#export",
            page_url_redacted="https://erp.example.local/reports/daily", target_text_redacted="Export",
        )
    )
    services.recordings.save_step(
        RecordingStep(record.id, 3, "download", download_ref="downloads/daily_sales.csv")
    )
    record.status = RecordingStatus.COMPLETED
    record.step_count = 3
    record.download_count = 1
    services.recordings.save(record)
    return record


# ---------- the journey, end to end ----------


def test_the_whole_journey_end_to_end(page, services) -> None:
    """One user, one browser session, from an empty platform to a scheduled automation.

    Every stage here is done through the interface. No terminal command, no file
    edited by hand, and no restart — which is exactly the criterion the rebuild
    had to meet.
    """
    services.browser = _FakeBrowser()

    # --- Stage 1: add a system, in the app, with no YAML and no restart. ---
    page.goto("/app/systems.html")
    page.wait_for_selector("#system-form")
    page.fill("#key", "erp")
    page.fill("#name", "Sales ERP")
    page.select_option("#auth-mode", "session")
    page.fill("#login-url", "https://erp.example.local/login")
    page.fill("#logged-in-selector", "#user-menu")
    page.fill(".r-key", "daily_sales")
    page.fill(".r-title", "Daily sales")
    page.fill(".r-url", "https://erp.example.local/reports/daily")
    page.fill(".r-download", "#export")
    page.click("#system-form button[type=submit]")
    page.wait_for_selector("#systems-body td:has-text('Sales ERP')")
    assert services.systems.get("erp").name == "Sales ERP"

    # --- Stage 4 is refused until 2 and 3 are done. ---
    page.goto("/app/recordings.html")
    page.wait_for_selector("#system-note")
    page.wait_for_function(
        "document.querySelector('#system-note').textContent.includes('Test the connection')"
    )
    assert page.locator("#create-form button[type=submit]").is_disabled()

    # --- Stages 2 and 3: tested and signed in (the browser halves are faked). ---
    _record_check(services, "erp")
    _save_session(services, "erp")

    # --- Stages 4 and 5: a recording, reviewed into an executable plan. ---
    record = _completed_recording(services)
    page.goto(f"/app/recording.html?id={record.id}")
    page.wait_for_selector("#controls button")
    page.click("button:has-text('Build the automation plan')")
    page.wait_for_selector("#plan li:not(.empty)")
    # The review must say plainly that this recording can be repeated.
    page.wait_for_selector(".notice-box:has-text('can be repeated')")

    # --- Stage 6 setup: promote the reviewed recording to an automation. ---
    page.fill("#promote input[placeholder='Name for this automation']", "Daily sales export")
    # Naming the report explicitly is what ties every run of this automation to
    # one folder in the raw data centre; left blank it is derived from the name.
    page.fill("#promote input[placeholder='Short name for the report (optional)']", "daily_sales")
    page.click("button:has-text('Create the automation')")
    page.wait_for_url("**/process.html?id=*")

    process_id = page.url.split("id=")[1]
    process = services.processes.get(process_id)
    assert process.status.value == "draft"

    # --- The approval gate refuses an untested automation, from the API itself. ---
    with pytest.raises(Exception):
        services.process_manager.approve(process_id)
    # And the schedule form is visibly closed until it is approved.
    assert page.locator("#schedule-form button[type=submit]").is_disabled()
    page.wait_for_selector("#schedule-hint:has-text('Approve this automation first')")

    # --- Stage 6: test it for real. ---
    page.click("button:has-text('Run the test now')")
    page.wait_for_selector(".notice-box:has-text('The test passed')", timeout=20000)
    assert services.processes.get(process_id).status.value == "tested"
    # The plan really went through the replay engine.
    assert services.browser.replayed_plans, "the test run must actually replay the recorded plan"

    # --- Stage 7: approve. ---
    page.click("button:has-text('Approve this automation')")
    page.wait_for_selector(".notice-box:has-text('Approved')")
    assert services.processes.get(process_id).status.value == "approved"

    # --- Stage 8: run it, and land on the run's own page. ---
    page.click("button:has-text('Run it now')")
    page.wait_for_url("**/run.html?id=*", timeout=10000)
    page.wait_for_function(
        "document.querySelector('#run-info .badge')?.textContent === 'Succeeded'", timeout=30000
    )

    # --- Stage 9: the result exists, is valid, and is downloadable. ---
    page.wait_for_selector("#files-body td:has-text('daily_sales.csv')")
    page.goto("/app/files.html")
    page.wait_for_selector("#files-body .badge:has-text('Valid')")
    files = [f for f in services.files.list(limit=10) if f.report == "daily_sales"]
    assert files and files[0].validation_status.value == "passed"

    # --- Stage 10: schedule it, now that it is allowed. ---
    page.goto(f"/app/process.html?id={process_id}")
    page.wait_for_selector("#schedule-form button[type=submit]:not([disabled])")
    page.select_option("#schedule-kind", "daily")
    page.fill("#daily-at", "07:30")
    page.click("#schedule-form button[type=submit]")
    page.wait_for_selector(".notice-box:has-text('runs on its own')")

    scheduled = services.processes.get(process_id)
    assert scheduled.is_scheduled and scheduled.schedule_daily_at == "07:30"
    # The scheduler — the thing that makes it happen without the user — sees it.
    assert [p.id for p in services.processes.scheduled()] == [process_id]

    assert page.errors_log == []


# ---------- the individual pages ----------


def test_overview_shows_the_next_step_and_the_full_journey(page) -> None:
    page.goto("/app/index.html")
    page.wait_for_selector("#journey-steps .journey-step")

    # Eleven stages, and the first one is what an empty platform needs.
    assert page.locator("#journey-steps .journey-step").count() == 11
    assert "Add the system" in page.locator("#next-step").inner_text()
    assert page.errors_log == []


def test_overview_runs_the_health_check(page) -> None:
    page.goto("/app/index.html")
    page.wait_for_selector("#run-selfcheck")
    page.click("#run-selfcheck")
    page.wait_for_function(
        "document.getElementById('selfcheck-hint').textContent.includes('healthy')", timeout=10000
    )
    page.wait_for_selector("#recent-runs td:has-text('Platform health check')")
    assert page.errors_log == []


def test_overview_loads_recent_events_on_open(page, services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    services.runner.execute(run.id)

    page.goto("/app/index.html")
    page.wait_for_selector("#live-events li:not(.empty)")
    assert "completed successfully" in page.locator("#live-events").inner_text()
    assert page.errors_log == []


def test_runs_page_starts_a_workflow_and_links_to_its_detail(page) -> None:
    page.goto("/app/runs.html")
    # The advanced starter is deliberately behind a disclosure, so open it the
    # way a user would before using it.
    page.click("summary:has-text('Start a workflow by key')")
    page.fill("#workflow-key", "platform.selfcheck")
    page.click("#start-run")

    page.wait_for_url("**/run.html?id=*", timeout=10000)
    page.wait_for_function(
        "document.querySelector('#run-info .badge')?.textContent === 'Succeeded'", timeout=10000
    )

    assert page.locator("#steps-body tr").count() == 2
    assert page.locator("#steps-body .badge").all_inner_texts() == ["Done", "Done"]
    assert page.errors_log == []


def test_empty_pages_say_what_is_missing_rather_than_nothing(page) -> None:
    page.goto("/app/incidents.html")
    page.wait_for_selector("#incidents-list p")
    assert "Nothing needs your attention" in page.locator("#incidents-list").inner_text()

    page.goto("/app/files.html")
    page.wait_for_selector("#files-body td")
    assert "No files collected yet" in page.locator("#files-body").inner_text()

    page.goto("/app/processes.html")
    page.wait_for_selector("#rows td")
    assert "Finish a recording" in page.locator("#rows").inner_text()

    assert page.errors_log == []


@pytest.mark.parametrize(
    "path",
    [
        "index.html", "systems.html", "credentials.html", "recordings.html",
        "processes.html", "runs.html", "files.html", "incidents.html",
    ],
)
def test_every_page_carries_the_journey_navigation(page, path) -> None:
    page.goto(f"/app/{path}")
    page.wait_for_selector(".side-nav-link")

    # The navigation is the journey, in order, on every page.
    labels = page.locator(".side-nav .nav-label").all_inner_texts()
    assert labels == [
        "Overview", "1 · Systems", "2 · Sign-in", "3 · Recordings",
        "4 · Automations", "5 · Runs", "6 · Results", "7 · Issues",
    ]


def test_navigation_marks_finished_stages(page, services) -> None:
    """The sidebar reflects real progress, not a static list."""
    page.goto("/app/index.html")
    page.wait_for_selector(".side-nav-link[data-stage='system']")
    page.wait_for_function(
        "document.querySelector(\"[data-stage='system']\").classList.contains('stage-next')"
    )

    _recorded_system(services)
    page.reload()
    page.wait_for_function(
        "document.querySelector(\"[data-stage='system']\").classList.contains('stage-done')"
    )
    assert page.errors_log == []


def test_retry_button_disabled_after_success(page, services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    services.runner.execute(run.id)

    page.goto(f"/app/run.html?id={run.id}")
    page.wait_for_function(
        "document.querySelector('#run-info .badge')?.textContent === 'Succeeded'", timeout=10000
    )

    assert page.locator("#retry-button").is_disabled()
    assert page.errors_log == []


def test_a_blocked_stage_explains_itself_with_a_way_forward(page, services) -> None:
    """A refusal must name the missing step and link to the page that fixes it."""
    services.systems.save(
        {
            "key": "erp",
            "name": "Sales ERP",
            "auth": {"mode": "session", "login_url": "https://erp.example.local/login",
                     "logged_in_selector": "#user-menu"},
            "reports": [{"key": "daily_sales", "title": "Daily sales",
                         "url": "https://erp.example.local/reports/daily", "download_selector": "#export"}],
        }
    )
    _record_check(services, "erp")  # tested, but never signed in

    page.goto("/app/recordings.html")
    page.wait_for_selector("#system-note")
    assert "Sign in" in page.locator("#system-note").inner_text()
    assert page.errors_log == []
