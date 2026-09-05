"""واجهة سطر أوامر لتشغيل SmartOps: تسجيل دخول، جمع تقرير يدوي، عامل خلفي،
وخادم HTTP. المستخدم النهائي غير التقني يستخدم الويب آب؛ هذا الملف للمشغّل
اللي بيجهّز المنصة (تسجيل الدخول لأول مرة، تشغيلها كخدمة).
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .core.errors import SmartOpsError
from .sessions import capture_login, session_age_hours


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smartops", description="مركز تشغيل SmartOps")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="فحص شامل للإعدادات والمجلدات والجلسات")
    sub.add_parser("systems", help="عرض الأنظمة والتقارير المعرّفة")

    login = sub.add_parser("login", help="تسجيل دخول يدوي لنظام وحفظ الجلسة")
    login.add_argument("system", help="مفتاح النظام كما في تعريفه")

    collect = sub.add_parser("collect", help="جمع تقرير واحد الآن وطباعة النتيجة")
    collect.add_argument("system")
    collect.add_argument("report")

    sub.add_parser("work", help="تشغيل العامل الخلفي + الجدولة (Ctrl-C للإيقاف)")
    sub.add_parser("serve", help="تشغيل خادم HTTP (uvicorn)")
    sub.add_parser("recordings-backup", help="نسخة احتياطية خاصة من SQLite والتسجيلات")
    sub.add_parser("recordings-recover", help="تسوية التسجيلات المتقطعة بعد إعادة التشغيل")
    sub.add_parser("recordings-purge", help="حذف دائم للتسجيلات المنتهية حسب retention الصريح")

    return parser


def _build_services():
    from .services import Services

    return Services()


def _cmd_doctor(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        settings = services.settings
        print(f"مصدر الإعدادات: {settings.source or '(افتراضي، بلا ملف)'}")
        print(f"البيئة: {settings.app.environment}")
        print()
        dirs = {
            "sqlite_path": settings.storage.sqlite_path.parent,
            "raw_data_dir": settings.storage.raw_data_dir,
            "incidents_dir": settings.storage.incidents_dir,
            "logs_dir": settings.storage.logs_dir,
            "history_dir": settings.storage.history_dir,
            "sessions_dir": settings.storage.sessions_dir,
            "systems_dir": settings.storage.systems_dir,
            "recordings_dir": settings.storage.recordings_dir,
            "recordings_backup_dir": settings.storage.recordings_backup_dir,
        }
        for label, path in dirs.items():
            exists = path.exists()
            writable = exists and _is_writable(path)
            status = "موجود وقابل للكتابة" if writable else ("موجود لكن غير قابل للكتابة" if exists else "غير موجود")
            print(f"  {label}: {path} — {status}")

        print()
        recorder = services.recording_recovery.health()
        print(f"مسجل المتصفح: {recorder['status']} — عمال نشطون: {recorder['active_workers']}")
        print(f"سياسة الحذف الدائم: {settings.storage.recordings_retention_days or 'معطلة'} يوم، مفعّل: {settings.safety.allow_recording_purge}")
        print()
        systems = services.systems.list()
        print(f"الأنظمة المحمّلة: {len(systems)}")
        for system in systems:
            if system.auth.mode not in ("session", "unattended"):
                session_status = "بلا مصادقة (لا يحتاج جلسة)"
            else:
                age = session_age_hours(settings.storage.sessions_dir, system.key)
                session_status = (
                    "لا توجد جلسة محفوظة" if age is None else f"عمر الجلسة: {age:.1f} ساعة"
                )
                if system.auth.mode == "unattended":
                    try:
                        session_status += "؛ بيانات الدخول الآمنة: " + ("موجودة" if services.credentials.get(system.auth.credential_ref or system.key) else "غير موجودة")
                    except Exception:
                        session_status += "؛ بيانات الدخول الآمنة: غير متاحة"
            print(f"  - {system.key} ({system.auth.mode}): {session_status}")

        print()
        agent = services.agent_runner
        print(f"وكيل الذكاء الاصطناعي: {'مفعّل (read_only)' if agent is not None else 'مطفأ'}")
        return 0
    finally:
        services.close()


def _is_writable(path) -> bool:
    probe = path / ".smartops_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _cmd_systems(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        systems = services.systems.list()
        if not systems:
            print("لا توجد أنظمة معرّفة. راجع SMARTOPS_SYSTEMS_DIR أو config/systems.")
            return 0
        for system in systems:
            print(f"{system.key} — {system.name} (مصادقة: {system.auth.mode})")
            for report in system.reports:
                schedule = report.schedule
                if schedule.daily_at:
                    schedule_desc = f"يوميًا الساعة {schedule.daily_at}"
                elif schedule.every_seconds:
                    schedule_desc = f"كل {schedule.every_seconds:.0f} ثانية"
                else:
                    schedule_desc = "بلا جدولة"
                print(f"    - {report.key}: {report.title} ({schedule_desc})")
        return 0
    finally:
        services.close()


def _cmd_login(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        system = services.systems.get(args.system)
        if system.auth.mode == "unattended":
            print("هذا النظام يستخدم تسجيل الدخول الليلي الآمن. احفظ username/password من صفحة Credentials.")
            return 1
        if system.auth.mode != "session":
            print(f"النظام {args.system} وضع مصادقته '{system.auth.mode}' — لا يحتاج تسجيل دخول.")
            return 1
        path = capture_login(
            system.key,
            system.auth.login_url,
            sessions_dir=services.settings.storage.sessions_dir,
            browser_settings=services.settings.browser,
            logged_in_selector=system.auth.logged_in_selector,
        )
        print(f"تم حفظ الجلسة في: {path}")
        return 0
    except SmartOpsError as exc:
        print(f"خطأ: {exc.message}")
        if exc.details:
            print(f"  التفاصيل: {exc.details}")
        return 1
    finally:
        services.close()


def _cmd_collect(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        params = services.systems.run_params(args.system, args.report)
        run = services.runner.create_run("collect.report", params=params)
        run = services.runner.drive(run.id)
        print(f"الحالة: {run.status.value}")
        if run.error_message:
            print(f"الخطأ: {run.error_message}")
        files = services.files.list(run_id=run.id)
        for f in files:
            print(f"الملف: {f.path} (حالة التحقق: {f.validation_status.value})")
        return 0 if run.status.value == "succeeded" else 1
    except SmartOpsError as exc:
        print(f"خطأ: {exc.message}")
        if exc.details:
            print(f"  التفاصيل: {exc.details}")
        return 1
    finally:
        services.close()


def _cmd_work(args: argparse.Namespace) -> int:
    from .worker import Worker

    services = _build_services()
    worker = Worker(services, scheduler=services.scheduler)
    print("العامل الخلفي شغّال. اضغط Ctrl-C للإيقاف.")
    worker.start()
    try:
        while worker.is_running():
            worker.join(timeout=1.0)
    except KeyboardInterrupt:
        print("جاري الإيقاف...")
    finally:
        worker.stop()
        worker.join(timeout=10)
        services.close()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    services = _build_services()
    try:
        app = create_app(services)
        uvicorn.run(app, host=services.settings.app.host, port=services.settings.app.port)
        return 0
    finally:
        services.close()


def _cmd_recordings_backup(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        print(f"تم إنشاء نسخة احتياطية خاصة: {services.recording_recovery.backup()}")
        return 0
    finally:
        services.close()


def _cmd_recordings_recover(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        print(f"تمت تسوية {services.recording_manager.recover()} تسجيلات متقطعة")
        return 0
    finally:
        services.close()


def _cmd_recordings_purge(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        print(f"حُذف نهائيًا {services.recording_recovery.purge_expired()} تسجيلات")
        return 0
    finally:
        services.close()


_HANDLERS = {
    "doctor": _cmd_doctor,
    "systems": _cmd_systems,
    "login": _cmd_login,
    "collect": _cmd_collect,
    "work": _cmd_work,
    "serve": _cmd_serve,
    "recordings-backup": _cmd_recordings_backup,
    "recordings-recover": _cmd_recordings_recover,
    "recordings-purge": _cmd_recordings_purge,
}


def _configure_console_output() -> None:
    """Keep Arabic CLI messages printable in legacy Windows PowerShell."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[args.command]
    try:
        return handler(args)
    except SmartOpsError as exc:
        print(f"خطأ: {exc.message}")
        if exc.details:
            print(f"  التفاصيل: {exc.details}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
