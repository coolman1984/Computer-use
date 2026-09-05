# Architectural Decision Log

## D001 — Python is the core
Reason: strong integration with files, the browser, local processes, analysis, tests, and AI agents.

## D002 — Playwright is the primary browser engine
Reason: reliable tab, session, download, and trace management, with background execution.

## D003 — We do not rely on DOM alone
Reason: some systems may be canvas-drawn or unreadable as normal elements. So a vision fallback exists.

## D004 — Network before UI
If the report can be obtained from an authorized network request using the
same user session, that is better than simulating dozens of clicks.

## D005 — No absolute coordinates
Because screen resolutions differ. We use semantic elements or relative coordinates.

## D006 — SQLite for operations, DuckDB/Parquet for analytics
We separate the operational log from the analytical data history.

## D007 — The web app is the primary interface
The end user should never need to touch code files or a terminal.

## D008 — Codex and Claude are background workers
They are run by the Agent Manager with explicit permissions, context, and logging.

## D009 — Escalation is gradual
A fixed solution, then a cheaper agent, then a stronger model, then human intervention.

## D010 — Self-healing is test-first
No patch is deployed just because an agent suggested it. A sandbox, tests, and rollback are required.

## D011 — Camoufox is not the system's foundation
It can be tried for special cases, but the core does not depend on it.

## D012 — The public repository carries no sensitive internal context
Any complete internal material waits for a private repository or a secure knowledge store.

## D013 — State is saved step by step
Every step is saved with its state and output, so resuming starts from the
last success instead of restarting from zero.

## D014 — Error classification precedes retry
Every error type has a different retry policy. A permanent error is never retried, and a transient one is retried with exponential backoff.

## D015 — An optimistic lock at the run level
Prevents two workers from executing the same run, with a validity timeout that prevents an eternal lock.

## D016 — The event log is ordered by a sequence number
Ordering does not rely on time alone, so events within the same fraction of a second never get mixed up.

## D017 — Models are split by decision difficulty
The structure and contracts target a strong model, while execution sits
inside a contract ready for a cheaper model. Details in `docs/EXECUTION_PLAN.md`.

## D018 — Default wiring: everything local and safe runs automatically
The file validator, browser engine, local alert log, analytical archive, and
system-definition registry are wired up by default in `Services` with no
extra configuration — so a real `collect.report` run with no fake adapters
works on the first try. None of them touches the network or an external
process except when actually invoked from a run step.

## D019 — The AI agent is off by default, and only read_only is supported now
Enabling it is an operational and security decision for the operator
(`agents.codex/claude.enabled`), and any `mode` other than `read_only` is
rejected with an explicit configuration error at `Services` construction
time, instead of silently enabling broader permission. Experiment and
Execute wait for a sandbox, testing, and human approval (D009, D010) before
they are actually built.

## D020 — The session is captured manually once, then reused
The platform never sees a password; the human signs in in a visible browser
(`python -m smartops login <system>`) and `storage_state` is saved outside
the repository in `storage.sessions_dir`. A session that expires during an
automated run is detected via `logged_in_selector`/`login_selector` and
raised as `AuthError` plus an incident with a message telling the operator
exactly what command to run.

## D021 — Evidence is written to disk, keyed by run_id
Screenshots and Playwright traces never enter the event log or SQLite
(bloat and potential data leakage), and are written to
`incidents_dir/evidence/<run_id>/` instead. `capture_evidence`'s internal key
is `run_id`, so evidence from parallel runs on the same system and report never mixes.

## D022 — Scheduling is built from the system definitions, not a new table
No new database schema; `Scheduler.tick()` computes each (system, report)
pair's due-ness from its last recorded run in the existing `runs` table, and
respects any unfinished run for the same pair so it is never duplicated.

## D023 — Real definitions live outside the repository
`storage.systems_dir` can be overridden via `SMARTOPS_SYSTEMS_DIR` to point
at a folder outside the repository, which puts into practice what D012
mandates: no sensitive internal details in the public repository.

## D024 — The Recording Center saves its index in SQLite and its evidence in a private space
Only the recording status and redacted steps appear in the UI; the raw HAR,
trace, storage state, and cookies never leave `recordings_dir` and have no API endpoint.

## D025 — Direct Chrome is an exception confined to the recording worker
The recording worker uses `Playwright channel="chrome"` and a private
profile because it needs a direct connection to the DOM, network,
downloads, and trace. The mandatory launcher provides no CDP endpoint or
dedicated profile; details and operational limits are in `RECORDING_OPERATIONS.md`.

## D026 — A recording becomes a Process, and a Process is the only runnable automation
A recording's captured steps are converted into an executable *plan*, and that
plan is owned by a new first-class object: the Process. Everything runnable and
schedulable is a Process, so "recorded once" and "runs every night" are two
points in one object's life rather than two disconnected features. Previously
the conversion produced a JSON draft nothing consumed, which meant a recording
could never become an automation at all.

## D027 — A plan only ever claims a layer the replay engine can execute
`build_plan` labels a step `dom` (a stable selector), `visual` (a click stored
as a fraction of the viewport, never absolute screen coordinates), or `manual`.
There is deliberately no `network` layer for replay: nothing can execute one, and
a plan that named it would describe a capability the platform does not have. A
`manual` step is reported honestly and blocks the review gate, so the failure
surfaces during review instead of deep inside a browser weeks later.

## D028 — Approval requires a passing test, and only approval unlocks running and scheduling
`ProcessManager` enforces DRAFT → TESTED → APPROVED. A test is an ordinary run
through the same engine, validator and incident path, so "it passed" means
exactly "it produced a valid file". Editing an automation drops it back to
DRAFT, because what was proven to work must remain the thing that would run. The
scheduler's repository query only ever sees approved processes, so an untested
automation cannot fire even if something upstream went wrong.

## D029 — The journey is computed and enforced on the server
`journey.py` is the single source of truth for the eleven stages. The web app
renders it rather than inventing its own idea of progress, and the API enforces
the same gates (409 naming the blocking stage and the page that fixes it).
Hiding a button in the UI is a suggestion; a server-side gate is a rule, and the
two can never disagree.

## D030 — The server, the worker and the scheduler start as one process
`create_app`'s lifespan starts the background worker and scheduler. Previously
`serve` ran only the API, so a schedule fired only if someone separately ran
`python -m smartops work` — the platform's central promise silently depended on
a second terminal. `SMARTOPS_DISABLE_WORKER=1` opts out for tests and tooling,
and endpoints that queue work fall back to running inline when no worker exists,
so a run is never queued into nothing.

## D031 — Systems are edited in the app, validated by the loader, and reloaded live
The Systems page writes the same YAML the loader reads, through the same
`parse_system_profile` validation, then reloads the registry in place. One source
of truth, two ways in, and no restart — which is what moved step one of the
journey inside the product.

## D032 — Every failure reaches the user as what happened, what to do, and one button
`guidance.py` translates an error class (or a blocked stage) into three fields
the UI renders identically everywhere. A blocked stage is deliberately worded as
"an earlier step is not finished", not as a fault, so the user is not sent
hunting for a problem that does not exist.

## D033 — The browser to drive is configurable
`browser.executable_path` (or `SMARTOPS_BROWSER_PATH`) points every launch site —
extraction, replay, connection test, sign-in, and the recorder — at a specific
browser. Without it, a machine that ran only the launcher and never
`playwright install` fails on every browser action with a raw Playwright message.
A missing browser is now detected and reported as its own actionable case.
