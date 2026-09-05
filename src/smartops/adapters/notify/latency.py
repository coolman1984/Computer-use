"""تقييم بطء التنزيل مقابل عتبات ثابتة (F-07): إنذار بسيط بلا أي حالة أو
اتصال خارجي — نقطة البداية لمحرك قواعد الإنذار المبكر الكامل لاحقًا (P5).
"""

from __future__ import annotations

from ...domain.enums import AlertLevel


def evaluate_latency(
    duration_seconds: float,
    *,
    warn_after_seconds: float | None,
    critical_after_seconds: float | None,
) -> AlertLevel | None:
    """يعيد مستوى الإنذار المناسب، أو None لو التنزيل ضمن الطبيعي.

    الحرج يغلب التحذير لو العتبتان متجاوَزتان معًا. عتبة None تُتجاهل.
    """
    if critical_after_seconds is not None and duration_seconds >= critical_after_seconds:
        return AlertLevel.RED
    if warn_after_seconds is not None and duration_seconds >= warn_after_seconds:
        return AlertLevel.YELLOW
    return None
