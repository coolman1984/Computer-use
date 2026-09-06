# Replay Automation Fix Plan

**Project:** SmartOps / G-MES pilot  
**Date:** 2026-09-06  
**Broken stage:** Record manually ✅ → **Replay automatically ❌** → Validate → Approve → Schedule  
**Status of this document:** Diagnosis complete from retained evidence and source review. No code changed yet. Nothing in this file contains credentials, cookies, corporate hostnames, or user names.

---

## 1. One-paragraph answer

The automatic replay is not failing because the recorded steps are wrong, and not primarily because of a Chrome profile lock. It fails inside the unattended login adapter **after the SSO login has already succeeded on the server**. G-MES completes AD SSO inside the same browser document: it mounts the signed-in frames and the Notice dialog while leaving the login frame in the DOM in a state Playwright still reports as visible. SmartOps' rule "a visible login control always means signed out" then classifies a signed-in page as signed-out, the Notice close and tab search time out, and the run is reported as an authentication failure. The evidence for this is that the recording attempt immediately after such a "failure" started already signed in and completed the export. Two smaller defects amplify the problem: the replay adapter discards the page that authentication found, and the Notice timeouts written in the system YAML never reach the adapter.

---

## 2. What the retained evidence shows

| # | Evidence | Where | What it proves |
|---|---|---|---|
| E1 | Sanitized auth diagnostic from a failed recording attempt: one open page, on the entry route, `login_visible: true`, `notice_titles: 2`, close controls present for both the top frame and the portal Notice dialog | recording artifact `session/auth-diagnostic.json` | After SSO returned, the signed-in frames and the Notice were mounted **in the same tab**, no popup or relay tab survived, and the login marker still counted as visible. SmartOps declared failure at stage "following the signed-in tab". |
| E2 | The very next recording attempt (same system, minutes later) completed 7 steps and 1 workbook download with no login diagnostic | recording manifest and `steps.jsonl` | The SSO login in E1 had succeeded server-side. The dedicated Chrome profile now held the session, so the next load skipped login entirely. Login works; **post-login detection does not**. |
| E3 | All 7 recorded steps happened on the same URL as the sign-in page, in the main tab, with no frame | `steps.jsonl` | G-MES is a single-document Nexacro app. The plan's `start_url` equals the login URL, so a replay that succeeds at login will not need to navigate again. There is no replacement-tab topology in the successful recording. |
| E4 | The latest replay run failed at the same stage as E1 and left a trace that was too small to use, with no sanitized diagnostic | incident evidence folder for that run | Replay has the same detection defect as the recorder had, but writes less diagnostic evidence than the recorder does. |
| E5 | An earlier replay failure screenshot shows the G-MES native login form populated with a user name and a masked password, a loading spinner, and English selected | incident evidence screenshot | (a) Failure screenshots on the auth path capture the user name and must be redacted. (b) Either Chrome autofill inside the automation profile or the adapter filled the native form. This must be ruled out; see experiment X4. |
| E6 | Google Chrome on this machine is version 152. The config passes `--load-extension` to branded Chrome | Chrome installation folder, `config/system.yaml` | Branded Chrome ignores `--load-extension` since Chrome 137, and the feature-flag workaround was removed in Chrome 142. The corporate SSO extension is **not** being side-loaded by that argument. If it is present in the automation profile at all, it is because machine policy force-installs it. |
| E7 | `notice_timeout_ms` and `notice_probe_timeout_ms` are set in the system YAML | `config/systems/*.yaml`, `src/smartops/workflows/profiles.py` | The auth profile parser has no fields for them, so the adapter always uses its 30 s and 3 s defaults. Saving the system from the web UI would delete them. |
| E8 | Replay passes no `on_authenticated_page` callback | `src/smartops/adapters/browser/playwright_engine.py` `_ensure_authenticated` | Whatever page authentication finds is discarded; replay continues on the original page object. Harmless for E3's same-tab case, fatal for any replacement-tab case. The recorder already adopts the page correctly. |
| E9 | Persistent profile mode never passes `storage_state`, and manual sign-in launches Chrome without the shared session factory | `session.py`, `login.py` | The real session lives in the dedicated Chrome profile. The JSON session file the journey gate checks is not what replay uses. |
| E10 | `browser.max_concurrency` is 2 with a single persistent profile | `config/system.yaml` | Two concurrent runs would launch two Chromes against one user-data directory; the second fails at launch with "Failed to launch the browser process". |

