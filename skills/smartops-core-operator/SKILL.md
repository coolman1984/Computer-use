---
name: smartops-core-operator
description: Use whenever building, debugging, or operating this repository's one daily Chrome download flow so confirmed lessons are reused and the scope stays small.
---

# SmartOps core operator

## Start every task

1. Run `python -m smartops brief`. It answers, from real data, what stage this
   deployment is at, what the next action is, which controls have stopped
   resolving, whether a recording is running, and which incidents are open with
   where their evidence is. Start from what is true rather than from what the
   last conversation said was true.
2. Read `AGENTS.md`, `PROJECT_EYE.md`, `README.md`, and
   `docs/DAILY_DOWNLOAD_FLOW.md`. `PROJECT_EYE.md` says which domain owns what,
   which way dependencies may point, and which red zones are known — so a
   change lands in the one place responsible for it.
3. If `logs/operator-memory.md` exists, read it before investigating. It is the
   current generated view of confirmed lessons; `logs/operator-learning.jsonl`
   is the append-only history.
4. Work only on the active daily download flow. Use the existing Chrome,
   recording, replay, storage, and validation path.

## What you can ask the running system

These are read-only and safe to call at any time. Prefer them over guessing;
every one of them answers a question that used to be answered by assumption.

| Question | How to ask |
| --- | --- |
| Where does this deployment stand? | `python -m smartops brief` |
| What can this screen offer an automation? | `python -m smartops probe <system>` |
| Are the settings, folders and sessions sound? | `python -m smartops doctor` |
| What is on screen in the recorder right now? | `GET /api/recordings/{id}/live` |
| Why do the screenshots look like that? | `GET /api/recordings/{id}/vision` |
| What has been captured since I last looked? | `GET /api/recordings/{id}/recent-steps?since_seq=N` |
| Which steps has nothing proved yet? | `GET /api/recordings/{id}/thin-evidence` |
| What does this system's automation depend on? | `GET /api/systems/{key}/elements` |

Two that change something, and are the only two you may use while a person is
recording:

| Action | How |
| --- | --- |
| Take back a mis-click, browser still open | `POST /api/recordings/{id}/undo-last-step` |
| Repair one control for every step that uses it | `PUT /api/systems/{key}/elements/{ref}` |

## The line you do not cross

During a human recording you read and explain. You do not click, type, or
navigate. The person has the hands; you have the memory and the arithmetic.
Nothing in the list above performs a browser action, and nothing you add may.

Repairing a control is the one repair worth making, because it is made once and
reaches every step that names it. Deciding that a renamed control is the same
control is still a person's call — propose it, do not apply it unasked.

## Evidence order

Use Network/API, then DOM, then Vision, then Desktop. The SmartOps Tab Bridge is
read-only DOM evidence. A screenshot is read-only visual evidence. Neither is a
second action engine; the existing replay path performs browser actions.

Samsung capture protection can make an otherwise live G-MES window appear as a
gray `No Picture Taking!` frame to desktop screenshots. Treat that as an
evidence limitation, not a failed page: inspect sanitized DOM structure,
Playwright actionability, and network/download events. The SmartOps server must
run on the interactive Windows desktop; a server started in the restricted
agent desktop cannot launch the recorder (`WinError 5`).

## Confirmed daily G-MES path

For `Production Plan by Order(Line)`, keep this order: open the screen, select
the `VD` organization checkbox, set and commit both dates, click the fixed
bottom-left `Inquiry` button, wait for the result grid to finish loading, click
the top Excel icon, then click `OK` in `PopupExcelExport`. A panel scroll and
modifier-only keypresses are recorder noise, not business actions.

Opening the screen may show the exact informational message `Selecting an org
chart before adding row(s)`. Dismiss only that exact message with its own
`Info_1.form.btnOk` control, then continue to `VD`; never auto-confirm another
dialog.

## When something fails

Work the failure from the first thing that went wrong, not from where it
showed. A run that reports success on an empty report is not a reporting bug.

1. `python -m smartops brief` — is the failure already known and does it have
   evidence collected?
2. Open the incident's evidence folder. It holds the run's steps, its events,
   the files it produced, and the incidents that looked like this one before.
3. Compare against what the recording expected: a step's proof is in the plan,
   and `timeline.jsonl` beside the recording says what actually changed when
   that step ran.
4. Reproduce it before fixing it. If you could not reproduce it, you have not
   fixed it — say so rather than claiming otherwise.

A frame that came back grey is an evidence limitation, never a failed page.
`GET /api/recordings/{id}/vision` says which capture route still sees the
window, and a recording whose pictures are worthless can still be checked from
the page's own state.

## Learn without polluting memory

Add a lesson through `POST /api/learnings` only after all four facts are known:
problem, confirmed cause, implemented solution, and actual verification. Never
promote a guess, secret, credential, cookie, company data, raw snapshot, or
download content.

Every accepted lesson automatically appends the private JSONL history and
regenerates `logs/operator-memory.md`; do not maintain a competing notes file.

When a new Samsung G-MES UI path is proven, also update and validate the
external `samsung-gmes-automation` skill as required by the machine rules.
