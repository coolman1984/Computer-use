# Target Architecture

```text
Local web interface
│
├── Operations
├── Monitoring
├── Incidents
├── History
├── Agents
└── Chat
        │
        ▼
Local control center
        │
 ┌──────┼────────┬───────────┬─────────────┐
 ▼      ▼        ▼           ▼             ▼
Work-  Adaptive Data &     Monitoring    AI
flow   browser  history    & alerts      agents
 │      │        │           │             │
 │   API/DOM/   SQLite     Incidents    Codex
 │   Vision     DuckDB     Proactive    Claude
 │   Desktop    Parquet    alerting
 │
 ▼
Department automation projects and their linking
```

## Component boundaries

### Control Plane
The official source of run state. Responsible for identity, status, dependencies, locking, retry, pausing, and resuming.

### Workflow Engine
Runs steps as explicit state, not as one long, unrecoverable script.

### Browser Engine
Executes site access and download in an adaptive order.

### Data Manager
Stores raw files, hashes, quality results, history, and derived data.

### Observability
Stores metrics, tracing, errors, and degradation.

### Incident Manager
Builds a complete incident pack and routes it for diagnosis.

### Agent Manager
Decides which agent and what reasoning level to use, and logs every input, output, and change.

### Web App
The primary user interface. Core functionality must never depend on the command line.

## State rules

Every run has a unique id and statuses such as:

`queued → running → waiting → retrying → succeeded | failed | cancelled`

Every step records its start, end, reason, and output.

## No overlap

Any change to code or a workflow uses a lock and a separate working copy before merging.