**What the evidence cannot show:** which of the three post-login predicates timed out first (Notice close via the explicit selector, the "logged-in marker visible" check, or the "login marker hidden" check). The retained trace is too small. Step 1 of the plan adds instrumentation so the next failure, if any, answers this in one run.

---

## 3. Root-cause model, ranked

| Rank | Cause | Status | Discriminating experiment |
|---|---|---|---|
| 1 | Signed-in detection treats a still-mounted but covered login frame as "signed out". Nexacro switches frames without `display:none`, so `is_visible()` stays true. | **Verified by E1 + E2** | X1 |
| 2 | Notice close via the explicit selector does not complete (element present and "visible" but not receiving pointer events, or the click waits on actionability until the run timeout). | Strong hypothesis (E1 shows the Notice still open at failure) | X1 |
| 3 | Replay discards the authenticated page and could navigate a stale tab. | Verified code defect; not the trigger in the same-tab case | X2 |
| 4 | Chrome autofill or wrong-form fill on the native G-MES login form. | Open (E5) | X4 |
| 5 | "Already open in background" is a G-MES per-account single-session guard triggered by the operator's own daily-Chrome session, not by SmartOps' Chrome. | Open | X5 |
| 6 | Profile lock or restored tabs from an unclean shutdown. | Weak for the last run (Chrome launched and login progressed for ~80 s), but a real risk for future runs | X3 |
| 7 | Wrong stored credential. | Contradicted by E2 | none needed; do not retry credentials |

---

## 4. Research findings that shape the fix

Playwright and Chrome behaviour:

- A persistent context starts with one `about:blank` page; `storage_state` is not an option of `launch_persistent_context`. Automating a daily-use Chrome profile is unsupported; use a dedicated automation profile. Source: Playwright BrowserType docs and `chromium.ts` default args.
- Playwright does **not** pass `--hide-crash-restore-bubble`; after an unclean shutdown Chrome marks the profile as crashed and offers session restore. Pass the flag explicitly or reset the exit state before launch. Sources: Playwright `chromiumSwitches.ts`; UiPath and Selenium community notes on the restore bubble.
- Chromium's process singleton is keyed on the user-data directory. A second Chrome against the same directory hands its URLs to the running instance and exits; Playwright then fails fast with "Failed to launch the browser process". Puppeteer detects this by checking for a `lockfile` in the user-data root on Windows. Sources: Chromium `process_singleton.h`, Puppeteer commit adding the lockfile check.
- Branded Google Chrome removed `--load-extension` in Chrome 137; the `DisableLoadExtensionCommandLineSwitch` feature-flag workaround was removed in Chrome 142. Extensions in branded Chrome come from `ExtensionInstallForcelist` / `ExtensionSettings` policy or the Web Store. Sources: Chromium extensions group PSA, Chrome developer blog June 2025, SeleniumBase issue 4053, Chrome Enterprise policy docs.
- Arm `expect_popup` / `expect_page` / `wait_for_url` **before** the triggering click. A popup that closes itself should be waited on via `wait_for_event("close")` while the opener is checked for the signed-in state. Sources: Playwright pages and navigations docs.
- Event handlers must only record facts and queue work; Playwright calls belong on the main control flow. Downloads are page-level events, so new pages need their own listener. Sources: Playwright downloads docs, playwright-python issues.
- `pytest-timeout` on Windows can only use the `thread` method, which kills the whole process on timeout. Acceptable for CI, but use generous per-test values. Source: pytest-timeout PyPI page.

