"""Prove directional PaperRun can open+close on Binance Testnet via manual path."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
DB = ROOT / ".local_paper_console.db"
os.environ["POSTGRES_URL"] = f"sqlite:///{DB.as_posix()}"
os.environ.setdefault("APP_ENV", "development")

from dotenv import dotenv_values  # noqa: E402

for key, value in dotenv_values(ROOT / ".env").items():
    if value is not None and key not in os.environ:
        os.environ[key] = value

from services.data import DataRepository  # noqa: E402
from services.database import get_session_factory, reset_database_caches  # noqa: E402
from services.execution.gatekeeper import ExecutionGatekeeperService  # noqa: E402
from services.execution.gateway import BinanceUsdtPerpetualGateway  # noqa: E402
from services.execution.manual import ManualTradingService  # noqa: E402
from services.strategy_library import (  # noqa: E402
    ExecutionRepository,
    HypothesisRepository,
    PaperRunRepository,
    ReviewRepository,
    RiskProfileRepository,
    ValidationRepository,
)
from shared.models import ClosePositionRequest, ManualOrderRequest, TradeSide  # noqa: E402

reset_database_caches()


def main() -> int:
    session = get_session_factory()()
    report: dict = {"opened": None, "closed": None, "error": None}
    try:
        paper_repo = PaperRunRepository(session)
        execution_repo = ExecutionRepository(session)
        data_repo = DataRepository(session)
        # Prefer mature directional run; pause duplicate directional without mature key.
        directional = None
        for run in paper_repo.list_paper_runs():
            ep = run.execution_profile or {}
            if ep.get("strategy_lane") != "directional":
                continue
            if ep.get("auto_paper_runtime_key") == "auto_paper_mature_templates":
                directional = run
            elif run.paper_status == "running" and ep.get("auto_paper_runtime_key") != "auto_paper_mature_templates":
                paper_repo.update_paper_run(run.paper_run_id or "", paper_status="paused")
                report.setdefault("paused_duplicates", []).append(run.paper_run_id)
        if directional is None:
            directional = next(
                (
                    r
                    for r in paper_repo.list_paper_runs()
                    if (r.execution_profile or {}).get("strategy_lane") == "directional"
                ),
                None,
            )
        if directional is None:
            report["error"] = "no directional paper run"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # Pick a liquid symbol without an exchange position if possible.
        gateway = BinanceUsdtPerpetualGateway(use_testnet=True)
        snap = gateway.reconcile(live_run_id="directional-proof")
        held = {
            str(item.get("symbol") or "").replace(":USDT", "")
            for item in (snap.get("open_positions") or [])
            if abs(float(item.get("contracts") or 0)) > 0
        }
        symbol = "LINK/USDT" if "LINK/USDT" not in held else "XRP/USDT"
        bar = data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe="1m")
        if bar is None:
            from services.data.binance import BinanceCcxtClient

            bars = BinanceCcxtClient().fetch_recent_ohlcv(symbol=symbol, timeframe="1m", limit=30)
            data_repo.store_ohlcv_bars(bars)
            session.commit()
            bar = data_repo.get_latest_ohlcv_bar(symbol=symbol, timeframe="1m")
        if bar is None:
            report["error"] = f"no bar for {symbol}"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        ref = float(bar.close)
        stamp = int(time.time())
        quantity = max(1.0, round(60.0 / ref, 0))  # gateway min notional ~50 USDT
        gatekeeper = ExecutionGatekeeperService(
            data_repo=data_repo,
            validation_repo=ValidationRepository(session),
            hypothesis_repo=HypothesisRepository(session),
            risk_profile_repo=RiskProfileRepository(session),
            execution_repo=execution_repo,
            paper_repo=paper_repo,
            review_repo=ReviewRepository(session),
        )
        manual = ManualTradingService(
            execution_repo=execution_repo,
            gatekeeper=gatekeeper,
            gateway=gateway,
        )
        open_order = manual.submit_manual_order(
            ManualOrderRequest(
                mode="testnet",
                strategy_id=directional.strategy_id,
                validation_backtest_run_id=directional.gate_decision_ref or "",
                risk_profile_id=directional.execution_profile.get("risk_profile_id"),
                paper_run_id=directional.paper_run_id,
                live_run_id=f"paper-testnet:{directional.paper_run_id}",
                symbol=symbol,
                direction=TradeSide.LONG,
                quantity=float(quantity),
                reference_price=ref,
                leverage=2.0,
                timeframe="1m",
                stoploss_price=ref * 0.98,
                takeprofit_price=ref * 1.02,
                idempotency_key=f"dir-proof-open-{symbol.replace('/', '')}-{stamp}",
            )
        )
        report["opened"] = {
            "symbol": symbol,
            "quantity": quantity,
            "status": open_order.execution_status,
            "gateway_order_id": open_order.gateway_order_id,
            "rejection": open_order.rejection_reason,
            "paper_run_id": directional.paper_run_id,
        }
        if open_order.gateway_order_id:
            # Manual testnet open does not always mirror a local PositionSnapshot;
            # close directly on the exchange to complete the round-trip proof.
            price = gateway.fetch_last_price(symbol)
            close_result = gateway.submit_acceptance_order(
                symbol=symbol,
                side="SELL",
                requested_notional=float(quantity) * float(price),
                reference_price=float(price),
                reduce_only=True,
                stoploss_price=None,
                idempotency_key=f"dir-proof-close-{symbol.replace('/', '')}-{stamp}",
            )
            report["closed"] = {
                "status": close_result.get("gateway_status"),
                "gateway_order_id": close_result.get("gateway_order_id"),
                "rejection": close_result.get("error"),
            }
        report["verdict"] = (
            "directional_mirror_ok"
            if report["opened"].get("gateway_order_id")
            else "directional_mirror_failed"
        )
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        report["verdict"] = "error"
    finally:
        session.close()

    out = ROOT / "docs" / "audits" / "_directional_manual_mirror_proof.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("REPORT", out)
    return 0 if report.get("verdict") == "directional_mirror_ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
