"""Tests for the final wiring decision: which adapters are actually enabled inside Services.

The safe adapters (file validator, browser, local log, analytical archive,
system registry) are always wired up by default. The AI agent stays off
unless explicitly enabled in settings, and only in read_only mode.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import zipfile
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from tests.conftest import _google_chrome_path, needs_google_chrome

from smartops.adapters.agents.cli_runner import CliAgentRunner
from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from smartops.adapters.history.archiver import HistoryArchiver
from smartops.adapters.notify.local import CompositeNotifier, WebhookNotifier
from smartops.adapters.validation.local import LocalFileValidator
from smartops.config import (
    AgentSettings,
    AgentsSettings,
    AppSettings,
    BrowserSettings,
    NotifySettings,
    SafetySettings,
    Settings,
    StorageSettings,
)
from smartops.core.errors import ConfigurationError
from smartops.domain.enums import RunStatus, ValidationStatus
from smartops.services import Services
from smartops.storage.db import Database
from smartops.ports.validation import ValidationRules
from smartops.workflows.profiles import SystemRegistry


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        app=AppSettings(environment="test"),
        storage=StorageSettings(
            sqlite_path=tmp_path / "smartops.db",
            raw_data_dir=tmp_path / "raw",
            incidents_dir=tmp_path / "incidents",
            logs_dir=tmp_path / "logs",
            history_dir=tmp_path / "history",
            sessions_dir=tmp_path / "sessions",
            systems_dir=tmp_path / "systems",
        ),
        browser=BrowserSettings(),
        safety=SafetySettings(),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_services(tmp_path: Path, **overrides) -> Services:
    return Services(_settings(tmp_path, **overrides), db=Database(":memory:"))


def test_safe_adapters_are_wired_by_default(tmp_path: Path) -> None:
    svc = _make_services(tmp_path)
    try:
        assert isinstance(svc.validator, LocalFileValidator)
        assert isinstance(svc.browser, PlaywrightBrowserAdapter)
        assert isinstance(svc.history, HistoryArchiver)
        assert isinstance(svc.systems, SystemRegistry)
        assert isinstance(svc.notifier, CompositeNotifier)
        assert svc.agent_runner is None  # off by default
    finally:
        svc.close()


def test_local_log_notifier_actually_writes(tmp_path: Path) -> None:
    svc = _make_services(tmp_path)
    try:
        from smartops.domain.enums import AlertLevel
        from smartops.ports.notify import Alert

        assert svc.notifier.send(Alert(level=AlertLevel.RED, title="test")) is True
        log_path = tmp_path / "logs" / "alerts.jsonl"
        assert log_path.exists()
        assert json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])["title"] == "test"
    finally:
        svc.close()


def test_webhook_url_adds_webhook_channel(tmp_path: Path) -> None:
    svc = _make_services(tmp_path, notify=NotifySettings(webhook_url="http://127.0.0.1:9/x"))
    try:
        assert any(isinstance(n, WebhookNotifier) for n in svc.notifier._notifiers)
    finally:
        svc.close()


def test_agent_disabled_by_default_is_none(tmp_path: Path) -> None:
    svc = _make_services(tmp_path, agents=AgentsSettings())
    try:
        assert svc.agent_runner is None
    finally:
        svc.close()


def test_agent_enabled_read_only_is_wired(tmp_path: Path) -> None:
    svc = _make_services(
        tmp_path, agents=AgentsSettings(claude=AgentSettings(enabled=True, mode="read_only"))
    )
    try:
        assert isinstance(svc.agent_runner, CliAgentRunner)
    finally:
        svc.close()


def test_claude_takes_precedence_over_codex_when_both_enabled(tmp_path: Path) -> None:
    svc = _make_services(
        tmp_path,
        agents=AgentsSettings(
            codex=AgentSettings(enabled=True, mode="read_only"),
            claude=AgentSettings(enabled=True, mode="read_only"),
        ),
    )
    try:
        name, settings = svc._chosen_agent()
        assert name == "claude"
        assert settings is svc.settings.agents.claude
    finally:
        svc.close()


@pytest.mark.parametrize("mode", ["experiment", "execute", "something_unknown"])
def test_unsupported_agent_mode_is_rejected_at_construction(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ConfigurationError, match="not yet supported"):
        _make_services(tmp_path, agents=AgentsSettings(claude=AgentSettings(enabled=True, mode=mode)))


# ---------- Real integration: collect.report with the actually wired adapters ----------

CSV_CONTENT = b"date,amount\n2026-01-01,100\n2026-01-02,200\n"
PAGE_HTML = """<!doctype html><html><body>
<a id="dl" download="daily_sales.csv" href="daily_sales.csv">Download</a>
</body></html>"""

XLSX_PAGE_HTML = """<!doctype html><html><body>
<a id="dl" download="report.xlsx" href="report.xlsx">Download workbook</a>
</body></html>"""


def _make_xlsx(path: Path, rows: list[list[str]]) -> None:
    """Build a tiny valid .xlsx file without extra dependencies."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    row_xml: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for c_idx, value in enumerate(row):
            col_letter = chr(ord("A") + c_idx)
            ref = f"{col_letter}{r_idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml"
    ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="xl/workbook.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _resolve_google_chrome_path() -> str:
    """Where branded Chrome is. Non-empty by the time this runs: the guard on the
    test below has already skipped it on a machine that does not have Chrome,
    which is a fact about the machine and not a failure of the wiring."""
    return _google_chrome_path()