Authentication and unattended operation:

- Store the session once, reuse it across jobs, re-authenticate only on expiry, never per job. Sources: Playwright auth docs, Checkly and BrowserStack guidance.
- Active Directory account lockout thresholds are commonly 10 attempts; retried connections count. One credential submission per run is the correct policy and is already enforced. Sources: Microsoft account-lockout guidance.
- A headed browser needs the interactive desktop. Scheduled work should run under "Run only when user is logged on" or an equivalent interactive token; Session 0 and disconnected RDP sessions break headed automation. Sources: Microsoft Task Scheduler security contexts, Session 0 isolation article, UiPath session troubleshooting.
- CDP `Debugger.setSkipAllPauses` after `Debugger.enable` neutralises `debugger` anti-automation loops. Source: Chrome DevTools Protocol Debugger domain.

Nexacro specifics:

- No public Tobesoft documentation describes a duplicate-instance guard or the frame-switching implementation. Treat the "already open" message as application-level and confirm its trigger with experiment X5. Nexacro renders ordinary components as DOM and Graphics as Canvas; id-path selectors that include MDI window instance counters (the `_0_900` style suffix seen in the recording) are fragile and need fallbacks.

---

## 5. Solution design

### 5.1 Signed-in detection that survives Nexacro frame switching

Replace the boolean `session_expired()` with a small classifier that returns one of `signed_in`, `signed_out`, `notice_open`, `transitioning`, `unknown`, based on **independent signals evaluated together**:

1. **Login control receives pointer events.** Use `locator.click(trial=True, timeout=short)` on the login marker. Trial click runs Playwright's actionability checks, including "receives events", without clicking. A login frame that is mounted but covered by the application frame fails this check. This replaces `is_visible()` as the meaning of "login visible".
2. **Positive signed-in marker, counted across all matches.** Use `locator.filter(visible=True).count() > 0` (Playwright ≥ 1.51) instead of checking the first 50 matches of a prefix selector. Prefer a specific control inside the top frame (for example the port close button seen in E1) over a subtree prefix.
3. **Notice open.** Visible dialog title text equal to "Notice", or the explicit close selector visible. This is a separate state, not a failure.
4. **Hit test.** `document.elementFromPoint` at the centre of the login control, reported as "login on top" or "covered by <coarse role>". Sanitized; no text captured.

Decision rule: `signed_in` when signal 2 is true and signal 1 is false. `notice_open` when signal 3 is true regardless of 1. `signed_out` only when signal 1 is true **and** signal 2 is false. Anything else is `transitioning` and is polled until the stage timeout.

`ensure_authenticated()` returns a structured `AuthenticationResult` (status, canonical page, stage reached, timings per stage, whether a saved session was used, whether a popup opened, whether the original page closed, sanitized per-page diagnostics). The string return is kept as a property for existing callers during the migration.

### 5.2 Notice handling

- Parse `notice_timeout_ms`, `notice_probe_timeout_ms`, and `login_success_timeout_ms` into the auth profile and its YAML round-trip so the configured 60 s applies.
- Close the Notice with the explicit selector first using a **bounded** click (`timeout` of a few seconds, `trial=True` first). If the click cannot receive events, fall back to the scoped DOM search already implemented, then to `Escape` on the dialog. Record which path closed it.
- After the Notice closes, re-run the classifier; do not assume success.

### 5.3 Canonical page adoption in replay

- Pass `on_authenticated_page` from the replay adapter, exactly as the recorder does, and make `ReplaySession.adopt(page)` set `_current` and track the page.
- Only navigate to `start_url` when the canonical page is on a different **path**; a same-document app must not be reloaded after login (E3).
- Inventory pages at context start. Close only pages that this context created, are not the canonical page, and are on the portal host or `about:blank`. Never touch pages the automation did not create.
- Add fixture tests for: same-tab transition with covered login frame (the G-MES case), popup that closes itself, popup that replaces the opener, relay tab plus application tab, and a valid session with a Notice on top.

