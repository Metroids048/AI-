"""Audit P0-P3 acceptance evidence against live DB/API/filesystem (read-only)."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".local_paper_console.db"
OUT = ROOT / "logs" / "p0_p3_acceptance_audit.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def api(path, timeout=20):
    req = urllib.request.Request(
        "http://127.0.0.1:8016" + path,
        headers={"Authorization": "Bearer dev-admin-token"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def table_count(cur, name):
    try:
        return cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    except Exception as e:
        return f"ERR:{e}"


def exists(rel):
    return (ROOT / rel).exists()


result = {"audited_at": datetime.now(UTC).isoformat(), "checks": {}}
c = result["checks"]

# Files / ADR
c["adr_078"] = exists(".github/agent/memory/decisions-log.md") and "ADR-078" in (
    ROOT / ".github/agent/memory/decisions-log.md"
).read_text(encoding="utf-8", errors="replace")
c["migration_0014"] = (
    bool(list((ROOT / "services/database/migrations/versions").glob("*0014*")))
    if (ROOT / "services/database/migrations/versions").exists()
    else bool(list(ROOT.glob("**/versions/*0014*")))
)
c["migration_0015"] = bool(list(ROOT.glob("**/versions/*0015*")))
c["capture_bundle_script"] = exists("scripts/capture_testnet_truth_bundle.py")
c["smoke_llm_script"] = exists("scripts/smoke_llm_provider.py")
c["p2_browser_checklist"] = exists("logs/p2-runtime-truth-verify/checklist.json")
if c["p2_browser_checklist"]:
    try:
        c["p2_browser_overall_pass"] = json.loads(
            (ROOT / "logs/p2-runtime-truth-verify/checklist.json").read_text(encoding="utf-8")
        ).get("overall_pass")
    except Exception as e:
        c["p2_browser_overall_pass"] = f"ERR:{e}"


# Code markers
def grep_any(path, needles):
    text = (ROOT / path).read_text(encoding="utf-8", errors="replace") if (ROOT / path).exists() else ""
    return {n: (n in text) for n in needles}


c["execution_mode_model"] = grep_any(
    "shared/models/execution_truth.py", ["ExecutionMode", "LOCAL_PAPER", "BINANCE_TESTNET", "ExchangeOrderState"]
)
c["order_transition_validator"] = (
    exists("services/execution/exchange_order_transitions.py")
    or "validate_exchange_order_transition"
    in (ROOT / "services/execution").joinpath("exchange_order_transitions.py").read_text(encoding="utf-8")
    if exists("services/execution/exchange_order_transitions.py")
    else False
)
c["market_review_wired"] = (
    "MARKET_REVIEW" in (ROOT / "services/execution/tasks.py").read_text(encoding="utf-8", errors="replace")
    if exists("services/execution/tasks.py")
    else False
)
c["trade_review_advisory"] = (
    "TRADE_REVIEW"
    in (ROOT / "services/agents").joinpath("trade_review_llm.py").read_text(encoding="utf-8", errors="replace")
    if exists("services/agents/trade_review_llm.py")
    else (
        "trade_review"
        in (ROOT / "services/execution/decision_pipeline.py").read_text(encoding="utf-8", errors="replace")
        if exists("services/execution/decision_pipeline.py")
        else False
    )
)
c["runtime_panel_freshness"] = (
    "data_freshness"
    in (ROOT / "frontend/admin/src/components/RuntimeTruthPanel.jsx").read_text(encoding="utf-8", errors="replace")
    if exists("frontend/admin/src/components/RuntimeTruthPanel.jsx")
    else False
)
c["runtime_panel_evidence"] = (
    "strategy_evidence"
    in (ROOT / "frontend/admin/src/components/RuntimeTruthPanel.jsx").read_text(encoding="utf-8", errors="replace")
    if exists("frontend/admin/src/components/RuntimeTruthPanel.jsx")
    else False
)

# DB evidence
conn = sqlite3.connect(DB)
cur = conn.cursor()
for t in [
    "exchange_orders",
    "exchange_fill_receipts",
    "decision_funnel_terminals",
    "llm_invocations",
    "position_records",
    "protection_records",
    "paper_runs",
    "alembic_version",
]:
    c[f"db_{t}"] = table_count(cur, t)
try:
    c["alembic_head"] = cur.execute("SELECT version_num FROM alembic_version").fetchone()
except Exception as e:
    c["alembic_head"] = str(e)
# natural directional exchange orders
try:
    c["natural_exchange_orders"] = cur.execute("SELECT COUNT(*) FROM exchange_orders").fetchone()[0]
except Exception as e:
    c["natural_exchange_orders"] = str(e)
try:
    rows = cur.execute(
        "SELECT reason_code, COUNT(*) FROM decision_funnel_terminals GROUP BY reason_code ORDER BY COUNT(*) DESC LIMIT 8"
    ).fetchall()
    c["funnel_reason_top"] = rows
except Exception as e:
    c["funnel_reason_top"] = str(e)
try:
    c["sampling_aligned_terminals"] = cur.execute(
        "SELECT COUNT(*) FROM decision_funnel_terminals WHERE reason_code LIKE '%SAMPLING%' OR details LIKE '%sampling_fallback%'"
    ).fetchone()[0]
except Exception:
    c["sampling_aligned_terminals"] = "n/a"
conn.close()

# Live API
try:
    snap = api("/api/v1/runtime/snapshot", timeout=35)
    c["api_snapshot"] = {
        "exchange_status": snap.get("exchange", {}).get("status"),
        "pos": len((snap.get("exchange", {}).get("value") or {}).get("positions") or []),
        "orders": len((snap.get("exchange", {}).get("value") or {}).get("open_orders") or []),
        "data_freshness": (snap.get("data_freshness") or {}).get("status"),
        "strategy_evidence": (snap.get("strategy_evidence") or {}).get("status"),
        "scheduler_running": ((snap.get("scheduler") or {}).get("value") or {}).get("running"),
    }
except Exception as e:
    c["api_snapshot"] = f"ERR:{e}"
try:
    recon = api("/api/v1/runtime/reconciliation", timeout=35)
    c["api_recon"] = {
        "status": recon.get("status"),
        "blocked": recon.get("entry_blocked_symbols"),
        "kill": recon.get("entry_kill_switch_active"),
    }
except Exception as e:
    c["api_recon"] = f"ERR:{e}"
try:
    dec = api("/api/v1/runtime/decisions?limit=1")
    item = (dec.get("items") or [None])[0]
    c["latest_funnel"] = (
        None
        if not item
        else {
            "symbol": item.get("symbol"),
            "bar_time": item.get("bar_time"),
            "reason_code": item.get("reason_code"),
            "status": item.get("status"),
            "terminal_stage": item.get("terminal_stage"),
        }
    )
except Exception as e:
    c["latest_funnel"] = f"ERR:{e}"

# Hard gates for COMPLETE
c["p03_natural_entry_fill_exit"] = False  # still no exchange_orders
c["p3_unlocked"] = False

OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
print(json.dumps(result, indent=2, default=str))
