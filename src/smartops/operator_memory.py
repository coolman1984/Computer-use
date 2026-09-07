"""Small, private memory for operating the one SmartOps download flow.

Browser structure is deliberately *not* written here.  Only an operator-confirmed
problem/cause/solution/verification record is durable.  The JSONL file is the
audit history; the Markdown view is regenerated after every append so the local
operator skill always has one short, current memory to read.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .core.clock import Clock, to_iso
from .core.ids import new_id


class OperatorMemory:
    def __init__(self, logs_dir: Path, clock: Clock) -> None:
        self.path = logs_dir / "operator-learning.jsonl"
        self.summary_path = logs_dir / "operator-memory.md"
        self.clock = clock
        self._lock = threading.Lock()

    def append(
        self,
        *,
        problem: str,
        cause: str,
        solution: str,
        verification: str,
        system_key: str = "",
    ) -> dict[str, Any]:
        entry = {
            "id": new_id("learn"),
            "createdAt": to_iso(self.clock.now()),
            "systemKey": system_key.strip(),
            "problem": problem.strip(),
            "cause": cause.strip(),
            "solution": solution.strip(),
            "verification": verification.strip(),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._refresh_summary_unlocked()
        return entry

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return self._list_unlocked(limit=limit)

    def _list_unlocked(self, *, limit: int) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                entries.append(value)
        return list(reversed(entries[-limit:]))

    def _refresh_summary_unlocked(self) -> None:
        entries = list(reversed(self._list_unlocked(limit=50)))
        lines = [
            "# SmartOps operator memory",
            "",
            "Auto-generated from confirmed local learnings. Do not add guesses here.",
            "",
        ]
        if not entries:
            lines.append("No confirmed learnings yet.")
        for entry in entries:
            scope = f" [{entry.get('systemKey')}]" if entry.get("systemKey") else ""
            lines.extend(
                [
                    f"## {entry.get('createdAt', '')}{scope}",
                    "",
                    f"- Problem: {entry.get('problem', '')}",
                    f"- Confirmed cause: {entry.get('cause', '')}",
                    f"- Solution: {entry.get('solution', '')}",
                    f"- Verification: {entry.get('verification', '')}",
                    "",
                ]
            )
        temporary = self.summary_path.with_suffix(".tmp")
        temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temporary.replace(self.summary_path)
