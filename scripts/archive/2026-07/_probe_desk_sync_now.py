"""One-shot desk vs Binance Demo sync probe (no secrets printed)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_token() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return "dev-admin-token"
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("ADMIN_API_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "dev-admin-token"


def get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:8016{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {path}: {body[:2000]}") from exc


def main() -> int:
    token = _load_token()
    account = get("/api/v1/execution/binance-testnet-account", token)
    status = get("/api/v1/execution/trading-status", token)
    overview = get(
        "/api/v1/console/overview?symbol=BTC/USDT&perp_symbol=BTC/USDT:USDT&timeframe=15m",
        token,
    )

    positions = account.get("positions") or []
    open_orders = account.get("open_orders") or []
    recent = account.get("recent_orders") or []
    overview_pos = overview.get("positions") or []
    overview_orders = overview.get("orders") or []
    runs = overview.get("paper_runs") or []

    report = {
        "trading_status": {
            "mode": status.get("mode"),
            "binance_use_testnet": status.get("binance_use_testnet"),
            "live_trading_enabled": status.get("live_trading_enabled"),
            "gateway_available": status.get("gateway_available"),
            "execution_ready": status.get("execution_ready"),
            "blockers": status.get("blockers"),
        },
        "binance_demo": {
            "connected": account.get("connected"),
            "error": account.get("error"),
            "trading_mode": account.get("trading_mode"),
            "api_backend": account.get("api_backend"),
            "api_base": account.get("api_base"),
            "web_ui_url": account.get("web_ui_url"),
            "warning": account.get("warning"),
            "wallet_balance": account.get("wallet_balance"),
            "available_balance": account.get("available_balance"),
            "open_position_count": account.get("open_position_count"),
            "positions": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "quantity": p.get("quantity"),
                    "entry_price": p.get("entry_price"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                }
                for p in positions
            ],
            "open_orders_count": len(open_orders),
            "recent_orders_count": len(recent),
            "recent_orders": [
                {
                    "order_id": o.get("order_id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "status": o.get("status"),
                    "avg_price": o.get("avg_price"),
                    "quantity": o.get("quantity"),
                }
                for o in recent[:10]
            ],
            "synced_at": account.get("synced_at"),
        },
        "local_overview": {
            "position_count": len(overview_pos),
            "positions": [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "quantity": p.get("quantity"),
                }
                for p in overview_pos
            ],
            "order_count": len(overview_orders),
            "paper_runs_armed": [
                {
                    "paper_run_id": r.get("paper_run_id"),
                    "mirror": (r.get("execution_profile") or {}).get("mirror_to_gateway"),
                    "mode": (r.get("execution_profile") or {}).get("execution_mode"),
                }
                for r in runs
            ],
        },
        "desk_expected": {
            "positions_source": "binance_exchange" if account.get("connected") else "local_overview",
            "desk_position_count": (
                len([p for p in positions if abs(float(p.get("quantity") or 0)) > 0])
                if account.get("connected")
                else len(overview_pos)
            ),
            "desk_order_count": (
                len(open_orders) + len(recent) if account.get("connected") else len(overview_orders)
            ),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
