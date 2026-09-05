# Observability and Self-Healing

## Goal

Detect degradation before failure, not only after it.

## Core metrics

- System login time.
- Report generation time.
- Download time.
- Number of retries.
- Failure rate.
- File size.
- Row count.
- Data refresh time.
- Memory and CPU usage.
- Number of pending tasks.

## Anomaly detection

A process may still succeed while gradually getting slower. This must
generate an early warning.

Example:

`8s → 10s → 15s → 23s → 31s`

We do not wait for the failure.

## Alert levels

- Green: normal.
- Yellow: degradation.
- Orange: imminent risk.
- Red: failure.
- Critical: wide impact or a cascade of failures.

## Incident cycle

```text
Detection
↓
Freeze evidence
↓
Search past solutions
↓
Try a safe fix
↓
Agent diagnosis
↓
Fix on a branch
↓
Tests
↓
Deploy or roll back
↓
Document the lesson
```

## Rollback

Every fix creates a new version. If post-deploy tests fail or performance
degrades, automatically roll back to the previous version.

## Watching the watcher

An independent watchdog confirms that:
- The database is available.
- The task queue is moving.
- The browser engine is alive.
- Logs are being written.
- The disk is not full.
- The web service is running.
