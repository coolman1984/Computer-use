"""Command line interface for SmartOps: sign-in, manual collection, the
background worker, and the HTTP server. Non-technical end users work in the
web app; this file is for the operator setting the platform up (first
sign-in, running it as a service).
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .core.errors import SmartOpsError
from .sessions import capture_login, session_age_hours


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smartops", description="SmartOps operations center")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Full check of settings, directories, and sessions")
    sub.add_parser("systems", help="List the defined systems and reports")

    login = sub.add_parser("login", help="Sign in to a system manually and save the session")
    login.add_argument("system", help="System key as written in its definition")

    collect = sub.add_parser("collect", help="Collect one report now and print the result")
    collect.add_argument("system")
    collect.add_argument("report")

    sub.add_parser("work", help="Run the background worker + scheduler (Ctrl-C to stop)")
    sub.add_parser("serve", help="Run the HTTP server (uvicorn)")
    sub.add_parser("recordings-backup", help="Private backup of SQLite and the recordings")
    sub.add_parser("recordings-recover", help="Settle interrupted recordings after a restart")
    sub.add_parser("recordings-purge", help="Permanently delete expired recordings per explicit retention")

    return parser


def _build_services():
    from .services import Services

    return Services()


def _cmd_doctor(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        settings = services.settings
        print(f"Settings source: {settings.source or '(default, no file)'}")
        print(f"Environment: {settings.app.environment}")
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
            status = "exists and writable" if writable else ("exists but not writable" if exists else "missing")
            print(f"  {label}: {path} — {status}")

        print()
        recorder = services.recording_recovery.health()
        print(f"Browser recorder: {recorder['status']} — active workers: {recorder['active_workers']}")
        print(f"Permanent delete policy: {settings.storage.recordings_retention_days or 'disabled'} days, enabled: {settings.safety.allow_recording_purge}")
        print()
        systems = services.systems.list()
        print(f"Systems loaded: {len(systems)}")
        for system in systems:
            if system.auth.mode not in ("session", "unattended"):
                session_status = "no authentication (no session needed)"
            else:
                age = session_age_hours(settings.storage.sessions_dir, system.key)
                session_status = (
                    "no saved session" if age is None else f"session age: {age:.1f} hours"
                )
                if system.auth.mode == "unattended":
                    try:
                        session_status += "; stored credential: " + ("present" if services.credentials.get(system.auth.credential_ref or system.key) else "missing")
                    except Exception:
                        session_status += "; stored credential: unavailable"
            print(f"  - {system.key} ({system.auth.mode}): {session_status}")

        print()
        agent = services.agent_runner
        print(f"AI agent: {'enabled (read_only)' if agent is not None else 'off'}")
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
            print("No systems defined. Check SMARTOPS_SYSTEMS_DIR or config/systems.")
            return 0
        for system in systems:
            print(f"{system.key} — {system.name} (auth: {system.auth.mode})")
            for report in system.reports:
                schedule = report.schedule
                if schedule.daily_at:
                    schedule_desc = f"daily at {schedule.daily_at}"
                elif schedule.every_seconds:
                    schedule_desc = f"every {schedule.every_seconds:.0f} seconds"
                else:
                    schedule_desc = "no schedule"
                print(f"    - {report.key}: {report.title} ({schedule_desc})")
        return 0
    finally:
        services.close()


def _cmd_login(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        system = services.systems.get(args.system)
        if system.auth.mode == "unattended":
            print("This system uses secure unattended sign-in. Save the username/password on the Sign-in page.")
            return 1
        if system.auth.mode != "session":
            print(f"System {args.system} has auth mode '{system.auth.mode}' — no sign-in needed.")
            return 1
        path = capture_login(
            system.key,
            system.auth.login_url,
            sessions_dir=services.settings.storage.sessions_dir,
            browser_settings=services.settings.browser,
            logged_in_selector=system.auth.logged_in_selector,
        )
        print(f"Session saved to: {path}")
        return 0
    except SmartOpsError as exc:
        print(f"Error: {exc.message}")
        if exc.details:
            print(f"  Details: {exc.details}")
        return 1
    finally:
        services.close()


def _cmd_collect(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        params = services.systems.run_params(args.system, args.report)
        run = services.runner.create_run("collect.report", params=params)
        run = services.runner.drive(run.id)
        print(f"Status: {run.status.value}")
        if run.error_message:
            print(f"Error: {run.error_message}")
        files = services.files.list(run_id=run.id)
        for f in files:
            print(f"File: {f.path} (validation: {f.validation_status.value})")
        return 0 if run.status.value == "succeeded" else 1
    except SmartOpsError as exc:
        print(f"Error: {exc.message}")
        if exc.details:
            print(f"  Details: {exc.details}")
        return 1
    finally:
        services.close()


def _cmd_work(args: argparse.Namespace) -> int:
    from .worker import Worker

    services = _build_services()
    worker = Worker(services, scheduler=services.scheduler)
    print("Background worker running. Press Ctrl-C to stop.")
    worker.start()
    try:
        while worker.is_running():
            worker.join(timeout=1.0)
    except KeyboardInterrupt:
        print("Stopping...")
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
        print(f"Private backup created: {services.recording_recovery.backup()}")
        return 0
    finally:
        services.close()


def _cmd_recordings_recover(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        print(f"Settled {services.recording_manager.recover()} interrupted recordings")
        return 0
    finally:
        services.close()


def _cmd_recordings_purge(args: argparse.Namespace) -> int:
    services = _build_services()
    try:
        print(f"Permanently deleted {services.recording_recovery.purge_expired()} recordings")
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
    """Keep CLI output printable in legacy Windows PowerShell code pages."""
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
        print(f"Error: {exc.message}")
        if exc.details:
            print(f"  Details: {exc.details}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