def _resolve_executable_path() -> str | None:
    """A workaround for the current development environment only (see
    test_browser_adapter.py): the Chromium build pre-installed here has a
    different revision number than what Playwright expects by default. This
    has nothing to do with the wiring logic in services.py — in an
    environment set up with a normal 'playwright install', no override is needed."""
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


def test_collect_report_works_end_to_end_with_real_wired_adapters(tmp_path: Path) -> None:
    """Proves the actual wiring decision runs: a real download (Playwright) +
    real validation (LocalFileValidator), with no fake adapter injected manually."""
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")
    (site_dir / "daily_sales.csv").write_bytes(CSV_CONTENT)

    svc = _make_services(tmp_path)
    # Redirect the Chromium executable to a path for the current dev
    # environment only; the wiring itself (PlaywrightBrowserAdapter built
    # from settings.browser) is unchanged.
    svc.browser = PlaywrightBrowserAdapter(
        svc.settings.browser, executable_path=_resolve_executable_path()
    )
    try:
        with _local_server(site_dir) as base_url:
            run = svc.runner.create_run(
                "collect.report",
                params={
                    "system": "erp_demo",
                    "report": "daily_sales",
                    "filters": {"url": f"{base_url}/page.html", "download_selector": "#dl"},
                    "rules": {
                        "expected_extensions": [".csv"],
                        "required_columns": ["date", "amount"],
                        "min_rows": 2,
                    },
                },
            )
            run = svc.runner.execute(run.id)

        assert run.status is RunStatus.SUCCEEDED, run.error_message
        files = svc.files.list(run_id=run.id)
        assert len(files) == 1
        assert files[0].validation_status is ValidationStatus.PASSED
        assert files[0].row_count == 2
        assert Path(files[0].path).read_bytes() == CSV_CONTENT
    finally:
        svc.close()


@needs_google_chrome
def test_process_replay_downloads_and_validates_xlsx_with_real_chrome(tmp_path: Path) -> None:
    """Exercises the real process.replay workflow in a fresh Chrome context against localhost."""
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    page = site_dir / "page.html"
    workbook = site_dir / "report.xlsx"
    page.write_text(XLSX_PAGE_HTML, encoding="utf-8")
    _make_xlsx(workbook, [["customer", "amount"], ["Alice", "10"], ["Bob", "20"]])

    svc = _make_services(tmp_path)
    svc.browser = PlaywrightBrowserAdapter(
        svc.settings.browser,
        executable_path=_resolve_google_chrome_path(),
    )
    try:
        with _local_server(site_dir) as base_url:
            run = svc.runner.create_run(
                "process.replay",
                params={
                    "system": "local",
                    "report": "monthly_sales",
                    "plan": {
                        "start_url": f"{base_url}/page.html",
                        "actions": [
                            {
                                "seq": 1,
                                "action": "click",
                                "target": {"page": "main", "frame": ""},
                                "locator": {"strategy": "css", "value": "#dl"},
                                "inputs": {},
                                "success": {"type": "download_started"},
                                "retry": {"max_attempts": 1, "safe_to_repeat": False},
                            }
                        ],
                        "expects_download": True,
                        "expected_download_count": 1,
                    },
                    "rules": {
                        "expected_extensions": [".xlsx"],
                        "required_columns": ["customer", "amount"],
                        "min_rows": 2,
                    },
                },
            )
            run = svc.runner.drive(run.id)

        assert run.status is RunStatus.SUCCEEDED, run.error_message
        files = svc.files.list(run_id=run.id)
        assert len(files) == 1

        artifact = files[0]
        assert artifact.validation_status is ValidationStatus.PASSED
        assert artifact.row_count == 2
        assert Path(artifact.path).read_bytes() == workbook.read_bytes()

        report = svc.validator.validate(
            Path(artifact.path),
            ValidationRules(
                expected_extensions=(".xlsx",),
                required_columns=("customer", "amount"),
                min_rows=2,
            ),
        )
        assert report.passed
        assert report.row_count == 2
        assert report.failures == []
    finally:
        svc.close()
