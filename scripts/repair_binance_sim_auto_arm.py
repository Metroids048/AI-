"""Re-arm Binance simulation auto runs after verified Top20 acceptance.

Fixes the operator-visible dead state:
1) trading-status missed acceptance outside the latest-50 task window
2) bootstrap clobbered cost_gate / mirror flags
3) local ghost positions blocked new opens via portfolio risk
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = (ROOT / ".env").read_text(encoding="utf-8")
token = next(
    line.split("=", 1)[1].strip().strip('"')
    for line in env.splitlines()
    if line.startswith("ADMIN_API_TOKEN=")
)
BASE = "http://127.0.0.1:8016"
AUTO_KEYS = {"auto_paper_btc_funding", "auto_paper_mature_templates"}


def api(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    status = api("GET", "/api/v1/execution/trading-status")
    print("before", json.dumps({
        "execution_ready": status.get("execution_ready"),
        "auto_execution_state": status.get("auto_execution_state"),
        "execution_blockers": status.get("execution_blockers"),
        "testnet_acceptance_verified": status.get("testnet_acceptance_verified"),
    }, ensure_ascii=False))

    if not status.get("testnet_acceptance_verified"):
        raise SystemExit(
            "acceptance still not verified after code fix — restart API then re-run this script"
        )

    runs = api("GET", "/api/v1/execution/paper-runs")
    items = runs if isinstance(runs, list) else runs.get("items") or []
    armed_ids: list[str] = []
    for run in items:
        ep = run.get("execution_profile") or {}
        key = ep.get("auto_paper_runtime_key")
        if key not in AUTO_KEYS:
            continue
        run_id = run["paper_run_id"]
        updated = api(
            "PATCH",
            f"/api/v1/execution/paper-runs/{run_id}/execution-profile",
            {
                "execution_mode": "binance_simulation_first",
                "mirror_to_gateway": True,
                "cost_gate_verified": True,
                "testnet_acceptance_verified_at": ep.get("testnet_acceptance_verified_at")
                or datetime.now(UTC).isoformat(),
            },
        )
        new_ep = updated.get("execution_profile") or {}
        print(
            "armed",
            key,
            {
                "execution_mode": new_ep.get("execution_mode"),
                "mirror_to_gateway": new_ep.get("mirror_to_gateway"),
                "cost_gate_verified": new_ep.get("cost_gate_verified"),
            },
        )
        armed_ids.append(run_id)

    # Trigger reconcile+scan so local ghosts clear against Binance flat account.
    for run_id in armed_ids:
        result = api(
            "POST",
            f"/api/v1/execution/paper-runs/{run_id}/auto-cycle",
            {"max_symbols": 20, "timeframe": "15m", "close_on_opposite_signal": True},
        )
        print(
            "cycle",
            run_id,
            {
                "opened": result.get("opened_positions"),
                "closed": result.get("closed_positions"),
                "rejected": result.get("rejected_orders"),
                "actions": len(result.get("actions") or []),
                "skipped": result.get("skipped_symbols"),
            },
        )

    after = api("GET", "/api/v1/execution/trading-status")
    print("after", json.dumps({
        "execution_ready": after.get("execution_ready"),
        "auto_execution_state": after.get("auto_execution_state"),
        "execution_blockers": after.get("execution_blockers"),
        "testnet_acceptance_verified": after.get("testnet_acceptance_verified"),
        "last_auto_cycle_at": after.get("last_auto_cycle_at"),
    }, ensure_ascii=False))

    # Latest open positions for armed runs
    for run_id in armed_ids:
        runtime = api("GET", f"/api/v1/execution/paper-runs/{run_id}/runtime-status")
        print("runtime", run_id, json.dumps(runtime, ensure_ascii=False, default=str)[:1500])


if __name__ == "__main__":
    main()
