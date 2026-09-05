"""S-09 tests: alert channels — a local log, a webhook, and a composite that fans out across channels."""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from smartops.adapters.notify.local import CompositeNotifier, LocalLogNotifier, WebhookNotifier
from smartops.core.clock import FrozenClock
from smartops.domain.enums import AlertLevel
from smartops.ports.notify import Alert


def _alert(**overrides) -> Alert:
    defaults: dict = dict(
        level=AlertLevel.RED,
        title="Report download is late",
        body="The report did not arrive within its normal time",
        run_id="run_demo",
        incident_id="inc_demo",
        payload={"minutes_late": 12},
    )
    defaults.update(overrides)
    return Alert(**defaults)


# ---------- LocalLogNotifier ----------


def test_local_log_notifier_appends_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "alerts.jsonl"
    notifier = LocalLogNotifier(log_path, clock=FrozenClock())

    assert notifier.send(_alert()) is True
    assert notifier.send(_alert(title="Second alert")) is True

    records = notifier.read_all()
    assert len(records) == 2
    assert records[0]["title"] == "Report download is late"
    assert records[0]["level"] == "red"
    assert records[0]["payload"] == {"minutes_late": 12}
    assert records[1]["title"] == "Second alert"
    assert "sent_at" in records[0]


def test_local_log_notifier_creates_parent_directory(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "dir" / "alerts.jsonl"
    notifier = LocalLogNotifier(log_path)

    assert notifier.send(_alert()) is True
    assert log_path.exists()


def test_local_log_notifier_read_all_on_missing_file_is_empty(tmp_path: Path) -> None:
    notifier = LocalLogNotifier(tmp_path / "never_written.jsonl")
    assert notifier.read_all() == []


def test_local_log_notifier_returns_false_on_write_failure(tmp_path: Path) -> None:
    # We pass a path that is itself a directory, so writing to it as a file fails with OSError.
    directory_as_file = tmp_path / "not_a_file"
    directory_as_file.mkdir()
    notifier = LocalLogNotifier(directory_as_file)

    assert notifier.send(_alert()) is False


# ---------- WebhookNotifier ----------


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    received: list[dict] = []
    status_code = 200

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).received.append(json.loads(body.decode("utf-8")))
        self.send_response(type(self).status_code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:  # silences http.server's default request logging
        pass


@contextmanager
def _local_server(status_code: int = 200):
    _RecordingHandler.received = []
    _RecordingHandler.status_code = status_code
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_webhook_notifier_posts_alert_as_json() -> None:
    with _local_server(status_code=200) as base_url:
        notifier = WebhookNotifier(base_url)
        result = notifier.send(_alert())

    assert result is True
    assert len(_RecordingHandler.received) == 1
    payload = _RecordingHandler.received[0]
    assert payload["level"] == "red"
    assert payload["title"] == "Report download is late"
    assert payload["payload"] == {"minutes_late": 12}


def test_webhook_notifier_reports_failure_on_server_error() -> None:
    with _local_server(status_code=500) as base_url:
        notifier = WebhookNotifier(base_url)
        result = notifier.send(_alert())

    assert result is False


def test_webhook_notifier_reports_failure_when_unreachable() -> None:
    notifier = WebhookNotifier("http://127.0.0.1:1", timeout=1.0)
    assert notifier.send(_alert()) is False


def test_webhook_notifier_reports_failure_on_invalid_url() -> None:
    notifier = WebhookNotifier("not-a-valid-url")
    assert notifier.send(_alert()) is False


# ---------- CompositeNotifier ----------


class _FakeNotifier:
    def __init__(self, result: bool = True, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls = 0

    def send(self, alert: Alert) -> bool:
        self.calls += 1
        if self.raises:
            raise RuntimeError("Broken channel")
        return self.result


def test_composite_notifier_succeeds_if_any_channel_succeeds() -> None:
    failing = _FakeNotifier(result=False)
    succeeding = _FakeNotifier(result=True)
    composite = CompositeNotifier([failing, succeeding])

    assert composite.send(_alert()) is True
    assert failing.calls == 1 and succeeding.calls == 1


def test_composite_notifier_fails_if_all_channels_fail() -> None:
    composite = CompositeNotifier([_FakeNotifier(result=False), _FakeNotifier(result=False)])
    assert composite.send(_alert()) is False


def test_composite_notifier_survives_one_channel_raising() -> None:
    broken = _FakeNotifier(raises=True)
    working = _FakeNotifier(result=True)
    composite = CompositeNotifier([broken, working])

    assert composite.send(_alert()) is True
    assert working.calls == 1
