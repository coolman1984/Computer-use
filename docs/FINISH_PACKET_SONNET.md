# FINISH PACKET — Sonnet 5 Medium, one shot, non-stop

**Mission:** take SmartOps from "core built, never used" to "a non-technical user
can log into a real system once, and the platform collects and validates that
report on a schedule, every day, with alerts and incidents."

Every architectural decision in this packet is **already made**. Do not redesign
anything. Write the bodies, write the tests, stop when green.

**Definition of done for the whole packet:**

```bash
pytest -q
```

…is green (target ≈135 tests, up from 105), and every item F-01..F-12 below is
implemented. Do not stop between items to ask questions — the decisions are all
here. If something genuinely contradicts, prefer this document over older docs.

---

## Hard rules (unchanged from AGENT_TASK_PACKETS.md)

1. **Write code comments and docstrings in Arabic**, matching the existing
   codebase style exactly. This document is English only because it is a spec.
2. Local edits only — never regenerate a whole file to change a few lines.
3. Never commit real internal URLs, credentials, cookies, or session files.
4. Every new adapter goes under `src/smartops/adapters/<domain>/`.
5. Backward compatibility is mandatory: **all 105 existing tests must still
   pass untouched.** Every new field on an existing contract is optional with a
   safe default. If you find yourself editing an existing test to make it pass,
   stop — you broke a contract.
6. `sync_playwright()` must never be called from a thread that has a running
   asyncio event loop. FastAPI runs `def` (non-`async`) endpoints in a worker
   thread, so the current API is safe. Keep new endpoints `def`, not `async def`.

---

## F-01 — Config: sessions dir + external systems dir

**Why:** real system profiles can't live in a public repo, and login sessions
need a home outside Git.

**Edit** `src/smartops/config.py`:

Add to `StorageSettings`:
```python
sessions_dir: Path = Path("data/sessions")
systems_dir: Path = Path("config/systems")
```

In `load_settings`, read them from the `storage:` YAML section, with env
overrides that win over YAML:
- `SMARTOPS_SESSIONS_DIR` → `sessions_dir`
- `SMARTOPS_SYSTEMS_DIR` → `systems_dir`

In `ensure_directories`, also create `sessions_dir`. After creating it, attempt
`os.chmod(sessions_dir, 0o700)` inside a `try/except OSError: pass` (no-op on
Windows, meaningful on POSIX).

**Also edit** `.gitignore` — add explicit lines even though `/data/` covers them:
```
/data/sessions/
*.storage_state.json
```

**Also edit** `config/system.example.yaml` — add under `storage:`:
```yaml
  sessions_dir: data/sessions
  systems_dir: config/systems   # للتعريفات الحقيقية: استخدم SMARTOPS_SYSTEMS_DIR لمجلد خارج المستودع
```

**Test** `tests/test_config.py` (new file): defaults are correct; env overrides
win over YAML; `ensure_directories` creates `sessions_dir`.

---

## F-02 — Browser contract: session, evidence, auth signal

**Why:** the adapter currently opens a fresh anonymous browser every time, so
"reuse the user's authorized session" (D004) is impossible, and failure evidence
is keyed globally so concurrent runs overwrite each other.

**Edit** `src/smartops/ports/browser.py`. Add these fields, **all optional**:

```python
@dataclass(frozen=True)
class ExtractionRequest:
    # ... كل الحقول الحالية كما هي، ثم:
    run_id: str = ""
    session_state_path: Path | None = None
    evidence_dir: Path | None = None
```

```python
@dataclass
class ExtractionResult:
    # ... كل الحقول الحالية كما هي، ثم:
    auth_required: bool = False
```

Two new **filter keys** (documented in the module docstring, same convention as
the existing navigation keys):
- `logged_in_selector` — if given and NOT present after navigation → session expired.
- `login_selector` — if given and IS present after navigation → session expired.

Update the `BrowserPort` protocol docstring to mention `capture_evidence(run_id)`
now returns evidence for **that specific run**.

**No test file of its own** — F-03 covers it.

---

## F-03 — Playwright adapter: sessions, real evidence, auth detection

**Edit** `src/smartops/adapters/browser/playwright_engine.py`. Five changes:

**(a) Load the session.** In `extract`, when building the context:
```python
context_kwargs = {"accept_downloads": True, "viewport": {...}}
if request.session_state_path and Path(request.session_state_path).exists():
    context_kwargs["storage_state"] = str(request.session_state_path)
context = browser.new_context(**context_kwargs)
```

**(b) Tracing.** Immediately after creating the context:
```python
context.tracing.start(screenshots=True, snapshots=True)
```
On **failure** (any path that returns a non-ok result), stop tracing into
`<evidence_dir>/<run_key>-trace.zip`. On **success**, `context.tracing.stop()`
with no path (discards it). Wrap every tracing call in `try/except Exception:
pass` — tracing must never be the reason an extraction fails. If
`evidence_dir` is None, skip saving the trace but still stop tracing.

**(c) Evidence keyed by run.** Replace the `system:report` key with:
```python
def _evidence_key(self, request) -> str:
    return request.run_id or f"{request.system}:{request.report}"
```
`capture_evidence(run_id)` looks up `self._last_evidence.get(run_id)` first and
only falls back to "most recent" when that run has no entry. Returned dict
always includes `run_id`.

**(d) Screenshots to disk, not base64.** In `_capture_failure_evidence`, write
the PNG to `<evidence_dir>/<run_key>-screenshot.png` and record
`{"screenshot_path": str(path)}`. **Delete the `screenshot_base64` key and the
`base64` import entirely** — full-page screenshots must never enter the event
payload or SQLite. If `evidence_dir` is None, skip the screenshot (keep the
message and URL). Sanitise `run_key` for the filename with
`smartops.storage.paths.slug`.

**(e) Auth detection.** Add a helper called after every `page.goto(...)`:
```python
def _session_expired(self, page, filters) -> bool:
    login_selector = filters.get("login_selector")
    logged_in_selector = filters.get("logged_in_selector")
    if login_selector and page.locator(login_selector).count() > 0:
        return True
    if logged_in_selector and page.locator(logged_in_selector).count() == 0:
        return True
    return False
```
Call it in `_extract_via_dom` right after `page.goto`, before waiting for
`wait_selector`. If it returns True, return a failure result with
`auth_required=True` and message
`"الجلسة منتهية أو غير مسجّلة الدخول للنظام <system> — شغّل: python -m smartops login <system>"`.
Also capture failure evidence in that case.

In `_try_network`, treat an HTML response as a silent login redirect:
```python
content_type = (response.headers.get("content-type") or "").lower()
if "text/html" in content_type:
    return None   # على الأرجح تحويل لصفحة الدخول — انزل لطبقة DOM
```

`_failure` gains an `auth_required: bool = False` parameter it passes through.

**Test** `tests/test_browser_session_auth.py` (new, uses the same local
`http.server` pattern as `test_browser_adapter.py` — **no real sites**):
- a page containing `#login-form` with `login_selector="#login-form"` → result
  `ok is False`, `auth_required is True`, message mentions `login`.
- a page WITHOUT `#dashboard` with `logged_in_selector="#dashboard"` → same.
- a page WITH `#dashboard` and `logged_in_selector="#dashboard"` → downloads
  normally, `auth_required is False`.
- failure with `evidence_dir=tmp_path` → a `*-screenshot.png` file exists on
  disk, and the evidence dict contains **no** key named `screenshot_base64`.
- two requests with different `run_id`s fail; `capture_evidence(run_a)` and
  `capture_evidence(run_b)` return **different** URLs — proves the race is fixed.
- a `direct_download_url` that serves `text/html` falls through to the DOM layer.

---

## F-04 — Session manager and the one-time login flow

**Why:** this is the single blocker to using the app on a real system. The human
logs in **once, manually, in a visible browser**; the platform reuses that
session afterwards. The platform never sees or stores a password.

**Create** `src/smartops/sessions.py`:

```python
def session_path(sessions_dir: Path | str, system_key: str) -> Path
    # <sessions_dir>/<slug(system_key)>.json   — استخدم paths.slug

def session_exists(sessions_dir, system_key) -> bool

def session_age_hours(sessions_dir, system_key, *, now=None) -> float | None
    # None لو الملف غير موجود

def capture_login(
    system_key: str,
    login_url: str,
    *,
    sessions_dir: Path | str,
    browser_settings: BrowserSettings,
    logged_in_selector: str = "",
    executable_path: str | None = None,
    wait_for_enter: Callable[[], None] | None = None,
    timeout_seconds: float = 600.0,
) -> Path
```

