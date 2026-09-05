"""Run interactive Playwright Codegen on the live desktop (WinSta0\\Default)
to record the full G-MES extraction steps: Python code, element selectors,
and saving the session and network HAR file.
"""

import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(r"d:\WORK\Software Development\GitHub\Computer Use\Computer-use")
SCRATCH_DIR = BASE_DIR / "scratch"
SESSIONS_DIR = BASE_DIR / "data" / "sessions"

SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

output_script = SCRATCH_DIR / "gmes_recorded_workflow.py"
storage_file = SESSIONS_DIR / "gmes.json"
har_file = SCRATCH_DIR / "gmes_network.har"
url = "http://seegmes4.sec.samsung.net/mes4/sm/nexacro/index_ext_2318.html"

cmd = [
    sys.executable,
    "-m",
    "playwright",
    "codegen",
    "--target=python",
    "-o",
    str(output_script),
    "--save-storage",
    str(storage_file),
    "--save-har",
    str(har_file),
    url,
]

print("Launching Playwright Codegen on interactive desktop...")
print(f"Target URL: {url}")
print(f"Output script will be saved to: {output_script}")
print(f"Storage state will be saved to: {storage_file}")
print(f"Network HAR will be saved to: {har_file}")

si = subprocess.STARTUPINFO()
si.lpDesktop = r"WinSta0\Default"

proc = subprocess.Popen(
    cmd,
    startupinfo=si,
    creationflags=subprocess.CREATE_NEW_CONSOLE,
)

print(f"Playwright Codegen process started with PID {proc.pid}.")
print("Both the Browser and Playwright Inspector windows should now be visible on your screen.")
