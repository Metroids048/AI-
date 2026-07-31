#!/usr/bin/env python3
"""Run exactly one formal V2 Celery/Scheduler task entry for recovery probing."""

from __future__ import annotations

import json
import socket

from services.execution.tasks import run_v2_automated_trading_cycles


def main() -> int:
    result = run_v2_automated_trading_cycles.run(
        {
            "scheduler_instance_id": f"formal-task-recovery:{socket.gethostname()}",
            "interval_seconds": 300,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"completed", "duplicate_slot_skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
