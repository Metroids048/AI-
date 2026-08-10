"""I-1 runtime state snapshot. READ-ONLY (mode=ro). No writes of any kind.

Records the pre-migration runtime state required by the operator-frozen I-1
sequence step 1. Emits JSON to artifacts/t0-i1-symbol-canonical-<stamp>/.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB = ".local_paper_console.db"


def _sha256(path: str, limit_mb: int | None = None) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
            read += len(chunk)
            if limit_mb is not None and read >= limit_mb * 1024 * 1024:
                break
    return h.hexdigest()


def _q(con: Any, sql: str, params: tuple = ()) -> list[tuple]:
    return list(con.execute(sql, params))


def collect() -> dict[str, Any]:
    import sqlite3

    st = os.stat(DB)
    snap: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": "I-1 pre-migration runtime state (operator-frozen sequence step 1)",
        "production_write": False,
        "db": {
            "path": DB,
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
        },
    }

    try:
        snap["git_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        snap["git_commit"] = f"unavailable: {exc}"

    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False).stdout
        snap["listeners_8016"] = [ln.strip() for ln in out.splitlines() if ":8016" in ln and "LISTENING" in ln]
    except Exception as exc:  # noqa: BLE001
        snap["listeners_8016"] = [f"unavailable: {exc}"]

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        ctrl_cols = [c[1] for c in con.execute("PRAGMA table_info(v2_runtime_controls)")]
        snap["runtime_controls"] = [
            dict(zip(ctrl_cols, r, strict=False)) for r in _q(con, "SELECT * FROM v2_runtime_controls")
        ]

        pos_cols = [c[1] for c in con.execute("PRAGMA table_info(v2_managed_positions)")]
        snap["open_positions"] = [
            dict(zip(pos_cols, r, strict=False))
            for r in _q(con, "SELECT * FROM v2_managed_positions WHERE state NOT IN ('CLOSED','QUARANTINED')")
        ]

        prot_cols = [c[1] for c in con.execute("PRAGMA table_info(v2_protection_records)")]
        snap["active_protections"] = [
            dict(zip(prot_cols, r, strict=False))
            for r in _q(con, "SELECT * FROM v2_protection_records WHERE state='PROTECTION_ACTIVE'")
        ]

        snap["counts"] = {
            "v2_execution_cycles": _q(con, "SELECT COUNT(*) FROM v2_execution_cycles")[0][0],
            "v2_execution_decisions": _q(con, "SELECT COUNT(*) FROM v2_execution_decisions")[0][0],
            "v2_exchange_fills": _q(con, "SELECT COUNT(*) FROM v2_exchange_fills")[0][0],
            "market_extras": _q(con, "SELECT COUNT(*) FROM market_extras")[0][0],
            "ohlcv_bars": _q(con, "SELECT COUNT(*) FROM ohlcv_bars")[0][0],
        }

        snap["market_extras_symbols"] = [
            {"symbol": r[0], "rows": r[1], "min_time": str(r[2]), "max_time": str(r[3])}
            for r in _q(
                con,
                "SELECT symbol, COUNT(*), MIN(time), MAX(time) FROM market_extras GROUP BY symbol ORDER BY symbol",
            )
        ]
        snap["market_extras_legacy_rows"] = _q(con, "SELECT COUNT(*) FROM market_extras WHERE symbol LIKE '%:USDT'")[0][
            0
        ]
        snap["market_extras_canonical_rows"] = _q(
            con, "SELECT COUNT(*) FROM market_extras WHERE symbol NOT LIKE '%:USDT'"
        )[0][0]

        snap["latest_cycles"] = [
            {"bar_timestamp": str(r[0]), "symbol": r[1], "terminal": r[2], "started_at": str(r[3])}
            for r in _q(
                con,
                "SELECT bar_timestamp, symbol, decision_terminal, started_at "
                "FROM v2_execution_cycles ORDER BY started_at DESC LIMIT 5",
            )
        ]
        snap["latest_ohlcv"] = {
            f"{sym}|{tf}": str(
                _q(con, "SELECT MAX(time) FROM ohlcv_bars WHERE symbol=? AND timeframe=?", (sym, tf))[0][0]
            )
            for sym in ("BTC/USDT", "ETH/USDT")
            for tf in ("15m",)
        }
    finally:
        con.close()
    return snap


def main() -> int:
    if not Path(DB).exists():
        print(f"FATAL: {DB} not found", file=sys.stderr)
        return 1
    snap = collect()
    out_dir = Path("artifacts/t0-i1-symbol-canonical-20260809")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "STEP01_PRE_MIGRATION_RUNTIME_STATE.json"
    out.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")

    ctrl = snap["runtime_controls"][0] if snap["runtime_controls"] else {}
    print("=== I-1 STEP 1: PRE-MIGRATION RUNTIME STATE ===")
    print(f"git_commit          : {snap['git_commit']}")
    print(f"db size / mtime     : {snap['db']['size_bytes']} bytes / {snap['db']['mtime']}")
    print(f"listeners :8016     : {snap['listeners_8016'] or '(none)'}")
    print(f"entry_enabled       : {ctrl.get('entry_enabled')}  reason={ctrl.get('reason')}")
    print(f"open positions      : {len(snap['open_positions'])}")
    for p in snap["open_positions"]:
        print(f"  {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} state={p['state']}")
    print(f"active protections  : {len(snap['active_protections'])}")
    for p in snap["active_protections"]:
        print(
            f"  pos={p['position_id'][:8]} SL={p['stop_loss_price']}@{p['stop_exchange_order_id']} "
            f"TP={p['take_profit_price']}@{p['tp_exchange_order_id']} state={p['state']}"
        )
    print(f"market_extras total : {snap['counts']['market_extras']}")
    print(f"  legacy (:USDT)    : {snap['market_extras_legacy_rows']}")
    print(f"  canonical         : {snap['market_extras_canonical_rows']}")
    print(f"cycles / decisions  : {snap['counts']['v2_execution_cycles']} / {snap['counts']['v2_execution_decisions']}")
    print(f"latest cycle        : {snap['latest_cycles'][0] if snap['latest_cycles'] else '(none)'}")
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