`capture_login` behaviour — exactly this:
1. Launch chromium **headed** (`headless=False`, ignoring `browser_settings.headless`)
   with the configured viewport.
2. New context; if a session file already exists, load it as `storage_state` so
   the user may only need to refresh a partly-valid session.
3. `page.goto(login_url)`.
4. Print (Arabic) instructions to stdout telling the user to complete login in
   the opened window, then press Enter here.
5. Wait: if `logged_in_selector` is given, `page.wait_for_selector(...)` with
   `timeout_seconds`; otherwise call `wait_for_enter()` (default `input`).
   `wait_for_enter` is injectable **purely so tests can drive it** — never
   auto-confirm in production.
6. `context.storage_state(path=str(session_path(...)))`, creating parent dirs.
7. Attempt `os.chmod(path, 0o600)` in `try/except OSError: pass`.
8. Close browser, return the path.

Raise `ConfigurationError` if `login_url` is empty.

**Test** `tests/test_sessions.py`: `session_path` slugs correctly;
`session_exists`/`session_age_hours` behave on a temp dir (including the None
case); `capture_login` against a **local** login page using
`logged_in_selector` writes a valid JSON storage-state file containing a
`cookies` key. Guard the browser test with the same
`pytest.importorskip("playwright.sync_api")` + `_resolve_executable_path()`
helpers used in `test_browser_adapter.py`.

---

## F-05 — Profiles: auth, schedule, alert thresholds

**Edit** `src/smartops/workflows/profiles.py`.

**New dataclasses:**
```python
@dataclass(frozen=True)
class AuthProfile:
    mode: str = "none"              # none | session
    login_url: str = ""
    logged_in_selector: str = ""
    login_selector: str = ""

@dataclass(frozen=True)
class ScheduleProfile:
    daily_at: str = ""              # "HH:MM" بتوقيت الجهاز المحلي
    every_seconds: float | None = None
    enabled: bool = True

    @property
    def is_active(self) -> bool:    # True لو enabled و(daily_at أو every_seconds)
```

`SystemProfile` gains `auth: AuthProfile = field(default_factory=AuthProfile)`.
`ReportProfile` gains `schedule: ScheduleProfile = field(default_factory=ScheduleProfile)`.

**Validation:** if `auth.mode == "session"` then `login_url` is required →
`ConfigurationError`. Reject `auth.mode` values other than `none`/`session`.
Reject a `daily_at` that isn't `HH:MM` with 24h ranges. Reject
`every_seconds <= 0`. Setting both `daily_at` and `every_seconds` is a
`ConfigurationError` — pick one.

**`SystemProfile.to_run_params(report_key)` must now also emit:**
- into `filters`: `logged_in_selector` and `login_selector` from the system's
  `auth` (only when non-empty)
- top level: `normal_duration_seconds`, and `warn_after_seconds` /
  `critical_after_seconds` from the report's `alert` (only when not None)

**`SystemRegistry`** gains `iter_scheduled()` yielding `(system, report)` pairs
where `report.schedule.is_active` — the scheduler consumes this.

**Edit** `config/systems/example.yaml` to demonstrate the new blocks (keep the
fake `.example.local` URLs):
```yaml
auth:
  mode: session
  login_url: "https://intranet.example.local/login"
  logged_in_selector: "#user-menu"
  login_selector: "#login-form"
```
and on `daily_sales`:
```yaml
    schedule:
      daily_at: "08:00"
```
and on `weekly_summary`:
```yaml
    schedule:
      every_seconds: 3600
```

**Test** — extend `tests/test_profiles.py`: auth parses; `mode: session`
without `login_url` raises; bad `daily_at` raises; both-schedule-kinds raises;
`to_run_params` carries the selectors into `filters` and the thresholds to top
level; `iter_scheduled` returns only active ones.

---

## F-06 — Wire sessions + evidence + systems dir into the collect workflow

