"""اختبارات S-08: تشغيل وكلاء الذكاء الاصطناعي كعمليات فرعية (وهمية بالكامل).

كل الوكلاء هنا عمليات Python وهمية يتحكم فيها الاختبار نفسه — بدون أي
استدعاء حقيقي لـ Codex أو Claude Code CLI. كل الاختبارات تستخدم
AgentMode.ANALYZE فقط، تنفيذًا لقاعدة الحزمة: ممنوع تفعيل وضع التنفيذ
أو التجربة هنا.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from smartops.adapters.agents.cli_runner import AgentSafetyViolation, CliAgentRunner
from smartops.domain.enums import AgentMode
from smartops.ports.agents import AgentRequest


def _request(**overrides) -> AgentRequest:
    defaults = dict(reason="تشخيص حادثة تجريبية", mode=AgentMode.ANALYZE, timeout_seconds=5.0)
    defaults.update(overrides)
    return AgentRequest(**defaults)


def _script_runner(script: str, *extra_args: str):
    def build(request: AgentRequest) -> list[str]:
        return [sys.executable, "-c", script, *extra_args]

    return build


SUCCESS_SCRIPT = """
import json
print("جارٍ التحليل...")
print("قراءة السجلات...")
print(json.dumps({
    "ok": True,
    "summary": "لا مشاكل واضحة في هذه الحادثة",
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
    assert response.summary == "لا مشاكل واضحة في هذه الحادثة"
    assert response.tokens_in == 120
    assert response.tokens_out == 340
    assert response.changed_files == []
    assert response.should_escalate is False
    assert "جارٍ التحليل" in response.raw_output


def test_output_is_streamed_live_via_on_output() -> None:
    seen: list[str] = []
    runner = CliAgentRunner(_script_runner(SUCCESS_SCRIPT), on_output=seen.append)
    runner.run(_request())

    joined = "".join(seen)
    assert "جارٍ التحليل" in joined
    assert "قراءة السجلات" in joined


TIMEOUT_SCRIPT = """
import time
print("بدأت الشغل...")
time.sleep(5)
print("لن تصل هذه الرسالة أبدًا في وقتها")
"""


def test_timeout_kills_process_and_reports_clearly() -> None:
    runner = CliAgentRunner(_script_runner(TIMEOUT_SCRIPT))
    started = time.monotonic()
    response = runner.run(_request(timeout_seconds=0.3))
    elapsed = time.monotonic() - started

    assert response.ok is False
    assert response.should_escalate is True
    assert "انتهت المهلة" in response.summary
    assert elapsed < 2.0  # ما استناش الخمس ثواني كاملة


CRASH_SCRIPT = """
import sys
print("انهيار غير متوقع أثناء التحليل")
sys.exit(1)
"""


def test_crash_without_json_summary_falls_back_to_last_line() -> None:
    runner = CliAgentRunner(_script_runner(CRASH_SCRIPT))
    response = runner.run(_request())

    assert response.ok is False
    assert response.summary == "انهيار غير متوقع أثناء التحليل"
    assert response.tokens_in == 0 and response.tokens_out == 0


CLAIMS_FILE_CHANGE_SCRIPT = """
import json
print(json.dumps({
    "ok": True,
    "summary": "عدّلت ملفًا",
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
        f.write("لو ظهر هذا الملف فده خرق لوضع التحليل فقط")

print(json.dumps({
    "ok": True,
    "summary": "تحليل فقط، لم أغيّر أي ملف",
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
    assert list(workspace.iterdir()) == []  # الإثبات الفعلي: لا ملف اتكتب


def test_unstartable_command_returns_clear_failure() -> None:
    runner = CliAgentRunner(lambda request: ["/no/such/executable/at/all"])
    response = runner.run(_request())

    assert response.ok is False
    assert "تعذّر تشغيل الوكيل" in response.summary
