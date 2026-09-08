# Recorder roadmap

How the recorder gets from where it is to a capture nobody has to babysit,
in the order the work actually has to happen.

This changes no business scope. The goal stays the one in
[`tasks/CURRENT.md`](../tasks/CURRENT.md): one G-MES daily report, downloaded,
proved from its bytes, validated, recorded. What this document sequences is the
*capture* side of that flow, because that is what keeps failing.

## The idea in one line

One recording engine, many senses, and a strategy that picks the strongest
sense a given screen actually offers.

Everything below follows from one observation: every attempt so far leaned on
whichever sensor was tried first — a CSS selector, or a screenshot — and a
corporate portal defeats each of them in turn. Ids are generated. The screen is
painted rather than built. The window's picture is withheld. The grid is not a
table. None of that is a reason to stop; each is a reason not to have bet
everything on one sense.

So the recorder does not get *more* automation. It gets more ways to see, one
place that puts what it saw in order, and an honest refusal when it saw nothing.

## The order, and why it is this order

Each phase is worth shipping alone, and each one exists because the phase after
it cannot be judged without it.

### Phase 1 — See honestly · **done**

Nothing can be built on evidence that lies. A screenshot that returned a flat
grey rectangle used to be indistinguishable from a good one.

* `recordings/vision.py` — frames come through the DevTools protocol and are
  measured before they are believed. A flat frame is re-taken by a route that
  never reads the window's surface; a page that needs that route keeps it. The
  verdict is exact (a tiny probe decoded by a stdlib PNG reader) and reported
  per region, so a grey panel over a working page is not confused with a dead
  window. A blank frame is still saved — and recorded as blank.
* `observe_page()` — a screen described in facts an agent can act on when there
  are no pixels at all, including whether it is painted rather than built.
* Every frame is indexed in `screenshots/quality.jsonl`; the recording ends with
  `screenshots/vision-summary.json`.

**Proved by** `tests/test_page_vision.py`, against real Chrome on pages that are
blank, partly blank, drawn, and ordinary.

### Phase 2 — Know what a screen offers · **done**

A recording spent on a screen that nothing can identify is a wasted afternoon,
and we only found that out afterwards.

* `recordings/probe.py` — asks one open screen every question that decides how
  it can be automated: is there a Nexacro object model behind it (named forms
  and components, grids bound to datasets), can Chrome name its controls, do its
  markup ids survive a reload, can anything see the window. It answers with the
  identity and the proof to build steps on, and says plainly when a screen
  carries neither.
* `smartops probe <system>` for the real screen, with the real profile; live
  during a recording at `/api/recordings/{id}/capabilities`.

**Proved by** `tests/test_capability_probe.py`, including a Nexacro-shaped screen
whose markup is deliberately useless — it scores well only through the object
model.

**Why the dataset matters more than the picture.** A bound grid's row count going
from 0 to 758 is direct proof that a query ran. It costs nothing, it cannot be
faked by a click, and it survives a machine that refuses to show the browser at
all. That is the single most valuable thing the probe looks for.

### Phase 3 — Run the probe on the real screen · **next, and it needs you present**

Everything after this is shaped by what the answer is, so it comes before more
code, not after it.

Open G-MES on the working report screen and run `smartops probe gmes --wait 60`.
The report says which sensors are real there. Concretely, the three answers that
change the plan:

* **Nexacro answers and its grids are bound** → Phase 5 becomes the highest
  value work in the project, and screenshots stop mattering much.
* **Nexacro is absent or its application is unreachable** → Phase 4 and the
  accessibility sensor carry the weight instead.
* **No route can see the window** → confirmed, written down, and worked around
  rather than fought. We do not attempt to defeat the company's capture
  protection.

**Stop condition:** if the probe reports no usable identity, do not record that
screen. Find the screen that does.

### Phase 3b — Survive the shapes real applications actually have · **done**

Seven ordinary things about modern web applications used to end a recording
without saying so. Each one now has a page in `tests/recorded_site/public/torture/`
that reproduces it, a capture path that handles it, and a replay path that can
repeat it.

