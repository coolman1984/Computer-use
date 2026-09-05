"""Build a default launch command for a CLI agent (Codex or Claude Code).

Deliberately simple: <executable> -p "<reason + optional JSON context>".
We assume no specific output format beyond what CliAgentRunner understands
(an optional trailing JSON line), and this has not been tested against an
actual real CLI invocation in the current development environment — review
it and try it manually against the CLI version you have installed before
relying on it in production.
"""

from __future__ import annotations

import json

from ...ports.agents import AgentRequest


def default_command_builder(executable: str):
    """Build a simple command_builder that invokes executable in non-interactive mode."""

    def build(request: AgentRequest) -> list[str]:
        prompt = request.reason
        if request.context:
            prompt = f"{prompt}\n\nAdditional context (JSON):\n{json.dumps(request.context, ensure_ascii=False)}"
        return [executable, "-p", prompt]

    return build
