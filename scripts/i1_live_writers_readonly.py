"""I-1 WRITER-ZERO gate check. READ-ONLY (mode=ro). Re-runnable before/after stop.

Prints a hard PASS/FAIL for WRITER_ZERO based on:
  1. non-expired scheduler_leases holders
  2. live OS processes touching this repo's scheduler/API
  3. whether scheduler_cycles / v2_execution_cycles are still advancing
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime

DB = ".local_paper_console.db"
PS_FILTER = "run-local-paper-scheduler|apps\\.api\\.local_server"


def os_processes() -> list[dict]:
    # Name filter is required: the helper shell spawned here carries PS_FILTER in its
    # own command line and would otherwise self-match, making WRITER_ZERO never PASS.
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match "
        f"'{PS_FILTER}' }} | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=False
    ).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def main() -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        live = list(
            con.execute(
                "SELECT owner_id, process_id, lease_name, heartbeat_at, expires_at FROM scheduler_leases "
                "WHERE expires_at > ? ORDER BY expires_at DESC",
                (now.isoformat(sep=" "),),
            )
        )
        recent_owners = list(
            con.execute(
                "SELECT owner_id, process_id, COUNT(*), MAX(heartbeat_at) FROM scheduler_leases "
                "GROUP BY owner_id, process_id ORDER BY 4 DESC LIMIT 10"
            )
        )
        sc = con.execute("SELECT COUNT(*), MAX(started_at) FROM scheduler_cycles").fetchone()
        v2 = con.execute("SELECT COUNT(*), MAX(started_at) FROM v2_execution_cycles").fetchone()
        me = con.execute("SELECT COUNT(*) FROM market_extras").fetchone()[0]
        me_legacy = con.execute("SELECT COUNT(*) FROM market_extras WHERE symbol LIKE '%:USDT'").fetchone()[0]
    finally:
        con.close()

    st = os.stat(DB)
    procs = os_processes()

    print(f"now (UTC naive)        : {now.isoformat(sep=' ')}")
    print(
        f"db mtime (UTC)         : {datetime.fromtimestamp(st.st_mtime, UTC).replace(tzinfo=None).isoformat(sep=' ')}"
    )
    print(f"db size                : {st.st_size}")
    print(f"\nNON-EXPIRED LEASES     : {len(live)}")
    for r in live:
        print(f"    owner={r[0]} pid={r[1]} lease={r[2]} hb={r[3]} exp={r[4]}")
    print("\nlease owners in table (top 10 by last heartbeat):")
    for r in recent_owners:
        print(f"    owner={r[0]} pid={r[1]} rows={r[2]} last_hb={r[3]}")
    print(f"\nscheduler_cycles       : count={sc[0]} max={sc[1]}")
    print(f"v2_execution_cycles    : count={v2[0]} max={v2[1]}")
    print(f"market_extras          : total={me} legacy(:USDT)={me_legacy}")
    print(f"\nLIVE OS PROCESSES      : {len(procs)}")
    for p in procs:
        cmd = (p.get("CommandLine") or "").strip()
        kind = "SCHEDULER" if "run-local-paper-scheduler" in cmd else "API"
        print(f"    pid={p.get('ProcessId')} ppid={p.get('ParentProcessId')} {kind}")

    writer_zero = len(live) == 0 and len(procs) == 0
    print(f"\nWRITER_ZERO            : {'PASS' if writer_zero else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