### 5.4 Persistent profile hygiene

- Single-owner guard: before launching, check for Chrome's `lockfile` in the automation user-data root and for a SmartOps owner file with PID and start time; refuse to launch with a clear message when another live owner exists; recover a stale owner file when its PID is gone.
- Add `--hide-crash-restore-bubble` to persistent launches; optionally reset the profile's exit state to normal before launch.
- Set `browser.max_concurrency` to 1 while a single persistent profile is the design, and make the doctor warn when it is higher.
- Keep the automation profile separate from daily Chrome (already true). Document that the JSON session file is a secondary copy under persistent mode and that the profile is the session (E9). Route manual sign-in through the shared session factory so it signs into the same profile.

### 5.5 Extension provisioning

- Stop treating `--load-extension` as the mechanism (E6). Keep `ignore_default_args=["--disable-extensions"]` so policy-installed extensions can run.
- Add a doctor check that opens the automation profile and lists installed extensions via `chrome://extensions` or the profile's `Extensions` folder and `Preferences`, and reports whether the corporate SSO extension is present and enabled.
- If it is absent, the decision is with corporate IT: `ExtensionInstallForcelist` at machine level applies to every profile, including the automation profile. Determine first whether the SSO popup flow needs the extension at all; E1 suggests the popup form rendered and submitted without evidence either way.

### 5.6 Diagnostics and evidence

- Move the recorder's `_write_auth_diagnostic` into a shared module and call it from replay, extraction, and recording. Per page record only: scheme and host class (portal / identity provider / other), path, closed flag, classifier result, per-signal booleans, Notice count, and stage timings. No text, no ids longer than the last segment, no screenshots of the login stage.
- Redact the failure screenshot on the auth path: inject a stylesheet that blanks `input` values before capture, or skip the screenshot when the stage is before "verifying the signed-in page".
- Wire `IncidentPackBuilder` into `_open_incident` so every failed run gets a registered pack path.
- Settle running step rows when recovery fails a stranded run.

### 5.7 Out of scope for this fix, tracked separately

Dynamic Nexacro window-id suffixes in selectors, plan review requiring proof on every click, username exposure through unauthenticated GET routes, the Claude CLI permission flags, and pytest timeouts. Each is real but none blocks the replay stage once login detection and page adoption are correct.

---

## 6. Implementation steps

Order matters. Each step has files, tests, and an exit condition. Do not start a corporate replay before step 6.

### Step 0 — Freeze

- No new G-MES Test, Run, or credential prompt until step 6.
- Confirm no SmartOps Chrome is running against the automation profile; if a `lockfile` exists in the automation user-data root with no live Chrome, delete only that file.
- Keep the process in `test_failed`, unapproved, unscheduled.

### Step 1 — Instrument before changing behaviour

Files: `src/smartops/adapters/browser/authentication.py`, new `src/smartops/adapters/browser/auth_diagnostics.py`, `src/smartops/adapters/browser/playwright_engine.py`, `src/smartops/recordings/worker.py`.

- Extract the sanitized per-page diagnostic into the shared module; add the classifier signals and stage timings to it.
- Call it from replay on any auth-stage failure, writing to the run's evidence directory.
- Redact inputs in the auth-stage screenshot.

Tests: unit tests with page doubles asserting the diagnostic contains no text or values, and that replay writes it on auth failure.

Exit: a failed replay leaves a diagnostic that names the failing predicate and the stage timings.

### Step 2 — Configuration round-trip

Files: `src/smartops/workflows/profiles.py`, `src/smartops/workflows/builtin.py`, `tests/test_profiles.py`.

- Add the three timeout fields to the auth profile, the parser, `to_run_params`, `_auth_filters`, and the YAML writer.

