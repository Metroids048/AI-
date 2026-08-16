from __future__ import annotations

import argparse
import json

from services.database import get_session_factory
from services.microstructure.readiness import evaluate_readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="sqlite:///.local_paper_console.db")
    args = parser.parse_args()
    session = get_session_factory(args.database_url)()
    try:
        report = evaluate_readiness(session)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ready else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
