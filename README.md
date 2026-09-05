# SmartOps — Intelligent Automation & Operations Control Center

A local platform that learns a task by watching you do it once, then repeats it
on its own: collecting reports from web systems, checking the files it produced,
running on a schedule, and telling you plainly when something breaks.

> ⚠️ This repository is public. Do not put company data, internal URLs, secrets,
> passwords, real files, or private architecture details in it.

## The one journey

Everything in SmartOps is one path, and each step depends on the one before it.
The platform enforces that order — it will not let you schedule something that
has never been tested — and the app always shows you the single next step.

| # | Step | What it is for | Done when |
| --- | --- | --- | --- |
| 1 | Add the system | Name the site the work happens on | It appears in **Systems** |
| 2 | Test the connection | Prove the address opens from this machine | The test says it opened |
| 3 | Sign in | Save a session so later runs never hit a login wall | Status is **Signed in** |
| 4 | Record the task | Do the job once; the platform watches | The recording is finished |
| 5 | Review the recording | Check the captured steps can be repeated | The review says it can be repeated |
| 6 | Test the automation | Run it once, for real, against the real system | The test passes |
| 7 | Approve it | Your go-ahead for it to run unattended | Status is **Approved** |
| 8 | Run it | Produce the first real result on demand | The run succeeds |
| 9 | Check the result | A download is not the same as a correct file | The file is **Valid** |
| 10 | Schedule it | Make it happen without you | It shows a schedule |
| 11 | Watch and fix | Know when something breaks, and what to do | Nothing needs attention |

**All eleven happen inside the app.** No terminal command and no hand-edited
file is required at any point.

## Running it

### Windows, one click

Double-click `START.cmd`. It checks Python, installs what is missing, starts
SmartOps, and opens it in your browser. Logs live outside the repository in
`%LOCALAPPDATA%\SmartOps\launcher`.

One command starts the whole platform — the web app, the background worker, and
the scheduler together — so a schedule you set really does fire. There is no
second process to remember.

### Anywhere else

```bash
pip install -e ".[dev]"
python -m smartops serve       # the app, the worker and the scheduler
```

Then open http://127.0.0.1:8765/app/index.html and follow step 1.

SmartOps drives a real browser. It uses Google Chrome if it is installed;
otherwise install Playwright's browser once (`playwright install chromium`) or
point SmartOps at any Chromium-based browser you already have:

```bash
set SMARTOPS_BROWSER_PATH=C:\Path\To\chrome.exe
```

### Where your data lives

Put your real system definitions outside this public repository:

```bash
set SMARTOPS_SYSTEMS_DIR=C:\smartops-private\systems
```

You never have to write those files by hand — the **Systems** page creates and
edits them for you — but they are plain YAML, so you can if you prefer. Either
way the change takes effect immediately, with no restart.

## Two ways to sign in

- **Session (recommended, and the only one that works with SSO or MFA).** You
  press *Sign in* in the app, a real browser window opens, you sign in there,
  and only the resulting session is saved. The platform never sees your
  password.
- **Username and password.** For plain login forms only. You save the credential
  once in the app; it is stored in Windows Credential Manager under your own
  account, never in YAML, the database, the logs, or a recording. See
  `docs/UNATTENDED_LOGIN.md`.

## How a recording becomes an automation

This is the heart of the platform.

1. You record the task once, in a real browser window.
2. The captured clicks become a **plan** — an ordered list of steps, each pinned
   to a stable name on the page where possible, and to a relative position on
   screen where not. Never to absolute screen coordinates.
3. The plan is **reviewed**: if a step could not be captured well enough to
   repeat, the platform says so and asks you to record again, rather than
   letting you build an automation that would fail later.
4. A good plan becomes an **automation** — the thing that gets tested, approved,
   run and scheduled.

An automation and a report defined in YAML end up in exactly the same place: a
file in the raw data centre, validated by the same rules, logged as the same
kind of run, and archived the same way.

## What it does when something breaks

Every failure is classified and shown as three things: what happened, what to
do, and one button that goes to the page where it gets fixed. An expired session
says "sign in again" and links to Sign-in; a moved button says "record it again"
and links to Recordings. Failures also open an issue automatically with the
evidence — screenshots and traces — kept on disk, not in the database.

## For the operator

`python -m smartops` still exists for diagnostics and for running SmartOps as a
service. Nothing here is a required step of the journey.

```
doctor              settings, folders, sessions, automations, agent status
systems             list what is defined
login <system>      sign in from the terminal (the app does this too)
collect <sys> <rpt> collect one YAML-defined report now
serve               the app + worker + scheduler
work                only the worker + scheduler (not needed alongside serve)
recordings-backup   private backup of the database and recordings
recordings-recover  settle interrupted recordings after a restart
recordings-purge    permanent delete, per the explicit retention setting
```

## Technical foundation

Python · FastAPI · Playwright · SQLite · DuckDB · Parquet · WebSocket

## Read in order

1. `docs/PROJECT_CONTEXT.md`
2. `docs/MASTER_PLAN.md`
3. `docs/ARCHITECTURE.md`
4. `docs/BROWSER_EXTRACTION_ENGINE.md`
5. `docs/AI_AGENT_ORCHESTRATION.md`
6. `docs/OBSERVABILITY_SELF_HEALING.md`
7. `AGENTS.md`

## Build status

The journey is complete and tested end to end: a browser test walks all eleven
stages in one session, and the replay engine has been driven against a real
website — clicking through, downloading a file, validating it, and then running
again on its own from a schedule with nobody present.

**Not yet run against a real production system.** Everything is proven against
local sites and controlled fakes. The first run against a real corporate portal
is the next step, and whatever it surfaces is the real entry point into the full
early-warning and self-healing phases (P5, P6). See `docs/EXECUTION_PLAN.md`.