Tests: parse → dict → YAML → parse round-trip keeps the values; defaults apply when absent.

Exit: the configured 60 s Notice timeout reaches the adapter.

### Step 3 — Signed-in classifier and Notice handling

Files: `authentication.py`, `tests/test_popup_login.py`, new `tests/test_auth_classifier.py`, a new fixture page under `tests/recorded_site/` that mimics the Nexacro transition (login div stays in the DOM, application div is mounted over it with a higher z-index, a Notice dialog appears, an SSO popup posts a message and closes itself).

- Implement the classifier and `AuthenticationResult`.
- Replace the "visible login wins" rule with the decision rule in 5.1.
- Bound the explicit Notice click and add the fallbacks in 5.2.
- Keep exactly one credential submission per run.

Tests: doubles for each state; one real-browser test against the fixture that fails on the old code and passes on the new code.

Exit: the fixture that reproduces E1 is classified `signed_in` after the Notice closes.

### Step 4 — Canonical page adoption and page inventory

Files: `playwright_engine.py`, `replay.py`, `tests/test_recorded_actions.py`, `tests/test_popup_login.py`.

- Add `ReplaySession.adopt(page)`; pass the callback; guard the `start_url` navigation by path comparison; inventory and close stale automation-owned pages.

Tests: the five topology fixtures from 5.3, asserting which page replay acts on and that no reload happens for a same-document app.

Exit: replay always performs step 1 on the page the classifier marked `signed_in`.

### Step 5 — Profile hygiene and extension check

Files: `session.py`, `src/smartops/checks.py` or the doctor command, `config/system.example.yaml`, `docs/RECORDING_OPERATIONS.md`.

- Single-owner guard, crash-bubble flag, concurrency warning, manual sign-in through the shared factory, extension presence check in doctor.

Tests: owner-file logic with fake PIDs; launch-argument assertions; doctor output assertions.

Exit: a second concurrent launch is refused with a clear message; doctor states whether the SSO extension is present in the automation profile.

### Step 6 — Headed smoke test on the local fixture

- Run the fixture end to end in visible Chrome with the persistent automation profile: sign-in with popup, Notice, same-document transition, two clicks, one download, validation. Repeat after a simulated unclean shutdown.

Exit: green on real Chrome, evidence pack complete, no secrets in any artifact.

### Step 7 — Exactly one controlled G-MES Test

- `python -m smartops doctor` clean.
- Open the web app through the mandated Chrome launcher; trigger **Test** once; do not touch automation-owned windows.
- On failure: stop, read the step-1 diagnostic, do not retry credentials.
- On success: confirm 7 step results, 1 download, workbook validation, file registration, and no open incident.

Exit: one complete controlled run with a registered validated workbook.

### Step 8 — Repeatability, then approval

- Normal saved session; expired session with one login; invalid credential without retry; interruption during an unsafe step; stale owner file; download timeout.
- Three consecutive successful Tests without manual intervention, then Approve the tested revision, then Schedule with concurrency 1.

---

## 7. Experiments that settle the open hypotheses

| Id | Question | Method | Reading the result |
|---|---|---|---|
| X1 | Which post-login predicate fails, and is the login frame covered rather than hidden? | Step 1 diagnostic on the next failed attempt, or a one-off headed run of the classifier signals on the fixture and on G-MES | "login on top: false, signed-in marker visible: true, Notice: open" confirms rank 1 and 2 |
| X2 | Does replay ever act on a stale page? | Step 4 tests plus the diagnostic's canonical-page id | Same page id in "adopted" and "step 1 performed on" |
| X3 | Does the automation profile restore tabs or hold a stale lock? | Kill Chrome mid-run, relaunch with and without `--hide-crash-restore-bubble`, inventory pages at start | Restored portal tabs appear only without the flag |
| X4 | Is the native login form being autofilled? | Inspect the automation profile's saved passwords and autofill settings; run the classifier with the form untouched | If autofill is present, disable password manager and autofill in the automation profile via policy or preferences |
| X5 | Is "already open in background" a per-account guard? | With the operator signed in to G-MES in daily Chrome, load G-MES in the automation profile once, no credentials submitted | If the message appears, unattended runs need a time window when the operator is signed out, or a service account |

