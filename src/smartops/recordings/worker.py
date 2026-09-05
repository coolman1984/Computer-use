"""Headed Chrome capture worker. It never logs typed input, cookies, or response bodies."""
from __future__ import annotations
import json, threading, time
from pathlib import Path
from typing import Callable
from .redaction import redact_text, redact_url, safe_network_summary

class PlaywrightRecordingWorker:
    def __init__(self, recording_id: str, artifact_dir: Path, start_url: str, on_step: Callable[[dict], None], on_heartbeat: Callable[[], None], on_finished: Callable[[str | None], None]) -> None:
        self.recording_id, self.artifact_dir, self.start_url = recording_id, artifact_dir, start_url
        self.on_step, self.on_heartbeat, self.on_finished = on_step, on_heartbeat, on_finished
        self._stop, self._paused = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
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
                context = p.chromium.launch_persistent_context(str(self.artifact_dir / "profile"), channel="chrome", headless=False, accept_downloads=True)
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.pages[0] if context.pages else context.new_page()
                def req(request): network.append(safe_network_summary(request.method, request.url, request.resource_type))
                context.on("request", req)
                def download(download):
                    target = self.artifact_dir / "downloads" / (download.suggested_filename or "download")
                    download.save_as(str(target))
                    if target.exists() and target.stat().st_size > 0: self.on_step({"kind":"download", "download_ref": f"downloads/{target.name}", "page_url_redacted": redact_url(page.url), "page_title": redact_text(page.title())})
                page.on("download", download)
                page.add_init_script("""document.addEventListener('click', e => { const r=e.target.getBoundingClientRect(); const w=innerWidth||1,h=innerHeight||1; window.__smartops_click={x:(e.clientX/w),y:(e.clientY/h),tag:e.target.tagName,text:(e.target.innerText||'').slice(0,80),selector:e.target.id ? '#'+e.target.id : e.target.getAttribute('name') ? '[name=\"'+e.target.getAttribute('name')+'\"]' : ''}; }, true);""")
                page.goto(self.start_url, wait_until="domcontentloaded", timeout=30000)
                last = None
                while not self._stop.wait(1):
                    self.on_heartbeat()
                    if self._paused.is_set(): continue
                    click = page.evaluate("window.__smartops_click || null")
                    marker = json.dumps(click, sort_keys=True) if click else None
                    if click and marker != last:
                        last = marker
                        self.on_step({"kind":"click", "page_url_redacted":redact_url(page.url), "page_title":redact_text(page.title()), "selector":redact_text(click.get("selector", "")), "target_text_redacted":redact_text(click.get("text", "")), "x_ratio":click.get("x"), "y_ratio":click.get("y")})
                context.storage_state(path=str(self.artifact_dir / "session" / "storage-state.json"))
                context.tracing.stop(path=str(self.artifact_dir / "trace" / "trace.zip"))
                context.close()
            (self.artifact_dir / "network" / "sanitized-summary.json").write_text(json.dumps(network, ensure_ascii=False), encoding="utf-8")
            self.on_finished(None)
        except Exception as exc:
            self.on_finished(f"Recorder failed: {type(exc).__name__}")
