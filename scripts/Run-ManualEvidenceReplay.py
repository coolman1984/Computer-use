"""Run one recorded report flow, save its file, and validate it.

This is an operator evidence command, not an automation approval command.  It
uses SmartOps' normal replay, file registration, validation, and history path,
but never approves or schedules the recording.  The recording must already be
complete and have a compiled plan.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartops.domain.enums import RecordingStatus, RunStatus, TriggerType
from smartops.services import Services


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording_id", help="Completed SmartOps recording to replay once")
    args = parser.parse_args()

    services = Services()
    try:
        recording = services.recordings.get(args.recording_id)
        if recording is None:
            raise SystemExit("Recording not found")
        if recording.status is not RecordingStatus.COMPLETED:
            raise SystemExit("Complete the recording before running its evidence replay")
        plan = recording.automation_draft or {}
        if not plan.get("actions"):
            raise SystemExit("Build the recording's plan before running its evidence replay")

        run = services.runner.create_run(
            "process.replay",
            params={
                "system": recording.system_key,
                "report": plan.get("report_key") or "recorded_report",
                "plan": plan,
                "recording_id": recording.id,
                "manual_evidence_replay": True,
                "rules": {
                    "expected_extensions": [".xlsx"],
                    "min_size_bytes": 1000,
                    "min_rows": 1,
                    "reject_duplicate_hash": True,
                },
            },
            trigger=TriggerType.MANUAL,
        )
        completed = services.runner.drive(run.id)
        files = services.files.list(run_id=completed.id)
        print(
            json.dumps(
                {
                    "run_id": completed.id,
                    "status": completed.status.value,
                    "error": completed.error_message,
                    "files": [
                        {
                            "name": Path(item.path).name,
                            "bytes": item.size_bytes,
                            "validation": item.validation_status.value,
                            "rows": item.row_count,
                        }
                        for item in files
                    ],
                }
            )
        )
        return 0 if completed.status is RunStatus.SUCCEEDED else 1
    finally:
        services.close()


if __name__ == "__main__":
    raise SystemExit(main())
