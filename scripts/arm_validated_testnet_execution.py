"""Arm the validated directional run from an existing exact-scope Testnet acceptance proof."""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url

    from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
    from services.database import get_session_factory, reset_database_caches
    from services.execution.testnet_authorization import arm_validated_directional_run
    from services.strategy_library import AgentTaskRepository

    reset_database_caches()
    symbols = list(AUTO_SIMULATION_EXECUTION_SYMBOLS)
    with get_session_factory()() as session:
        acceptance = AgentTaskRepository(session).find_verified_testnet_acceptance(symbols)
        if acceptance is None:
            print(f"FAIL: no completed exact-scope Testnet acceptance for {symbols}")
            return 1
        armed = arm_validated_directional_run(
            session,
            symbols=symbols,
            verified_at=acceptance.created_at.isoformat() if acceptance.created_at is not None else None,
        )
    print(f"OK: armed {armed} validated directional run(s) for {symbols}")
    return 0 if armed else 1


if __name__ == "__main__":
    raise SystemExit(main())
