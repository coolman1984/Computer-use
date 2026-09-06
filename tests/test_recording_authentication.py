from __future__ import annotations

import smartops.recordings.worker as worker_module
from smartops.recordings.worker import PlaywrightRecordingWorker


def test_recorder_finishes_login_before_any_capture_is_installed(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    class Cdp:
        def send(self, method, params=None):
            events.append(f"cdp:{method}")

    class Page:
        url = "https://portal.example/login"

        def __init__(self):
            self.main_frame = self
            self.frames = [self]

        def goto(self, *args, **kwargs):
            events.append("goto")

        def evaluate(self, script):
            events.append("capture-script")

        def screenshot(self, **kwargs):
            events.append("screenshot")

        def on(self, event, handler):
            events.append(f"page-on:{event}")

    page = Page()

    class Tracing:
        def start(self, **kwargs):
            events.append("trace-start")

    class Context:
        pages = []
        tracing = Tracing()

        def new_page(self):
            return page

        def new_cdp_session(self, target):
            return Cdp()

        def add_init_script(self, script):
            events.append("init-script")

        def on(self, event, handler):
            events.append(f"context-on:{event}")

        def expose_binding(self, name, handler):
            events.append("binding")

    store = object()

    def fake_login(context, target, **kwargs):
        events.append("login")
        assert kwargs["manage_tracing"] is False
        assert kwargs["credential_store"] is store
        return None

    monkeypatch.setattr(worker_module, "ensure_authenticated", fake_login)
    worker = PlaywrightRecordingWorker(
        "rec",
        tmp_path,
        "https://portal.example/login",
        lambda _: None,
        lambda: None,
        lambda _: None,
        system_key="portal",
        auth_filters={"credential_ref": "portal", "login_selector": "#login"},
        credential_store=store,
    )
    worker.stop()  # make the capture loop finish immediately

    worker._capture(Context(), [])

    assert events.index("goto") < events.index("login")
    assert events.index("login") < events.index("trace-start")
    assert events.index("login") < events.index("init-script")
    assert events.index("login") < events.index("binding")
    assert events.index("login") < events.index("screenshot")
