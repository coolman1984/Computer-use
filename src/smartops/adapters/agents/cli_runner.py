"""محوّل تشغيل وكلاء الذكاء الاصطناعي كعمليات فرعية (Codex CLI / Claude
Code CLI)، مع بث مخرجات حي، مهلة زمنية، وتسجيل توكنز، واحترام صارم
لوضع AgentMode.ANALYZE: قراءة وتحليل فقط، بلا أي تعديل ملفات.

الوكيل يطبع سطر JSON أخير على مخرجه القياسي يلخّص النتيجة (توكنز، ملفات
معدّلة، هل نجحت الاختبارات، هل يُنصح بالتصعيد). أي سطر آخر يُعامل كمخرج
حي عادي يُبث عبر on_output فقط.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from ...core.clock import Clock, SystemClock
from ...core.errors import PermanentError
from ...domain.enums import AgentMode
from ...ports.agents import AgentRequest, AgentResponse

CommandBuilder = Callable[[AgentRequest], list[str]]
OutputSink = Callable[[str], None]


class AgentSafetyViolation(PermanentError):
    """رُفعت لأن الوكيل ادّعى تعديل ملفات وهو في وضع Analyze — ممنوع إطلاقًا."""


def _extract_last_json(lines: list[str]) -> dict[str, Any]:
    """يبحث من آخر الأسطر للأول عن أول سطر JSON صالح (يبدأ بـ '{')."""
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


class CliAgentRunner:
    """ينفّذ AgentRunnerPort: يشغّل الوكيل كعملية فرعية ويبني AgentResponse."""

    def __init__(
        self,
        command_builder: CommandBuilder,
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        on_output: OutputSink | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._command_builder = command_builder
        self._cwd = Path(cwd) if cwd is not None else None
        self._env = env or {}
        self._on_output = on_output
        self._clock = clock or SystemClock()

    def run(self, request: AgentRequest) -> AgentResponse:
        command = self._command_builder(request)
        env = {
            **os.environ,
            **self._env,
            # مخرجات الوكيل بروتوكول نصي/JSON وقد تحتوي العربية. على Windows
            # الترميز الافتراضي للعملية الفرعية قد يكون cp1252، فيفشل الوكيل
            # نفسه قبل أن يطبع النتيجة. نفرض UTF-8 على الطرفين.
            "PYTHONIOENCODING": "utf-8",
            "SMARTOPS_AGENT_MODE": request.mode.value,
        }

        try:
            process = subprocess.Popen(
                command,
                cwd=str(self._cwd) if self._cwd is not None else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            return AgentResponse(ok=False, summary=f"تعذّر تشغيل الوكيل: {exc}")

        lines: list[str] = []

        def _reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                if self._on_output is not None:
                    try:
                        self._on_output(line)
                    except Exception:
                        pass  # مستمع البث لا يُسقط تشغيل الوكيل أبدًا

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        timed_out = False
        try:
            process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        reader_thread.join(timeout=5)

        raw_output = "".join(lines)
        if timed_out:
            return AgentResponse(
                ok=False,
                summary=f"انتهت المهلة الزمنية ({request.timeout_seconds:.0f} ثانية) قبل أن ينهي الوكيل عمله",
                raw_output=raw_output,
                should_escalate=True,
            )

        summary = _extract_last_json(lines)
        response = self._build_response(process.returncode, summary, raw_output)

        if request.mode is AgentMode.ANALYZE and response.changed_files:
            raise AgentSafetyViolation(
                "الوكيل ادّعى تعديل ملفات وهو في وضع التحليل فقط (Analyze) — غير مسموح إطلاقًا",
                details={"changed_files": response.changed_files, "reason": request.reason},
            )

        return response

    @staticmethod
    def _build_response(
        returncode: int, summary: dict[str, Any], raw_output: str
    ) -> AgentResponse:
        ok = summary.get("ok")
        if ok is None:
            ok = returncode == 0
        return AgentResponse(
            ok=bool(ok),
            summary=summary.get("summary", "") or (raw_output.strip().splitlines() or [""])[-1],
            changed_files=list(summary.get("changed_files", []) or []),
            tests_passed=summary.get("tests_passed"),
            tokens_in=int(summary.get("tokens_in", 0) or 0),
            tokens_out=int(summary.get("tokens_out", 0) or 0),
            should_escalate=bool(summary.get("should_escalate", False)),
            raw_output=raw_output,
        )
