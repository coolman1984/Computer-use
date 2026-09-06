"""Headed Chrome capture worker: watches a whole human task, not just its clicks.

It never records typed secrets, cookies, or response bodies.

What a person does to a web application is mostly not clicking. They type into
fields, choose from lists, press Enter, wait for something to appear, work in a
panel that is really an iframe, and end up with a tab they did not open on
purpose. A click log describes none of that, and an automation built from one
replays an empty form against the wrong page.

Everything here is wired to the **context**, not to a page, so a popup or a
second tab is captured exactly like the first one — corporate portals open SSO
and download confirmations in new windows as a matter of routine.

Two rules shape what gets written down:

* **A password is captured as a reference, never as a value.** The recording
  goes into the database and onto a review screen; a secret in it would be a
  secret in both. The real value is fetched from the credential store during the
  run and exists only for that instant.
* **Every step carries the evidence of its own success**, chosen while the page
  is in front of us: the element that appeared, the value that changed, the tab
  that opened. Deciding that later, from a click log, is guesswork.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from .redaction import redact_text, redact_url, safe_network_summary

# Runs inside every recorded page and every frame. It listens in the CAPTURING
# phase (the `true` third argument) so it sees the event before the page's own
# handlers have reacted to it, and reports through an exposed binding rather
# than a polled global: a poll samples once a tick and silently drops repeats of
# the same action within it, while a binding fires once per real event, in order.
#
# Selectors are attribute selectors — `[id="…"]` via JSON.stringify — rather than
# CSS's `#id` shorthand, which breaks on any id containing a dot or colon
# (routine in Nexacro-style frameworks, e.g. "mainframe.vFrameSet1.form.grid").
# A bare `#mainframe.vFrameSet1…` parses as id "mainframe" plus bogus classes and
# points replay at the wrong element entirely.
_CAPTURE_SCRIPT = """
(() => {
  const q = (v) => JSON.stringify(v);

  // Several ways to find the same element, best first. Replay tries them in
  // order, so a page that drops its ids can still be driven by name or label.
  function locatorFor(el) {
    const out = { strategy: 'css', value: '', fallbacks: [] };
    if (!el || !el.tagName) return out;
    const add = (sel) => { if (sel && !out.fallbacks.includes(sel)) out.fallbacks.push(sel); };
    if (el.id) add('[id=' + q(el.id) + ']');
    const name = el.getAttribute && el.getAttribute('name');
    if (name) add('[name=' + q(name) + ']');
    const testId = el.getAttribute && (el.getAttribute('data-testid') || el.getAttribute('data-test'));
    if (testId) add('[data-testid=' + q(testId) + ']');
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria) add('[aria-label=' + q(aria) + ']');
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.getAttribute('href')) add('a[href=' + q(el.getAttribute('href')) + ']');
    out.value = out.fallbacks[0] || '';
    out.fallbacks = out.fallbacks.slice(1);
    return out;
  }

  // A field is sensitive if the browser treats it as one. Type and autocomplete
  // are what the page itself declares, so this does not depend on guessing at
  // names in any particular language.
  function isSecret(el) {
    if (!el) return false;
    if (el.type === 'password') return true;
    const auto = (el.getAttribute && el.getAttribute('autocomplete')) || '';
    return /current-password|new-password|one-time-code/i.test(auto);
  }

  const report = (payload) => {
    try { window.__smartopsReport(payload); } catch (_) { /* recording ended */ }
  };

  // What has been typed into a field but not yet committed. "change" is the
  // right event to record — it fires once, with the finished value, instead of
  // once per keystroke — but on a text input it only fires on blur, and a person
  // who types and then presses Enter never blurs it. So the latest value is held
  // here and flushed by whichever comes first: the change event, losing focus, or
  // any other action being recorded. Flushing before another action is what keeps
  // the steps in the order they really happened.
  let pending = null;
  let pendingEl = null;
  // What has already been written down for each field. A text input reports its
  // value twice — once when the person moves on and once when the browser fires
  // change on blur — and without this the same typing lands in the recording
  // twice, so replay types it, types it again, and the step numbering no longer
  // matches what the person did.
  const committed = new WeakMap();

  function flushPending() {
    if (!pending) return;
    const p = pending, el = pendingEl;
    pending = null;
    pendingEl = null;
    if (el && committed.get(el) === p.value && !p.secret) return;
    if (el) committed.set(el, p.value);
    report(p);
  }

  // Lets the recorder commit anything still being typed when the person stops
  // the recording. Without it, a value typed into the last field and never
  // followed by another action would simply not be in the recording.
  window.__smartopsFlush = flushPending;

  function rememberFill(el) {
    // Something else was being typed and has not been written down yet: commit
    // it first, so the steps stay in the order they really happened.
    if (pendingEl && pendingEl !== el) flushPending();
    const secret = isSecret(el);
    pendingEl = el;
    pending = {
      action: 'fill',
      locator: locatorFor(el),
      // Only a non-secret value travels. For a secret the platform records that
      // something must be typed here and where to get it at run time.
      value: secret ? '' : (el.value || ''),
      secret: secret,
      ...describe(el),
    };
  }

  const describe = (el) => ({
    tag: el && el.tagName ? el.tagName.toLowerCase() : '',
    text: (el && (el.innerText || el.value || '') || '').slice(0, 80),
  });

  document.addEventListener('input', (e) => {
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea') return;
    if (el.type === 'checkbox' || el.type === 'radio') return;
    rememberFill(el);
  }, true);

  document.addEventListener('blur', () => flushPending(), true);

  document.addEventListener('click', (e) => {
    flushPending();
    const el = e.target;
    const w = innerWidth || 1, h = innerHeight || 1;
    report({
      action: 'click',
      locator: locatorFor(el),
      x: e.clientX / w, y: e.clientY / h,
      ...describe(el),
    });
  }, true);

  // "change" rather than "input": it fires once, when the person has finished,
  // instead of once per keystroke. A per-keystroke log would record a hundred
  // steps for one typed reference and leak the value character by character.
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      // Whatever was being typed happened first and belongs on the record
      // first; discarding it here lost the value entirely.
      flushPending();
      report({
        action: 'select',
        locator: locatorFor(el),
        value: el.value,
        text: (el.selectedOptions && el.selectedOptions[0] && el.selectedOptions[0].text) || '',
      });
      return;
    }
    if (tag === 'input' || tag === 'textarea') {
      if (el.type === 'checkbox' || el.type === 'radio') {
        flushPending();
        report({ action: 'check', locator: locatorFor(el), checked: !!el.checked, ...describe(el) });
        return;
      }
      rememberFill(el);
      flushPending();
    }
  }, true);

  // Only keys that mean something on their own. Recording every keystroke would
  // both bury the real steps and capture whatever was being typed.
  const MEANINGFUL = new Set(['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'PageDown', 'PageUp']);
  document.addEventListener('keydown', (e) => {
    const combo = e.ctrlKey || e.altKey || e.metaKey;
    if (!combo && !MEANINGFUL.has(e.key)) return;
    // Whatever was typed goes on the record before the key that acts on it.
    flushPending();
    const parts = [];
    if (e.ctrlKey) parts.push('Control');
    if (e.altKey) parts.push('Alt');
    if (e.metaKey) parts.push('Meta');
    if (e.shiftKey) parts.push('Shift');
    parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
    report({ action: 'press', locator: locatorFor(e.target), key: parts.join('+'), ...describe(e.target) });
  }, true);
})();
"""


class PlaywrightRecordingWorker:
    """Runs the recording browser and turns what happens in it into steps."""

    def __init__(
        self,
        recording_id: str,
        artifact_dir: Path,
        start_url: str,
        on_step: Callable[[dict], None],
        on_heartbeat: Callable[[], None],
        on_finished: Callable[[str | None], None],
        executable_path: str = "",
        session_state_path: Path | None = None,
        headless: bool = False,
        on_ready: Callable[[Any], None] | None = None,
    ) -> None:
        self.recording_id, self.artifact_dir, self.start_url = recording_id, artifact_dir, start_url
        # The one saved session for this system — the same file the connection
        # test, the test run and every scheduled run use. Recording in its own
        # private profile meant signing in twice and possibly recording as a
        # different account than the automation would later run as. Read in and
        # written back out, so a sign-in done here serves later runs too.
        self.session_state_path = session_state_path
        # Empty means "use the installed Google Chrome"; a configured path lets a
        # machine without Chrome record with whatever Chromium it does have.
        self.executable_path = executable_path
        # Headed for a real recording; see BrowserSettings.record_headless for
        # why running without a screen is possible at all.
        self.headless = headless
        # Called on this worker's own thread once the first page has loaded.
        # Playwright's sync objects belong to the thread that created them, so
        # this is the only place anything can drive the recorded page. Unused in
        # normal operation, where the hands belong to a person.
        self.on_ready = on_ready
        self.on_step, self.on_heartbeat, self.on_finished = on_step, on_heartbeat, on_finished
        self._stop, self._paused = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._shot_seq = 0  # screenshot counter, unique within one recording
        # The first page the recorder opens. Exposed so a test can drive the same
        # page a person would be clicking in, rather than a second browser.
        self.primary_page: Any = None
        self._pages: list[Any] = []
        self._download_count = 0
        # Events arrive from inside Playwright's own dispatch — a page binding
        # firing during a click, a download starting during that same click. Any
        # Playwright call made from there re-enters the sync API on a call that
        # has not returned yet, and the whole recorder deadlocks. So handlers do
        # the cheapest possible thing (read already-cached values, queue the
        # event) and every protocol call — screenshots, titles, saving a
        # download — happens on the worker's own loop below.
        self._events: queue.Queue = queue.Queue()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"recording-{self.recording_id}", daemon=True
        )
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- naming a page and a frame ----------

    def _page_name(self, page: Any) -> str:
        """A stable name for a tab, so replay can return to the same one.

        "main" is the tab the task started in. Everything else is numbered in the
        order it opened, which is how replay can follow a popup and come back.
        """
        if page is self.primary_page:
            return "main"
        try:
            index = self._pages.index(page)
        except ValueError:
            return "latest"
        return f"page-{index}"

    @staticmethod
    def _frame_selector(frame: Any, page: Any) -> str:
        """Which iframe a step happened in, "" for the page's own document.

        Recorded as the frame's URL rather than its element: the element selector
        belongs to the parent document and is not always available from inside
        the frame, whereas the URL identifies it from either side.
        """
        try:
            if frame is None or frame == page.main_frame:
                return ""
            return redact_url(frame.url)
        except Exception:
            return ""

    # ---------- the browser thread ----------

    def _run(self) -> None:
        network: list[dict] = []
        try:
            from playwright.sync_api import sync_playwright

            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            for item in ("screenshots", "downloads", "network", "trace", "session"):
                (self.artifact_dir / item).mkdir(exist_ok=True)

            with sync_playwright() as p:
                launch_kwargs: dict[str, Any] = {"headless": self.headless}
                if self.executable_path:
                    launch_kwargs["executable_path"] = self.executable_path
                else:
                    launch_kwargs["channel"] = "chrome"
                browser = p.chromium.launch(**launch_kwargs)
                context_kwargs: dict[str, Any] = {"accept_downloads": True}
                if self.session_state_path and Path(self.session_state_path).exists():
                    context_kwargs["storage_state"] = str(self.session_state_path)
                context = browser.new_context(**context_kwargs)
                try:
                    self._capture(context, network)
                finally:
                    self._preserve(context)
                    context.close()
                    browser.close()
            (self.artifact_dir / "network" / "sanitized-summary.json").write_text(
                json.dumps(network, ensure_ascii=False), encoding="utf-8"
            )
            self.on_finished(None)
        except Exception as exc:
            self.on_finished(f"Recorder failed: {type(exc).__name__}: {exc}")

    def _capture(self, context: Any, network: list[dict]) -> None:
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        # Context-level: applies to every page and every frame this context ever
        # opens, including popups created after this point.
        context.add_init_script(_CAPTURE_SCRIPT)
        context.on(
            "request",
            lambda request: network.append(
                safe_network_summary(request.method, request.url, request.resource_type)
            ),
        )
        context.expose_binding("__smartopsReport", self._handle_event)
        context.on("page", self._track_page)

        first = context.pages[0] if context.pages else context.new_page()
        self.primary_page = first
        self._track_page(first)
        first.goto(self.start_url, wait_until="domcontentloaded", timeout=30000)
        self._last_shot = self._shoot(first)

        if self.on_ready is not None:
            self.on_ready(first)

        while not self._stop.is_set():
            self._drain(limit=50)
            self.on_heartbeat()
            self._stop.wait(0.2)
        # A value typed into the last field and never followed by another action
        # is still part of the task; ask every page to commit what it is holding.
        self._flush_pages()
        # Whatever arrived while we were stopping still belongs in the recording.
        self._drain(limit=500)

    def _flush_pages(self) -> None:
        for page in list(self._pages):
            try:
                page.evaluate("() => window.__smartopsFlush && window.__smartopsFlush()")
                for frame in page.frames:
                    try:
                        frame.evaluate("() => window.__smartopsFlush && window.__smartopsFlush()")
                    except Exception:
                        pass
            except Exception:
                pass  # a page that has already closed has nothing left to commit

    def _drain(self, *, limit: int) -> None:
        """Process queued events on this thread, where Playwright calls are safe."""
        for _ in range(limit):
            try:
                kind, item = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "step":
                    self._finish_step(item)
                elif kind == "emit":
                    self._emit(item)
                else:
                    self._finish_download(item)
            except Exception:
                pass  # one lost step must not end the recording

    def _preserve(self, context: Any) -> None:
        """Save what we captured even if the session ended badly."""
        try:
            context.storage_state(path=str(self.artifact_dir / "session" / "storage-state.json"))
        except Exception:
            pass
        # Write back to the shared session too, so a sign-in performed inside the
        # recording window is the same session later runs will use.
        if self.session_state_path:
            try:
                Path(self.session_state_path).parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(self.session_state_path))
            except Exception:
                pass
        try:
            context.tracing.stop(path=str(self.artifact_dir / "trace" / "trace.zip"))
        except Exception:
            pass

    # ---------- page and download tracking ----------

    def _track_page(self, page: Any) -> None:
        if page in self._pages:
            return  # new_page() also fires the "page" event; do not double-bind
        self._pages.append(page)
        self._bind_downloads(page)
        page.on("close", lambda: self._pages.remove(page) if page in self._pages else None)
        if page is not self.primary_page and self.primary_page is not None:
            # A tab the task opened is itself a step: replay has to know to follow
            # it, and later steps refer to it by the name assigned here.
            #
            # Queued rather than emitted directly, because everything else is
            # queued. Emitting here put the "a tab opened" step *before* the click
            # that opened it — and replay then tried to switch to a tab that
            # nothing had opened yet.
            self._events.put(("emit", {
                "kind": "switch_page",
                "action": "switch_page",
                "target": {"page": self._page_name(page), "frame": ""},
                "inputs": {},
                "locator": {},
                "success": {"type": "page_available"},
                "retry": {"max_attempts": 3, "safe_to_repeat": True},
                "page_url_redacted": redact_url(_safe_url(page)),
                "target_text_redacted": "a new tab opened",
            }))

    def _bind_downloads(self, page: Any) -> None:
        def on_download(download: Any) -> None:
            # A download usually starts *during* the click that caused it, so
            # saving it here would re-enter Playwright mid-call. Queue it.
            if self._paused.is_set():
                return
            self._events.put(("download", (download, page, _safe_url(page))))

        page.on("download", on_download)

    def _finish_download(self, item: tuple) -> None:
        download, page, page_url = item
        name = download.suggested_filename or f"download-{self._download_count + 1}"
        directory = self.artifact_dir / "downloads"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        suffix = 2
        while target.exists():
            target = directory / f"{Path(name).stem}-{suffix}{Path(name).suffix}"
            suffix += 1
        download.save_as(str(target))
        if not (target.exists() and target.stat().st_size > 0):
            return
        self._download_count += 1
        self._emit({
            "kind": "download",
            "action": "download",
            "target": {"page": self._page_name(page), "frame": ""},
            "locator": {},
            "inputs": {"file_name": target.name},
            "download_ref": f"downloads/{target.name}",
            # A file arriving is its own proof; nothing else to check.
            "success": {"type": "download_started"},
            # Never repeat a download on its own: the click that caused it is the
            # step that retries, and only when repeating it is safe.
            "retry": {"max_attempts": 1, "safe_to_repeat": False},
            "page_url_redacted": redact_url(page_url),
            "target_text_redacted": redact_text(name),
        })

    # ---------- turning a browser event into a step ----------

    def _handle_event(self, source: dict, payload: dict) -> None:
        """Runs inside Playwright's dispatch: read cached values only, then queue.

        `page.url` and `frame.url` are cached attributes, so they are safe here
        and are read now rather than later — by the time the queue is drained the
        page may already have navigated somewhere else.
        """
        if self._paused.is_set():
            return  # dropped, not queued — matches the existing pause behaviour
        try:
            page = source["page"]
            frame = source.get("frame")
            self._events.put(("step", (payload, page, _safe_url(page), self._frame_selector(frame, page))))
        except Exception:
            pass

    def _finish_step(self, item: tuple) -> None:
        payload, page, page_url, frame_selector = item
        try:
            action = payload.get("action") or "click"
            locator = payload.get("locator") or {}
            before = getattr(self, "_last_shot", "")
            after = self._shoot(page)
            if after:
                self._last_shot = after

            step: dict[str, Any] = {
                "kind": action,
                "action": action,
                "target": {"page": self._page_name(page), "frame": frame_selector},
                "locator": {
                    "strategy": "css",
                    "value": redact_text(locator.get("value", "")),
                    "fallbacks": [redact_text(f) for f in (locator.get("fallbacks") or [])],
                },
                "inputs": {},
                "page_url_redacted": redact_url(page_url),
                "page_title": redact_text(_safe_title(page)),
                "selector": redact_text(locator.get("value", "")),
                "target_text_redacted": redact_text(payload.get("text", "")),
                "x_ratio": payload.get("x"),
                "y_ratio": payload.get("y"),
                "before_image": before,
                "after_image": after,
            }
            self._fill_contract(step, payload)
            self._emit(step)
        except Exception:
            pass  # a step we failed to record must not take down the recording

    def _fill_contract(self, step: dict[str, Any], payload: dict) -> None:
        """Inputs, success evidence and retry policy, decided per action type."""
        action = step["action"]

        if action == "fill":
            if payload.get("secret"):
                # The reference names the system whose credential fills this
                # field at run time. The value itself is never written down.
                step["inputs"] = {
                    "secret_ref": "",  # resolved to the system key when the plan is built
                    "secret_field": "password",
                }
                step["success"] = {"type": "value_not_empty"}
            else:
                value = payload.get("value", "")
                step["inputs"] = {"value": value}
                step["success"] = {"type": "value_equals", "value": value}
            # Typing the same value again lands on the same state.
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "select":
            value = payload.get("value", "")
            step["inputs"] = {"value": value}
            step["success"] = {"type": "value_equals", "value": value}
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "check":
            checked = bool(payload.get("checked"))
            step["inputs"] = {"checked": checked}
            step["success"] = {"type": "checked_is", "value": checked}
            step["retry"] = {"max_attempts": 3, "safe_to_repeat": True}

        elif action == "press":
            step["inputs"] = {"key": payload.get("key", "")}
            # What a key press does is entirely page-specific, so its evidence is
            # filled in during review rather than guessed at here.
            step["success"] = {"type": "none"}
            # Enter usually submits. Repeating a submit can double-file a request.
            step["retry"] = {"max_attempts": 1, "safe_to_repeat": False}

        else:  # click
            step["inputs"] = {}
            step["success"] = {"type": "none"}
            step["retry"] = {"max_attempts": 1, "safe_to_repeat": False}

    def _emit(self, step: dict[str, Any]) -> None:
        step.setdefault("target", {"page": "main", "frame": ""})
        step.setdefault("locator", {})
        step.setdefault("inputs", {})
        step.setdefault("success", {"type": "none"})
        step.setdefault("retry", {"max_attempts": 1, "safe_to_repeat": False})
        self.on_step(step)

    def _shoot(self, page: Any) -> str:
        """Best-effort screenshot; "" (never None) on failure, so callers treat it uniformly."""
        self._shot_seq += 1
        rel = f"screenshots/{self._shot_seq:06d}.png"
        try:
            page.screenshot(path=str(self.artifact_dir / rel), timeout=5000)
        except Exception:
            return ""
        return rel


def _safe_url(page: Any) -> str:
    try:
        return page.url
    except Exception:
        return ""


def _safe_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:
        return ""
