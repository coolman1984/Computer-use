"""عقد محرك الاستخراج. الطبقات: شبكة ← DOM ← إصلاح ذاتي ← رؤية ← سطح مكتب.

أي تنفيذ فعلي (Playwright وغيره) يلتزم بهذا العقد فقط، فتبقى النواة مستقلة.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..domain.enums import ExtractionLayer


@dataclass(frozen=True)
class ExtractionRequest:
    system: str
    report: str
    destination_dir: Path
    period: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    allowed_layers: tuple[ExtractionLayer, ...] = (
        ExtractionLayer.NETWORK,
        ExtractionLayer.DOM,
        ExtractionLayer.SELF_HEALING,
    )
    timeout_seconds: float = 300.0


@dataclass
class ExtractionResult:
    ok: bool
    layer_used: ExtractionLayer
    file_path: Path | None = None
    original_name: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class BrowserPort(Protocol):
    """ينفّذ طلب استخراج واحد ويعيد الملف الناتج مع دليل ما حدث."""

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...

    def capture_evidence(self, run_id: str) -> dict[str, Any]:
        """لقطة شاشة/تتبع/شبكة عند الفشل، لبناء حزمة الحادثة."""
        ...
