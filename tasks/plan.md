# Implementation Plan: Recording Center

## Objective

Build a first-class Recording Center inside SmartOps so a non-technical user can start a guided browser recording from the web app, perform a real workflow in visible Google Chrome, stop and review it, re-record it as a new version, archive or restore it, and later convert an approved recording into an automation draft.

The first production pilot is Samsung G-MES. Corporate URLs, cookies, HAR files, screenshots, downloads, and storage state must remain outside the public repository.

## User Flow

1. Open `/app/recordings.html` and select **New recording**.
2. Enter a recording name and select a configured system.
3. SmartOps creates a recording and launches a visible, dedicated Chrome recording session.
4. The user completes login and the business steps manually.
5. SmartOps records DOM context when available, click ratios for canvas-style pages, network metadata, downloads, screenshots, and a Playwright trace.
6. The user stops and saves the recording from the web app.
7. The detail page shows the captured steps and artifacts with sensitive values redacted.
8. **Re-record** creates a new version linked to the original recording.
9. **Delete** moves a recording to the recycle state; restore is available before a separate purge action.
10. An approved recording can be converted into an automation draft and verified before scheduling.

## Architecture Decisions

- **SmartOps owns the recording lifecycle.** A dedicated `RecordingManager` controls a worker process and persists state. The web app does not invoke raw PowerShell commands.
- **Playwright is the primary recorder.** It captures network, DOM, downloads, storage state, and trace data. Relative click capture is retained for Nexacro/canvas screens where semantic selectors are unavailable.
- **Google Chrome is required.** The recorder uses the installed Chrome channel in headed mode and a dedicated recording profile; it never reuses or modifies the user's normal Chrome profile.
- **SQLite stores searchable metadata only.** Screenshots, HAR, trace, downloads, and storage state live under `storage.recordings_dir` outside the public repository for real systems.
- **Secrets are never returned by API.** Artifact access uses recording IDs and allow-listed filenames. Storage-state files are never previewable or downloadable from the UI.
- **Recording is resumable at the control-plane level.** A process heartbeat distinguishes `recording`, `stopping`, `completed`, `failed`, and `interrupted`; an interrupted process can be re-recorded without losing its captured steps.
- **Re-recording is versioned.** A new recording receives `parent_recording_id` and an incremented version. The previous version remains reviewable.
- **Deletion is recoverable.** The normal delete action sets `deleted_at`. Permanent artifact purge is a separate maintenance operation.
- **No generated recording executes automatically.** Conversion produces a draft. A successful replay and validated download are required before scheduling.

## State Model

```text
draft -> starting -> recording -> stopping -> completed
                    |     |           |
                    |     +-> failed <-+
                    +-> interrupted

completed | failed | interrupted -> rerecord (new linked version)
any inactive state -> deleted -> restored
```

Only one active recording is allowed per configured system and Chrome recording profile. State transitions must be idempotent so a repeated API request does not launch or stop the worker twice.

## Proposed Data Model

### `recordings`

- `id`, `name`, `system_key`, `version`, `parent_recording_id`
- `status`, `started_at`, `finished_at`, `heartbeat_at`
- `artifact_dir`, `worker_pid`, `error_message`
- `step_count`, `download_count`, `created_at`, `updated_at`, `deleted_at`

### `recording_steps`

- `recording_id`, `seq`, `kind`, `occurred_at`
- `page_url_redacted`, `page_title`, `selector`, `target_text_redacted`
- `x_ratio`, `y_ratio`, `changed_ratio`
- `request_ref`, `download_ref`, `before_image`, `after_image`

Large or sensitive payloads stay on disk. Database rows contain references and redacted summaries.

## API Contract

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/recordings` | List active, completed, or deleted recordings |
| `POST` | `/api/recordings` | Create a draft recording |
| `GET` | `/api/recordings/{id}` | Recording metadata and steps |
| `POST` | `/api/recordings/{id}/start` | Launch the visible Chrome recording worker |
| `POST` | `/api/recordings/{id}/pause` | Pause capture without closing Chrome |
| `POST` | `/api/recordings/{id}/resume` | Resume capture |
| `POST` | `/api/recordings/{id}/stop` | Flush artifacts and complete recording |
| `POST` | `/api/recordings/{id}/rerecord` | Create and start a linked new version |
| `POST` | `/api/recordings/{id}/delete` | Soft-delete an inactive recording |
| `POST` | `/api/recordings/{id}/restore` | Restore a soft-deleted recording |
| `GET` | `/api/recordings/{id}/artifacts/{name}` | Preview an allow-listed screenshot or sanitized summary |
| `POST` | `/api/recordings/{id}/draft` | Create an automation draft after review |

All mutating endpoints return the current recording representation. Start/stop/re-record must be safe to retry.

## Artifact Layout

```text
<recordings_dir>/<recording_id>/
  manifest.json
  steps.jsonl
  screenshots/
  downloads/
  network/
    sanitized-summary.json
    session.har                 # private; never exposed directly
  trace/
    trace.zip                   # private; never exposed directly
  session/
    storage-state.json          # secret; never exposed directly