**Edit** `src/smartops/workflows/builtin.py`, function `download_report`. It
currently builds `ExtractionRequest` with no run id, session, or evidence dir.

Build the request with:
```python
run_id=ctx.run_id,
session_state_path=session_path(settings.storage.sessions_dir, system),
evidence_dir=Path(settings.storage.incidents_dir) / "evidence" / slug(ctx.run_id),
```
Create the evidence dir lazily — pass the path; the adapter mkdirs it only when
it actually writes evidence. (Do not create an empty dir for every successful run.)

**Auth failure:** after `browser.extract(...)`, before the generic failure path:
```python
if result.auth_required:
    raise AuthError(
        result.message or "الجلسة منتهية",
        details={"system": system, "needs_login": True,
                 "command": f"python -m smartops login {system}"},
    )
```
`AuthError` retries once (5s) per the existing policy, then fails the run and
opens an incident — that is the intended behaviour, don't change `retry.py`.

**Edit** `src/smartops/services.py`: `SystemRegistry.load()` must become
`SystemRegistry.load(self.settings.storage.systems_dir)`.

**Test** — extend `tests/test_collect_workflow.py` with a fake browser that
returns `auth_required=True`: run fails, `run.error_class == "auth"`, and an
incident was opened. Keep the existing three tests untouched.

---

## F-07 — Delay alert (the missing pilot deliverable)

**Why:** README and MASTER_PLAN §26 both list "one delay alert" as pilot scope.
`normal_duration_seconds` and `alert.*` are currently parsed and ignored.

**Create** `src/smartops/adapters/notify/latency.py`:
```python
def evaluate_latency(
    duration_seconds: float,
    *,
    warn_after_seconds: float | None,
    critical_after_seconds: float | None,
) -> AlertLevel | None
```
Pure function, no I/O. Returns `AlertLevel.RED` when over critical,
`AlertLevel.YELLOW` when over warn, else `None`. Critical wins. `None`
thresholds are ignored. Keep it dependency-free so it is trivially testable —
the P5 rules engine will grow from here.

**Edit** `download_report` in `builtin.py`: it already knows the duration
(`result.duration_seconds`; fall back to measuring around the `extract` call if
that is 0). After a successful download:
```python
level = evaluate_latency(duration, warn_after_seconds=ctx.get("warn_after_seconds"),
                         critical_after_seconds=ctx.get("critical_after_seconds"))
if level is not None:
    ctx.emit(EventType.ALERT_RAISED,
             severity=Severity.WARNING if level is AlertLevel.YELLOW else Severity.ERROR,
             message=f"التنزيل أبطأ من المتوقع ({duration:.1f} ثانية)",
             payload={"level": level.value, "duration_seconds": duration,
                      "normal_duration_seconds": ctx.get("normal_duration_seconds")})
    notifier = getattr(ctx.services, "notifier", None)
    if notifier is not None:
        notifier.send(Alert(level=level, title=f"بطء في {system}/{report}",
                            body=..., run_id=ctx.run_id, payload={...}))
```
**A slow run is still a successful run.** Never fail the step on latency.
Guard the notifier call so a failing channel can't fail the run.

**Test** `tests/test_latency_alert.py` (new): the pure function across all
threshold combinations; and an end-to-end run with a fake slow browser that
asserts an `alert_raised` event exists, the run still `SUCCEEDED`, and the
alert landed in `logs/alerts.jsonl`.

---

## F-08 — Scheduler (nothing currently creates scheduled runs)

**Why:** `Worker` polls `runs.due()`, but only a human calling the API ever
creates a run. Without this the app cannot run daily — the whole point.

**Create** `src/smartops/scheduler.py`:

```python
class Scheduler:
    def __init__(self, services, *, clock=None, lookback: int = 500) -> None
    def tick(self, *, now: datetime | None = None) -> list[Run]
```

