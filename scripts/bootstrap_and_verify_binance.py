"""One-shot local bootstrap + Binance API verification for Paper Console."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / ".local_paper_console.db"


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _ensure_env_keys() -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    keys = {line.split("=", 1)[0].strip() for line in lines if "=" in line and not line.strip().startswith("#")}
    additions: list[str] = []
    if "BINANCE_AUTO_EXECUTE" not in keys:
        additions.append("BINANCE_AUTO_EXECUTE=false")
    if additions and ENV_PATH.exists():
        text = ENV_PATH.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(additions) + "\n"
        ENV_PATH.write_text(text, encoding="utf-8")


def _bootstrap_db() -> None:
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("POSTGRES_URL", f"sqlite:///{DB_PATH.as_posix()}")
    os.environ.setdefault("BINANCE_USE_TESTNET", "true")
    os.environ.setdefault("BINANCE_AUTO_EXECUTE", "false")
    from services.data.repository import create_timeseries_schema
    from services.database import create_relational_schema, get_engine, reset_database_caches
    from services.execution.bootstrap import bootstrap_local_paper_runtime

    reset_database_caches()
    create_relational_schema()
    create_timeseries_schema(get_engine())
    bootstrap_local_paper_runtime()


def _probe_binance() -> dict:
    from services.execution.gateway import probe_testnet_account

    status = probe_testnet_account(order_limit=5)
    return status.model_dump(mode="json")


def _run_one_cycle() -> dict:
    from services.data import DataRepository
    from services.database import get_session_factory
    from services.execution.gatekeeper import ExecutionGatekeeperService
    from services.execution.gateway import configured_gateways
    from services.execution.paper_runtime import PaperRuntimeService
    from services.strategy_library import (
        AgentTaskRepository,
        ExecutionRepository,
        HypothesisRepository,
        NotificationRepository,
        PaperRunRepository,
        ReviewRepository,
        RiskProfileRepository,
        StrategyRepository,
        ValidationRepository,
    )
    from shared.models import PaperRuntimeCycleRequest

    session = get_session_factory()()
    try:
        paper_repo = PaperRunRepository(session)
        runs = [run for run in paper_repo.list_paper_runs() if run.paper_status == "running" and run.paper_run_id]
        if not runs:
            return {"error": "no running paper runs"}
        target = next(
            (run for run in runs if run.execution_profile.get("auto_paper_runtime_key")),
            runs[0],
        )
        runtime = PaperRuntimeService(
            data_repo=DataRepository(session),
            execution_repo=ExecutionRepository(session),
            paper_repo=paper_repo,
            strategy_repo=StrategyRepository(session),
            agent_repo=AgentTaskRepository(session),
            review_repo=ReviewRepository(session),
            notification_repo=NotificationRepository(session),
            gatekeeper=ExecutionGatekeeperService(
                data_repo=DataRepository(session),
                validation_repo=ValidationRepository(session),
                hypothesis_repo=HypothesisRepository(session),
                risk_profile_repo=RiskProfileRepository(session),
                execution_repo=ExecutionRepository(session),
                paper_repo=paper_repo,
                review_repo=ReviewRepository(session),
            ),
            gateway=configured_gateways()[0],
        )
        return runtime.run_cycle(
            paper_run_id=target.paper_run_id or "",
            request=PaperRuntimeCycleRequest(
                symbols=["BTC/USDT"],
                timeframe="1m",
                enable_decision_veto=False,
            ),
        ).model_dump(mode="json")
    finally:
        session.close()


def main() -> int:
    _load_dotenv()
    _ensure_env_keys()
    _bootstrap_db()
    before = _probe_binance()
    cycle = _run_one_cycle()
    time.sleep(2)
    after = _probe_binance()
    report = {
        "binance_before": before,
        "cycle": cycle,
        "binance_after": after,
    }
    out_path = ROOT / "scripts" / "_verify_out.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not after.get("connected"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