| What broke it | What was happening | What happens now |
| --- | --- | --- |
| A control inside a web component | The browser reports the *host*, so the recording held the wrapper and replay clicked nothing | The composed path gives the real element |
| Ids regenerated on every load | A locator that is valid and matches nothing tomorrow | Generated ids rank last, behind what the control is *called* |
| A menu that opens on hover | Only the click was recorded; replaying it found a closed menu | The revealing hover is recorded as its own step — including CSS-only menus, which fire no event and change no attribute |
| An export behind a native confirmation | Playwright cancelled it invisibly, and no file was ever made | The dialog is answered, recorded with its message, and answered again at replay |
| A rich-text field | It has no `value`, so what was typed was simply absent | Typing into a contenteditable is captured like any other field |
| A form two frames deep | — | Kept, with its frame identity, so it replays against the right document |
| A right-click, and a drag | Both discarded silently — one as "not a left click", the other as a stray hand | Recorded as their own actions, with both ends of the drag |
| A filter with several choices picked | A list reports only its *first* selection, so a report filtered by three plants replayed filtered by one — and came back smaller, and looked valid | Every chosen option is recorded, replayed, and proved as a set |
| A click that only changes the address | An SPA route change makes nothing appear or vanish, so the click had no evidence at all | The route change is proof, credited to the click that caused it rather than the step after it |

The rule these share is the one in the last section: **a gesture the platform
cannot represent must fail loudly, never disappear.** Two of these tests exist
only to prove the failure — a plan missing its hover, and a drag with no
destination, both of which now stop rather than click somewhere arbitrary.

### Phase 3c — Anything done in a popup window · **done, and it was a hole**

A corporate sign-in opens in a popup. Everything the person did in it — the
username they typed, the button they pressed — was recorded as if none of it
had happened. The recording showed a tab opening, then closing, and nothing in
between.

The cause was not the browser refusing to report events. A popup opened by
`window.open()` runs the capture script once, on the transient `about:blank`
the window starts with, and never again once the real page navigates in. The
window survives that swap; its document does not. Every listener was therefore
attached to a document nobody would ever act in again. Listeners now attach to
the window, which outlives the swap and sees the same events one step earlier
in the capture phase; the mutation observer that watches for revealed menus is
re-armed against whichever document is live; and a tab's *name* is now
remembered past the tab's own life, because a popup that closes itself was
taking the identity of its own steps with it.

### Phase 3d — What the rest of the industry already learned · **done**

Three techniques taken from how established automation tools survive real
enterprise screens, each adapted to this project's rule that it must fail
rather than guess.

**Find a field by the words beside it.** The signature technique of commercial
RPA — UiPath calls it an anchor — and the answer to the ordinary enterprise
form: inputs with no name, no test id, and an id the framework invents on every
load, sitting in a table cell next to the words "From date". The words are the
stable part. The recorder now reads the label tied to a field, or failing that
the nearest text to its left on the same line, and records
`input:right-of(:text-is("From date"))` alongside the field's own identities —
ranked above an invented id, because a label outlives a redesign that renumbers
everything else. Verified against the browser before being adopted: Playwright's
layout selectors order matches by distance, so the nearest field to the label
is the one that wins.

**Prove a query by what changed, not by what exists.** The single most valuable
thing found in the research, because it is the exact shape of this project's
hardest step. The results grid is usually already on screen holding the previous
answer, so nothing appears and nothing vanishes when the query runs: a check
that the grid is visible passes instantly, against stale rows, and the run
reports success for a query that never ran. Every serious testing guide names
this — the element exists but its content is stale — as a top cause of false
passes. The recorder now records how much each container holds (a count of
descendants and a length of text, never the text itself), the compiler turns a
container that actually filled into a `content_changed` proof, and the engine
measures that container before the step and waits for it to differ after.

**Say what the page has now.** Self-healing tools respond to a locator that no
longer resolves by scoring the live page against a stored description of the
element and swapping in the closest match at runtime. The scoring is the good
half; the swapping is a business decision — the difference between "Search" and
"Submit" is not a lookup — and this platform is not allowed to make it. So the
recorder stores what a person would say about a control (what kind of thing,
what it is called, the words beside it, roughly where on screen), and a run that
cannot find it names the closest thing the page now has, in that control's real
name, and stops: *"the button called 'Inquiry' is no longer on this page. The
closest thing on it now is a button called 'Search'. Nothing was clicked."*