`tick` logic, exactly:
1. For each `(system, report)` from `services.systems.iter_scheduled()`:
2. Find that pair's most recent run: `services.runs.list(workflow_key="collect.report",
   limit=self.lookback)` filtered in Python on
   `params.get("system") == system.key and params.get("report") == report.key`.
   (Python-side filtering is deliberate — pilot scale is 3 systems × 5 reports.
   Do not add a JSON1 SQL query.)
3. Skip if that pair already has a run in a **non-terminal** status (queued,
   running, waiting, retrying) — never stack duplicate work.
4. Decide due-ness with a pure helper in the same module:
   ```python
   def is_due(schedule: ScheduleProfile, last_run_at: datetime | None,
              now: datetime) -> bool
   ```
   - `every_seconds`: due when `last_run_at is None` or
     `(now - last_run_at).total_seconds() >= every_seconds`.
   - `daily_at "HH:MM"`: interpret in **local machine time** (`now.astimezone()`,
     since the user thinks in local time while the clock stores UTC). Compute
     today's slot; due when `now >= slot` and (`last_run_at is None` or
     `last_run_at < slot`).
5. For each due pair: `services.runner.create_run("collect.report",
   params=services.systems.run_params(system.key, report.key),
   trigger=TriggerType.SCHEDULE)`. Do **not** execute it — the `Worker` picks it
   up via `runs.due()`. Return the created runs.
6. One failing profile must not stop the tick: wrap each pair in
   `try/except Exception` + `logger.exception`, continue.

**Test** `tests/test_scheduler.py` (new), all with `FrozenClock` — no sleeping:
`is_due` for both schedule kinds including the boundary; a first tick creates a
run; an immediate second tick creates nothing; a tick creates nothing while a
run is still queued; after the run finishes and the clock advances past the next
slot, a tick creates a new one; a broken profile doesn't stop the others.

---

## F-09 — Worker runs the scheduler

**Edit** `src/smartops/worker.py`. Add constructor param
`scheduler: Any | None = None`. At the **start** of `_poll_once`, if a scheduler
is set, call `self.scheduler.tick()` inside `try/except Exception` +
`logger.exception` — a scheduler failure must never kill the worker loop.

**Edit** `src/smartops/services.py`: build `self.scheduler = Scheduler(self)`
after the registries exist.

**Test** — extend `tests/test_worker.py`: a worker with a stub scheduler calls
`tick` once per poll; a scheduler that raises does not stop run dispatch.

---

## F-10 — CLI: the way a human actually drives this

**Why:** the end user is non-technical, but the *operator* setting this up needs
login, a dry run, and a daemon. There is currently no entry point but uvicorn.

**Create** `src/smartops/cli.py` and `src/smartops/__main__.py`
(`from .cli import main; main()`), using `argparse` only — no new dependency.

Commands:

| Command | Behaviour |
|---|---|
| `python -m smartops doctor` | Print config source, all resolved dirs, whether each exists/writable, systems loaded, per-system session status (missing / age in hours), whether the agent is enabled. Exit 0 always — it's a report. |
| `python -m smartops systems` | List systems and reports, with schedule and auth mode per report. |
| `python -m smartops login <system>` | Look up the profile, require `auth.mode == "session"`, call `sessions.capture_login(...)` with the profile's `login_url`/`logged_in_selector`. Print the saved path. |
| `python -m smartops collect <system> <report>` | Build params via `systems.run_params`, `create_run`, then `runner.drive`. Print final status, error, and the resulting file path. Exit 1 on failure. |
| `python -m smartops work` | Build `Services`, start `Worker(services, scheduler=services.scheduler)`, run until Ctrl-C, then `worker.stop()` + `join()`. This is the daily driver. |
| `python -m smartops serve` | `uvicorn.run(create_app(), host=settings.app.host, port=settings.app.port)`. |

Every command builds `Services` once and closes it in a `finally`. Print human
Arabic text, no tracebacks for expected failures — catch `SmartOpsError` and
print `exc.message` plus `exc.details`, exit 1.

Add to `pyproject.toml`:
```toml
[project.scripts]
smartops = "smartops.cli:main"
```

**Test** `tests/test_cli.py` (new): `build_parser()` parses each command's args
correctly; `doctor` and `systems` run against a temp `Services` and produce
non-empty output (use `capsys`); `login` on a system whose `auth.mode == "none"`
exits with a clear error. Do **not** launch a real browser in CLI tests.

---

## F-11 — API + web: surface sessions and alerts

Small, but it's what makes the web app honest about why a run failed.

**Edit** `src/smartops/api/app.py`, three new `def` endpoints:
- `GET /api/systems` → systems, reports, schedule, auth mode, and session
  status (`exists`, `age_hours`) per system.
- `GET /api/alerts?limit=100` → last N entries from `logs/alerts.jsonl` via
  `LocalLogNotifier.read_all()` (reverse-chronological, tolerate a missing file).
- `POST /api/systems/{system}/{report}/collect` → create + drive a
  `collect.report` run from the profile; 404 for an unknown system/report.

**Edit** `web/index.html` + `web/static/app.js`: a "الأنظمة" panel listing
systems with session status, a red badge reading
`الجلسة منتهية — سجّل الدخول من الطرفية` when a session is missing, and a
"جمع الآن" button hitting the new POST endpoint. Match the existing pages' style;
no framework, no build step.

**Test** — extend `tests/test_api.py`: the three endpoints return 200 with the
expected shape, and the unknown-system case returns 404.

---

## F-12 — Documentation and honest status

1. **`docs/DECISION_LOG.md`** — append (Arabic, one short paragraph each):
   - **D020 — الجلسة تُلتقط يدويًا مرة واحدة ثم يُعاد استخدامها.** المنصة لا ترى
     كلمة مرور أبدًا؛ الإنسان يسجّل الدخول في متصفح مرئي و`storage_state` يُحفظ
     خارج المستودع. جلسة منتهية = `AuthError` وحادثة برسالة تخبر المشغّل بالأمر المطلوب.
   - **D021 — الأدلة تُكتب على القرص ومفتاحها `run_id`.** لقطات الشاشة والتتبع لا
     تدخل سجل الأحداث ولا SQLite أبدًا (تضخّم + تسريب)، والمفتاح `run_id` حتى لا
     تختلط أدلة تشغيلات متوازية.
   - **D022 — الجدولة تُبنى من تعريفات الأنظمة لا من جدول جديد.** لا سكيمة جديدة؛
     الاستحقاق يُحسب من آخر تشغيل للزوج (نظام، تقرير).
   - **D023 — التعريفات الحقيقية خارج المستودع** عبر `SMARTOPS_SYSTEMS_DIR` (ينفّذ D012 عمليًا).
2. **`README.md`** — replace the "حالة البناء" section with an honest one, and
   add a **"التشغيل الأول"** section with this exact sequence:
   ```bash
   pip install -e ".[dev]"
   playwright install chromium
   set SMARTOPS_SYSTEMS_DIR=C:\smartops-private\systems
   python -m smartops doctor
   python -m smartops login <system>
   python -m smartops collect <system> <report>
   python -m smartops work
   ```
   State plainly: **the pilot is built and tested locally, but has not yet run
   against a real production system.** Do not claim otherwise anywhere.
3. **`docs/EXECUTION_PLAN.md`** — mark P5 as partially done (delay alert only;
   baselines and trend detection still open) and add a line that scheduling and
   session management are now implemented.
4. **`docs/AGENT_TASK_PACKETS.md`** — append an `F-01..F-12 (تم ✅)` line under
   the recommended order so the next agent sees this packet happened.

---

## Explicitly OUT of scope — do not build these

Touching any of these means stopping and escalating:

- Agent **Experiment** or **Execute** modes. `_SUPPORTED_AGENT_MODES` stays
  `{"read_only"}`. Do not weaken the check in `services.py`.
- P6 self-healing, sandboxing, patch deployment, rollback.
- P7 vision or desktop extraction layers (3, 4, 5 of the ladder).
- P8 cross-department dependency graph.
- Baseline/trend anomaly detection (F-07 is fixed thresholds only, on purpose).
- Any change to `core/`, `domain/`, `storage/db.py`, or `engine/`. **The only
  approved core contract change in this packet is `ports/browser.py` in F-02.**
- OpenTelemetry.

---

## Suggested order

`F-01 → F-02 → F-03 → F-04` (sessions work end to end) → `F-05 → F-06`
(profiles drive it) → `F-07 → F-08 → F-09` (alerts + it runs itself) →
`F-10 → F-11` (humans can drive it) → `F-12` (docs tell the truth).

Run `pytest -q` after each item. If one item's tests fail twice for the same
reason, stop and escalate rather than reshaping the design around the failure.
