"""عامل خلفي: يسحب التشغيلات المستحقة وينفّذها بالتوازي المحدود.

بدل انتظار نداء يدوي لكل تشغيل، الحلقة تستطلع runs.due() وترسل كل
تشغيل مستحق إلى WorkflowRunner.execute داخل عدد عمال محدود بـ
browser.max_concurrency. القفل الفعلي (منع تنفيذ نفس التشغيل مرتين)
موجود بالفعل داخل WorkflowRunner.execute عبر runs.claim/release، فالعامل
لا يعيد اختراعه؛ فقط يحترمه ويضيف حدًا للتوازي داخل نفس العملية.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger("smartops.worker")


class Worker:
    """يستطلع التشغيلات المستحقة على فترات وينفّذها في مجمّع خيوط محدود."""

    def __init__(
        self,
        services: Any,
        *,
        poll_interval: float = 1.0,
        max_concurrency: int | None = None,
        scheduler: Any | None = None,
        on_run_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.services = services
        self.poll_interval = poll_interval
        self.max_concurrency = max(1, max_concurrency or services.settings.browser.max_concurrency)
        self.scheduler = scheduler
        self._on_run_done = on_run_done
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._in_flight: set[str] = set()
        self._in_flight_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---------- دورة الحياة ----------

    def start(self) -> None:
        """يشغّل الحلقة في خيط خلفي منفصل. لا شيء لو كانت شغالة بالفعل."""
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="smartops-worker")
        self._thread.start()

    def stop(self) -> None:
        """طلب إيقاف نظيف: لا استطلاع جديد، والتشغيلات الجارية تكمل طبيعيًا."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_forever(self) -> None:
        """حلقة الاستطلاع، بمجمّع خيوط واحد يعيش طوال عمر الحلقة."""
        self._stop_event.clear()
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            while not self._stop_event.is_set():
                self._poll_once(executor)
                self._stop_event.wait(self.poll_interval)

    # ---------- الاستطلاع ----------

    def poll_once(self) -> int:
        """دورة استطلاع واحدة مكتفية بذاتها: تُنشئ مجمّعها وتنتظر اكتماله.

        مفيدة للاختبار والتشغيل اليدوي دون تشغيل حلقة خلفية.
        """
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            return self._poll_once(executor)

    def _poll_once(self, executor: ThreadPoolExecutor) -> int:
        if self.scheduler is not None:
            try:
                self.scheduler.tick()
            except Exception:
                logger.exception("فشل تِك الجدولة — العامل يكمل استطلاعه بلا توقف")

        with self._in_flight_lock:
            available_slots = self.max_concurrency - len(self._in_flight)
        if available_slots <= 0:
            return 0

        due_runs = self.services.runs.due(limit=available_slots)
        dispatched = 0
        for run in due_runs:
            if not self._claim_slot(run.id):
                continue
            dispatched += 1
            executor.submit(self._execute_one, run.id)
        return dispatched

    # ---------- تتبّع الشغل الجاري داخل نفس العملية ----------

    def _claim_slot(self, run_id: str) -> bool:
        with self._in_flight_lock:
            if run_id in self._in_flight or len(self._in_flight) >= self.max_concurrency:
                return False
            self._in_flight.add(run_id)
            return True

    def _release_slot(self, run_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight.discard(run_id)

    def _execute_one(self, run_id: str) -> None:
        try:
            run = self.services.runner.execute(run_id)
            if self._on_run_done is not None:
                self._on_run_done(run)
        except Exception as exc:  # تشغيل واحد فاشل لا يُسقط العامل كله
            logger.exception("فشل تنفيذ التشغيل %s", run_id)
            if self._on_error is not None:
                self._on_error(run_id, exc)
        finally:
            self._release_slot(run_id)
