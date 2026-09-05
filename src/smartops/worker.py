"""Background worker: pulls due runs and executes them with bounded concurrency.

Instead of waiting for a manual call per run, the loop polls runs.due() and
dispatches each due run to WorkflowRunner.execute within a worker count
bounded by browser.max_concurrency. The actual lock (preventing the same run
from executing twice) already lives inside WorkflowRunner.execute via
runs.claim/release — the worker does not reinvent it, only respects it and
adds a concurrency cap within this one process.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger("smartops.worker")


class Worker:
    """Polls due runs on an interval and executes them in a bounded thread pool."""

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
        self._last_poll_at: float | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Run the loop on a separate background thread. No-op if already running."""
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="smartops-worker")
        self._thread.start()

    def stop(self) -> None:
        """Request a clean stop: no new polling, and in-flight runs finish normally."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_forever(self) -> None:
        """The polling loop, with one thread pool that lives for the loop's whole lifetime.

        A single failed poll must never end the loop. Before this guard, an
        unexpected error anywhere in a poll — a momentarily locked database, a
        malformed row — killed the thread, and the platform went on reporting
        itself healthy while every schedule silently stopped firing.
        """
        self._stop_event.clear()
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            while not self._stop_event.is_set():
                try:
                    self._poll_once(executor)
                except Exception:
                    logger.exception("A polling cycle failed; the worker keeps running")
                self._last_poll_at = time.time()
                self._stop_event.wait(self.poll_interval)

    def seconds_since_last_poll(self) -> float | None:
        """How long since the loop completed a cycle. None before the first one.

        This is what makes "automatic runs: on" mean something. A thread that is
        alive but wedged reports the same is_running() as a healthy one; only the
        time since it last got round the loop tells them apart.
        """
        if self._last_poll_at is None:
            return None
        return max(0.0, time.time() - self._last_poll_at)

    def is_healthy(self, *, tolerance: float = 30.0) -> bool:
        """Running, and actually getting round its loop."""
        if not self.is_running():
            return False
        since = self.seconds_since_last_poll()
        if since is None:
            return True  # started, first cycle not finished yet
        return since <= max(tolerance, self.poll_interval * 5)

    # ---------- polling ----------

    def poll_once(self) -> int:
        """One self-contained poll cycle: creates its own pool and waits for it to finish.

        Useful for testing and manual runs without a background loop.
        """
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            return self._poll_once(executor)

    def _poll_once(self, executor: ThreadPoolExecutor) -> int:
        if self.scheduler is not None:
            try:
                self.scheduler.tick()
            except Exception:
                logger.exception("Scheduler tick failed — the worker keeps polling regardless")

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

    # ---------- tracking in-flight work within this process ----------

    def _claim_slot(self, run_id: str) -> bool:
        with self._in_flight_lock:
            if run_id in self._in_flight or len(self._in_flight) >= self.max_concurrency:
                return False
            self._in_flight.add(run_id)
            return True

    def _release_slot(self, run_id: str) -> None:
        with self._in_flight_lock:
            self._in_flight.discard(run_id)

    def _settle_process(self, run: Any) -> None:
        """Feed a finished automation run back to the process that owns it.

        A test started from the web app finishes here, on the worker, not in the
        request that started it — so this is where "the test passed" becomes
        "this automation may now be approved". Without it a test would run,
        succeed, and leave the automation stuck at 'testing' forever.
        """
        process_id = (run.params or {}).get("process_id")
        if not process_id:
            return
        manager = getattr(self.services, "process_manager", None)
        if manager is None:
            return
        try:
            manager.settle_test(process_id, run.id)
        except Exception:
            logger.exception("Could not record the outcome of run %s on its automation", run.id)

    def _execute_one(self, run_id: str) -> None:
        try:
            run = self.services.runner.execute(run_id)
            self._settle_process(run)
            if self._on_run_done is not None:
                self._on_run_done(run)
        except Exception as exc:  # one failed run must not take down the whole worker
            logger.exception("Failed to execute run %s", run_id)
            if self._on_error is not None:
                self._on_error(run_id, exc)
        finally:
            self._release_slot(run_id)
