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

## D034 — One run, one output folder
Raw output was addressed by date (`raw/YYYY/MM/DD/<system>/<report>/`), so the
second run of a day wrote over the first one's file — same folder, same
server-suggested filename — while both database rows still pointed at that one
path. The earlier result was gone with nothing to show it had existed. The run
id is now a path segment. That makes a result impossible to overwrite, makes
"which run produced this file" answerable from the path alone, and — the part
that was not obvious — revives the duplicate-hash check, which excludes the
file's own path and so could never fire while two runs shared it.

## D035 — A file is identified by its content, not its name
A portal that has lost your session answers a download with an HTML page. It
arrives with the right filename, a healthy size, and a unique hash, so every
check passed and a login screen was filed as a valid report. The validator now
sniffs the leading bytes and rejects a web page whatever its extension, rejects
an empty file outright, and rejects an `.xlsx` that is not really a zip. A
`must_contain` rule covers the wrong-period case, which no structural check can
catch: a report exported for the wrong month is a perfectly valid file that
happens to be the wrong answer.

## D036 — A session file is not a session
`session_exists` only asked whether a file was on disk. A storage_state written
before a sign-in completed, or one whose cookies had since expired, passed —
so the platform let the user record and schedule against a system it could not
reach, and the failure surfaced overnight with nobody present. `session_is_usable`
requires at least one unexpired cookie or an origin carrying storage. Proving
the session opens a protected page stays with the connection test, which is the
one place already paying for a browser launch.

## D037 — Recording, testing and running share one session
The recorder used its own persistent browser profile, so a user signed in twice
and could record as a different account than the automation would later run as.
It now seeds from the system's saved session and writes back to it, which also
means a sign-in performed inside a recording serves every later run.

## D038 — A retry is a new attempt, not a resumed one
The retry button called `drive()` on the failed run. `execute()` returns
immediately for anything terminal, and FAILED is terminal, so the button did
nothing at all — silently, forever. Even had it re-entered, the engine skips
steps already recorded as succeeded, so it would have resumed a finished run
rather than repeating the work. `runner.retry()` creates a new run with the same
workflow and parameters and a `retry_of` link back. Every attempt keeps its own
record and its own files, which is what makes "it worked on the third try"
visible. `/start` still continues an unfinished run; conflating the two is what
hid the defect.

## D039 — Startup settles whatever a crash left behind
`due()` deliberately never returns RUNNING runs, so a second worker cannot grab
one mid-flight — but that also meant a run interrupted by a restart stayed
RUNNING forever: never finished, never failed, never picked up, and counted as
active on the overview. The lock lease distinguishes a live worker from a dead
one, and `RecoveryService` re-queues the dead ones (the engine is resumable, so
completed steps are not repeated), settles automations stranded mid-test from
their run's real outcome, and never marks anything succeeded on a guess. It runs
during `Services` construction, because starting the platform is the only
trigger a non-technical user will ever pull.

## D040 — The worker is supervised, and health means "working"
Nothing watched the background worker. If its thread ended, the server stayed
up, health still said ok, and every schedule stopped firing with no signal.
The loop now survives a failed cycle, records when it last completed one, and a
supervisor restarts it and recovers stranded work. `is_healthy()` is liveness of
the loop rather than of the thread, because a wedged thread reports the same
`is_running()` as a working one.

## D041 — Monitoring asks whether results are arriving
Every "is anything wrong" check rested on incidents and run status, which is
blind to the failure that matters most: silence. An automation that quietly
stops producing files raises no incident, because nothing failed and nothing
ran. `overdue_automations` compares each scheduled automation's last *validated*
file against its own schedule, with one period of slack, so a stopped scheduler
turns the monitoring stage red instead of leaving it green.

## D042 — One automation, one run at a time
The scheduler already refused to stack runs, but a second click on "Run it now"
did not. Two runs of one automation share a browser session and a target system
and can each half-download the same report. `ProcessManager.active_run` is now
the single answer to "is it running", used by both paths.

## D043 — A recorded step is a contract, not a click
A step now carries seven things: what the action was, which tab and frame it
happened in, how to find the element again (several ways, best first), what went
in, **what proves it worked**, where execution may resume, and whether repeating
it is safe. The old shape — kind, selector, x/y — could describe only clicking,
so a form the person filled in came back as two clicks with no idea what was
typed, and replaying it produced an empty form and a report for the wrong thing.
The old columns are kept and still populated, so recordings made before this
still load and still replay on the layer they can support.

