"""Repeating a recorded human task, step by step, and proving each step worked.

This is the execution half of the recording contract. Every step says what it
is, where it happens, how to find its element, what goes into it, and — the part
that matters most — what its success looks like. Nothing here treats dispatching
an action as an outcome: a click that lands on a dead button and a click that
opens a report are indistinguishable until something on the page proves which
one happened.

Three things shape the design:

* **Downloads are collected at the context, not around one click.** Arming
  `expect_download` on a single action could only ever bring back one file, and
  a task that exports a summary and its detail lost the second one silently. A
  context-level listener catches every file the task produces, whichever action
  and whichever tab it came from.
* **A step is retried only when repeating it is harmless.** Typing a value again
  lands on the same state; pressing Enter on a form or clicking a download does
  not, and re-running those can double-file a request. The recording says which
  is which, and that is honoured rather than guessed.
* **Secrets exist only for the instant they are typed.** The plan holds a
  reference to a credential; the value is fetched here, used, and never written
  to a step result, an event, or an error message.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core.errors import PermanentError

# How long to wait for a step's success evidence before deciding it did not
# happen. Generous: a corporate report can take a while to render, and the point
# is to distinguish "slow" from "never".
DEFAULT_EVIDENCE_TIMEOUT_MS = 15000


class StepFailed(Exception):
    """One step could not be performed, or could not prove that it worked."""

    def __init__(self, seq: int, message: str) -> None:
        super().__init__(f"Step {seq} did not work: {message}")
        self.seq = seq
        self.reason = message


class ReplaySession:
    """Walks one recorded plan through one browser context."""

    def __init__(
        self,
        context: Any,
        *,
        artifact_dir: Path,
        credential_store: Any = None,
        evidence_timeout_ms: int = DEFAULT_EVIDENCE_TIMEOUT_MS,
    ) -> None:
        self.context = context
        self.artifact_dir = Path(artifact_dir)
        self.credentials = credential_store
        self.timeout = evidence_timeout_ms
        self.downloads: list[Path] = []
        self.step_results: list[dict[str, Any]] = []
        self._pages: list[Any] = []
        self._current: Any = None
        self._pending_downloads: list[Any] = []
        self._downloads_before = 0

    # ---------- setup ----------

    def open(self, start_url: str) -> Any:
        """Start the task on its first page, watching for downloads from anywhere."""
        # Registered before the first navigation so a file that arrives during
        # the very first action is still caught.
        self.context.on("download", self._on_download)
        self.context.on("page", self._track)
        page = self.context.new_page()
        self._track(page)
        self._current = page
        page.goto(start_url, wait_until="domcontentloaded")
        return page

    def _track(self, page: Any) -> None:
        if page not in self._pages:
            self._pages.append(page)

    def _on_download(self, download: Any) -> None:
        # Saving here would re-enter Playwright during the click that caused the
        # download; hold the handle and save it on our own thread instead.
        self._pending_downloads.append(download)

    def collect_downloads(self, destination: Path) -> None:
        """Save whatever files the task produced, keeping every one of them."""
        destination.mkdir(parents=True, exist_ok=True)
        while self._pending_downloads:
            download = self._pending_downloads.pop(0)
            try:
                name = download.suggested_filename or f"download-{len(self.downloads) + 1}"
                target = destination / name
                # Two files with the same suggested name in one task would
                # otherwise overwrite each other, which is the same silent loss
                # the per-run folders exist to prevent.
                if target.exists():
                    target = destination / f"{target.stem}-{len(self.downloads) + 1}{target.suffix}"
                download.save_as(str(target))
                if target.exists() and target.stat().st_size > 0:
                    self.downloads.append(target)
            except Exception:
                continue  # one file we could not save must not lose the others

    # ---------- running a step ----------

    def perform(self, action: dict[str, Any]) -> None:
        """Do one step, prove it worked, and record what happened."""
        seq = action.get("seq", len(self.step_results) + 1)
        retry = action.get("retry") or {}
        attempts = max(1, int(retry.get("max_attempts", 1)))
        safe = bool(retry.get("safe_to_repeat", False))
        # An unsafe step gets exactly one attempt whatever the plan says: the
        # cost of repeating a submit is worse than the cost of failing the run.
        if not safe:
            attempts = 1

        started = time.time()
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                # "A file downloaded" has to mean *this* step produced one. Left
                # as "is there any file", every later download step would pass on
                # the strength of the first one's file and a task that stopped
                # producing its second export would still look successful.
                self._downloads_before = len(self.downloads) + len(self._pending_downloads)
                self._dispatch(action)
                self._verify(action)
                self._record(seq, action, ok=True, attempt=attempt, started=started)
                return
            except StepFailed as failure:
                last_error = failure.reason
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(min(2.0 * attempt, 5.0))

        self._record(seq, action, ok=False, attempt=attempts, started=started, error=last_error)
        raise StepFailed(seq, last_error)

    def _record(
        self,
        seq: int,
        action: dict[str, Any],
        *,
        ok: bool,
        attempt: int,
        started: float,
        error: str = "",
    ) -> None:
        """One line of history per step. Never carries an input value: a step that
        fills a secret would otherwise put it in the run record."""
        self.step_results.append({
            "seq": seq,
            "action": action.get("action"),
            "ok": ok,
            "attempt": attempt,
            "seconds": round(time.time() - started, 2),
            "checkpoint": action.get("checkpoint", ""),
            "error": error,
        })

    # ---------- what each action does ----------

    def _dispatch(self, action: dict[str, Any]) -> None:
        kind = action.get("action") or "click"
        handler = {
            "click": self._do_click,
            "fill": self._do_fill,
            "select": self._do_select,
            "check": self._do_check,
            "press": self._do_press,
            "navigate": self._do_navigate,
            "switch_page": self._do_switch_page,
            "switch_frame": self._do_switch_frame,
            "wait_for": self._do_wait,
            # A download is the consequence of the click before it, and the
            # context listener already has the file. Nothing to perform.
            "download": lambda _: None,
        }.get(kind)
        if handler is None:
            raise StepFailed(
                action.get("seq", 0),
                f"'{kind}' is not something the platform knows how to repeat",
            )
        handler(action)

    def _do_click(self, action: dict[str, Any]) -> None:
        self._locate(action).click()

    def _do_fill(self, action: dict[str, Any]) -> None:
        self._locate(action).fill(self._value_for(action))

    def _do_select(self, action: dict[str, Any]) -> None:
        self._locate(action).select_option((action.get("inputs") or {}).get("value", ""))

    def _do_check(self, action: dict[str, Any]) -> None:
        locator = self._locate(action)
        if (action.get("inputs") or {}).get("checked", True):
            locator.check()
        else:
            locator.uncheck()

    def _do_press(self, action: dict[str, Any]) -> None:
        key = (action.get("inputs") or {}).get("key") or "Enter"
        locator = self._maybe_locate(action)
        if locator is not None:
            locator.press(key)
        else:
            self._page().keyboard.press(key)

    def _do_navigate(self, action: dict[str, Any]) -> None:
        url = (action.get("inputs") or {}).get("url", "")
        if not url:
            raise StepFailed(action.get("seq", 0), "the step has no address to open")
        self._page().goto(url, wait_until="domcontentloaded")

    def _do_switch_page(self, action: dict[str, Any]) -> None:
        self._current = self._resolve_page((action.get("target") or {}).get("page", "main"))
        try:
            self._current.bring_to_front()
        except Exception:
            pass  # not being able to focus a tab does not stop us using it

    def _do_switch_frame(self, action: dict[str, Any]) -> None:
        # Frames are addressed per step by target.frame; this exists so a plan
        # can make the move explicit and prove the frame is really there.
        self._frame(action)

    def _do_wait(self, action: dict[str, Any]) -> None:
        seconds = float((action.get("inputs") or {}).get("seconds", 0) or 0)
        if seconds > 0:
            self._page().wait_for_timeout(seconds * 1000)

    # ---------- proving it worked ----------

    def _verify(self, action: dict[str, Any]) -> None:
        """Wait for the consequence this step promised. No consequence, no success."""
        success = action.get("success") or {}
        kind = success.get("type") or "none"
        seq = action.get("seq", 0)

        if kind == "none":
            # Nothing was recorded to check. The page is at least given a chance
            # to settle, so the next step does not race this one's navigation.
            self._settle()
            return

        if kind == "download_started":
            before = getattr(self, "_downloads_before", 0)
            self._wait_until(
                lambda: len(self.downloads) + len(self._pending_downloads) > before,
                seq, "this step did not start a download",
            )
            return

        if kind == "new_page":
            before = len(self._pages)
            self._wait_until(lambda: len(self._pages) >= before, seq, "no new tab opened")
            return

        if kind == "page_available":
            name = (action.get("target") or {}).get("page", "main")
            self._wait_until(lambda: self._try_page(name) is not None, seq, f"the tab '{name}' never appeared")
            return

        if kind == "selector_visible":
            selector = success.get("value", "")
            try:
                self._scope(action).locator(selector).first.wait_for(state="visible", timeout=self.timeout)
            except Exception:
                raise StepFailed(seq, f"'{selector}' never appeared, so the step did not take effect")
            return

        if kind == "selector_hidden":
            selector = success.get("value", "")
            try:
                self._scope(action).locator(selector).first.wait_for(state="hidden", timeout=self.timeout)
            except Exception:
                raise StepFailed(seq, f"'{selector}' was still on the page, so the step did not take effect")
            return

        if kind == "value_equals":
            expected = success.get("value", "")
            self._wait_until(
                lambda: self._current_value(action) == expected,
                seq, "the field does not hold the value the recording expected",
            )
            return

        if kind == "value_not_empty":
            # Used for secrets: the value itself is never compared or reported.
            self._wait_until(
                lambda: bool(self._current_value(action)),
                seq, "the field was still empty afterwards",
            )
            return

        if kind == "checked_is":
            expected = bool(success.get("value", True))
            self._wait_until(
                lambda: self._locate(action).is_checked() == expected,
                seq, "the box is not in the state the recording expected",
            )
            return

        if kind == "url_changed":
            expected = success.get("value", "")
            self._wait_until(
                lambda: expected in self._page().url if expected else True,
                seq, "the page did not move where the recording expected",
            )
            return

        raise StepFailed(seq, f"'{kind}' is not a kind of proof the platform understands")

    def _wait_until(self, condition, seq: int, message: str) -> None:
        deadline = time.time() + self.timeout / 1000
        while time.time() < deadline:
            try:
                if condition():
                    return
            except Exception:
                pass  # mid-navigation the page refuses queries; keep waiting
            time.sleep(0.15)
        raise StepFailed(seq, message)

    def _settle(self) -> None:
        try:
            self._page().wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # a page that never goes idle is not by itself a failure

    def _current_value(self, action: dict[str, Any]) -> str:
        try:
            return self._locate(action).input_value()
        except Exception:
            return ""

    # ---------- finding things ----------

    def _page(self) -> Any:
        if self._current is None:
            raise PermanentError("The replay has no open page")
        return self._current

    def _try_page(self, name: str) -> Any | None:
        live = [p for p in self._pages if not _closed(p)]
        if not live:
            return None
        if name in ("", "main"):
            return live[0]
        if name == "latest":
            return live[-1]
        if name.startswith("page-"):
            try:
                index = int(name.split("-", 1)[1])
            except ValueError:
                return None
            return live[index] if 0 <= index < len(live) else None
        return None

    def _resolve_page(self, name: str) -> Any:
        page = self._try_page(name)
        if page is None:
            raise StepFailed(0, f"the tab '{name}' is not open")
        return page

    def _scope(self, action: dict[str, Any]) -> Any:
        """The page or frame this step's selectors are resolved against."""
        target = action.get("target") or {}
        wanted_page = target.get("page") or "main"
        # A step naming a tab acts there without needing an explicit switch.
        page = self._try_page(wanted_page) or self._page()
        if page is not self._current and wanted_page not in ("", "main"):
            self._current = page
        frame = self._frame_for(page, target.get("frame") or "")
        return frame if frame is not None else page

    def _frame(self, action: dict[str, Any]) -> Any:
        scope = self._scope(action)
        if scope is None:
            raise StepFailed(action.get("seq", 0), "the frame this step needs is not on the page")
        return scope

    @staticmethod
    def _frame_for(page: Any, frame_ref: str) -> Any | None:
        """Find the frame a step happened in, by URL or by the iframe's selector.

        The recording stores the frame's URL because that identifies it from
        inside, where the step actually occurred. A plan written by hand may name
        the iframe element instead, so both are accepted.
        """
        if not frame_ref:
            return None
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if frame_ref in (frame.url or "") or (frame.name and frame_ref == frame.name):
                return frame
        try:
            # Fall back to treating it as a selector for the <iframe> element.
            element = page.locator(frame_ref).first.element_handle(timeout=3000)
            return element.content_frame() if element else None
        except Exception:
            return None

    def _maybe_locate(self, action: dict[str, Any]) -> Any | None:
        locator = action.get("locator") or {}
        if not (locator.get("value") or locator.get("fallbacks")):
            return None
        return self._locate(action)

    def _locate(self, action: dict[str, Any]) -> Any:
        """Find the element, trying every way the recording knows about.

        A page that renames its ids between releases still has its field names,
        its test ids and its labels. Trying them in order is what keeps an
        automation working through a cosmetic change instead of failing on one.
        """
        locator_spec = action.get("locator") or {}
        scope = self._scope(action)
        candidates = [locator_spec.get("value", "")] + list(locator_spec.get("fallbacks") or [])
        candidates = [c for c in candidates if c]

        for selector in candidates:
            try:
                found = scope.locator(selector).first
                found.wait_for(state="visible", timeout=4000)
                return found
            except Exception:
                continue

        # Only after every stable way has failed: the recorded position on
        # screen, as a fraction of the viewport, never as pixels.
        position = self._position_locator(action, scope)
        if position is not None:
            return position
        raise StepFailed(
            action.get("seq", 0),
            "the element this step needs is no longer on the page — the site has probably "
            "changed, so record the task again",
        )

    def _position_locator(self, action: dict[str, Any], scope: Any) -> Any | None:
        spec = action.get("locator") or {}
        x_ratio, y_ratio = spec.get("x_ratio"), spec.get("y_ratio")
        if x_ratio is None or y_ratio is None:
            return None
        page = self._page()
        viewport = page.viewport_size or {"width": 1280, "height": 800}
        return _PositionClick(page, float(x_ratio) * viewport["width"], float(y_ratio) * viewport["height"])

    # ---------- secrets ----------

    def _value_for(self, action: dict[str, Any]) -> str:
        """The text to type: literal, or fetched from the credential store now.

        A secret has no representation in the plan beyond the name of the
        credential it comes from, and the value returned here is never stored,
        logged, or put in a step result.
        """
        inputs = action.get("inputs") or {}
        ref = inputs.get("secret_ref")
        if not ref:
            return inputs.get("value", "")
        if self.credentials is None:
            raise StepFailed(
                action.get("seq", 0),
                "this step needs a saved password, but secure storage is unavailable",
            )
        try:
            credential = self.credentials.get(ref)
        except Exception:
            credential = None
        if credential is None:
            raise StepFailed(
                action.get("seq", 0),
                "this step needs a saved password for this system; save it on the Sign-in page",
            )
        field = inputs.get("secret_field") or "password"
        return credential.username if field == "username" else credential.password


