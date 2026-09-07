# Daily Download Flow

## Goal

Use the existing web app with one small core that repeats a captured Chrome
task and finishes with one validated report file.

## Operator path

1. Open SmartOps and follow the highlighted next step.
2. In **Systems**, add the system and report URL, then test the connection.
3. In **Sign in**, open the configured Chrome profile and finish login.
4. In **Recordings**, start a recording and perform the download once.
5. Stop only after SmartOps has detected the download.
6. Build the plan and repair only a step the review says is unrepeatable.
7. Run one real test. Do not approve or schedule before it passes.
8. Open the run result and confirm the file is **Valid**.

## What valid means

- The download completed and its bytes were saved.
- The real format was identified from the file structure, not its name.
- An extensionless OOXML workbook was renamed with `.xlsx`.
- The file is not an HTML login or error page.
- Minimum size and configured row or column checks passed.
- The saved file and validation verdict appear in local run history.

## How we fix a failure

Work on one failed app step at a time:

1. Read that run's error and local evidence.
2. Reproduce only the failed step.
3. Make the smallest change in the existing capture, replay, or validation path.
4. Run the focused regression test for that change once.
5. Retry the same app step.

Do not create another automation framework, profile, browser, service, or plan.

## Safety gates

- A manual evidence replay may prove a new recording once, but it does not
  approve it and cannot enable a schedule or automatic retry.
- Business-impact clicks need a directly observed outcome.
- A missing or ambiguous selector fails closed.
- Real sessions, screenshots, traces, and downloads stay outside Git.

## Samsung/G-MES

Use Google Chrome Profile 19 and its managed extensions. Reuse the known G-MES
automation actions. When a new UI path is learned with the operator, update the
`samsung-gmes-automation` skill before completing the work.
