"""اختبار S-07 (تكاملي): غرفة القيادة الثابتة تشتغل فعليًا فوق خادم حقيقي.

يشغّل خادم uvicorn حقيقي على منفذ محلي، ثم يفتح المتصفح (Playwright) على
صفحات web/ الثابتة ويتفاعل معها كمستخدم حقيقي، للتأكد أنها تعمل على
بيانات platform.selfcheck دون أي تعديل في الخلفية (كل الاتصال عبر
نقاط API الموجودة بالفعل في api/app.py).
"""

from __future__ import annotations

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
        assert self.server.started, "خادم الاختبار لم يبدأ خلال المهلة"

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
        pg.errors_log = errors  # نلصقها بالكائن عشان تتفحص في الاختبار
        yield pg
        browser.close()


def test_dashboard_runs_selfcheck_and_updates_live(page) -> None:
    page.goto("/app/index.html")
    page.wait_for_selector("#run-selfcheck")

    page.click("#run-selfcheck")
    page.wait_for_function(
        "document.getElementById('selfcheck-hint').textContent.includes('Completed')",
        timeout=5000,
    )

    status_badge = page.locator("#selfcheck-status .badge")
    assert status_badge.inner_text() == "Succeeded"

    # لازم ظهر في جدول آخر التشغيلات كمان
    page.wait_for_selector("#recent-runs td:has-text('platform.selfcheck')")

    assert page.errors_log == []


def test_dashboard_loads_recent_events_on_open(page, services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    services.runner.execute(run.id)

    page.goto("/app/index.html")
    page.wait_for_selector("#live-events li:not(.empty)")

    assert "Run completed" in page.locator("#live-events").inner_text()
    assert page.errors_log == []


def test_runs_page_lists_run_and_links_to_detail(page) -> None:
    page.goto("/app/runs.html")
    page.fill("#workflow-key", "platform.selfcheck")
    page.click("#start-run")

    page.wait_for_url("**/run.html?id=*", timeout=5000)

    page.wait_for_function(
        "document.querySelector('#run-info .badge')?.textContent === 'Succeeded'", timeout=5000
    )

    step_rows = page.locator("#steps-body tr")
    assert step_rows.count() == 2
    statuses = page.locator("#steps-body .badge").all_inner_texts()
    assert statuses == ["Succeeded", "Succeeded"]

    assert page.errors_log == []


def test_incidents_and_files_pages_load_without_errors(page) -> None:
    page.goto("/app/incidents.html")
    page.wait_for_selector("#incidents-body td")
    assert "No matching incidents" in page.locator("#incidents-body").inner_text()

    page.goto("/app/files.html")
    page.wait_for_selector("#files-body td")
    assert "No matching files" in page.locator("#files-body").inner_text()

    assert page.errors_log == []


@pytest.mark.parametrize(
    "path",
    ["index.html", "runs.html", "run.html", "incidents.html", "files.html", "recordings.html"],
)
def test_every_primary_page_links_to_recording_center(page, path) -> None:
    page.goto(f"/app/{path}")

    link = page.locator('.side-nav a[href="recordings.html"]')
    assert link.count() == 1
    assert "Recordings" in link.inner_text()


def test_sidebar_opens_recording_center(page) -> None:
    page.goto("/app/index.html")

    page.locator('.side-nav a[href="recordings.html"]').click()

    page.wait_for_url("**/app/recordings.html")
    assert page.locator("section.panel h2").first.inner_text() == "Capture a workflow"
    assert page.errors_log == []


def test_retry_button_disabled_after_success(page, services) -> None:
    run = services.runner.create_run("platform.selfcheck")
    services.runner.execute(run.id)

    page.goto(f"/app/run.html?id={run.id}")
    page.wait_for_function(
        "document.querySelector('#run-info .badge')?.textContent === 'Succeeded'", timeout=5000
    )

    assert page.locator("#retry-button").is_disabled()
    assert page.errors_log == []
