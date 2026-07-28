import json
from pathlib import Path

state = json.loads(Path("logs/scheduler-state.json").read_text(encoding="utf-8-sig"))
print("heartbeat", state.get("heartbeat_at"))
print("last_auto", state.get("last_auto_cycle_at"))
print("last_scheduled_for", state.get("last_scheduled_for"))
paper = (state.get("task_last_results") or {}).get("paper_runtime_cycle") or {}
print("cycle_time", paper.get("cycle_time"))
for a in paper.get("actions") or []:
    print(a.get("symbol"), a.get("action"), a.get("reason"), a.get("sampling_reason"), a.get("evaluated_at"))
