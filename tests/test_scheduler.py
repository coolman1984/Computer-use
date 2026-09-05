"""اختبارات F-08: is_due النقي، وTick يخلق تشغيلات بلا تكرار أو تعليق."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartops.domain.enums import RunStatus
from smartops.scheduler import Scheduler, is_due
from smartops.workflows.profiles import ScheduleProfile

UTC = timezone.utc


# ---------- is_due: اختبارات نقية بلا أي I/O ----------


def test_every_seconds_due_when_never_run() -> None:
    schedule = ScheduleProfile(every_seconds=3600)
    assert is_due(schedule, None, datetime(2026, 1, 1, tzinfo=UTC)) is True


def test_every_seconds_not_due_before_interval() -> None:
    schedule = ScheduleProfile(every_seconds=3600)
    last = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    now = last + timedelta(minutes=30)
    assert is_due(schedule, last, now) is False


def test_every_seconds_due_after_interval() -> None:
    schedule = ScheduleProfile(every_seconds=3600)
    last = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    now = last + timedelta(hours=1, seconds=1)
    assert is_due(schedule, last, now) is True



# daily_at يُفسَّر بتوقيت الجهاز المحلي (astimezone()). نترك بايثون يربط
# كل تاريخ بالـ offset المحلي الصحيح لذلك اليوم؛ نسخ tzinfo من "اليوم" فقط
# يثبّت Offset التوقيت الصيفي وقد يغيّر الساعة في تاريخ شتوي على Windows.
def _local_datetime(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute).astimezone()


def test_daily_at_not_due_before_slot_today() -> None:
    schedule = ScheduleProfile(daily_at="08:00")
    now = _local_datetime(2026, 1, 1, 7)
    assert is_due(schedule, None, now) is False


def test_daily_at_due_at_or_after_slot_when_never_run() -> None:
    schedule = ScheduleProfile(daily_at="08:00")
    now = _local_datetime(2026, 1, 1, 8)
    assert is_due(schedule, None, now) is True


def test_daily_at_not_due_twice_same_day() -> None:
    schedule = ScheduleProfile(daily_at="08:00")
    last_run = _local_datetime(2026, 1, 1, 8, 5)
    now = _local_datetime(2026, 1, 1, 20)
    assert is_due(schedule, last_run, now) is False


def test_daily_at_due_again_next_day() -> None:
    schedule = ScheduleProfile(daily_at="08:00")
    last_run = _local_datetime(2026, 1, 1, 8, 5)
    now = _local_datetime(2026, 1, 2, 8)
    assert is_due(schedule, last_run, now) is True


def test_inactive_schedule_never_due() -> None:
    schedule = ScheduleProfile()
    assert is_due(schedule, None, datetime(2026, 1, 1, tzinfo=UTC)) is False


# ---------- Scheduler.tick: تكامل مع services حقيقية (ساعة مزيّفة) ----------

SYSTEM_YAML = """
key: erp_demo
name: نظام تجريبي
reports:
  - key: hourly_report
    title: تقرير كل ساعة
    url: "https://intranet.example.local/reports/hourly"
    download_selector: "#dl"
    schedule:
      every_seconds: 3600
  - key: no_schedule_report
    title: تقرير بلا جدولة
    url: "https://intranet.example.local/reports/none"
    download_selector: "#dl"
"""


@pytest.fixture
def scheduled_services(services, tmp_path: Path):
    from smartops.workflows.profiles import SystemRegistry

    (tmp_path / "erp.yaml").write_text(SYSTEM_YAML, encoding="utf-8")
    services.systems = SystemRegistry.load(tmp_path)
    return services


def test_tick_creates_run_for_due_pair(scheduled_services, clock) -> None:
    scheduler = Scheduler(scheduled_services, clock=clock)
    created = scheduler.tick()

    assert len(created) == 1
    assert created[0].params["system"] == "erp_demo"
    assert created[0].params["report"] == "hourly_report"


def test_tick_does_not_duplicate_immediately(scheduled_services, clock) -> None:
    scheduler = Scheduler(scheduled_services, clock=clock)
    scheduler.tick()
    second = scheduler.tick()
    assert second == []


def test_tick_skips_pair_with_non_terminal_run(scheduled_services, clock) -> None:
    scheduler = Scheduler(scheduled_services, clock=clock)
    first = scheduler.tick()
    assert len(first) == 1
    # التشغيل لسه queued (مفيش execute) — تِك تاني ميجبش حاجة.
    assert scheduled_services.runs.get(first[0].id).status is RunStatus.QUEUED
    assert scheduler.tick() == []


def test_tick_creates_again_after_run_finished_and_interval_passed(scheduled_services, clock) -> None:
    scheduler = Scheduler(scheduled_services, clock=clock)
    first = scheduler.tick()
    run = first[0]
    # نخلّص التشغيل يدويًا (بدل تنفيذه فعليًا) عشان نختبر منطق الجدولة بمعزل.
    run.status = RunStatus.SUCCEEDED
    scheduled_services.runs.update(run)

    clock.advance(3600 * 2)
    second = scheduler.tick()
    assert len(second) == 1


def test_broken_profile_does_not_stop_others(scheduled_services, clock, monkeypatch) -> None:
    scheduler = Scheduler(scheduled_services, clock=clock)

    original = scheduled_services.systems.run_params

    def _boom(system_key, report_key):
        if report_key == "hourly_report":
            raise RuntimeError("عطل مصطنع")
        return original(system_key, report_key)

    monkeypatch.setattr(scheduled_services.systems, "run_params", _boom)
    created = scheduler.tick()
    assert created == []  # الزوج التاني مالوش جدولة أصلًا، فمفيش تشغيلات، لكن مفيش استثناء طلع برا tick()
