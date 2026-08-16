from __future__ import annotations

import argparse
import json

from services.database import get_session_factory
from services.microstructure.readiness import evaluate_readiness
from services.microstructure.replay import replay_candidate_windows


def main() -> int:
    parser = argparse.ArgumentParser(description="One-command future maker/limit replay gate")
    parser.add_argument("--database-url", default="sqlite:///.local_paper_console.db")
    args = parser.parse_args()
    session = get_session_factory(args.database_url)()
    try:
        report = evaluate_readiness(session)
        payload = {
            "readiness": report.to_dict(),
            "replay": replay_candidate_windows(session),
            "gate": "blocked_until_readiness" if not report.ready else "ready_to_run",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report.ready else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
