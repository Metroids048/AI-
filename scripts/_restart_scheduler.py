#!/usr/bin/env python3
"""One-shot script: kill the current scheduler process and restart it with fresh code."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "logs" / "scheduler-state.json"
DB_URL = f"sqlite:///{ROOT / '.local_paper_console.db'}".replace("\\", "/")

# Read current scheduler PID from state file
pid_to_kill = None
try:
    state = json.loads(STATE_FILE.read_text())
    instance_id = state.get("scheduler_instance_id", "")
    # format: "ssss:16564:..."
    parts = instance_id.split(":")
    if len(parts) >= 2:
        pid_to_kill = int(parts[1])
except Exception as e:
    print(f"Could not read PID from state: {e}")

# Clear bytecode cache for account_equity
import glob

for p in glob.glob(str(ROOT / "services" / "execution" / "__pycache__" / "account_equity*")):
    try:
        os.remove(p)
        print(f"Removed: {p}")
    except Exception:
        pass

# Kill old process
if pid_to_kill:
    print(f"Killing PID {pid_to_kill}...")
    subprocess.run(["taskkill", "/F", "/PID", str(pid_to_kill), "/T"], capture_output=True)
    time.sleep(2)

# Start new scheduler
print("Starting new scheduler...")
log_file = ROOT / "logs" / "scheduler.log"
err_file = ROOT / "logs" / "scheduler-error.log"

proc = subprocess.Popen(
    [sys.executable, str(ROOT / "scripts" / "run-local-paper-scheduler.py"), "--database-url", DB_URL],
    cwd=str(ROOT),
    stdout=open(str(log_file), "a"),
    stderr=open(str(err_file), "a"),
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)
print(f"New scheduler PID: {proc.pid}")
print("Done. Wait ~30 seconds then check logs/scheduler-state.json")