### Phase 3e — Describe each control once · **done**

The keystone, and the piece that changes the shape of everything else.

Until now every step carried its own private copy of how to find its element.
Twelve steps pressing the same Inquiry button held twelve locator lists, so a
site that renamed that button broke twelve steps and needed twelve separate
repairs — each one a fresh chance to get it slightly wrong. Nothing connected
them, so "what does this automation actually depend on?" had no answer at all.

`recordings/elements.py` is the answer commercial platforms reached long ago: a
system holds screens, a screen holds controls, and a step names a control
instead of restating how to find it. Repair the control once and every step
that names it is repaired — proved by a test that takes a plan nobody edited,
whose button has been renamed, and gets it running with one repair.

Two rules keep it safe. **A step never depends on the repository existing**: its
own locators stay on it, the repository is consulted first only because it is
the fresher description, and a plan made before any of this behaves exactly as
it did. And **nothing here decides anything** — that a renamed control is "the
same" control is a person's call, made once, in review.

Three things fall out of it:

* **Several anchors, not one.** A screen with "Plant" above a column of
  identical dropdowns defeats a single anchor: the words are there, just
  several times. Each control now records up to three stable neighbours with
  the direction each sits in.
* **A check before the browser closes.** A recording is normally judged when
  somebody tries to replay it, days later, which is a bad moment to learn a
  step was ambiguous from the start. Every recorded control is now asked for
  while the browser is still open, and both failures are caught: nothing
  matched, and *several* matched.
* **Taking back a mis-click.** A stray click used to mean throwing the whole
  recording away, which is why long tasks stopped being recorded. The last step
  can be removed while the browser is still open; the next action takes the
  number it gave up, so nothing already referring to an earlier step shifts
  underneath it.

### Phase 4 — One timeline instead of five subsystems · **done**

Until now each sense keeps its own notes: steps in the database, frames on disk,
network in a summary file, tabs in the worker's memory. An assistant asked "what
happened after the last click" has to reassemble that from four places, and a
step's proof is chosen from whichever fragment happened to be at hand.

Build one record per action — what was done, where, what the page looked like
before and after, what changed, what could prove it — and have every sense write
into it. Nothing new is captured in this phase. It is the same evidence, put in
order, which is what makes the two phases after it possible at all.

**Done when** one action's full before/after is one object, and the recorder's
existing steps are built from it rather than beside it.

### Phase 5 — Ask the application, not the page

Only if Phase 3 says Nexacro is really there.

The probe already reads the object model once. This phase reads it around every
action: which component was pressed, by its own name inside the application; and
what its datasets did afterwards. A step then says *"pressed the component
called btnInquiry; the grid's dataset went from 0 rows to 758"* instead of
*"clicked at 71% of a div"*.

This is the phase that would have prevented the failure that started all of
this — a report requested with no organisation selected, which every sensor we
had called a success.

**Done when** a recorded step carries a component identity and, where a grid is
involved, a row-count change as its proof.

### Phase 6 — Prove the download from the export that made it

A file arriving is good evidence. A file arriving *because the export we watched
start finished* is better, and it is what separates the real report from a login
page saved under an Excel name.

Correlate the export the application began, the browser's download, and the
validator's verdict on the bytes. The validation half already exists and works;
this connects it to its cause.

### Phase 7 — Give the assistant real instruments · **done**

The current Recording Coach receives no page, no timeline, no state — it offers
generic advice at the start and the interface then calls it "watching". It is
not watching.

Replace the advice with read-only instruments over the Phase 4 timeline: what
just happened, what changed, what the current screen offers, where the proof is
thin. Send differences, not full snapshots, so following a long recording stays
cheap. `/api/recordings/{id}/live` is the first of these and already works.

**The line that does not move:** during a human recording the assistant reads and
explains. It does not click. You have the hands; it has the memory and the
arithmetic.

### Phase 8 — Say which steps are weak, while they can still be redone · **done**

Score each step on how well it was identified, how observable its effect was,
and how safely it can be repeated. Say so during the recording — *"that step is
weak, please do it again more slowly"* — instead of discovering it forty minutes
into a replay.

This is the phase that pays back the previous four, and it cannot be built
before them: a score is only worth having once there are several senses to
disagree.

