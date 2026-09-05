"""بناء أمر تشغيل افتراضي لوكيل CLI (Codex أو Claude Code).

بسيط ومتعمّد التبسيط: <executable> -p "<السبب + سياق JSON اختياري>".
لا نفترض صيغة مخرجات محددة غير ما يفهمه CliAgentRunner (سطر JSON أخير
اختياري)، ولم يُختبَر هذا ضد استدعاء فعلي لأي CLI حقيقي في بيئة التطوير
الحالية — راجعه وجرّبه يدويًا مقابل نسخة CLI المثبتة عندك قبل الاعتماد
عليه في الإنتاج.
"""

from __future__ import annotations

import json

from ...ports.agents import AgentRequest


def default_command_builder(executable: str):
    """يبني command_builder بسيط يستدعي executable في وضع غير تفاعلي."""

    def build(request: AgentRequest) -> list[str]:
        prompt = request.reason
        if request.context:
            prompt = f"{prompt}\n\nسياق إضافي (JSON):\n{json.dumps(request.context, ensure_ascii=False)}"
        return [executable, "-p", prompt]

    return build
