"""F-03 tests: saved sessions, session-expiry detection, and evidence keyed by
run_id instead of system:report — all against local pages only, never a real site.
"""

from __future__ import annotations

import http.server
import os
import threading
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from smartops.config import BrowserSettings
from smartops.domain.enums import ExtractionLayer
from smartops.ports.browser import ExtractionRequest


def _resolve_executable_path() -> str | None:
    env_path = os.environ.get("SMARTOPS_TEST_CHROMIUM_PATH") or os.environ.get(
        "PLAYWRIGHT_CHROMIUM_PATH"
    )
    if env_path:
        return env_path
    default = Path("/opt/pw-browsers/chromium")
    return str(default) if default.exists() else None


@contextmanager
def _local_server(directory: Path):
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _adapter() -> PlaywrightBrowserAdapter:
    settings = BrowserSettings(headless=True, viewport_width=1024, viewport_height=768)
    return PlaywrightBrowserAdapter(settings, executable_path=_resolve_executable_path())


CSV_CONTENT = b"col_a,col_b\n1,2\n"

LOGIN_PAGE_HTML = """<!doctype html><html><body>
<form id="login-form"><input name="user"/></form>
</body></html>"""

DASHBOARD_PAGE_HTML = """<!doctype html><html><body>
<div id="dashboard">Welcome</div>
<a id="dl" download="report.csv" href="report.csv">Download</a>
</body></html>"""

NO_DASHBOARD_PAGE_HTML = """<!doctype html><html><body>
<p>No dashboard here</p>
</body></html>"""


def _site(tmp_path: Path, name: str, html: str) -> Path:
    site_dir = tmp_path / name
    site_dir.mkdir()
    (site_dir / "page.html").write_text(html, encoding="utf-8")
    (site_dir / "report.csv").write_bytes(CSV_CONTENT)
    return site_dir


def test_login_form_present_means_session_expired(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site1", LOGIN_PAGE_HTML)
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw",
            filters={"url": f"{base_url}/page.html", "download_selector": "#dl", "login_selector": "#login-form"},
        )
        result = _adapter().extract(request)

    assert not result.ok
    assert result.auth_required is True
    assert "login" in result.message.lower() or "session" in result.message.lower()


def test_missing_logged_in_marker_means_session_expired(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site2", NO_DASHBOARD_PAGE_HTML)
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw",
            filters={
                "url": f"{base_url}/page.html",
                "download_selector": "#dl",
                "logged_in_selector": "#dashboard",
            },
        )
        result = _adapter().extract(request)

    assert not result.ok
    assert result.auth_required is True


def test_logged_in_marker_present_downloads_normally(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site3", DASHBOARD_PAGE_HTML)
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw",
            filters={
                "url": f"{base_url}/page.html",
                "download_selector": "#dl",
                "logged_in_selector": "#dashboard",
            },
        )
        result = _adapter().extract(request)

    assert result.ok, result.message
    assert result.auth_required is False
    assert result.layer_used is ExtractionLayer.DOM


def test_failure_evidence_writes_screenshot_to_disk_not_base64(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site4", LOGIN_PAGE_HTML)
    evidence_dir = tmp_path / "evidence"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw",
            evidence_dir=evidence_dir,
            filters={"url": f"{base_url}/page.html", "download_selector": "#dl", "login_selector": "#login-form"},
        )
        result = _adapter().extract(request)

    assert not result.ok
    assert "screenshot_base64" not in result.evidence
    screenshot_path = result.evidence.get("screenshot_path")
    assert screenshot_path is not None
    assert Path(screenshot_path).exists()
    assert Path(screenshot_path).is_relative_to(evidence_dir)


def test_concurrent_runs_do_not_mix_evidence(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site5", LOGIN_PAGE_HTML)
    adapter = _adapter()
    with _local_server(site_dir) as base_url:
        request_a = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw_a",
            run_id="run_a",
            filters={"url": f"{base_url}/page.html", "download_selector": "#dl", "login_selector": "#login-form"},
        )
        request_b = ExtractionRequest(
            system="crm",
            report="weekly",
            destination_dir=tmp_path / "raw_b",
            run_id="run_b",
            filters={"url": f"{base_url}/page.html", "download_selector": "#dl", "login_selector": "#login-form"},
        )
        result_a = adapter.extract(request_a)
        result_b = adapter.extract(request_b)

    assert not result_a.ok and not result_b.ok
    evidence_a = adapter.capture_evidence("run_a")
    evidence_b = adapter.capture_evidence("run_b")
    assert evidence_a["run_id"] == "run_a"
    assert evidence_b["run_id"] == "run_b"
    assert evidence_a["url"] != "" and evidence_b["url"] != ""


def test_direct_download_url_serving_html_falls_back_to_dom(tmp_path: Path) -> None:
    site_dir = _site(tmp_path, "site6", DASHBOARD_PAGE_HTML)
    # No actual .csv file exists at this URL, so the server responds with the
    # HTML page itself instead of the requested file — exactly like a redirect to a login page.
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=tmp_path / "raw",
            filters={
                "url": f"{base_url}/page.html",
                "direct_download_url": f"{base_url}/page.html",
                "download_selector": "#dl",
            },
        )
        result = _adapter().extract(request)

    assert result.ok, result.message
    assert result.layer_used is ExtractionLayer.DOM
