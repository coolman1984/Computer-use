"""محوّلات محلية لعقد NotifierPort: سجل محلي (JSONL)، Webhook اختياري،
وناقل يجمّع عدة قنوات معًا.

send() لا يرفع استثناء أبدًا؛ أي فشل يُعاد كـ False حتى لا يُسقط تنبيه
واحد فاشل بقية سلسلة الإنذار.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from ...core.clock import Clock, SystemClock, to_iso
from ...ports.notify import Alert

logger = logging.getLogger("smartops.notify")


def _alert_to_dict(alert: Alert) -> dict[str, Any]:
    return {
        "level": alert.level.value,
        "title": alert.title,
        "body": alert.body,
        "run_id": alert.run_id,
        "incident_id": alert.incident_id,
        "payload": alert.payload,
    }


class LocalLogNotifier:
    """يكتب كل تنبيه كسطر JSON واحد في ملف سجل محلي append-only."""

    def __init__(self, log_path: Path | str, *, clock: Clock | None = None) -> None:
        self._log_path = Path(log_path)
        self._clock = clock or SystemClock()

    def send(self, alert: Alert) -> bool:
        record = {"sent_at": to_iso(self._clock.now()), **_alert_to_dict(alert)}
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str))
                handle.write("\n")
            return True
        except OSError:
            logger.exception("فشل كتابة تنبيه في السجل المحلي: %s", self._log_path)
            return False

    def read_all(self) -> list[dict[str, Any]]:
        """يقرأ كل التنبيهات المسجّلة بالترتيب. مفيد للاختبار وشاشة المراقبة."""
        if not self._log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records


class WebhookNotifier:
    """يرسل التنبيه كـ POST JSON لرابط Webhook، بمكتبات المعيار القياسي فقط."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json", **(headers or {})}

    def send(self, alert: Alert) -> bool:
        body = json.dumps(_alert_to_dict(alert), ensure_ascii=False).encode("utf-8")
        try:
            request = urllib.request.Request(
                self._url, data=body, headers=self._headers, method="POST"
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            logger.warning("Webhook رفض التنبيه (%s): %s", exc.code, self._url)
            return False
        except (urllib.error.URLError, OSError, ValueError):
            logger.exception("فشل الاتصال بـ Webhook: %s", self._url)
            return False


class CompositeNotifier:
    """يرسل عبر كل القنوات المُعطاة؛ ينجح لو نجحت قناة واحدة على الأقل."""

    def __init__(self, notifiers: Iterable[Any]) -> None:
        self._notifiers = list(notifiers)

    def send(self, alert: Alert) -> bool:
        succeeded = False
        for notifier in self._notifiers:
            try:
                if notifier.send(alert):
                    succeeded = True
            except Exception:
                logger.exception("قناة إنذار رفعت استثناء غير متوقع: %r", notifier)
        return succeeded
