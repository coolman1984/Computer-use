# SmartOps — Intelligent Automation & Operations Control Center

A unified local platform for collecting data from web systems, running business automations, monitoring processes and sites, keeping a full history log, and using AI agents for diagnosis, safe repair, and escalation.

> ⚠️ This repository is currently public. Do not put sensitive company data, internal URLs, secrets, passwords, real raw files, or private internal architecture details in it.

## Vision

```text
Systems and sites
    ↓
Extraction and download
    ↓
File quality validation
    ↓
Department automation
    ↓
Linking processes and data
    ↓
Monitoring + history + alerts
    ↓
AI diagnosis and repair
```

## Main components

- Adaptive multi-layer browser engine.
- Operations and workflow center.
- Raw file management and data history.
- Full event and incident log.
- Early warning and performance monitoring.
- Agent manager to run Codex CLI and Claude Code CLI.
- Smart escalation based on problem difficulty.
- Testing and rollback before deploying any fix.
- Local web interface with a side chat for control.

## Technical foundation

- Python
- FastAPI
- Playwright
- SQLite
- DuckDB
- Parquet
- WebSocket
- OpenTelemetry later

## Read in order

1. `docs/PROJECT_CONTEXT.md`
2. `docs/MASTER_PLAN.md`
3. `docs/ARCHITECTURE.md`
4. `docs/BROWSER_EXTRACTION_ENGINE.md`
5. `docs/AI_AGENT_ORCHESTRATION.md`
6. `docs/OBSERVABILITY_SELF_HEALING.md`
7. `docs/IMPLEMENTATION_ROADMAP.md`
8. `docs/EXECUTION_PLAN.md`
9. `docs/AGENT_TASK_PACKETS.md`
10. `AGENTS.md`

## First execution goal

A small pilot version:

- 3 systems
- 5 reports
- Automatic download
- File validation
- Full logging
- Monitoring dashboard
- Lateness alert
- Running Codex to analyze an incident

After proving stability, we expand the platform.

## Running locally

### One-click run on Windows

Double-click `START.cmd` at the project root. It checks Python and the
requirements, starts the SmartOps server exactly once, waits for `/health`
to succeed, then opens the operations center in Google Chrome. Run logs and
the PID file are kept outside the repository, in
`%LOCALAPPDATA%\SmartOps\launcher`.

For manual runs or development:

```bash
pip install -e ".[dev]"
pytest -q
uvicorn smartops.main:app --reload --port 8765   # or: python -m smartops serve
```

## Workflow order (the UI follows the same order)

Every step depends on the one before it, and the UI is ordered to match:

| # | Step | Where | Done when |
| --- | --- | --- | --- |
| 1 | Define the system (YAML outside the repo) | `SMARTOPS_SYSTEMS_DIR` then the **Systems** page | The system shows up in the list |
| 2 | Sign in to the system | `python -m smartops login <system>` or the **Sign-in** page | Status is **Connected** |
| 3 | Record the workflow once | The **Recordings** page | The recording is **Completed** and has a draft |
| 4 | Run collection | **Collect now** button in **Systems** | A file downloaded and passed validation |
| 5 | Review the results | **Runs** then **Files** | The file's status is Valid |
| 6 | Handle failures | **Incidents** | No open incidents |

The **Overview** page shows the first four steps with their real status, and
the first incomplete step is the one you should do next.

## First run (a real system)

```bash
pip install -e ".[dev]"
playwright install chromium
set SMARTOPS_SYSTEMS_DIR=C:\smartops-private\systems
python -m smartops doctor
python -m smartops login <system>
python -m smartops collect <system> <report>
python -m smartops work
```

`SMARTOPS_SYSTEMS_DIR` must point at a folder **outside this public
repository** (see D012 and D023) containing `*.yaml` files with your real
system definitions, following the pattern in
`config/systems/example.yaml`. `smartops login` opens a visible browser for
you to sign in manually once; the platform never sees your password (D020).

For unattended overnight runs with no one present, define the system
externally with `auth.mode: unattended` and only the username/password/submit
selectors, then open `/app/credentials.html` and save the credential once.
It is stored in Windows Credential Manager under your own Windows account;
it is never saved in YAML, SQLite, logs, or recordings. See
`docs/UNATTENDED_LOGIN.md`. This path assumes no MFA/CAPTCHA, and that the
machine, VPN, and worker all stay running overnight.

Current interface endpoints: `/health`, `/api/workflows`, `/api/runs`,
`/api/runs/{id}`, `/api/runs/{id}/events`, `/api/events`, `/api/incidents`,
`/api/files`, `/api/systems`, `/api/alerts`,
`/api/systems/{system}/{report}/collect`.

## Build status

The core is complete and tested locally: settings, a migrated SQLite
database, an event log, a resumable workflow engine with locking and
classified retry, automatic incident opening with a full evidence pack
(screenshots and traces on disk, no base64 in the database), local and
webhook notifications, live streaming over WebSocket, a Playwright adapter
with saved login sessions and session-expiry detection, a file validator,
system definitions from YAML (with authentication and scheduling), automatic
scheduling + a background worker with bounded concurrency, slowness alerting
with fixed thresholds, analytical Parquet/DuckDB archiving, an AI agent
runner in analyze-only mode, a command-line interface
(`python -m smartops`), and a web interface (`web/`, available at `/app`).

**Not yet run against a real production system.** Everything above is built
and tested locally with pages and mocks, but the first real run against a
real system has not happened yet — that is the actual next step, and any
problems it surfaces are the real entry point into the full early-warning
and self-healing phases (P5, P6). Details in `docs/EXECUTION_PLAN.md`,
`docs/AGENT_TASK_PACKETS.md`, and `docs/FINISH_PACKET_SONNET.md`.
