# SmartOps Daily Download

SmartOps has one priority: repeat one report-download task in Google Chrome,
save the completed file, prove what it really is, validate it, and record the
result.

> This repository is public. Never commit company URLs, credentials, sessions,
> recordings, screenshots, traces, or downloaded reports.

## Start the app

On Windows, double-click `START.cmd`. It starts the web app and its small local
worker, then opens:

`http://127.0.0.1:8765/app/index.html`

Launcher logs are stored in `%LOCALAPPDATA%\SmartOps\launcher`.

## The one workflow

1. Add the system and report address.
2. Test the connection.
3. Sign in with the required Chrome profile.
4. Record the download once while SmartOps watches.
5. Build and review the replay plan.
6. Run one real test.
7. Confirm that the saved file is valid.
8. Approve and schedule only after that real test passes.

A successful click is not a successful run. Success means one completed file
was saved, its type was identified from its bytes, its validation rules passed,
and the run history contains the outcome.

## Download handling

Portal filenames and extensions are untrusted. SmartOps saves the finished
bytes first. If an extensionless file is an OOXML Excel workbook, SmartOps
detects its package structure and adds `.xlsx`. It then checks configured size,
row, and column requirements. A login page or error page downloaded under an
Excel-looking name fails validation.

## Browser rules

- Use Google Chrome and the configured profile and extensions.
- Keep one owner of a persistent Chrome profile at a time.
- Use DOM/browser replay for actions and screenshots only as evidence.
- Never store absolute desktop coordinates in an automation.

## Development rule

Keep the existing web app and one small replay/validation core. Fix the blocker
found in the real workflow, run only its focused regression test, then retry the
same app step. Do not introduce a second browser framework or a competing
automation path.

See [docs/ARCHITECTURE_MAP.md](docs/ARCHITECTURE_MAP.md) for what each module
owns and what depends on it — read it before adding one.
See [docs/DAILY_DOWNLOAD_FLOW.md](docs/DAILY_DOWNLOAD_FLOW.md) for the concise
operator and troubleshooting guide, and
[docs/RECORDER_ROADMAP.md](docs/RECORDER_ROADMAP.md) for how the capture side is
being developed and in what order.

## When the screenshots are grey

Some screens cannot be photographed: the machine, not the page, withholds the
browser's picture. SmartOps measures every frame it takes and will not pass off
a blank one as evidence. To find out what a screen does offer, open it and run:

```powershell
python -m smartops probe <system>
```

It clicks nothing. It reports which sensors that screen exposes — a framework
object model, accessible control names, stable ids, a usable picture — and which
identity and which proof a recording there should be built on.

## Optional diagnostics

```powershell
python -m smartops doctor
python -m smartops serve
```

The web app is the normal operating interface; commands are diagnostics only.
