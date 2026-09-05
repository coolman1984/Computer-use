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