---

## 8. Risks and how the plan bounds them

- **Account lockout.** Unchanged: one submission per run, no automatic retry, stop and alert on rejection or MFA.
- **Wrong classification in the other direction** (declaring signed-in when not). The decision rule requires a positive marker that only exists after login, and step 1 of the replay must still prove itself. A misclassification fails at step 1 with evidence, not at the download.
- **Fixture does not match G-MES exactly.** The fixture reproduces the mechanism (covered login frame, Notice, self-closing popup). X1 on the real system confirms before step 7.
- **Extension policy is outside SmartOps' control.** The doctor check makes the state visible; the decision is escalated to IT rather than worked around.
- **Regression in recording.** The recorder shares `ensure_authenticated`; step 3 tests cover both callers, and the recorder's existing "login before capture" test stays green.

---

## 9. Definition of done for "Replay automatically ✅"

- The Nexacro-style fixture and all five topology fixtures pass on real headed Chrome.
- Replay adopts the classifier's signed-in page and never reloads a same-document app after login.
- Configured Notice and login timeouts reach the adapter.
- Every auth-stage failure leaves a sanitized diagnostic naming the failing predicate; no screenshot contains a user name.
- The automation profile has a single-owner guard, the crash-restore bubble is suppressed, and concurrency is 1.
- Doctor reports the SSO extension state in the automation profile.
- One controlled G-MES Test completes with a registered validated workbook, followed by three consecutive successes.
- No secret, cookie, screenshot with identity, trace, download, or company data is tracked by Git.

---

## 10. Sources

- Playwright BrowserType API (persistent context options, profile warning): https://playwright.dev/python/docs/api/class-browsertype
- Playwright default Chromium switches: https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/chromium/chromiumSwitches.ts
- Playwright pages, popups, navigations, downloads, authentication: https://playwright.dev/python/docs/pages , https://playwright.dev/python/docs/navigations , https://playwright.dev/python/docs/downloads , https://playwright.dev/python/docs/auth
- Playwright Chrome extensions guidance: https://playwright.dev/python/docs/chrome-extensions
- Chromium PSA on removal of `--load-extension` in Chrome 137: https://groups.google.com/a/chromium.org/g/chromium-extensions/c/1-g8EFx2BBY/m/S0ET5wPjCAAJ
- Chrome developer blog, June 2025 extension news: https://developer.chrome.com/blog/extension-news-june-2025
- SeleniumBase issue on the Chrome 142 removal of the feature-flag workaround: https://github.com/seleniumbase/SeleniumBase/issues/4053
- Chrome Enterprise `ExtensionInstallForcelist`: https://chromeenterprise.google/policies/extension-install-forcelist/
- Chromium process singleton: https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/browser/process_singleton.h
- Puppeteer lockfile detection: https://github.com/puppeteer/puppeteer/commit/8d3a60b99629ec345b34dae9687057d3a9261dc5
- Crash-restore bubble suppression: https://forum.uipath.com/t/chrome-crash-restore-bubble-restore-files-and-how-to-disable/573261
- Chrome DevTools Protocol Debugger domain: https://chromedevtools.github.io/devtools-protocol/tot/Debugger/
- Microsoft account lockout threshold: https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/account-lockout-threshold
- Task Scheduler security contexts: https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks
- Session 0 isolation: https://techcommunity.microsoft.com/blog/askperf/application-compatibility---session-0-isolation/372361
- CredUI prompt APIs: https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-creduipromptforwindowscredentialsw
- pytest-timeout: https://pypi.org/project/pytest-timeout/
- Playwright sync API threading constraints: https://playwright.dev/python/docs/library
