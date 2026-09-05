"""Adapter that runs AI agents as subprocesses (Codex CLI / Claude Code CLI),
with live output streaming, a timeout, token accounting, and strict
enforcement of AgentMode.ANALYZE: read and analyze only, never modify a file.

The agent prints a final JSON line on its standard output summarizing the
result (tokens, changed files, whether tests passed, whether escalation is
advised). Every other line is treated as ordinary live output, streamed only
through on_output.
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
    """Raised because the agent claimed to modify files while in Analyze mode — never permitted."""


def _extract_last_json(lines: list[str]) -> dict[str, Any]:
    """Search from the last line backward for the first valid JSON line (starting with '{')."""
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
    """Implements AgentRunnerPort: runs the agent as a subprocess and builds an AgentResponse."""

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
            # The agent's output is a text/JSON protocol and may contain
            # non-ASCII text. On Windows the subprocess's default encoding
            # may be cp1252, which can make the agent itself fail before it
            # ever prints its result. Force UTF-8 on both ends.
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
            return AgentResponse(ok=False, summary=f"Could not launch the agent: {exc}")

        lines: list[str] = []

        def _reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                if self._on_output is not None:
                    try:
                        self._on_output(line)
                    except Exception:
                        pass  # a streaming listener must never take down the agent run

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
                summary=f"Timed out ({request.timeout_seconds:.0f} seconds) before the agent finished its work",
                raw_output=raw_output,
                should_escalate=True,
            )

        summary = _extract_last_json(lines)
        response = self._build_response(process.returncode, summary, raw_output)

        if request.mode is AgentMode.ANALYZE and response.changed_files:
            raise AgentSafetyViolation(
                "The agent claimed to modify files while in analyze-only mode (Analyze) — never permitted",
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
