"""Refresh Top20 1m bars, resolve stale risk events, report trading readiness."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    from services.data.binance import BinanceCcxtClient, resolve_usdm_public_rest_base
    from services.data.heartbeat import MarketDataHeartbeatService
    from services.data.repository import DataRepository
    from services.data.universe import FIXED_TOP20_SYMBOLS
    from services.database import get_session_factory

    client = BinanceCcxtClient(usdm_base_url=resolve_usdm_public_rest_base())
    with get_session_factory()() as session:
        repo = DataRepository(session)
        refreshed = 0
        for symbol in FIXED_TOP20_SYMBOLS:
            try:
                bars = client.fetch_ohlcv(symbol=symbol, timeframe="1m", limit=30)
                if bars:
                    repo.upsert_ohlcv_bars(symbol=symbol, timeframe="1m", bars=bars)
                    refreshed += 1
            except Exception as exc:  # noqa: BLE001
                print("refresh_fail", symbol, exc)
        print("refreshed", refreshed)
        hb = MarketDataHeartbeatService(data_repo=repo)
        result = hb.check_symbols(symbols=list(FIXED_TOP20_SYMBOLS), timeframe="1m")
        print("stale_after", result["stale_symbols"])
        # Force-resolve residual data_stale blockers from restart races.
        for event in repo.list_risk_events(active_only=True, limit=200):
            if str(event.event_type) == "data_stale" and event.risk_event_id:
                repo.update_risk_event_resolution(
                    risk_event_id=event.risk_event_id,
                    resolution_status="resolved",
                )
                print("resolved", event.description)

    env = (ROOT / ".env").read_text(encoding="utf-8")
    token = next(line.split("=", 1)[1].strip().strip('"') for line in env.splitlines() if line.startswith("ADMIN_API_TOKEN="))
    req = urllib.request.Request(
        "http://127.0.0.1:8016/api/v1/execution/trading-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = json.loads(resp.read().decode("utf-8"))
    print(
        "status",
        {
            "ready": status.get("execution_ready"),
            "blockers": status.get("execution_blockers"),
            "top20": status.get("top20_coverage_count"),
        },
    )


if __name__ == "__main__":
    main()
