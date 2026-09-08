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

import math
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...core.errors import PermanentError
from .downloads import discard_empty_reservation, reserve_download_path
from ...recordings.object_repository import resolve_locator
from ...recordings.healing import repair_proposal

# How long to wait for a step's success evidence before deciding it did not
# happen. Generous: a corporate report can take a while to render, and the point
# is to distinguish "slow" from "never".
DEFAULT_EVIDENCE_TIMEOUT_MS = 15000

# G-MES can open this informational dialog while the Production Plan screen is
# mounting.  It is safe to dismiss only when the exact message is visible; no
# other confirmation dialog is ever clicked by this rule.
_GMES_ORG_ALERT = "Selecting an org chart before adding row(s)"
_GMES_ORG_ALERT_SELECTOR = '[id$=".Info_1.form.divBody.form.staContents:text"]'


def _extension_from_download_content(path: Path) -> str:
    """Identify a downloaded Excel workbook when the portal omitted its suffix.

    A ZIP signature alone is not enough: arbitrary ZIP attachments are common.
    Excel workbooks always carry both their package manifest and workbook part,
    so add ``.xlsx`` only when those facts are present after the browser has
    saved the completed download.
    """
    if path.suffix:
        return ""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return ""
    return ".xlsx" if {"[Content_Types].xml", "xl/workbook.xml"} <= names else ""


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
        object_repository: dict[str, Any] | None = None,
    ) -> None:
        self.context = context
        self.artifact_dir = Path(artifact_dir)
        self.credentials = credential_store
        self.timeout = evidence_timeout_ms
        self.object_repository = object_repository or {}
        self.downloads: list[Path] = []
        self.download_errors: list[str] = []
        self.step_results: list[dict[str, Any]] = []
        self.repair_candidates: list[dict[str, Any]] = []
        self._pages: list[Any] = []
        # Context pages that existed before this replay belong to the caller.
        # Stale-page cleanup may close only pages created and tracked by this
        # session; keeping this inventory also protects an operator's open tab.
        self._initial_context_pages: list[Any] = list(
            getattr(context, "pages", None) or []
        )
        # Replay identities are assigned once and remain stable after closure.
        self._page_names: dict[int, str] = {}
        self._page_by_name: dict[str, Any] = {}
        self._main_page: Any = None
        self._next_page_number = 1
        # Authentication can create popup/relay tabs before replay starts. Keep
        # them in _pages for history, but exclude them from replay targets.
        self._replay_pages: list[Any] = []
        self._replay_started = False
        self._current: Any = None
        self._pending_downloads: list[Any] = []
        self._downloads_before = 0
        self._responses: list[dict[str, Any]] = []
        self._responses_before = 0

    # ---------- setup ----------

    def open(self, start_url: str) -> Any:
        """Start the task on its first page, watching for downloads from anywhere."""
        # Registered before the first navigation so a file that arrives during
        # the very first action is still caught.
        self.context.on("page", self._track)
        self.context.on("response", self._on_response)
        page = self.context.new_page()
        self._track(page, main=True)
        self._current = page
        page.goto(start_url, wait_until="domcontentloaded")
        return page

    def _track(self, page: Any, *, main: bool = False) -> None:
        """Track a page once and assign a stable replay identity."""
        if any(existing is page for existing in self._pages):
            return
        self._pages.append(page)
        if main or self._main_page is None:
            name = "main"
            self._main_page = page
        else:
            name = f"page-{self._next_page_number}"
            self._next_page_number += 1
        self._page_names[id(page)] = name
        self._page_by_name[name] = page
        # Download capture is part of the replay contract; propagate an event
        # binding failure instead of silently reporting a missing file later.
        page.on("download", self._on_download)
        if self._replay_started:
            self._replay_pages.append(page)

    def adopt(self, page: Any) -> None:
        """Continue on the page sign-in settled on, not the one we opened.

        Authentication can leave popup and relay pages in the context. They are
        retained in _pages for history, while this verified page becomes the
        sole ``main`` target and the first post-auth page will be ``page-1``.
        """
        if page is None or _closed(page):
            return  # a closed page carries nothing; keep what we have
        self._track(page)
        self._main_page = page
        self._replay_started = True
        self._replay_pages = [page]
        self._next_page_number = 1
        self._page_names[id(page)] = "main"
        self._page_by_name = {"main": page}
        self._current = page

    def current_page(self) -> Any:
        """The page the next step will act on."""
        return self._current

    def drop_stale_pages(self) -> list[str]:
        """Leave exactly one page for the first step to act on.

        Sign-in can leave leftovers behind: the blank page a persistent Chrome
        profile starts with, a short-lived relay tab, or a second copy of the
        portal opened during the handoff. A step recorded on the "main" tab
        would resolve to whichever of those is first, so they are closed here.

        Only pages this context owns are ever considered, and only when they
        are blank or on the same host as the page we are keeping — a tab
        belonging to something else is never touched.
        """
        keeper = self._current
        if keeper is None:
            return []
        try:
            keeper_host = urlsplit(keeper.url or "").netloc
        except Exception:
            return []
        # Only pages observed by this session's page listener are candidates.
        # A context may already contain an operator tab, and an untracked page
        # is outside this replay's ownership boundary.
        candidates: list[Any] = list(self._pages)
        closed: list[str] = []
        for page in candidates:
            if page is keeper or _closed(page):
                continue
            # Existing context tabs are not owned by this replay, even when
            # they happen to share the portal host.
            if any(existing is page for existing in self._initial_context_pages):
                continue
            try:
                url = page.url or ""
            except Exception:
                continue
            host = urlsplit(url).netloc
            if url not in ("", "about:blank") and host != keeper_host:
                continue
            try:
                page.close()
            except Exception:
                continue  # a tab we cannot close is not a reason to stop
            closed.append(url or "about:blank")
        # Keep closed entries in _pages so page identities and event history do
        # not get renumbered after cleanup.
        return closed

    def _on_download(self, download: Any) -> None:
        # Saving here would re-enter Playwright during the click that caused the
        # download; hold the handle and save it on our own thread instead.
        self._pending_downloads.append(download)

    def _on_response(self, response: Any) -> None:
        """Keep only response facts needed for success evidence."""
        try:
            self._responses.append(
                {
                    "method": response.request.method,
                    "url": response.url,
                    "status": response.status,
                }
            )
        except Exception:
            pass

    def collect_downloads(self, destination: Path) -> None:
        """Save whatever files the task produced, keeping every one of them."""
        destination.mkdir(parents=True, exist_ok=True)
        while self._pending_downloads:
            download = self._pending_downloads.pop(0)
            target: Path | None = None
            try:
                target = reserve_download_path(
                    destination,
                    download.suggested_filename,
                    fallback=f"download-{len(self.downloads) + 1}",
                )
                download.save_as(str(target))
                extension = _extension_from_download_content(target)
                if extension:
                    normalized = target.with_name(f"{target.name}{extension}")
                    duplicate = 2
                    while normalized.exists():
                        normalized = target.with_name(f"{target.name}-{duplicate}{extension}")
                        duplicate += 1
                    target.replace(normalized)
                    target = normalized
                if target.exists() and target.stat().st_size > 0:
                    self.downloads.append(target)
                else:
                    discard_empty_reservation(target)
                    self.download_errors.append("download was empty")
            except Exception as exc:
                if target is not None:
                    discard_empty_reservation(target)
                self.download_errors.append(f"{type(exc).__name__}: {exc}")
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
        # A pointer fallback is used precisely because mousedown can trigger
        # the business effect before the element is replaced. It is never safe
        # to issue a second physical press just because the result was unclear.
        # Keep this retry decision independent of V2 repository resolution.
        # A dangling object reference must be raised inside the attempt loop so
        # the failed step is recorded with the same evidence as every other
        # resolution failure.
        if (action.get("locator") or {}).get("interaction") == "pointer":
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
                self._responses_before = len(self._responses)
                self._pages_before = len([p for p in self._pages if not _closed(p)])
                self._active_timeout_ms = int(
                    float(action.get("wait_timeout_seconds", self.timeout / 1000)) * 1000
                )
                self._verify_precondition(action)
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
            "pointer_click": self._do_pointer_click,
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
        locator = self._locate(action)
        spec = self._locator_spec(action)
        if spec.get("interaction") == "pointer":
            self._do_pointer_click(action, locator)
            return
        if spec.get("position_mode") != "element_relative":
            locator.click()
            return

        try:
            box = locator.bounding_box()
            x_ratio = float(spec["element_x_ratio"])
            y_ratio = float(spec["element_y_ratio"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise StepFailed(
                action.get("seq", 0),
                "the recorded control no longer exposes the area that was clicked",
            )
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            raise StepFailed(
                action.get("seq", 0),
                "the recorded control is present but has no clickable area",
            )
        # Playwright expects a point relative to the located element. Clamp the
        # stored fractions so a changed border cannot put the click outside it.
        x = min(max(x_ratio, 0.0), 1.0) * float(box["width"])
        y = min(max(y_ratio, 0.0), 1.0) * float(box["height"])
        locator.click(position={"x": x, "y": y})

    def _do_pointer_click(self, action: dict[str, Any], locator: Any) -> None:
        """Repeat a recorded press/re-render gesture through a live locator.

        Some component frameworks replace a control on mousedown. Locator.click
        correctly treats that detachment as a failed DOM click, although a person
        completed the physical gesture. We still require Playwright's trial
        actionability check and a current bounding box; no absolute screen point,
        force click, or blind retry is allowed.
        """
        try:
            locator.click(trial=True, timeout=max(1, int(getattr(self, "_active_timeout_ms", self.timeout))))
            box = locator.bounding_box()
        except Exception as exc:
            raise StepFailed(action.get("seq", 0), f"the recorded pointer target is not ready: {exc}")
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            raise StepFailed(action.get("seq", 0), "the recorded pointer target has no clickable area")
        spec = self._locator_spec(action)
        x_ratio = self._ratio(spec.get("element_x_ratio", 0.5), action, "x")
        y_ratio = self._ratio(spec.get("element_y_ratio", 0.5), action, "y")
        x = float(box["x"]) + x_ratio * float(box["width"])
        y = float(box["y"]) + y_ratio * float(box["height"])
        page_name = (action.get("target") or {}).get("page", "main")
        page = self._resolve_page(page_name)
        page.mouse.move(x, y)
        pressed = False
        try:
            page.mouse.down()
            pressed = True
            page.mouse.up()
        except Exception as exc:
            if pressed:
                try:
                    page.mouse.up()
                except Exception:
                    pass
                raise StepFailed(
                    action.get("seq", 0),
                    "the pointer press was sent but its release could not be confirmed; "
                    "the remote effect is unknown, so do not retry this step",
                ) from exc
            raise StepFailed(action.get("seq", 0), "the pointer gesture could not be sent") from exc

    @staticmethod
    def _ratio(value: Any, action: dict[str, Any], axis: str) -> float:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            raise StepFailed(action.get("seq", 0), f"the recorded pointer {axis} position is invalid")
        if not math.isfinite(ratio):
            raise StepFailed(action.get("seq", 0), f"the recorded pointer {axis} position is invalid")
        return min(max(ratio, 0.0), 1.0)

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
            # A keyboard event belongs to a Page, not a Frame.  Resolve the
            # recorded target before using it so a press cannot silently land
            # on whichever tab happened to be current after a popup race.
            target = action.get("target") or {}
            if target.get("frame"):
                raise StepFailed(
                    action.get("seq", 0),
                    "a frame keyboard step needs a recorded focus locator",
                )
            self._scope(action).keyboard.press(key)

    def _do_navigate(self, action: dict[str, Any]) -> None:
        url = (action.get("inputs") or {}).get("url", "")
        if not url:
            raise StepFailed(action.get("seq", 0), "the step has no address to open")
        target = action.get("target") or {}
        if target.get("frame"):
            raise StepFailed(
                action.get("seq", 0),
                "a navigation step cannot target a frame",
            )
        # Resolve the requested page, rather than navigating the current tab.
        # Frame navigation needs its own explicit action type; treating it as
        # Page.goto would be an unsafe, silent change of scope.
        self._scope(action).goto(url, wait_until="domcontentloaded")

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

    def _verify_precondition(self, action: dict[str, Any]) -> None:
        """Reject a chained proof whose later control was already usable.

        A later recorded step is meaningful evidence only when this action made
        it possible.  ``trial=True`` uses the browser's own user-actionability
        checks (visible, stable, enabled, and receiving pointer events) without
        dispatching a second click.
        """
        success = action.get("success") or {}
        if success.get("type") != "next_step_actionable":
            return
        if self._next_step_actionable(action):
            raise StepFailed(
                action.get("seq", 0),
                "the next recorded control was already actionable before this click, "
                "so it cannot prove the click worked",
            )

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

        if kind == "network_response":
            expected_method = str(success.get("method") or "").upper()
            expected_path = str(success.get("path") or "")
            expected_status = int(success.get("status") or 200)
            before = getattr(self, "_responses_before", 0)

            def matched() -> bool:
                from urllib.parse import urlsplit

                for response in self._responses[before:]:
                    if expected_method and response.get("method", "").upper() != expected_method:
                        continue
                    if expected_path and urlsplit(response.get("url", "")).path != expected_path:
                        continue
                    if int(response.get("status") or 0) != expected_status:
                        continue
                    return True
                return False

            self._wait_until(matched, seq, "the expected report response did not arrive")
            return

        if kind == "new_page":
            before = getattr(self, "_pages_before", len(self._pages))
            self._wait_until(
                lambda: len([p for p in self._pages if not _closed(p)]) > before,
                seq, "no new tab opened",
            )
            return

        if kind == "page_available":
            name = (action.get("target") or {}).get("page", "main")
            self._wait_until(lambda: self._try_page(name) is not None, seq, f"the tab '{name}' never appeared")
            return

        if kind == "next_step_actionable":
            self._wait_until(
                lambda: self._next_step_actionable(action),
                seq,
                "the next recorded control never became actionable, so this click did not take effect",
                on_wait=self._dismiss_known_safe_interruption,
            )
            return

        if kind == "selector_visible":
            selector = success.get("value", "")
            try:
                found = self._scope(action).locator(selector)
                found.wait_for(
                    state="visible", timeout=getattr(self, "_active_timeout_ms", self.timeout)
                )
                count = int(found.count())
                if count != 1:
                    raise ValueError(f"{count} matching elements")
            except Exception:
                raise StepFailed(
                    seq,
                    f"'{selector}' was not a unique visible result, so the step did not take effect",
                )
            return

        if kind == "selector_hidden":
            selector = success.get("value", "")
            try:
                found = self._scope(action).locator(selector)
                count = int(found.count())
                if count == 0:
                    return
                if count != 1:
                    raise ValueError(f"{count} matching elements")
                found.wait_for(
                    state="hidden", timeout=getattr(self, "_active_timeout_ms", self.timeout)
                )
            except Exception:
                raise StepFailed(
                    seq,
                    f"'{selector}' did not become a unique hidden result, so the step did not take effect",
                )
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

    def _wait_until(self, condition, seq: int, message: str, *, on_wait=None) -> None:
        deadline = time.time() + getattr(self, "_active_timeout_ms", self.timeout) / 1000
        while time.time() < deadline:
            try:
                if condition():
                    return
            except Exception:
                pass  # mid-navigation the page refuses queries; keep waiting
            if on_wait is not None:
                try:
                    on_wait()
                except Exception:
                    pass  # an optional interruption must never hide the real proof result
            try:
                self._page().wait_for_timeout(150)
            except Exception:
                time.sleep(0.15)
        raise StepFailed(seq, message)

    def _dismiss_known_safe_interruption(self) -> bool:
        """Close only the proven, informational G-MES organisation alert."""
        page = self._page()
        messages = page.locator(_GMES_ORG_ALERT_SELECTOR)
        for index in range(min(messages.count(), 20)):
            message = messages.nth(index)
            if not message.is_visible():
                continue
            text = " ".join((message.text_content() or "").split())
            if text != _GMES_ORG_ALERT:
                continue
            message_id = message.get_attribute("id") or ""
            prefix, marker, _ = message_id.partition(".Info_1.form.")
            if not marker:
                continue
            button = page.locator(f'[id="{prefix}.Info_1.form.btnOk:icontext"]')
            if button.count() != 1 or not button.first.is_visible():
                continue
            button.first.click()
            return True
        return False

    def _settle(self) -> None:
        """Yield one short UI turn; network idleness is not app readiness."""
        try:
            self._page().wait_for_timeout(150)
        except Exception:
            pass  # a closing page cannot receive the optional short yield

    def _current_value(self, action: dict[str, Any]) -> str:
        try:
            return self._locate(action).input_value()
        except Exception:
            return ""

    def _next_step_actionable(self, action: dict[str, Any]) -> bool:
        """Whether the chained-proof target could receive a real user click now."""
        success = action.get("success") or {}
        target = success.get("target")
        locator = success.get("locator")
        if not isinstance(target, dict) or not isinstance(locator, dict):
            return False
        candidates = [locator.get("value", "")] + list(locator.get("fallbacks") or [])
        candidates = [candidate for candidate in candidates if isinstance(candidate, str) and candidate]
        if not candidates:
            return False

        proof_action = {
            "seq": action.get("seq", 0),
            "target": target,
            "locator": locator,
        }
        try:
            scope = self._scope(proof_action)
        except StepFailed:
            return False

        # Keep an individual trial short.  The outer evidence wait supplies the
        # full configured timeout, while a short trial lets us observe a menu
        # transition promptly rather than blocking one long actionability wait.
        trial_timeout = min(500, max(100, getattr(self, "_active_timeout_ms", self.timeout)))
        for selector in candidates:
            try:
                scope.locator(selector).first.click(trial=True, timeout=trial_timeout)
                return True
            except Exception:
                continue
        return False

    # ---------- finding things ----------

    def _locator_spec(self, action: dict[str, Any]) -> dict[str, Any]:
        """Resolve a V2 object reference while preserving old inline plans."""
        try:
            return resolve_locator(action, self.object_repository)
        except KeyError:
            raise StepFailed(
                action.get("seq", 0),
                f"the referenced UI object '{action.get('object_ref')}' is missing from this plan",
            )

    def _page(self) -> Any:
        if self._current is None:
            raise PermanentError("The replay has no open page")
        if _closed(self._current):
            raise PermanentError("The replay's current page is closed")
        return self._current

    def _ensure_page_identities(self) -> None:
        """Backfill identities for compatibility with focused test doubles."""
        for page in list(self._pages):
            if id(page) in self._page_names:
                continue
            if self._main_page is None:
                name = "main"
                self._main_page = page
            else:
                name = f"page-{self._next_page_number}"
                self._next_page_number += 1
            self._page_names[id(page)] = name
            self._page_by_name[name] = page

    def _try_page(self, name: str) -> Any | None:
        self._ensure_page_identities()
        wanted = name or "main"
        if wanted == "page-0":
            wanted = "main"
        if wanted == "latest":
            candidates = self._replay_pages if self._replay_started else self._pages
            live = [p for p in candidates if not _closed(p)]
            return live[-1] if live else None
        page = self._page_by_name.get(wanted)
        if page is None or _closed(page):
            return None
        return page

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
        page = self._try_page(wanted_page)
        if page is None:
            raise StepFailed(
                action.get("seq", 0), f"the tab '{wanted_page}' is not open"
            )
        if page is not self._current:
            self._current = page
        frame_ref = target.get("frame") or ""
        frame = self._frame_for(page, frame_ref)
        if frame_ref and frame is None:
            raise StepFailed(action.get("seq", 0), "the frame this step needs is not on the page")
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
            matches = page.locator(frame_ref)
            if int(matches.count()) != 1:
                return None
            element = matches.element_handle(timeout=3000)
            return element.content_frame() if element else None
        except Exception:
            return None

    def _maybe_locate(self, action: dict[str, Any]) -> Any | None:
        locator = self._locator_spec(action)
        if not (
            locator.get("value")
            or locator.get("fallbacks")
            or locator.get("anchor")
            or locator.get("anchors")
            or locator.get("semantic")
        ):
            return None
        return self._locate(action)

    def _locate(self, action: dict[str, Any]) -> Any:
        """Find the element, trying every way the recording knows about.

        A page that renames its ids between releases still has its field names,
        its test ids and its labels. Trying them in order is what keeps an
        automation working through a cosmetic change instead of failing on one.
        """
        locator_spec = self._locator_spec(action)
        scope = self._scope(action)
        candidates = [locator_spec.get("value", "")] + list(locator_spec.get("fallbacks") or [])
        candidates = [c for c in candidates if c]
        deadline = time.monotonic() + max(
            1, int(getattr(self, "_active_timeout_ms", self.timeout))
        ) / 1000
        ambiguous_count: int | None = None

        for index, selector in enumerate(candidates):
            try:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    break
                found = scope.locator(selector)
                count = int(found.count())
                if count > 1:
                    ambiguous_count = count
                    continue
                if count == 0:
                    continue
                found.wait_for(state="visible", timeout=max(1, remaining_ms))
                self._record_resolution(action, "primary" if index == 0 else "fallback", selector)
                return found
            except Exception:
                continue

        # The control itself may be dynamically regenerated, while the form
        # group around it is stable. An anchor is a constrained fallback, not
        # a licence to guess: exactly one container and exactly one relative
        # target must exist in the already-resolved page/frame scope.
        anchors: list[Any] = []
        if locator_spec.get("anchor"):
            anchors.append(locator_spec["anchor"])
        anchors.extend(locator_spec.get("anchors") or [])
        anchor_problem = ""
        for anchor in anchors[:3]:
            container_selector = anchor.get("container") if isinstance(anchor, dict) else ""
            target_selector = anchor.get("target") if isinstance(anchor, dict) else ""
            if not (
                isinstance(container_selector, str)
                and isinstance(target_selector, str)
                and container_selector
                and target_selector
            ):
                continue
            try:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms > 0:
                    containers = scope.locator(container_selector)
                    container_count = int(containers.count())
                    if container_count != 1:
                        anchor_problem = (
                            "the recorded anchor is not unique "
                            f"({container_count} matching containers)"
                        )
                        continue
                    containers.wait_for(state="visible", timeout=max(1, remaining_ms))
                    anchored = containers.locator(target_selector)
                    target_count = int(anchored.count())
                    if target_count != 1:
                        anchor_problem = (
                            "the target inside its recorded anchor is not unique "
                            f"({target_count} matching elements)"
                        )
                        continue
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    if remaining_ms > 0:
                        anchored.wait_for(state="visible", timeout=max(1, remaining_ms))
                        self._record_resolution(action, "anchor", container_selector)
                        return anchored
            except Exception:
                # A detached/replaced anchor is treated like any other stale
                # selector below, never as permission to use coordinates.
                continue

        # The final deterministic DOM option is the recorded control role/tag.
        # It intentionally carries no visible label or business value, so it is
        # safe to store but can only be used when it uniquely identifies one
        # live element in the correct page/frame.
        semantic_selector = _semantic_selector(locator_spec.get("semantic"))
        if semantic_selector:
            try:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms > 0:
                    semantic = scope.locator(semantic_selector)
                    semantic_count = int(semantic.count())
                    if semantic_count == 1:
                        semantic.wait_for(state="visible", timeout=max(1, remaining_ms))
                        self._record_resolution(action, "semantic", semantic_selector)
                        return semantic
                    if semantic_count > 1:
                        ambiguous_count = semantic_count
            except Exception:
                pass

        if ambiguous_count is not None:
            raise StepFailed(
                action.get("seq", 0),
                f"the recorded target is ambiguous ({ambiguous_count} matching elements); "
                "record a more specific control before retrying",
            )

        if anchor_problem:
            raise StepFailed(
                action.get("seq", 0),
                f"{anchor_problem}; record a more specific nearby control",
            )

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

    def _record_resolution(self, action: dict[str, Any], strategy: str, selector: str) -> None:
        proposal = repair_proposal(action, strategy=strategy, selector=selector)
        if proposal and proposal not in self.repair_candidates:
            self.repair_candidates.append(proposal)

    def _position_locator(self, action: dict[str, Any], scope: Any) -> Any | None:
        # A DOM step must fail when all of its recorded selectors fail. Falling
        # through to an old coordinate would turn a clear site-change failure
        # into a click on an arbitrary element. Pure positional plans are marked
        # visual and are blocked by the review gate before production use.
        if action.get("layer") != "visual":
            return None
        spec = self._locator_spec(action)
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


def _semantic_selector(semantic: Any) -> str:
    """Build a restricted CSS selector from safe recorded role/tag metadata."""
    if not isinstance(semantic, dict):
        return ""
    tag = str(semantic.get("tag") or "").lower()
    role = str(semantic.get("role") or "")
    field_type = str(semantic.get("type") or "")
    if not tag or not role or not tag.replace("-", "").isalnum():
        return ""
    import json

    selector = tag
    if semantic.get("explicit_role"):
        selector += f"[role={json.dumps(role)}]"
    if field_type:
        selector += f"[type={json.dumps(field_type)}]"
    return selector
