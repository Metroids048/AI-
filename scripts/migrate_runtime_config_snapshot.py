"""Explicitly stage the promoted BTC/ETH Paper strategy as a config snapshot."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    os.environ["POSTGRES_URL"] = args.database_url

    from services.database import get_session_factory, reset_database_caches
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, resolve_auto_paper_technical_evidence
    from services.execution.runtime_config_migration import stage_promoted_runtime_config

    reset_database_caches()
    promoted_rules, _ = resolve_auto_paper_technical_evidence()
    with get_session_factory()() as session:
        result = stage_promoted_runtime_config(
            session,
            strategy_key=AUTO_PAPER_TECHNICAL_KEY,
            promoted_rules=promoted_rules,
        )
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
