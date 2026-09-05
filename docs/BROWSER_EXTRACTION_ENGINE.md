# Browser Data Extraction Engine

## Goal

Build an engine that tolerates differences between sites and technologies, instead of relying on a single automation approach.

## The extraction ladder

### 1. Network / direct download
Watch network requests during export. If an authorized request can be
repeated with the same user session, use it.

### 2. Page structure
Use Playwright to reach elements, frames, windows, tabs, downloads, and smart waiting.

### 3. Self-healing for changing pages
Draw on Stagehand-style techniques: try the fixed path first, then a smart
fallback discovery, and cache the new path once verified.

### 4. Visual vision
Use a Midscene/Browser Use-style approach for pages whose elements cannot
be read. Rely on the image and the semantic goal.

### 5. Full UI control
Use UI-TARS/Agent TARS-style ideas only for cases that cannot be handled
from within the browser.

## Resolution and screens

- Never store absolute coordinates.
- Use the semantic element whenever possible.
- In visual mode, use relative coordinates.
- Run the background browser at a uniform viewport size when appropriate.

## Many tabs

Do not open an unbounded number. Use:

- A task queue.
- A dynamic concurrency limit.
- Isolated sessions.
- Memory and CPU monitoring.
- Automatically raising or lowering concurrency.

## Download health

Never consider a task successful just because a file appeared.

Verify:
- The file exists.
- Its size is non-zero.
- It can be opened.
- The expected date.
- Columns and sheets.
- Row count within a sane range.
- No duplication.
- The file's hash.

## Evidence on failure

Save:
- A screenshot.
- The browser trace.
- The important requests.
- The last successful steps.
- Tab names.
- The page title.
- Each step's duration.
- The expected and actual files.

## Camoufox

It remains an experimental or secondary option, not the default engine. The
foundation is Playwright with adaptive layers.