```

For the G-MES pilot, configure `SMARTOPS_RECORDINGS_DIR` and `SMARTOPS_SESSIONS_DIR` under `C:\smartops-private`.

## Execution Tasks

### Task 1: Prove the G-MES capture strategy

**Description:** Build a disposable local probe that opens headed Chrome, captures a short Nexacro interaction, records request/response metadata, and determines which of DOM selectors, injected click events, or relative visual coordinates are reliable.

**Acceptance criteria:**

- [ ] One G-MES interaction produces a private trace, sanitized network summary, and at least one recorded step.
- [ ] The probe documents which event source works for Nexacro without committing corporate data.
- [ ] Login credentials and response bodies are absent from console output and repository files.

**Verification:** Run the probe against G-MES manually, then inspect only the sanitized manifest.

**Dependencies:** None.

**Files likely touched:** `scripts/recording_probe.py`, `docs/RECORDING_CAPTURE_NOTES.md`, `.gitignore`.

**Estimated scope:** Medium.

### Task 2: Add recording configuration and private-path checks

**Description:** Add `storage.recordings_dir`, environment override `SMARTOPS_RECORDINGS_DIR`, directory creation, and doctor output. Real internal systems must warn or fail when the resolved recording directory sits inside the public repository.

**Acceptance criteria:**

- [ ] Configuration resolves and creates the recordings directory on Windows.
- [ ] `doctor` reports existence, writability, and whether the path is outside the repository.
- [ ] Recording artifacts and storage-state patterns are ignored by Git.

**Verification:** `python -m pytest -q tests/test_config.py tests/test_cli.py`.

**Dependencies:** Task 1.

**Files likely touched:** `src/smartops/config.py`, `src/smartops/cli.py`, `config/system.example.yaml`, `.gitignore`, `tests/test_config.py`.

**Estimated scope:** Medium.

### Task 3: Deliver a minimal recording catalog

**Description:** Add recording models, migration, repository operations, and list/create API endpoints so a draft recording survives restarts and appears in the web app before browser control is introduced.

**Acceptance criteria:**

- [ ] A draft recording can be created and listed with name, system, version, and status.
- [ ] Migration is repeatable and preserves the existing database.
- [ ] Duplicate active names are allowed because identity is the generated ID.

**Verification:** `python -m pytest -q tests/test_recording_storage.py tests/test_recording_api.py`.

**Dependencies:** Task 2.

**Files likely touched:** `src/smartops/domain/models.py`, `src/smartops/domain/enums.py`, `src/smartops/storage/db.py`, `src/smartops/storage/repositories.py`, `tests/test_recording_storage.py`.

**Estimated scope:** Medium.

### Task 4: Add the Recording Center list page

**Description:** Add navigation and `/app/recordings.html` with create form, status filters, recording rows, and empty/error states backed by the catalog API.

**Acceptance criteria:**

- [ ] The page creates and lists draft recordings without terminal use.
- [ ] Status, version, system, date, step count, and actions are readable in English.
- [ ] Existing dashboard pages and navigation continue to work.

**Verification:** `python -m pytest -q tests/test_web_recordings.py tests/test_web_app.py`.

**Dependencies:** Task 3.

**Files likely touched:** `web/recordings.html`, `web/static/recordings.js`, `web/static/app.js`, `web/static/style.css`, `tests/test_web_recordings.py`.

**Estimated scope:** Medium.

### Task 5: Define the recorder worker contract

**Description:** Introduce a small port for start, pause, resume, stop, heartbeat, and recovery plus a `RecordingManager` that enforces valid state transitions and one active worker per system.

**Acceptance criteria:**

- [ ] Repeated start/stop requests are idempotent.
- [ ] Invalid transitions return a clear domain error.
- [ ] A stale heartbeat changes an active recording to `interrupted` on recovery.

**Verification:** `python -m pytest -q tests/test_recording_manager.py`.

**Dependencies:** Task 3.

**Files likely touched:** `src/smartops/ports/recording.py`, `src/smartops/recordings/manager.py`, `src/smartops/services.py`, `tests/test_recording_manager.py`.

**Estimated scope:** Medium.

### Task 6: Implement the headed Chrome recording worker

**Description:** Implement the Playwright adapter selected by Task 1. It launches installed Google Chrome with a dedicated private profile, captures step metadata, trace, sanitized network metadata, downloads, screenshots, and heartbeat updates, then flushes a manifest on stop.

**Acceptance criteria:**

- [ ] A local test site can be recorded from start through stop with visible Chrome.
- [ ] Worker termination leaves a readable partial manifest and becomes `interrupted` or `failed`.
- [ ] Storage state, HAR bodies, cookies, authorization headers, and typed secrets are never exposed through logs.

**Verification:** `python -m pytest -q tests/test_recording_worker.py tests/test_recording_redaction.py` plus one headed local-browser check.

**Dependencies:** Tasks 1, 2, and 5.

**Files likely touched:** `src/smartops/adapters/recording/playwright.py`, `src/smartops/recordings/worker.py`, `src/smartops/recordings/redaction.py`, `tests/test_recording_worker.py`, `tests/test_recording_redaction.py`.

**Estimated scope:** Medium.

### Task 7: Connect browser controls to API and events

**Description:** Add start, pause, resume, stop, and recovery endpoints. Publish recording lifecycle events through the existing event bus and WebSocket payloads using `recording_id` in the event payload.

**Acceptance criteria:**

- [ ] Web requests control the worker and return the persisted state.
- [ ] Lifecycle changes reach connected clients without page reload.
- [ ] Worker launch failure produces a failed recording and an evidence-backed event.

**Verification:** `python -m pytest -q tests/test_recording_api.py tests/test_recording_events.py tests/test_ws.py`.

**Dependencies:** Tasks 5 and 6.

**Files likely touched:** `src/smartops/api/recordings.py`, `src/smartops/api/app.py`, `src/smartops/domain/enums.py`, `tests/test_recording_api.py`, `tests/test_recording_events.py`.

**Estimated scope:** Medium.

### Task 8: Deliver recording detail and live controls

**Description:** Add `/app/recording.html?id=...` with live state, elapsed time, step counter, start/pause/resume/stop buttons, and clear handoff instructions for login/MFA.

**Acceptance criteria:**

- [ ] Controls enable only for valid states and remain correct after refresh.
- [ ] Live events update status and step count while recording.
- [ ] Closing the web page does not terminate the recording worker.

**Verification:** `python -m pytest -q tests/test_web_recording_detail.py` plus an end-to-end local recording.

**Dependencies:** Tasks 4 and 7.

**Files likely touched:** `web/recording.html`, `web/static/recording.js`, `web/static/app.js`, `web/static/style.css`, `tests/test_web_recording_detail.py`.

**Estimated scope:** Medium.

### Task 9: Add safe step and artifact review

**Description:** Show the ordered timeline with before/after images, selector or relative click information, network request references, and downloads. Serve only allow-listed, path-contained artifacts.

**Acceptance criteria:**

- [ ] Screenshot previews cannot escape the recording directory via path traversal.
- [ ] Session state, raw HAR, headers, cookies, and response bodies are unavailable from the UI API.
- [ ] Missing or partial artifacts render a useful state instead of breaking the page.

**Verification:** `python -m pytest -q tests/test_recording_artifacts.py tests/test_web_recording_detail.py`.

**Dependencies:** Tasks 6 and 8.

**Files likely touched:** `src/smartops/api/recordings.py`, `src/smartops/recordings/artifacts.py`, `web/recording.html`, `web/static/recording.js`, `tests/test_recording_artifacts.py`.

**Estimated scope:** Medium.

### Task 10: Add re-record, soft delete, and restore

**Description:** Add versioned re-recording and recoverable lifecycle actions. Re-record copies only safe configuration, creates a new artifact directory, and never overwrites the previous recording.

**Acceptance criteria:**

- [ ] Re-record creates version `N+1` linked to the prior recording.
- [ ] Delete is rejected for active recordings and hides deleted rows by default.
- [ ] Restore returns metadata and artifacts to the normal list without copying files.

**Verification:** `python -m pytest -q tests/test_recording_lifecycle.py tests/test_web_recordings.py`.

**Dependencies:** Tasks 7 through 9.

**Files likely touched:** `src/smartops/recordings/manager.py`, `src/smartops/storage/repositories.py`, `src/smartops/api/recordings.py`, `web/static/recordings.js`, `tests/test_recording_lifecycle.py`.

**Estimated scope:** Medium.

### Task 11: Convert an approved recording into an automation draft

**Description:** Generate a reviewable workflow draft that preserves the extraction ladder: direct network request first, semantic DOM second, relative visual action last. Do not activate schedules automatically.

**Acceptance criteria:**

- [ ] Draft generation explains the chosen extraction layer for each step.
- [ ] Replay runs only on explicit request and writes a normal SmartOps run/event trail.
- [ ] A downloaded file must pass configured validation before the draft can be marked verified.

**Verification:** `python -m pytest -q tests/test_recording_conversion.py tests/test_collect_workflow.py` plus local replay.

**Dependencies:** Tasks 9 and 10.

**Files likely touched:** `src/smartops/recordings/converter.py`, `src/smartops/workflows/profiles.py`, `src/smartops/api/recordings.py`, `tests/test_recording_conversion.py`.

**Estimated scope:** Medium.

### Task 12: Complete the G-MES production pilot

**Description:** Configure one real G-MES report outside the repository, record it, re-record it once, convert it to a draft, replay it, validate the real output, then enable a controlled schedule and alert.

**Acceptance criteria:**

- [ ] Login, inquiry, download, validation, and event history succeed end to end.
- [ ] A forced session expiry and a failed download create clear incidents and alerts.
- [ ] No internal URL, screenshot, HAR, session file, or downloaded report appears in Git status.

**Verification:** Run `python -m smartops doctor`, the real pilot, and `python -m pytest -q`.

**Dependencies:** Tasks 1 through 11.

**Files likely touched:** external private system YAML and operational notes only; repository changes limited to generalized fixes discovered by the pilot.

**Estimated scope:** Medium.

### Task 13: Add operations and recovery

**Description:** Document startup, shutdown, backup, restore, artifact retention, interrupted-worker recovery, and permanent purge. Add a health check for the recorder subsystem.

**Acceptance criteria:**

- [ ] SQLite plus private recording artifacts can be backed up and restored together.
- [ ] Startup recovery reconciles stale PIDs and heartbeats without losing completed recordings.
- [ ] Retention and permanent purge require explicit configuration and produce audit events.

**Verification:** Recovery drill, backup/restore drill, and `python -m pytest -q`.

**Dependencies:** Task 12.

**Files likely touched:** `src/smartops/recordings/recovery.py`, `src/smartops/api/app.py`, `docs/RECORDING_OPERATIONS.md`, `tests/test_recording_recovery.py`.

**Estimated scope:** Medium.

## Checkpoints

### Checkpoint A: After Tasks 1–4

- Recording strategy proven against Nexacro.
- Private storage is configured.
- Draft recordings can be created and listed from the web app.
- Existing test suite remains green.

### Checkpoint B: After Tasks 5–8

- One-click start and stop works on a local test page in visible Chrome.
- Recording state survives page refresh and server restart.
- Live UI reflects worker state and failures.

### Checkpoint C: After Tasks 9–11

- Steps and safe artifacts are reviewable.
- Re-record, delete, and restore work.
- An approved recording produces a replayable automation draft.

### Checkpoint D: After Tasks 12–13

- One real G-MES report completes end to end.
- Failure, backup, restore, and recovery drills pass.
- The feature is ready for controlled daily use.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Nexacro renders controls without useful DOM selectors | High | Run Task 1 first; combine network capture with relative click events and verified screenshots |
| Web server cannot display Chrome on the interactive Windows desktop | High | Preflight the desktop session; keep recorder worker behind a port so a user-session bridge can replace local spawning |
| HAR or storage state exposes credentials | High | Private directory, restrictive permissions, redaction, and no raw-artifact API |
| Chrome/profile lock or corruption | High | Dedicated recording profile; never point Playwright at the normal corporate Chrome profile |
| Stop/re-record races create duplicate workers | Medium | Idempotent manager operations, per-system lock, PID and heartbeat reconciliation |
| Recording captures a successful click but not the business outcome | High | Require explicit postconditions, successful replay, and validated downloaded output |
| Large traces/screenshots fill disk | Medium | Size counters, retention settings, warnings, and audited purge |

## Definition of Done

- All task acceptance criteria and targeted tests pass.
- Full `python -m pytest -q` passes.
- Every recording state change emits an event.
- Downloads are validated before a converted workflow is considered verified.
- No corporate data or authentication material is tracked by Git.
- Windows headed-Chrome flow is verified from the SmartOps web app.
- The G-MES pilot includes successful, failed, session-expired, re-record, delete, restore, backup, and recovery scenarios.

## Decisions Needed During Implementation

- Task 1 decides whether G-MES replay uses network requests, DOM selectors, injected relative events, or a verified mixture.
- Before Task 12, select the first G-MES report and its validation rules: expected filename/type, columns, minimum rows, freshness, and schedule.
- Before permanent purge is enabled, select retention days and backup location.

