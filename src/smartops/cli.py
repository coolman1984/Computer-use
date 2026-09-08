"""Command line interface for SmartOps.

Everything a normal user needs now lives in the web app — adding a system,
testing it, signing in, recording, approving, running and scheduling. Nothing
here is a required step of the journey any more; these commands exist for the
operator (diagnostics, running the platform as a service) and as a fallback
when the app itself will not start.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .adapters.browser.session import concurrency_warning_message
from .checks import extension_provisioning_status
from .core.errors import SmartOpsError
from .domain.enums import IncidentStatus
from .sessions import capture_login, session_age_hours


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smartops", description="SmartOps operations center")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Full check of settings, directories, and sessions")
    sub.add_parser("systems", help="List the defined systems and reports")

    login = sub.add_parser(
        "login",
        help="Sign in to a system and save the session (the Sign-in page does this too)",
    )
    login.add_argument("system", help="System key as written in its definition")

    collect = sub.add_parser("collect", help="Collect one report now and print the result")
    collect.add_argument("system")
    collect.add_argument("report")

    sub.add_parser(
        "work",
        help="Run only the background worker + scheduler. Not needed alongside 'serve', "
        "which already runs them.",
    )
    sub.add_parser(
        "serve", help="Run SmartOps: the web app, the background worker, and the scheduler"
    )
    brief = sub.add_parser(
        "brief",
        help="Where this deployment stands, in one page, for whoever works on it next",
    )
    brief.add_argument("--json", action="store_true", help="Print the brief as JSON")

    probe = sub.add_parser(
        "probe",
        help="Ask a screen how it can be automated, before spending a recording on it",
    )
    probe.add_argument("system", help="System key as written in its definition")
    probe.add_argument(
        "--url",
        default="",
        help="The exact screen to probe. Defaults to the system's first report URL.",
    )
    probe.add_argument(
        "--wait",
        type=int,
        default=0,
        help="Seconds to wait before probing, so you can navigate to the working screen first.",
    )
    probe.add_argument("--json", action="store_true", help="Print the full report as JSON")

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
        extension = extension_provisioning_status(settings.browser)
        print(
            f"SSO extension: {extension.status} — {extension.detail} "
            f"({extension.path or 'no persistent profile configured'})"
        )
        concurrency_warning = concurrency_warning_message(settings.browser)
        if concurrency_warning:
            print(f"WARNING: {concurrency_warning}")

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
        processes = services.processes.list(limit=500)
        approved = [p for p in processes if p.is_runnable]
        scheduled = [p for p in processes if p.is_scheduled]
        print(f"Automations: {len(processes)} total, {len(approved)} approved, {len(scheduled)} scheduled")
        for process in processes:
            print(f"  - {process.name} [{process.status.value}] {process.system_key}/{process.report_key}")

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
        if services.settings.safety.allow_development_features is not True:
            print(
                "Error: Direct collection bypasses recording approval and is disabled. "
                "Create, test, and approve a recorded process instead."
            )
            return 1
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


def _cmd_brief(args: argparse.Namespace) -> int:
    """Everything somebody starting work on this deployment needs, in one place.

    Not a new opinion about the project: it reads the journey the web app
    already computes, the elements the recordings already registered, and the
    incidents runs have already opened. All of that existed and was reachable
    only through the interface, so an assistant working in a terminal had to
    guess at the state of the system it was changing — and guessing is how the
    wrong thing gets fixed confidently.
    """
    import json

    from .journey import build_journey
    from .recordings.elements import ElementRepository

    services = _build_services()
    try:
        journey = build_journey(services)
        stages = journey.to_dict()["stages"]
        current = next((stage for stage in stages if stage["key"] == journey.current), None)

        systems = []
        for system in services.systems.list():
            path = services.recording_manager.system_elements_path(system.key)
            repository = ElementRepository(path).load() if path.exists() else ElementRepository()
            unresolved = [
                element for element in repository
                if element.last_check and not element.last_check.get("resolves")
            ]
            systems.append({
                "key": system.key,
                "elements": len(repository),
                "unresolved": [
                    {"reference": element.reference, "found": element.last_check.get("found")}
                    for element in unresolved
                ],
            })

        recording_now = [
            record.to_dict()["id"]
            for record in services.recordings.list(limit=20)
            if record.status.value in {"starting", "recording", "paused"}
        ]
        open_incidents = [
            {
                "id": incident.id,
                "title": incident.title,
                # Where the evidence is. Empty means the incident was opened
                # without any, which is itself worth knowing.
                "evidence": incident.pack_path or "",
            }
            for incident in services.incidents.list(status=IncidentStatus.OPEN, limit=10)
        ]

        report = {
            "stage": journey.current,
            "next_action": (current or {}).get("detail", ""),
            "blocked": bool((current or {}).get("blocked")),
            "stages": [
                {"key": stage["key"], "done": stage["done"], "blocked": stage["blocked"]}
                for stage in stages
            ],
            "systems": systems,
            "recording_now": recording_now,
            "open_incidents": open_incidents,
        }

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        done = sum(1 for stage in stages if stage["done"])
        print(f"Stage: {journey.current}  ({done} of {len(stages)} stages done)")
        if report["next_action"]:
            print(f"Next:  {report['next_action']}")
        if report["blocked"]:
            print("       This stage is blocked; the detail above says by what.")
        print()
        for system in systems:
            print(f"  {system['key']}: {system['elements']} known controls")
            for element in system["unresolved"]:
                found = element["found"]
                trouble = "nothing matched it" if found == 0 else f"{found} things matched it"
                print(f"    - {element['reference']}: {trouble}")
        if recording_now:
            print(f"\n  Recording in progress: {', '.join(recording_now)}")
        if open_incidents:
            print("\n  Open incidents:")
            for incident in open_incidents:
                where = incident["evidence"] or "(no evidence was collected)"
                print(f"    - {incident['title']}\n      {where}")
        return 0
    except SmartOpsError as exc:
        print(f"Error: {exc.message}")
        return 1
    finally:
        services.close()


def _cmd_probe(args: argparse.Namespace) -> int:
    """Open one screen in the configured browser and report what can automate it.

    Nothing is clicked, typed, downloaded or saved. This exists because every
    failed attempt so far started by recording a screen and finding out
    afterwards that nothing on it could be identified or checked. Run it with
    the real screen open and it answers that first.
    """
    import json
    import time

    from .adapters.browser.session import open_browser_context
    from .recordings.probe import probe_page
    from .recordings.vision import PageVision
    from .sessions import session_path

    services = _build_services()
    try:
        system = services.systems.get(args.system)
        url = args.url or next((report.url for report in system.reports if report.url), "")
        if not url:
            print(f"System {args.system} has no report URL; pass --url with the screen to probe.")
            return 1

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            session = open_browser_context(
                playwright,
                services.settings.browser,
                # Headed: the point is to probe the screen the operator is
                # looking at, on the profile the automation would really use.
                headless=False,
                accept_downloads=False,
                storage_state_path=session_path(
                    services.settings.storage.sessions_dir, system.key
                ),
            )
            try:
                page = session.context.pages[0] if session.context.pages else session.context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if args.wait:
                    print(
                        f"Navigate to the screen you want probed. Probing in {args.wait} seconds…"
                    )
                    time.sleep(args.wait)
                report = probe_page(page, PageVision(services.settings.storage.logs_dir))
            finally:
                session.close()

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        print(f"Screen: {report['title'] or '(no title)'}")
        print(f"Address: {report['url']}")
        print()
        for sensor in report["sensors"]:
            print(f"  {sensor['sensor']:<14} {sensor['status']:<8} {sensor['detail']}")
        print()
        print("  Identify steps by:")
        for item in report["identity_ladder"]:
            mark = "available" if item["available"] else "not available"
            print(f"    - {item['strategy']:<24} {mark:<14} ({item['buys']})")
        print()
        print("  Prove a step worked by:")
        for item in report["evidence_ladder"]:
            mark = "available" if item["available"] else "not available"
            print(
                f"    - {item['proof']:<20} {mark:<14} {item['applies_to']:<30} ({item['buys']})"
            )
        print()
        print(report["verdict"])
        # A screen with no usable identity cannot carry a recording, and saying
        # so through the exit code lets this be used as a gate rather than read.
        return 0 if report["recommended_identity"] else 2
    except SmartOpsError as exc:
        print(f"Error: {exc.message}")
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
        # create_app starts the worker and scheduler with the server (see its
        # lifespan), so this one command is the whole platform.
        app = create_app(services)
        print(
            f"SmartOps is starting on http://{services.settings.app.host}:{services.settings.app.port}/app/index.html"
        )
        print("Scheduled automations run automatically while this is up.")
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
    "brief": _cmd_brief,
    "probe": _cmd_probe,
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