## D044 — Nothing in the recorder may call Playwright from inside Playwright
Page bindings and download events are dispatched *during* the click that caused
them. Any Playwright call from that handler — a screenshot, a title, saving a
download — re-enters the sync API on a call that has not returned, and the whole
recorder deadlocks. Handlers now read only cached values (`page.url`,
`frame.url`) and queue the event; every protocol call happens on the worker's
own loop. This is why the first version hung on the very first key press.

## D045 — A typed value is committed once, in the order it was typed
`change` is the right event to record — one step with the finished value rather
than one per keystroke — but on a text input it only fires on blur, and someone
who types and presses Enter never blurs. The recorder holds the latest value and
commits it on whichever comes first: change, blur, another action being
recorded, or the recording ending. Committing before another action is what
keeps the steps in the order they really happened; remembering what was already
committed per element is what stops the same typing being recorded twice, which
made replay type it, type it again, and shift every step number after it.

## D046 — Success is a consequence, never a dispatched action
A click that lands on a dead button and a click that opens a report are
indistinguishable until the page says which. Every step names its own evidence —
an element appeared or vanished, a value took, a tab opened, a file started
downloading, the page moved — and a step that cannot prove itself fails the run
at that step, naming it. A step with no recorded evidence blocks review and must
be given observable evidence or recorded again; it is never approved as a guess.

## D047 — Downloads are collected at the context, and counted
`expect_download` armed around one action could only ever return one file, so a
task exporting a summary and its detail lost the second silently. A context-level
listener catches every file from any action and any tab. The plan records how
many the recording produced, and a run that brings back fewer fails: a partial
result reported as success is how a missing detail file goes unnoticed for a
month. Two files with the same suggested name no longer overwrite each other.

## D048 — A step is retried only when repeating it is harmless
Typing a value again lands on the same state; pressing Enter on a form or
clicking a download does not, and repeating those can double-file a request. The
recording marks which is which, and an unsafe step gets exactly one attempt
whatever the plan says — failing a run costs less than submitting twice.

## D049 — A secret is a reference in the recording and a value only at run time
The recorder identifies a sensitive field by what the page declares (input type,
autocomplete), records that something must be typed there and which system's
credential it comes from, and never the value. The credential is fetched during
the run, used, and kept out of step results, events and error messages. Its
success check is "the field is no longer empty" — never a comparison that would
need the value.

## D050 — A plan starts where its recording started
The plan was handed the system's sign-in URL, so every replay began somewhere the
recorded steps do not exist and step one failed as "the element is no longer on
the page" — reporting a change to the site that had never happened. The first
captured page URL is the start; the system URL is only the fallback for a
recording that never reached a real page.

## D051 — Review edits facts; it never invents a recording
The review screen shows every action's page, tab, frame, ordered selectors,
non-secret input, success evidence, timeout, checkpoint, and retry safety. It may
edit only selectors, non-secret inputs, evidence, timeout, and retry limits.
Action type and browser scope stay immutable. A secret is never returned or
accepted as a literal, and an unsafe action cannot be relabelled safe; either
case requires a new recording or an updated credential.

## D052 — `file_paths` is the result contract
Extraction and replay hand the workflow a list even when one file arrives.
Registration, validation, UI history, and Parquet history process every member.
Recorded tasks also enforce their expected count at the workflow boundary, so a
different browser adapter cannot turn a partial result into success. `file_path`
and `file_id` remain output aliases only for old callers.

## D053 — Development switches need a separate explicit permission
Headless human recording, disabling the embedded worker, invoking internal
workflows directly, and legacy YAML scheduling are refused unless
`safety.allow_development_features` is explicitly true. These checks live in
the service/API/scheduler boundaries, not in hidden buttons.

## D054 — Interrupted unsafe work stops for review
A failed or stranded replay containing an action that is not safe to repeat is
never retried automatically. Because the browser replay is one engine step, a
crash cannot prove whether that external effect happened before state was saved.
The truthful recovery is therefore a failed run requiring human review, while a
fully repeatable replay can still be re-queued.
