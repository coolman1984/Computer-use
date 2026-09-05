"""اختبارات F-04: مسار الجلسة، عمرها، والتقاط تسجيل الدخول اليدوي."""

from __future__ import annotations

import http.server
import json
import os
import threading
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest

from smartops.config import BrowserSettings
from smartops.core.clock import FrozenClock
from smartops.core.errors import ConfigurationError
from smartops.sessions import capture_login, session_age_hours, session_exists, session_path


def test_session_path_slugs_system_key(tmp_path: Path) -> None:
    path = session_path(tmp_path, "ERP Demo!!")
    assert path.parent == tmp_path
    assert path.name.endswith(".json")
    assert " " not in path.name


def test_session_exists_and_age(tmp_path: Path) -> None:
    assert session_exists(tmp_path, "erp") is False
    assert session_age_hours(tmp_path, "erp") is None

    path = session_path(tmp_path, "erp")
    path.write_text("{}", encoding="utf-8")
    assert session_exists(tmp_path, "erp") is True

    clock = FrozenClock()
    age = session_age_hours(tmp_path, "erp", now=clock)
    assert age is not None and age >= 0.0


def test_capture_login_requires_login_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        capture_login(
            "erp",
            "",
            sessions_dir=tmp_path,
            browser_settings=BrowserSettings(),
        )


# ---------- اختبار حقيقي بمتصفح مرئي على صفحة محلية ----------

pytestmark_playwright = pytest.importorskip("playwright.sync_api")


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


LOGIN_PAGE_HTML = """<!doctype html><html><body>
<div id="dashboard">Logged in</div>
</body></html>"""


@pytest.mark.skipif(
    os.environ.get("SMARTOPS_SKIP_HEADED_TESTS") == "1",
    reason="بيئة بدون واجهة عرض (headless-only CI) لا تدعم متصفحًا مرئيًا",
)
def test_capture_login_writes_valid_storage_state(tmp_path: Path) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "login.html").write_text(LOGIN_PAGE_HTML, encoding="utf-8")

    with _local_server(site_dir) as base_url:
        path = capture_login(
            "erp_test",
            f"{base_url}/login.html",
            sessions_dir=tmp_path / "sessions",
            browser_settings=BrowserSettings(viewport_width=800, viewport_height=600),
            logged_in_selector="#dashboard",
            executable_path=_resolve_executable_path(),
            timeout_seconds=15,
        )

    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "cookies" in data
