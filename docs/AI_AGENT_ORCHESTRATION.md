# AI Agent Orchestration

## Goal

Run Codex CLI and Claude Code CLI as organized background workers, not as random standalone tools.

## Task levels

### Read only
Analyze logs, code, and incidents without modifying anything.

### Experiment
Create a working branch, modify it, and run the tests.

### Execute
Deploy a fix that passed the tests and was allowed by policy.

## Escalation

```text
Known fixed solution
↓
Economical agent
↓
Codex, medium reasoning
↓
Codex, high reasoning
↓
Claude, stronger
↓
Human
```

Escalation must be based on:
- The type of problem.
- The risk.
- The number of attempts.
- The history of solutions.
- The confidence level.
- The cost in time and compute.

## The context pack for the agent

Never send just "an error occurred."

Send:
- A description of the task.
- The run log.
- The failed step.
- A screenshot.
- The browser trace.
- The current version.
- The latest changes.
- Similar successful runs.
- Past solutions.
- The tests required.
- The permission boundaries.

The Recording Coach is a deliberately narrower exception. It receives generic
action structure only. It must not receive page addresses, selectors, page
text, screenshots, response bodies, cookies, credentials, downloaded files, or
recording artifacts. It runs analyze-only and cannot block the recorder.

## Agent log

For every agent run:
- Who invoked it.
- Why.
- The model.
- The reasoning level.
- The files read.
- The commands.
- The files modified.
- The tests.
- The result.
- The execution time.
- The next escalation, if any.

## Safeguards

- No direct production modification without policy.
- No data deletion.
- No permission changes.
- No touching secrets.
- Any modification must be reversible.
