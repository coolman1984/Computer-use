"""محوّل Playwright لعقد BrowserPort: طبقة الشبكة أولًا ثم DOM (D004، القاعدة الذهبية).

بقية سلم الاستخراج (إصلاح ذاتي، رؤية، سطح مكتب) مؤجّلة لمراحل لاحقة.

ملاحظة معمارية: عقد BrowserPort الحالي (ExtractionRequest) لا يحمل بعد حقل
"نقطة دخول" عامًا لكل نظام، لذلك تُمرَّر معلومات الملاحة (الرابط، محدد
التنزيل...) عبر request.filters إلى أن تُبنى ملفات تعريف الأنظمة (S-03)
وتُحسَم صيغة الحقل النهائية على مستوى العقد.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright

from ...config import BrowserSettings
from ...domain.enums import ExtractionLayer
from ...ports.browser import ExtractionRequest, ExtractionResult


class PlaywrightBrowserAdapter:
    """ينفّذ طلب استخراج واحد: يجرّب الشبكة إن أمكن، ثم DOM."""

    def __init__(
        self,
        settings: BrowserSettings,
        *,
        executable_path: str | None = None,
        clock: Any = None,
    ) -> None:
        self._settings = settings
        self._executable_path = executable_path
        self._clock = clock or time.time
        # آخر دليل فشل لكل (نظام, تقرير) — يُستخدم من capture_evidence.
        self._last_evidence: dict[str, dict[str, Any]] = {}

    def _launch(self, playwright: Playwright):
        kwargs: dict[str, Any] = {"headless": self._settings.headless}
        if self._executable_path:
            kwargs["executable_path"] = self._executable_path
        return playwright.chromium.launch(**kwargs)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        started = self._clock()
        Path(request.destination_dir).mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                browser = self._launch(playwright)
                try:
                    context = browser.new_context(
                        accept_downloads=True,
                        viewport={
                            "width": self._settings.viewport_width,
                            "height": self._settings.viewport_height,
                        },
                    )
                    context.set_default_timeout(request.timeout_seconds * 1000)
                    return self._extract_with_context(context, request, started)
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            return self._failure(request, ExtractionLayer.DOM, f"انتهت المهلة: {exc}", started)
        except Exception as exc:  # لا نُسقط العملية بصمت على أي خطأ غير متوقع
            return self._failure(request, ExtractionLayer.DOM, f"فشل غير متوقع: {exc}", started)

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
                finally:
                    page.close()
            network_result = self._try_network(context, request, direct_url, started)
            if network_result is not None:
                return network_result
            # فشلت طبقة الشبكة، ننزل لطبقة DOM إن كانت مسموحة.

        if ExtractionLayer.DOM not in request.allowed_layers:
            return self._failure(
                request, ExtractionLayer.NETWORK, "طبقة الشبكة فشلت ولا توجد طبقة DOM مسموحة", started
            )
        return self._extract_via_dom(context, request, filters, started)

    def _try_network(
        self, context, request: ExtractionRequest, direct_url: str, started: float
    ) -> ExtractionResult | None:
        try:
            response = context.request.get(direct_url)
            if not response.ok:
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

    def _extract_via_dom(
        self, context, request: ExtractionRequest, filters: dict[str, Any], started: float
    ) -> ExtractionResult:
        entry_url = filters.get("url")
        if not entry_url:
            return self._failure(request, ExtractionLayer.DOM, "لا يوجد رابط دخول (filters['url'])", started)

        download_selector = filters.get("download_selector")
        if not download_selector:
            return self._failure(
                request, ExtractionLayer.DOM, "لا يوجد محدد للتنزيل (filters['download_selector'])", started
            )

        page = context.new_page()
        try:
            page.goto(entry_url, wait_until="networkidle")
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
            self._capture_failure_evidence(page, request, f"انتهت المهلة أثناء DOM: {exc}")
            return self._failure(request, ExtractionLayer.DOM, f"انتهت المهلة أثناء DOM: {exc}", started)
        except Exception as exc:
            self._capture_failure_evidence(page, request, str(exc))
            return self._failure(request, ExtractionLayer.DOM, f"فشل تفاعل DOM: {exc}", started)
        finally:
            page.close()

    def _capture_failure_evidence(self, page, request: ExtractionRequest, message: str) -> None:
        key = f"{request.system}:{request.report}"
        evidence: dict[str, Any] = {"message": message, "url": page.url}
        try:
            screenshot = page.screenshot(full_page=True)
            evidence["screenshot_base64"] = base64.b64encode(screenshot).decode("ascii")
        except Exception:
            pass
        self._last_evidence[key] = evidence

    def _failure(
        self, request: ExtractionRequest, layer: ExtractionLayer, message: str, started: float
    ) -> ExtractionResult:
        key = f"{request.system}:{request.report}"
        return ExtractionResult(
            ok=False,
            layer_used=layer,
            message=message,
            duration_seconds=self._clock() - started,
            evidence=self._last_evidence.get(key, {}),
        )

    def capture_evidence(self, run_id: str) -> dict[str, Any]:
        """يعيد آخر دليل فشل مسجّل.

        ملاحظة: extract() لا يستقبل run_id حاليًا، فلا يمكن ربط الدليل
        بتشغيل بعينه بدقة إلا بتوسيع العقد لاحقًا؛ حاليًا نعيد آخر دليل
        عام مع تمرير run_id للسياق فقط.
        """
        if not self._last_evidence:
            return {"run_id": run_id}
        last_key = next(reversed(self._last_evidence))
        return {"run_id": run_id, **self._last_evidence[last_key]}