### Phase 9 — Break every sensor on purpose · **started**

The torture lab exists (`tests/torture_lab` and `tests/torture_replay`) and
covers the capture shapes above. Still to add as the later phases land: a grid
that fills late, a popup login, an HTML login page named `.xlsx`, and a sensor
that is switched off mid-recording.

The test is not that the recorder succeeds. It is that when a sensor dies, the
recorder notices and uses the next one — and when none is left, it says so
rather than guessing.

### Phase 10 — The controlled corporate run

One real run, with the operator present, per `tasks/CURRENT.md`. It stops at the
first failure and is diagnosed from sanitized evidence. Three consecutive clean
unattended runs before anything is approved or scheduled.

## Where these ideas came from

The techniques in Phase 3d were taken from how established tools handle the
same problems, then adapted rather than copied — every one of them had to be
reconciled with failing closed:

* [UiPath — advanced descriptor configuration](https://docs.uipath.com/activities/other/latest/ui-automation/advanced-descriptor-configuration)
  and [fuzzy selectors and anchors](https://apix-drive.com/en/blog/other/fuzzy-selector-vs-strict-selector-uipath):
  anchoring an element to a stable neighbour. Adopted. Their fuzzy matching —
  accepting an element whose attributes merely *resemble* the recorded ones —
  was not: a Levenshtein-scored near-match is precisely the guess this platform
  refuses.
* [Playwright's locator guidance](https://playwright.dev/docs/locators): prefer
  what a control *is* and what it is *called* over how it is built. Already the
  recorder's ordering; the research confirmed the priority and prompted the
  label-anchored fallback beneath it.
* [Flaky-test analyses of Playwright suites](https://mergify.com/learn/flaky-tests/playwright):
  the element exists but holds stale data, and `networkidle` never settles in a
  single-page application. Both shaped `content_changed`; the settle helper
  already caps its wait rather than trusting network quiet.
* [Healenium and the self-healing category](https://qaskills.sh/blog/healenium-selenium-self-healing-guide):
  score the live page against a stored fingerprint of the element. Adopted as
  diagnosis. Rejected as action.
* **UiPath's object repository** — an application holds screens, a screen holds
  elements, and a workflow refers to an element rather than repeating a
  selector. Adopted whole in `recordings/elements.py`; it is the single
  highest-leverage idea found in any of this research.
* **Automation Anywhere's live recapture** — showing what was captured while
  recording, so a wrong click can be taken back before the recording ends.
  Adopted as undo.
* **OpenAdapt's refusal to overwrite** — a healed workflow is written beside the
  original for review, and a run with no trusted candidate stops rather than
  guessing. Already this project's rule; the fingerprint diagnosis follows it.
* **OpenAdapt's authentication handoff** — capture pauses through sign-in and
  resumes after. Already true here for a stronger reason: authentication
  completes *before* any capture facility is installed, so a credential cannot
  enter a screenshot, a trace, or a step even in principle.
* [Stagehand, Skyvern and the agent-driven frameworks](https://www.skyvern.com/blog/browser-use-alternatives/):
  their answer to a changed page is to let a model decide at run time. This
  project deliberately keeps the model out of the run: it helps compile the
  plan, and deterministic replay executes it.

## What is deliberately not in this plan

Native Windows UI automation, remote-desktop fallbacks, OCR, visual grounding,
and network-level replay are all real techniques and none of them is needed to
find out whether the first flow works. They stay out until it does.

The same goes for the business side, unchanged from `tasks/CURRENT.md`:
no multi-system orchestration, no department linking, no self-healing, no
analytics.

## The rules the phases do not get to break

* **One engine acts.** Sensors observe; Playwright drives. Two things moving the
  same browser is not redundancy, it is a race.
* **Replay is deterministic.** The assistant helps compile the plan. It does not
  execute it.
* **Fail rather than guess.** A missing or ambiguous target stops the run.
* **No absolute screen coordinates**, ever. A press is a fraction of a known
  element or it is not a press.
* **Nothing private leaves the machine**, and nothing sensitive enters the
  repository — no company URLs, credentials, sessions, recordings, screenshots,
  traces, or downloaded reports.
* **A click is not a result.** A step is proved by what the page did, and a run
  is proved by a validated file.