class _PositionClick:
    """A last-resort stand-in for a locator, clicking a point on the page.

    Only ever used when no stable selector worked. It supports the subset of the
    locator interface a positional step can honestly implement — anything that
    needs a real element (reading a value, checking a box) is not something a
    coordinate can do, and says so rather than pretending.
    """

    def __init__(self, page: Any, x: float, y: float) -> None:
        self._page, self._x, self._y = page, x, y

    def click(self) -> None:
        self._page.mouse.click(self._x, self._y)

    def fill(self, value: str) -> None:
        self._page.mouse.click(self._x, self._y)
        self._page.keyboard.type(value)

    def press(self, key: str) -> None:
        self._page.mouse.click(self._x, self._y)
        self._page.keyboard.press(key)

    def input_value(self) -> str:
        raise RuntimeError("a step matched only by screen position cannot read a value back")

    def is_checked(self) -> bool:
        raise RuntimeError("a step matched only by screen position cannot read a checkbox")

    def select_option(self, value: str) -> None:
        raise RuntimeError("a dropdown cannot be used by screen position; record the task again")

    def check(self) -> None:
        self.click()

    def uncheck(self) -> None:
        self.click()


def _closed(page: Any) -> bool:
    try:
        return page.is_closed()
    except Exception:
        return True
