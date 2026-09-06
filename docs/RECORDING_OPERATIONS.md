# Running and Maintaining the Recording Center

Set `SMARTOPS_RECORDINGS_DIR` and `SMARTOPS_RECORDINGS_BACKUP_DIR` to two
private folders outside the repository before recording any real system. Do
not put backups, session files, HAR files, or traces inside Git or a public
shared space.

Start the platform as usual with `python -m smartops serve`. When services
are wired up, recordings that stopped with no heartbeat are settled to
`interrupted`; their steps or partial evidence are not deleted, and a
re-record can be started from them.

After finishing a recording, open its review. Every step exposes its captured
scope, ordered selectors, non-secret input, proof, timeout, checkpoint and retry
policy. Safe fields may be corrected there. A position-only or unproven step is
blocked and must be re-recorded unless a real selector and observable proof can
be supplied. An unsafe step can never be made repeatable in review.

Long Nexacro selectors are stored intact. If several controls are drawn inside
one large browser element, the click point is stored relative to that element
and scales with its current size. It is not an absolute desktop coordinate.
Recordings made before this rule may contain shortened selectors and must be
recorded again; the missing selector text cannot be reconstructed safely.

The Recording page is the recorder controller; no separate native controller
window is required. The target system opens in a second headed Chrome window,
and the page shows live action, tab, proof-gap, protected-credential, and
download counts plus the exact next action. A completed recording requires a
detected download; stopping before that marks it incomplete rather than creating
a false success. Stopping performs a final flush before closing Chrome.

The read-only Recording Coach starts before the recorder and displays its
guidance on the same page. It receives only generic workflow structure—never
selectors, field values, URLs, page text, screenshots, cookies, downloads, or
company data—and capture continues with built-in guidance if Codex is off.

Both login usernames and passwords are stored only as credential references,
never as typed values or human-readable labels. The Sign-in page has no password
form: it opens a native Windows prompt whose child process writes directly to
Windows Credential Manager. A saved session is used first; if expired, the
bounded popup-login adapter may select a language, open SSO, fill the two
references, submit once, close the post-login notice, and save the refreshed
session. MFA, CAPTCHA, and interactive approvals remain human-only.

`browser.record_headless`, `SMARTOPS_DISABLE_WORKER=1`, direct internal workflow
calls and legacy YAML schedules are development tools. They work only when
`safety.allow_development_features: true` is deliberately set in an isolated
configuration. Keep it false in normal use.

Check status via `python -m smartops doctor` or `GET /health`. The check
shows whether the recording and backup paths are writable, and how many
recorder workers are active.

To create a restorable private backup, run:

```powershell
python -m smartops recordings-backup
```

This produces a local ZIP containing a consistent SQLite copy and the
private evidence tree. To restore, stop SmartOps, unpack the archive in a
private space, and replace both the SQLite database and the recordings
folder together, then run `python -m smartops recordings-recover` before
opening the UI.

Deleting from the UI is recoverable only. Permanent deletion is disabled by
default. To enable it, set `storage.recordings_retention_days` to a positive
value and `safety.allow_recording_purge` to `true` in a private setting,
then run `python -m smartops recordings-purge`. Every permanent deletion
logs an event, and it only touches a recording that is already in the trash
and past its retention period.

## Chrome recording exception

The repository's instructions mandate `windows-chrome-launcher` for opening
interactive links, because it guarantees the correct Windows desktop. The
Recording Center does not use the launcher: Playwright needs to create a
dedicated **Chrome persistent context** and attach to it directly to capture
DOM, network, downloads, and trace in the same session. The launcher only
opens Chrome and does not provide a CDP endpoint or a dedicated profile that
Playwright can control; using it would lose recording capture and could mix
the user's normal corporate profile with recording files.

So this is a narrow exception for recording only: `channel="chrome"` via
Playwright, a private profile under `recordings_dir/<id>/profile`, and a
headed window. It never uses the user's normal Chrome, nor `Start-Process`
or `chrome.exe` directly. If Chrome cannot appear on the user's desktop
because of a Windows service session, the recording fails clearly and needs
a worker bridge in the user's session; there is no fallback that captures
data from a normal Chrome instance.

Every page and popup in the recording context also enables Chrome DevTools
Protocol `Debugger.setSkipAllPauses`. Corporate SSO pages can contain
anti-debug `debugger` statements; without this setting an AD SSO popup can
remain grey with “Debugger paused in another tab” instead of rendering its
sign-in form. The recorder resumes a popup once as well, covering a pause that
happened before Playwright delivered the new-page event.
