# Current SmartOps Goal

## One active objective

Make one G-MES daily report download complete reliably without operator intervention:

`Sign in → Replay → Download one file → Detect real file type → Validate workbook → Record outcome`

## Current status

- Recording path: proven on the real pilot.
- Replay authentication classifier: repaired and covered by focused tests.
- Authenticated-page adoption: repaired.
- Replay page/tab identity: repaired and stable after SSO/popups/closures.
- Local real-Chrome replay: passed on the target Windows machine.
- Local real OOXML workbook download and validation: passed.
- Process lifecycle focused suite: passed.
- Whole-session replay retry: capped to one attempt; only explicitly safe action-level retries remain.

## Current gate

Corporate replay is still blocked until the dedicated automation Chrome profile has the required SSO capability and the doctor check is green. Do not repeat credential submission just to probe the gate.

## Next proof

0. Run `smartops probe <system>` on the real report screen while the operator is
   present. It clicks nothing and downloads nothing; it reports which sensors
   that screen actually exposes, so the recording strategy is chosen from
   evidence. See `docs/RECORDER_ROADMAP.md`.
1. Resolve the Chrome-profile / SSO-extension gate.
2. Run one controlled corporate replay while the operator is present.
3. Prove the expected report file was downloaded, opened, and validated for the configured workbook rules.
4. If it fails, stop and diagnose from sanitized evidence before another attempt.
5. Require three consecutive successful unattended runs before approval and scheduling.

## Not active work

Do not expand SmartOps into multi-system orchestration, department linking, AI self-healing, new browser frameworks, vision-first automation, advanced analytics, or broad monitoring until the single daily download flow is proven.

## Canonical references

Read in this order:

1. `README.md`
2. `AGENTS.md`
3. `docs/DAILY_DOWNLOAD_FLOW.md`
4. `skills/smartops-core-operator/SKILL.md`
5. `docs/RECORDER_ROADMAP.md` when working on capture, sensors, or agent tools
6. `docs/REPLAY_AUTOMATION_FIX_PLAN.md` only when working on replay/authentication
7. The focused source files and tests for the blocker being changed
