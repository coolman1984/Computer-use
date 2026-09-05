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
from ...ports.browser import ExtractionRequest, ExtractionResult
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
        self._executable_path = executable_path
        self._clock = clock or time.time
        self._credential_store = credential_store
        # Last failure evidence per (system, report) — used by capture_evidence.
        self._last_evidence: dict[str, dict[str, Any]] = {}

    def _launch(self, playwright: Playwright):
        kwargs: dict[str, Any] = {"headless": self._settings.headless}
        if self._executable_path:
            kwargs["executable_path"] = self._executable_path
        return playwright.chromium.launch(**kwargs)

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
                browser = self._launch(playwright)
                try:
                    context_kwargs: dict[str, Any] = {
                        "accept_downloads": True,
                        "viewport": {
                            "width": self._settings.viewport_width,
                            "height": self._settings.viewport_height,
                        },
                    }
                    if request.session_state_path and Path(request.session_state_path).exists():
                        context_kwargs["storage_state"] = str(request.session_state_path)
                    context = browser.new_context(**context_kwargs)
                    context.set_default_timeout(request.timeout_seconds * 1000)
                    try:
                        context.tracing.start(screenshots=True, snapshots=True)
                    except Exception:
                        pass  # tracing is a bonus, it must never stop the extraction if it fails
                    result = self._extract_with_context(context, request, started)
                    self._finish_tracing(context, request, result)
                    return result
                finally:
                    browser.close()
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
                file_path=target,
                original_name=name,
                size_bytes=len(body),
                duration_seconds=self._clock() - started,
            )
        except Exception:
            return None

    def _session_expired(self, page, filters: dict[str, Any]) -> bool:
        """Check for session-expiry signs after any goto: a visible login form, or a
        post-login element that is missing."""
        login_selector = filters.get("login_selector")
        logged_in_selector = filters.get("logged_in_selector")
        if login_selector and page.locator(login_selector).count() > 0:
            return True
        if logged_in_selector and page.locator(logged_in_selector).count() == 0:
            return True
        return False

    def _ensure_authenticated(
        self, context, page, request: ExtractionRequest, filters: dict[str, Any]
    ) -> str | None:
        """Use the saved session first, then one credential-backed login attempt.

        Tracing is stopped before any credential is entered. The password field is
        cleared in every path before tracing/evidence can resume.
        """
        if not self._session_expired(page, filters):
            return None
        credential_ref = filters.get("credential_ref")
        if not credential_ref:
            return (
                f"Session expired for {request.system}. "
                f"Run: python -m smartops login {request.system}"
            )
        if self._credential_store is None:
            return f"Secure credentials are unavailable for {request.system}."

        try:
            credential = self._credential_store.get(credential_ref)
        except Exception:
            return f"Secure credentials could not be read for {request.system}."
        if credential is None:
            return f"No secure credentials are stored for {request.system}."

        try:
            context.tracing.stop()
        except Exception:
            pass

        password_field = None
        try:
            login_url = filters.get("login_url")
            if login_url and not page.url.startswith(login_url):
                page.goto(login_url, wait_until="networkidle")
            page.locator(filters["username_selector"]).fill(credential.username)
            password_field = page.locator(filters["password_selector"])
            password_field.fill(credential.password)
            page.locator(filters["submit_selector"]).click()

            logged_in_selector = filters.get("logged_in_selector")
            login_selector = filters.get("login_selector")
            if logged_in_selector:
                page.wait_for_selector(logged_in_selector, state="visible")
            elif login_selector:
                page.wait_for_selector(login_selector, state="hidden")
            if self._session_expired(page, filters):
                return f"Automatic login was rejected for {request.system}."

            if request.session_state_path:
                Path(request.session_state_path).parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(request.session_state_path))
            return None
        except Exception:
            return f"Automatic login failed for {request.system}."
        finally:
            if password_field is not None:
                try:
                    password_field.fill("")
                except Exception:
                    pass
            try:
                context.tracing.start(screenshots=True, snapshots=True)
            except Exception:
                pass

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
                file_path=target,
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
