# Project Eye

## 1. Project summary

SmartOps is a local control centre that records a browser report task once,
turns it into a reviewed process, tests and approves it, then runs it on demand
or on schedule.  A run is successful only after its artifacts are saved,
validated, registered, and made visible through the same run history.

The platform is deliberately deterministic by default.  Browser DOM replay is
the implemented path; vision and native desktop automation are explicitly
unsupported until they have their own runtime, contracts, fixtures, and gates.

## 2. Main domains

| Domain | Owner modules | Responsibility |
|---|---|---|
| Control and journey | `api/app.py`, `journey.py`, `services.py` | Exposes the user journey and wires the local runtime. |
| Recording | `recordings/manager.py`, `recordings/worker.py` | Captures a human task and stores its semantic steps. |
| Plan and UI objects | `recordings/converter.py`, `object_repository.py` | Builds the reviewed plan and reusable screen/object descriptors. |
| Browser and authentication | `adapters/browser/*` | Owns browser launch, sign-in, replay page identity, downloads, and evidence. |
| Process governance | `processes/manager.py`, `admission.py` | Creates, tests, approves, runs, and schedules a process revision. |
| Results | `workflows/builtin.py`, `adapters/validation/local.py` | Registers every produced artifact and validates it before success. |
| Storage and observability | `storage/*`, `events/*`, `recordings/observation.py` | Owns durable state, event history, and safe diagnostic timelines. |

## 3. Runtime applications

- **Web client/API:** `web/` → `smartops.main:app` → `api/app.py`. The client
  is a dependency-free local interface and is mounted at `/app`.
- **CLI:** `smartops.cli:main`; `serve` starts API, worker, and scheduler.
- **Worker/Scheduler:** `worker.py` consumes queued runs; `scheduler.py` creates
  only approved due runs.
- **Browser:** one owned Playwright context per operation through the shared
  launch factory; no Playwright object crosses threads.
- **Storage:** SQLite records state; run-owned private folders retain artifacts
  and evidence.

## 4. Dependency direction

```text
API / CLI → Journey and managers → Workflow engine → Browser or validation adapters
                                      ↓
                               Storage repositories and event log
```

Recording is the one parallel branch:

```text
Recording manager → recording worker → shared browser launch → recording repository
                                                   ↓
                                         safe observation timeline
```

Adapters do not approve processes; repositories do not decide business policy;
the API does not directly change browser pages or database rows.

## 5. Critical journeys

### J1 — Record, review, and create a process

`API create/start → RecordingManager → PlaywrightRecordingWorker →
RecordingRepository → draft compiler → object repository → review gate →
ProcessManager.create_from_recording`

The recording owns raw semantic steps.  The compiler owns the plan.  The
embedded object repository owns reusable locator descriptors for that plan.

### J2 — Test/run and validate a recorded process

`API or Scheduler → ProcessManager → WorkflowRunner → replay_recording →
PlaywrightBrowserAdapter → ReplaySession → download reservation → FileRepository
→ validation → run result/events`

Replay owns the live page identity.  A non-primary locator resolution produces
only a `review_required` repair proposal; it travels in the successful step
output and never rewrites a plan.

### J3 — Correct a recording while it is live

`API pause → RecordingManager.pause → API undo → RecordingRepository.delete_step
→ API resume → worker captures replacement step`

Undo is allowed only while paused and never removes an external download.

## 6. Data ownership

| Data/state | Single owner | Rule |
|---|---|---|
| Recording status and semantic steps | `RecordingRepository` | Raw artifacts are retained when one semantic step is undone. |
| Reviewed plan and object descriptors | `Recording.automation_draft` | `object_ref` uses the embedded repository as authority; old plans remain compatible. |
| Browser page/tab identity | `ReplaySession` / recording worker | Names are stable; no index-based tab selection. |
| Authentication session | shared browser profile and session manager | No credential values enter plans, logs, or evidence. |
| Artifact lifecycle | browser adapter + `FileRepository` | Received → saved → validated → registered. |
| Process approval identity | `processes/admission.py` | A changed plan/config/runtime digest invalidates a stale approval. |
| Run state and visible event history | workflow runner + repositories/events | The scheduler never invents a second execution path. |

## 7. Recorder V2 connection map

| Capability | Capture | Review/plan | Replay/run | Proof |
|---|---|---|---|---|
| Dynamic mousedown redraw | `worker.py` | pointer interaction | `replay.py` | pointer result is never blindly retried |
| Nearby anchors | `worker.py` | `object_repository.py` | strict unique anchor chain | `test_replay_target_safety.py` |
| Semantic fallback | `worker.py` | `converter.py` | strict tag/role/type fallback | `test_object_repository.py` |
| Object reuse | capture locator | `object_ref` | authoritative descriptor resolution | `test_object_repository.py` |
| Repair learning | n/a | human review only | `healing.py` proposal | workflow step output |
| Pause/undo/recapture | worker pause | manager/repository | API control route | `test_recording_lifecycle.py` |

## 8. Current red zones

1. **Live browser proof is blocked locally.** This workspace lacks Python
   Playwright, pytest, and a runnable browser binary; static and fake tests are
   not evidence of headed-browser success.
2. **The local web client has not had a live browser proof yet.** Its static
   contract test covers the required pages and API routes, but visible-browser
   interaction remains pending with the same unavailable test runtime.
3. **Vision/native desktop are planned, not implemented.** They must fail as
   unsupported, not silently become coordinate clicks.
4. **The current working tree is an uncommitted cross-domain batch.** It needs
   focused tests and one clean checkpoint before integration.
5. **Architecture drift — API direct reads.** `api/app.py` still reads several
   repositories through the service container for detail/list views. The
   intended direction is API → query/use-case boundary → repository. Keep this
   visible until a small read-model migration can be made with API contract
   tests; do not hide it by weakening the project rules.

## 9. Hotspots

- `adapters/browser/authentication.py` and `replay.py`: session and external
  effect boundaries.
- `recordings/worker.py`: privacy, event ordering, and browser-thread rules.
- `processes/manager.py`: test/approval/schedule gate integrity.
- `workflows/builtin.py`: artifact registration and validation handoff.
- `storage/repositories.py`: durable state and recovery contracts.

## 10. Last verified revision

Base revision: `4c3a525` on `fix/replay-authenticated-page`, checked on
2026-09-08.  The current working tree contains the uncommitted recorder and
map batch. Static compilation, capture-script syntax, graph validation, and
focused non-browser smoke checks passed. Full pytest and headed-browser proof
remain pending; do not represent this revision as production proven.

The machine-readable graph is `.project-eye/graph.yaml`. Update the affected
nodes and edges whenever a cross-domain contract changes.
