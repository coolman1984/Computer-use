"""Headed Chrome capture worker. It never logs typed input, cookies, or response bodies.

Corporate portals (SSO in particular) routinely open the login form in a popup
window rather than the tab we launched — Playwright models that as a second
`Page` on the same `BrowserContext`. Everything here that used to be wired to
"the page" (init script, binding, download handler) is wired to the context
instead, so a popup is captured exactly like the main tab.
"""
from __future__ import annotations
import json, threading
from pathlib import Path
from typing import Callable
from .redaction import redact_text, redact_url, safe_network_summary

# Runs inside the recorded page, in the CAPTURING phase (the `true` third
# arg), so it fires before the target's own click handler — i.e. before
# anything on screen has reacted to the click yet. It reports through an
# exposed binding rather than a polled `window` global: a 1-second poll can
# only sample once a second, so it silently drops a click repeated on the
# same element within that window and can miss others between ticks
# entirely. The binding call is push-based and fires once per real click, in
# order, with no dedup needed.
#
# The selector is an attribute selector — `[id="..."]` via JSON.stringify —
# instead of `#id`, because CSS's `#id` shorthand breaks on any id containing
# a dot, colon, or other CSS-special character (routine in Nexacro-style
# frameworks, e.g. "mainframe.vFrameSet1.form.grid"). A bare
# `#mainframe.vFrameSet1...` parses as id "mainframe" plus bogus classes and
# points replay at the wrong element.
_CLICK_CAPTURE_SCRIPT = """
document.addEventListener('click', e => {
    const w = innerWidth || 1, h = innerHeight || 1;
    let selector = '';
    if (e.target.id) selector = '[id=' + JSON.stringify(e.target.id) + ']';
    else if (e.target.getAttribute('name')) selector = '[name=' + JSON.stringify(e.target.getAttribute('name')) + ']';
    window.__smartopsReportClick({
        x: (e.clientX / w), y: (e.clientY / h), tag: e.target.tagName,
        text: (e.target.innerText || '').slice(0, 80), selector,
    });
}, true);
"""

class PlaywrightRecordingWorker:
    def __init__(self, recording_id: str, artifact_dir: Path, start_url: str, on_step: Callable[[dict], None], on_heartbeat: Callable[[], None], on_finished: Callable[[str | None], None], executable_path: str = "") -> None:
        self.recording_id, self.artifact_dir, self.start_url = recording_id, artifact_dir, start_url
        # Empty means "use the installed Google Chrome". A configured path lets
        # a machine without Chrome record with whatever Chromium it does have,
        # instead of failing with a raw Playwright message.
        self.executable_path = executable_path
        self.on_step, self.on_heartbeat, self.on_finished = on_step, on_heartbeat, on_finished
        self._stop, self._paused = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._shot_seq = 0  # screenshot filename counter, unique within one recording
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"recording-{self.recording_id}", daemon=True); self._thread.start()
    def pause(self) -> None: self._paused.set()
    def resume(self) -> None: self._paused.clear()
    def stop(self) -> None: self._stop.set()
    def alive(self) -> bool: return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        network: list[dict] = []
        try:
            from playwright.sync_api import sync_playwright
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            for item in ("screenshots", "downloads", "network", "trace", "session", "profile"): (self.artifact_dir / item).mkdir(exist_ok=True)
            with sync_playwright() as p:
                launch_kwargs = {"headless": False, "accept_downloads": True}
                if self.executable_path:
                    launch_kwargs["executable_path"] = self.executable_path
                else:
                    launch_kwargs["channel"] = "chrome"
                context = p.chromium.launch_persistent_context(str(self.artifact_dir / "profile"), **launch_kwargs)
                try:
                    context.tracing.start(screenshots=True, snapshots=True, sources=False)
                    # Context-level, not page-level: applies to every page this
                    # context ever opens, including SSO/download popups.
                    context.add_init_script(_CLICK_CAPTURE_SCRIPT)
                    context.on("request", lambda request: network.append(safe_network_summary(request.method, request.url, request.resource_type)))

                    pages: list[object] = []  # every open page/popup, tracked so its downloads are captured
                    last_shot: dict[int, str] = {}  # id(page) -> most recent screenshot on that page, chained forward as the NEXT click's "before" image on that same page

                    def bind_download(page) -> None:
                        def on_download(download):
                            target = self.artifact_dir / "downloads" / (download.suggested_filename or "download")
                            download.save_as(str(target))
                            if target.exists() and target.stat().st_size > 0:
                                self.on_step({"kind": "download", "download_ref": f"downloads/{target.name}", "page_url_redacted": redact_url(page.url), "page_title": redact_text(page.title())})
                        page.on("download", on_download)

                    def track_page(page) -> None:
                        if page in pages: return  # context.new_page() below can also fire the "page" event — don't double-bind the same page
                        bind_download(page)
                        pages.append(page)
                        page.on("close", lambda: pages.remove(page) if page in pages else None)

                    def shoot(page) -> str:
                        """Best-effort screenshot; "" (never None) on failure so callers can treat it as 'no image' uniformly."""
                        self._shot_seq += 1
                        rel = f"screenshots/{self._shot_seq:06d}.png"
                        try:
                            page.screenshot(path=str(self.artifact_dir / rel), timeout=5000)
                        except Exception:
                            return ""
                        return rel

                    def handle_click(source, payload) -> None:
                        if self._paused.is_set(): return  # dropped, not queued — matches the old "skip while paused" behaviour
                        try:
                            page = source["page"]
                            before = last_shot.get(id(page), "")
                            after = shoot(page)
                            if after: last_shot[id(page)] = after
                            self.on_step({"kind": "click", "page_url_redacted": redact_url(page.url), "page_title": redact_text(page.title()), "selector": redact_text(payload.get("selector", "")), "target_text_redacted": redact_text(payload.get("text", "")), "x_ratio": payload.get("x"), "y_ratio": payload.get("y"), "before_image": before, "after_image": after})
                        except Exception:
                            pass  # a step we failed to record must not take down the whole recording

                    context.expose_binding("__smartopsReportClick", handle_click)
                    context.on("page", track_page)  # catches popups opened after this point (SSO login, download confirm, ...)

                    first_page = context.pages[0] if context.pages else context.new_page()
                    track_page(first_page)
                    first_page.goto(self.start_url, wait_until="domcontentloaded", timeout=30000)
                    last_shot[id(first_page)] = shoot(first_page)  # seed a "before" image so the first click isn't blank

                    while not self._stop.wait(1):
                        self.on_heartbeat()
                finally:
                    # Covers the whole session, including a goto() failure on
                    # the very first navigation: always try to preserve what
                    # we captured so far and close the context cleanly. A
                    # transient error must not throw away the whole recording.
                    try: context.storage_state(path=str(self.artifact_dir / "session" / "storage-state.json"))
                    except Exception: pass
                    try: context.tracing.stop(path=str(self.artifact_dir / "trace" / "trace.zip"))
                    except Exception: pass
                    context.close()
            (self.artifact_dir / "network" / "sanitized-summary.json").write_text(json.dumps(network, ensure_ascii=False), encoding="utf-8")
            self.on_finished(None)
        except Exception as exc:
            self.on_finished(f"Recorder failed: {type(exc).__name__}")
