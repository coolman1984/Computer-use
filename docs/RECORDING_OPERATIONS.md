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
