"""S-08 tests: running AI agents as subprocesses (fully fake).

Every agent here is a fake Python process controlled entirely by the test
itself — no real invocation of Codex or Claude Code CLI ever happens. Every
test uses AgentMode.ANALYZE only, enforcing the package rule: Execute or
Experiment mode must never be enabled here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from smartops.adapters.agents.cli_runner import AgentSafetyViolation, CliAgentRunner
from smartops.adapters.agents.commands import default_command_builder
from smartops.domain.enums import AgentMode
from smartops.ports.agents import AgentRequest


def _request(**overrides) -> AgentRequest:
    defaults = dict(reason="Diagnose a test incident", mode=AgentMode.ANALYZE, timeout_seconds=5.0)
    defaults.update(overrides)
    return AgentRequest(**defaults)


def _script_runner(script: str, *extra_args: str):
    def build(request: AgentRequest) -> list[str]:
        return [sys.executable, "-c", script, *extra_args]

    return build


SUCCESS_SCRIPT = """
import json
print("Analyzing...")
print("Reading logs...")
print(json.dumps({
    "ok": True,
    "summary": "No obvious problems in this incident",
    "changed_files": [],
    "tests_passed": None,
    "tokens_in": 120,
    "tokens_out": 340,
    "should_escalate": False,
}))
"""


def test_successful_run_parses_summary_and_tokens() -> None:
    runner = CliAgentRunner(_script_runner(SUCCESS_SCRIPT))
    response = runner.run(_request())

    assert response.ok is True
    assert response.summary == "No obvious problems in this incident"
    assert response.tokens_in == 120
    assert response.tokens_out == 340
    assert response.changed_files == []
    assert response.should_escalate is False
    assert "Analyzing" in response.raw_output


def test_output_is_streamed_live_via_on_output() -> None:
    seen: list[str] = []
    runner = CliAgentRunner(_script_runner(SUCCESS_SCRIPT), on_output=seen.append)
    runner.run(_request())

    joined = "".join(seen)
    assert "Analyzing" in joined
    assert "Reading logs" in joined


TIMEOUT_SCRIPT = """
import time
print("Work started...")
time.sleep(5)
print("This message will never arrive in time")
"""


def test_timeout_kills_process_and_reports_clearly() -> None:
    runner = CliAgentRunner(_script_runner(TIMEOUT_SCRIPT))
    started = time.monotonic()
    response = runner.run(_request(timeout_seconds=0.3))
    elapsed = time.monotonic() - started

    assert response.ok is False
    assert response.should_escalate is True
    assert "Timed out" in response.summary
    assert elapsed < 2.0  # did not wait out the full five seconds


CRASH_SCRIPT = """
import sys
print("Unexpected crash during analysis")
sys.exit(1)
"""


def test_crash_without_json_summary_falls_back_to_last_line() -> None:
    runner = CliAgentRunner(_script_runner(CRASH_SCRIPT))
    response = runner.run(_request())

    assert response.ok is False
    assert response.summary == "Unexpected crash during analysis"
    assert response.tokens_in == 0 and response.tokens_out == 0


CLAIMS_FILE_CHANGE_SCRIPT = """
import json
print(json.dumps({
    "ok": True,
    "summary": "I modified a file",
    "changed_files": ["evil.py"],
    "tokens_in": 5,
    "tokens_out": 5,
}))
"""


def test_analyze_mode_rejects_response_claiming_file_changes() -> None:
    runner = CliAgentRunner(_script_runner(CLAIMS_FILE_CHANGE_SCRIPT))

    with pytest.raises(AgentSafetyViolation, match="Analyze"):
        runner.run(_request(mode=AgentMode.ANALYZE))


RESPECTS_ANALYZE_MODE_SCRIPT = """
import json
import os
import sys

target_dir = sys.argv[1]
if os.environ.get("SMARTOPS_AGENT_MODE") != "analyze":
    with open(os.path.join(target_dir, "should_not_exist.txt"), "w", encoding="utf-8") as f:
        f.write("If this file exists, analyze-only mode was violated")

print(json.dumps({
    "ok": True,
    "summary": "Analysis only, I changed no file",
    "changed_files": [],
    "tokens_in": 10,
    "tokens_out": 20,
}))
"""


def test_analyze_mode_writes_no_files_to_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    runner = CliAgentRunner(_script_runner(RESPECTS_ANALYZE_MODE_SCRIPT, str(workspace)))
    response = runner.run(_request(mode=AgentMode.ANALYZE))

    assert response.ok is True
    assert response.changed_files == []
    assert list(workspace.iterdir()) == []  # the real proof: no file was written


def test_unstartable_command_returns_clear_failure() -> None:
    runner = CliAgentRunner(lambda request: ["/no/such/executable/at/all"])
    response = runner.run(_request())

    assert response.ok is False
    assert "Could not launch the agent" in response.summary


def test_codex_builder_is_ephemeral_noninteractive_and_read_only() -> None:
    command = default_command_builder("codex")(_request(reason="Guide this recording"))

    assert command[:2] == ["codex", "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
