"""S-02 tests: the Playwright adapter, network then DOM layers, against a local page only."""

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


CSV_CONTENT = b"col_a,col_b\n1,2\n3,4\n"

PAGE_HTML = """<!doctype html><html><body>
<h1>SmartOps test page</h1>
<a id="dl" download="report.csv" href="report.csv">Download report</a>
</body></html>"""


def _adapter() -> PlaywrightBrowserAdapter:
    settings = BrowserSettings(headless=True, viewport_width=1024, viewport_height=768)
    return PlaywrightBrowserAdapter(settings, executable_path=_resolve_executable_path())


def test_dom_layer_downloads_and_saves_file(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")
    (site_dir / "report.csv").write_bytes(CSV_CONTENT)

    destination = tmp_path / "raw"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            filters={"url": f"{base_url}/page.html", "download_selector": "#dl"},
        )
        result = _adapter().extract(request)

    assert result.ok, result.message
    assert result.layer_used is ExtractionLayer.DOM
    assert result.file_path is not None and result.file_path.exists()
    assert result.file_path.read_bytes() == CSV_CONTENT
    assert result.size_bytes == len(CSV_CONTENT)


def test_network_layer_is_tried_before_dom(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")
    (site_dir / "report.csv").write_bytes(CSV_CONTENT)

    destination = tmp_path / "raw"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            filters={
                "url": f"{base_url}/page.html",
                "direct_download_url": f"{base_url}/report.csv",
                # No download_selector here: if the system fell back to DOM
                # it would fail, so success here proves the network layer is what actually ran.
            },
        )
        result = _adapter().extract(request)

    assert result.ok, result.message
    assert result.layer_used is ExtractionLayer.NETWORK
    assert result.file_path.read_bytes() == CSV_CONTENT


def test_falls_back_to_dom_when_direct_url_is_broken(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")
    (site_dir / "report.csv").write_bytes(CSV_CONTENT)

    destination = tmp_path / "raw"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            filters={
                "url": f"{base_url}/page.html",
                "direct_download_url": f"{base_url}/does-not-exist.csv",
                "download_selector": "#dl",
            },
        )
        result = _adapter().extract(request)

    assert result.ok, result.message
    assert result.layer_used is ExtractionLayer.DOM


def test_missing_selector_fails_with_clear_message(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")

    destination = tmp_path / "raw"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            filters={"url": f"{base_url}/page.html"},
        )
        result = _adapter().extract(request)

    assert not result.ok
    assert result.layer_used is ExtractionLayer.DOM
    assert "download_selector" in result.message


def test_missing_url_fails_with_clear_message(tmp_path: Path) -> None:
    request = ExtractionRequest(
        system="erp",
        report="daily_sales",
        destination_dir=tmp_path / "raw",
        filters={},
    )
    result = _adapter().extract(request)

    assert not result.ok
    assert "url" in result.message


def test_selector_not_found_captures_evidence(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")

    destination = tmp_path / "raw"
    adapter = _adapter()
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            timeout_seconds=3,
            filters={"url": f"{base_url}/page.html", "download_selector": "#does-not-exist"},
        )
        result = adapter.extract(request)

    assert not result.ok
    assert result.layer_used is ExtractionLayer.DOM
    assert result.evidence  # there must be evidence (a message + URL, and maybe a screenshot)

    evidence = adapter.capture_evidence("run_demo")
    assert evidence["run_id"] == "run_demo"
    assert "message" in evidence


def test_dom_disabled_and_network_failed_reports_clear_reason(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")

    destination = tmp_path / "raw"
    with _local_server(site_dir) as base_url:
        request = ExtractionRequest(
            system="erp",
            report="daily_sales",
            destination_dir=destination,
            allowed_layers=(ExtractionLayer.NETWORK,),
            filters={
                "url": f"{base_url}/page.html",
                "direct_download_url": f"{base_url}/does-not-exist.csv",
            },
        )
        result = _adapter().extract(request)

    assert not result.ok
    assert result.layer_used is ExtractionLayer.NETWORK
    assert "DOM" in result.message
