"""Playwright adapter for the BrowserPort contract: network layer first, then DOM (D004, the golden rule).

The rest of the extraction ladder (self-healing, vision, desktop) is deferred to later stages.

Architecture note: the current BrowserPort contract (ExtractionRequest) does
not yet carry a generic "entry point" field per system, so navigation info
(the URL, the download selector...) is passed through request.filters until
the system definition files are built (S-03) and the final field shape is
settled at the contract level.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ...config import BrowserSettings
from ...credentials import CredentialStore
from ...domain.enums import ExtractionLayer
from ...ports.browser import ExtractionRequest, ExtractionResult, ReplayRequest
from .replay import ReplaySession, StepFailed
from .authentication import ensure_authenticated, prevent_debugger_pauses, session_expired
from .session import open_browser_context
from ...storage.paths import slug


class PlaywrightBrowserAdapter:
    """Executes one extraction request: tries the network layer if possible, then DOM."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        executable_path: str | None = None,
        clock: Any = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._settings = settings
        # An explicit argument wins (tests pin a specific binary); otherwise the
        # configured path, so a machine with Chrome but no Playwright download
        # still works.
        self._executable_path = executable_path or settings.executable_path or None
        self._clock = clock or time.time
        self._credential_store = credential_store
        # Last failure evidence per (system, report) — used by capture_evidence.
        self._last_evidence: dict[str, dict[str, Any]] = {}

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        started = self._clock()
        Path(request.destination_dir).mkdir(parents=True, exist_ok=True)

        # Reject an unexecutable request before launching Chromium. This
        # keeps the original error message and avoids spinning up a whole
        # browser process for nothing.
        filters = request.filters or {}
        has_network_path = bool(filters.get("direct_download_url")) and (
            ExtractionLayer.NETWORK in request.allowed_layers
        )
        if not has_network_path and ExtractionLayer.DOM in request.allowed_layers:
            if not filters.get("url"):
                return self._failure(
                    request,
                    ExtractionLayer.DOM,
                    "No entry URL (filters['url'])",
                    started,
                )
            if not filters.get("download_selector"):
                return self._failure(
                    request,
                    ExtractionLayer.DOM,
                    "No download selector (filters['download_selector'])",
                    started,
                )

        try:
            with sync_playwright() as playwright:
                session = open_browser_context(
                    playwright,
                    self._settings,
                    executable_path=self._executable_path,
                    accept_downloads=True,
                    storage_state_path=request.session_state_path,
                )
                try:
                    context = session.context
                    context.set_default_timeout(request.timeout_seconds * 1000)
                    try:
                        context.tracing.start(screenshots=True, snapshots=True)
                    except Exception:
                        pass  # tracing is a bonus, it must never stop the extraction if it fails
                    result = self._extract_with_context(context, request, started)
                    self._finish_tracing(context, request, result)
                    return result
                finally:
                    session.close()
        except PlaywrightTimeoutError as exc:
            return self._failure(request, ExtractionLayer.DOM, f"Timed out: {exc}", started)
        except Exception as exc:  # never fail silently on an unexpected error
            return self._failure(request, ExtractionLayer.DOM, f"Unexpected failure: {exc}", started)

    def _finish_tracing(
        self, context, request: ExtractionRequest, result: ExtractionResult
    ) -> None:
        """Save the trace only on failure, into the evidence folder; otherwise discard it."""
        try:
            if not result.ok and request.evidence_dir is not None:
                evidence_dir = Path(request.evidence_dir)
                evidence_dir.mkdir(parents=True, exist_ok=True)
                trace_path = evidence_dir / f"{self._evidence_key(request)}-trace.zip"
                context.tracing.stop(path=str(trace_path))
                result.evidence.setdefault("trace_path", str(trace_path))
            else:
                context.tracing.stop()
        except Exception:
            pass  # same rule as the start: tracing must never take down the result

    def _extract_with_context(
        self, context, request: ExtractionRequest, started: float
    ) -> ExtractionResult:
        filters = request.filters or {}
        direct_url = filters.get("direct_download_url")

        if direct_url and ExtractionLayer.NETWORK in request.allowed_layers:
            entry_url = filters.get("url")
            if entry_url:
                page = context.new_page()
                try:
                    page.goto(entry_url, wait_until="networkidle")
                    auth_error = self._ensure_authenticated(context, page, request, filters)
                    if auth_error:
                        return self._failure(
                            request, ExtractionLayer.NETWORK, auth_error, started, auth_required=True
                        )
                finally:
                    page.close()
            network_result = self._try_network(context, request, direct_url, started)
            if network_result is not None:
                return network_result
            # The network layer failed, fall through to the DOM layer if allowed.

        if ExtractionLayer.DOM not in request.allowed_layers:
            return self._failure(
                request, ExtractionLayer.NETWORK, "Network layer failed and no DOM layer is allowed", started
            )
        return self._extract_via_dom(context, request, filters, started)

    def _evidence_key(self, request: ExtractionRequest) -> str:
        """Evidence key: run_id if present (prevents parallel runs colliding), otherwise system:report."""
        return slug(request.run_id) if request.run_id else f"{slug(request.system)}:{slug(request.report)}"

    def _try_network(
        self, context, request: ExtractionRequest, direct_url: str, started: float
    ) -> ExtractionResult | None:
        try:
            response = context.request.get(direct_url)
            if not response.ok:
                return None
            content_type = (response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                # Most likely a redirect to a login page (session expired) or
                # an error page, not the requested file. Fall through to the
                # DOM layer instead of treating this as a false success.
                return None
            body = response.body()
            name = Path(direct_url.split("?")[0]).name or f"{request.report}"
            target = Path(request.destination_dir) / name
            target.write_bytes(body)
            return ExtractionResult(
                ok=True,
                layer_used=ExtractionLayer.NETWORK,
                file_paths=[target],
                original_name=name,
                size_bytes=len(body),
                duration_seconds=self._clock() - started,
            )
        except Exception:
            return None

    def _session_expired(self, page, filters: dict[str, Any]) -> bool:
        return session_expired(page, filters)

    def _ensure_authenticated(
        self, context, page, request: ExtractionRequest, filters: dict[str, Any]
    ) -> str | None:
        return ensure_authenticated(
            context,
            page,
            system=request.system,
            filters=filters,
            credential_store=self._credential_store,
            session_state_path=request.session_state_path,
            pause_guard=self._prevent_debugger_pauses,
        )

    @staticmethod
    def _prevent_debugger_pauses(context, page) -> None:
        prevent_debugger_pauses(context, page)

    def _extract_via_dom(
        self, context, request: ExtractionRequest, filters: dict[str, Any], started: float
    ) -> ExtractionResult:
        entry_url = filters.get("url")
        if not entry_url:
            return self._failure(request, ExtractionLayer.DOM, "No entry URL (filters['url'])", started)

        download_selector = filters.get("download_selector")
        if not download_selector:
            return self._failure(
                request, ExtractionLayer.DOM, "No download selector (filters['download_selector'])", started
            )

        page = context.new_page()
        try:
            page.goto(entry_url, wait_until="networkidle")

            message = self._ensure_authenticated(context, page, request, filters)
            if message:
                self._capture_failure_evidence(page, request, message)
                return self._failure(request, ExtractionLayer.DOM, message, started, auth_required=True)
            # A successful login can land on a home page; return to the report URL.
            if page.url != entry_url:
                page.goto(entry_url, wait_until="networkidle")
            if self._session_expired(page, filters):
                message = f"Automatic login did not establish a valid session for {request.system}."
                self._capture_failure_evidence(page, request, message)
                return self._failure(request, ExtractionLayer.DOM, message, started, auth_required=True)

            wait_selector = filters.get("wait_selector")
            if wait_selector:
                page.wait_for_selector(wait_selector)

            with page.expect_download() as download_info:
                page.click(download_selector)
            download = download_info.value
            suggested = download.suggested_filename or request.report
            target = Path(request.destination_dir) / suggested
            download.save_as(target)
            return ExtractionResult(
                ok=True,
                layer_used=ExtractionLayer.DOM,
                file_paths=[target],
                original_name=suggested,
                size_bytes=target.stat().st_size,
                duration_seconds=self._clock() - started,
            )
        except PlaywrightTimeoutError as exc:
            self._capture_failure_evidence(page, request, f"Timed out during DOM interaction: {exc}")
            return self._failure(request, ExtractionLayer.DOM, f"Timed out during DOM interaction: {exc}", started)
        except Exception as exc:
            self._capture_failure_evidence(page, request, str(exc))
            return self._failure(request, ExtractionLayer.DOM, f"DOM interaction failed: {exc}", started)
        finally:
            page.close()

    def _capture_failure_evidence(self, page, request: ExtractionRequest, message: str) -> None:
        key = self._evidence_key(request)
        evidence: dict[str, Any] = {"message": message, "url": page.url}
        if request.evidence_dir is not None:
            try:
                evidence_dir = Path(request.evidence_dir)
                evidence_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = evidence_dir / f"{key}-screenshot.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                evidence["screenshot_path"] = str(screenshot_path)
            except Exception:
                pass
        self._last_evidence[key] = evidence

    def _failure(
        self,
        request: ExtractionRequest,
        layer: ExtractionLayer,
        message: str,
        started: float,
        *,
        auth_required: bool = False,
    ) -> ExtractionResult:
        key = self._evidence_key(request)
        return ExtractionResult(
            ok=False,
            layer_used=layer,
            message=message,
            duration_seconds=self._clock() - started,
            evidence=self._last_evidence.get(key, {}),
            auth_required=auth_required,
        )

    # ---------- replay of a recorded plan ----------

    def replay(self, request: ReplayRequest) -> ExtractionResult:
        """Replay a recorded plan: authenticate, walk the actions, capture the download.

        The whole plan runs inside one browser context so the site sees a single
        continuous visit, the same as when the human recorded it. Authentication
        is the identical code path used by extract(), so a recorded automation
        benefits from saved sessions and unattended login without duplicating
        any of that logic.
        """
        started = self._clock()
        Path(request.destination_dir).mkdir(parents=True, exist_ok=True)

        plan = request.plan or {}
        actions = plan.get("actions") or []
        start_url = plan.get("start_url") or ""
        # Reject an unrunnable plan before paying for a browser launch, and say
        # which half is missing so the message is actionable on its own.
        if not start_url:
            return self._replay_failure(request, "The recorded plan has no starting page.", started)
        if not actions:
            return self._replay_failure(request, "The recorded plan has no steps to repeat.", started)

        try:
            with sync_playwright() as playwright:
                session = open_browser_context(
                    playwright,
                    self._settings,
                    executable_path=self._executable_path,
                    accept_downloads=True,
                    storage_state_path=request.session_state_path,
                )
                try:
                    context = session.context
                    context.set_default_timeout(request.timeout_seconds * 1000)
                    try:
                        context.tracing.start(screenshots=True, snapshots=True)
                    except Exception:
                        pass  # tracing is a bonus; it must never stop a replay
                    result = self._replay_with_context(context, request, plan, started)
                    self._finish_tracing(context, self._as_extraction_request(request), result)
                    return result
                finally:
                    session.close()
        except PlaywrightTimeoutError as exc:
            return self._replay_failure(request, f"Timed out while repeating the recording: {exc}", started)
        except Exception as exc:
            return self._replay_failure(request, f"Unexpected failure while repeating the recording: {exc}", started)

    def _as_extraction_request(self, request: ReplayRequest) -> ExtractionRequest:
        """Adapt a replay request to the shape the shared auth/evidence helpers expect."""
        return ExtractionRequest(
            system=request.system,
            report=request.report,
            destination_dir=request.destination_dir,
            period=request.period,
            filters=request.filters or {},
            timeout_seconds=request.timeout_seconds,
            run_id=request.run_id,
            session_state_path=request.session_state_path,
            evidence_dir=request.evidence_dir,
        )

    def _replay_with_context(
        self, context, request: ReplayRequest, plan: dict[str, Any], started: float
    ) -> ExtractionResult:
        """Walk the recorded steps, proving each one, and keep every file produced."""
        filters = request.filters or {}
        as_extraction = self._as_extraction_request(request)
        session = ReplaySession(
            context,
            artifact_dir=Path(request.destination_dir),
            credential_store=self._credential_store,
            evidence_timeout_ms=int(min(request.timeout_seconds, 60) * 1000),
        )
        page = session.open(plan["start_url"])
        try:
            auth_message = self._ensure_authenticated(context, page, as_extraction, filters)
            if auth_message:
                self._capture_failure_evidence(page, as_extraction, auth_message)
                return self._replay_failure(request, auth_message, started, auth_required=True)
            # A sign-in redirect can land somewhere else; return to the recorded
            # starting page before replaying the first action.
            if page.url != plan["start_url"]:
                page.goto(plan["start_url"], wait_until="domcontentloaded")

            for action in plan.get("actions") or []:
                session.perform(action)
                # Files are saved as they arrive rather than all at the end: a
                # later step can navigate away or close the tab a download
                # belongs to, and the handle would be gone with it.
                session.collect_downloads(Path(request.destination_dir))

            session.collect_downloads(Path(request.destination_dir))
            return self._replay_outcome(request, plan, session, started, as_extraction, page)
        except StepFailed as failure:
            self._capture_failure_evidence(page, as_extraction, failure.reason)
            return self._replay_failure(
                request, str(failure), started, step_results=session.step_results
            )
        except PlaywrightTimeoutError as exc:
            message = f"A recorded step no longer works on the site: {exc}"
            self._capture_failure_evidence(page, as_extraction, message)
            return self._replay_failure(request, message, started, step_results=session.step_results)
        except Exception as exc:
            message = f"Repeating the recording failed: {exc}"
            self._capture_failure_evidence(page, as_extraction, message)
            return self._replay_failure(request, message, started, step_results=session.step_results)

    def _replay_outcome(
        self,
        request: ReplayRequest,
        plan: dict[str, Any],
        session: ReplaySession,
        started: float,
        as_extraction: ExtractionRequest,
        page: Any,
    ) -> ExtractionResult:
        """Decide whether the task really produced what the recording promised."""
        expected = int(plan.get("expected_download_count") or 0)
        if not plan.get("expects_download"):
            message = "The recording produced no file, so there is nothing to validate."
            self._capture_failure_evidence(page, as_extraction, message)
            return self._replay_failure(request, message, started, step_results=session.step_results)

        if not session.downloads:
            message = "The recorded steps ran, but no file was downloaded."
            if session.download_errors:
                message += f" Saving it failed: {session.download_errors[-1]}"
            self._capture_failure_evidence(page, as_extraction, message)
            return self._replay_failure(request, message, started, step_results=session.step_results)

        # Fewer files than the recording produced is a partial result, and a
        # partial result reported as success is how a missing detail file goes
        # unnoticed for a month.
        if expected and len(session.downloads) < expected:
            message = (
                f"The task produced {len(session.downloads)} file(s) but the recording "
                f"produced {expected}. Something the recording did is no longer happening."
            )
            self._capture_failure_evidence(page, as_extraction, message)
            return self._replay_failure(request, message, started, step_results=session.step_results)

        first = session.downloads[0]
        return ExtractionResult(
            ok=True,
            layer_used=ExtractionLayer.DOM,
            file_paths=list(session.downloads),
            original_name=first.name,
            size_bytes=first.stat().st_size,
            duration_seconds=self._clock() - started,
            step_results=session.step_results,
        )

    def _replay_failure(
        self,
        request: ReplayRequest,
        message: str,
        started: float,
        *,
        auth_required: bool = False,
        step_results: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        result = self._failure(
            self._as_extraction_request(request),
            ExtractionLayer.DOM,
            message,
            started,
            auth_required=auth_required,
        )
        # Even a failed run says how far it got: which steps passed, which one
        # stopped it, and how long each took.
        result.step_results = list(step_results or [])
        return result

    def capture_evidence(self, run_id: str) -> dict[str, Any]:
        """Return the failure evidence for this exact run, otherwise the last recorded
        evidence (for calls that do not pass a matching run_id)."""
        key = slug(run_id) if run_id else ""
        if key and key in self._last_evidence:
            return {"run_id": run_id, **self._last_evidence[key]}
        if not self._last_evidence:
            return {"run_id": run_id}
        last_key = next(reversed(self._last_evidence))
        return {"run_id": run_id, **self._last_evidence[last_key]}
