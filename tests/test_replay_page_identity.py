"""Focused regressions for replay page identity and ownership."""
from __future__ import annotations

from pathlib import Path

import pytest

from smartops.adapters.browser.playwright_engine import PlaywrightBrowserAdapter
from smartops.adapters.browser.replay import ReplaySession, StepFailed
from smartops.config import BrowserSettings
from smartops.ports.browser import ReplayRequest

class _IdentityPage:
    def __init__(self, url: str = "https://portal.test/entry") -> None:
        self.url = url
        self.closed = False
        self.handlers: dict[str, object] = {}
        self.gotos: list[str] = []
        self.locator_calls: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def close(self) -> None:
        self.closed = True

    def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)
        self.url = url

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def emit(self, event: str, value) -> None:
        handler = self.handlers.get(event)
        if handler is not None:
            handler(value)


class _IdentityContext:
    def __init__(self, *initial_pages: _IdentityPage) -> None:
        self.pages = list(initial_pages)
        self.handlers: dict[str, object] = {}
        self.next_page: _IdentityPage | None = None

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def new_page(self) -> _IdentityPage:
        page = self.next_page or _IdentityPage()
        self.next_page = None
        self.pages.append(page)
        handler = self.handlers.get("page")
        if handler is not None:
            handler(page)
        return page

    def add_page(self, page: _IdentityPage) -> None:
        self.pages.append(page)
        handler = self.handlers.get("page")
        if handler is not None:
            handler(page)


def test_auth_popup_and_relay_tabs_do_not_consume_replay_page_numbers(tmp_path: Path) -> None:
    opener = _IdentityPage()
    relay = _IdentityPage("https://identity.test/adsso_index.html")
    app = _IdentityPage("https://portal.test/entry?ready=1")
    context = _IdentityContext()
    session = ReplaySession(context, artifact_dir=tmp_path)
    session._track(opener, main=True)
    session._track(relay)
    session.adopt(app)
    report = _IdentityPage("https://portal.test/report")
    context.pages.append(report)
    session._track(report)

    assert session.current_page() is app
    assert session._try_page("main") is app
    assert session._try_page("page-0") is app
    assert session._try_page("page-1") is report
    assert session._try_page("page-2") is None


def test_page_ids_remain_stable_after_a_middle_tab_closes(tmp_path: Path) -> None:
    main, first, second = _IdentityPage(), _IdentityPage(), _IdentityPage()
    context = _IdentityContext()
    session = ReplaySession(context, artifact_dir=tmp_path)
    session._track(main, main=True)
    session._track(first)
    session._track(second)

    assert session._try_page("page-1") is first
    assert session._try_page("page-2") is second
    first.closed = True
    assert session._try_page("page-1") is None
    assert session._try_page("page-2") is second


def test_main_target_fails_closed_when_canonical_page_closes_after_switch(tmp_path: Path) -> None:
    main, report = _IdentityPage(), _IdentityPage("https://portal.test/report")
    session = ReplaySession(_IdentityContext(), artifact_dir=tmp_path)
    session._track(main, main=True)
    session._track(report)
    assert session._scope({"target": {"page": "page-1", "frame": ""}}) is report
    main.closed = True
    with pytest.raises(StepFailed, match="tab 'main' is not open"):
        session._scope({"seq": 2, "target": {"page": "main", "frame": ""}})


def test_drop_stale_pages_does_not_close_a_preexisting_context_tab(tmp_path: Path) -> None:
    preexisting = _IdentityPage("https://portal.test/operator")
    opener = _IdentityPage("https://portal.test/entry")
    stale = _IdentityPage("https://portal.test/relay")
    context = _IdentityContext(preexisting)
    session = ReplaySession(context, artifact_dir=tmp_path)
    # Include the pre-existing tab in the inventory too, as persistent-context
    # startup code may do; the ownership guard must still protect it.
    session._track(preexisting)
    session._track(opener, main=True)
    session._track(stale)
    session.adopt(opener)

    closed = session.drop_stale_pages()

    assert not preexisting.closed
    assert stale.closed
    assert stale.url in closed
    assert stale in session._pages  # history is retained for stable identities


class _IdentityDownload:
    suggested_filename = "report.xlsx"

    def save_as(self, path: str) -> None:
        Path(path).write_bytes(b"fake-download")


class _IdentityLocator:
    def __init__(self, page: "_ReplayIdentityPage") -> None:
        self.page = page
        self.first = self

    def wait_for(self, **_kwargs) -> None:
        return None

    def click(self, **_kwargs) -> None:
        self.page.emit("download", _IdentityDownload())


class _ReplayIdentityPage(_IdentityPage):
    def locator(self, selector: str) -> _IdentityLocator:
        self.locator_calls.append(selector)
        return _IdentityLocator(self)


class _ReplayIdentityContext(_IdentityContext):
    def __init__(self, opener: _ReplayIdentityPage) -> None:
        super().__init__()
        self.next_page = opener


@pytest.mark.parametrize(
    ("canonical_url", "start_url", "must_navigate"),
    [
        ("https://portal.test/entry?state=ready", "https://portal.test/entry#report", False),
        ("https://portal.test/home", "https://portal.test/entry", True),
        ("https://identity.test/login", "https://portal.test/entry", True),
    ],
)
def test_adapter_wrapper_adopts_verified_page_and_captures_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canonical_url: str,
    start_url: str,
    must_navigate: bool,
) -> None:
    import smartops.adapters.browser.playwright_engine as engine_module

    opener = _ReplayIdentityPage(start_url)
    canonical = _ReplayIdentityPage(canonical_url)
    context = _ReplayIdentityContext(opener)
    adapter = PlaywrightBrowserAdapter(BrowserSettings(headless=True))
    callbacks: list[object] = []

    def fake_auth(auth_context, _page, **kwargs):
        callbacks.append(kwargs["on_authenticated_page"])
        auth_context.pages.append(canonical)
        kwargs["on_authenticated_page"](canonical)
        return None

    monkeypatch.setattr(engine_module, "ensure_authenticated", fake_auth)
    plan = {
        "start_url": start_url,
        "actions": [{
            "seq": 1,
            "action": "click",
            "target": {"page": "main", "frame": ""},
            "locator": {"value": "#download"},
            "success": {"type": "download_started"},
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
        }],
        "expects_download": True,
        "expected_download_count": 1,
    }
    request = ReplayRequest(
        system="portal", report="report", destination_dir=tmp_path,
        plan=plan, timeout_seconds=1,
    )

    result = adapter._replay_with_context(context, request, plan, 0.0)

    assert result.ok, result.message
    assert callbacks and callbacks[0] is not None
    assert opener.locator_calls == []
    assert canonical.locator_calls == ["#download"]
    assert result.file_paths == [tmp_path / "report.xlsx"]
    assert result.file_paths[0].read_bytes() == b"fake-download"
    assert canonical.gotos == ([start_url] if must_navigate else [])
