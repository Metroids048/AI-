"""Finalize ops: pause duplicate directional runs; summarize armed state."""

from __future__ import annotations

import json
import os
import sys
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

from services.database import get_session_factory, reset_database_caches  # noqa: E402
from services.strategy_library import PaperRunRepository  # noqa: E402

reset_database_caches()


def main() -> int:
    session = get_session_factory()()
    report: dict = {"paused": [], "armed_directional": [], "running": []}
    try:
        paper_repo = PaperRunRepository(session)
        mature = None
        for run in paper_repo.list_paper_runs():
            ep = run.execution_profile or {}
            if ep.get("strategy_lane") != "directional":
                continue
            item = {
                "paper_run_id": run.paper_run_id,
                "paper_status": run.paper_status,
                "execution_mode": ep.get("execution_mode"),
                "auto_paper_runtime_key": ep.get("auto_paper_runtime_key"),
                "cost_gate_verified": ep.get("cost_gate_verified"),
            }
            if ep.get("auto_paper_runtime_key") == "auto_paper_mature_templates":
                mature = run
                report["armed_directional"].append(item)
            elif run.paper_status == "running":
                paper_repo.update_paper_run(run.paper_run_id or "", paper_status="paused")
                report["paused"].append(item)
            else:
                report.setdefault("other_directional", []).append(item)
        if mature and mature.paper_status != "running":
            paper_repo.update_paper_run(mature.paper_run_id or "", paper_status="running")
            report["resumed_mature"] = mature.paper_run_id
        for run in paper_repo.list_paper_runs():
            if run.paper_status == "running":
                ep = run.execution_profile or {}
                report["running"].append(
                    {
                        "paper_run_id": run.paper_run_id,
                        "strategy_lane": ep.get("strategy_lane"),
                        "execution_mode": ep.get("execution_mode"),
                        "auto_paper_runtime_key": ep.get("auto_paper_runtime_key"),
                    }
                )
        session.commit()
    finally:
        session.close()

    out = ROOT / "docs" / "audits" / "_directional_ops_finalize.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("REPORT", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
