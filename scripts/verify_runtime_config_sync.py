"""Verify a running instance's live Strategy rules match services/execution/bootstrap.py.

Module 0 of the auto-trading diagnosis remediation plan. Two failure modes this
tool exists to catch:

1. A promoted rules change that never reached the immutable runtime config
   snapshot. Restarts preserve operator state; promotion requires the explicit
   runtime-config migration command.
2. The running API process being connected to a different database than the one
   this script (or the operator) expects, so a green rules diff against DB A
   says nothing about what DB B-connected process is actually deciding on.

This script only ever reads. It never writes to the Strategy row or any other
table.

Usage:
    python scripts/verify_runtime_config_sync.py
    python scripts/verify_runtime_config_sync.py --database-url sqlite:///.local_paper_console.db
    python scripts/verify_runtime_config_sync.py --skip-api-check
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULE_BLOCK_NAMES = ("entry_rules", "exit_rules", "stoploss_rules", "takeprofit_rules", "position_rules")

# Bootstrap functions register several strategy_key -> rules-dict pairs. Keep
# this table in sync with services/execution/bootstrap.py's *_KEY / *_RULES
# constants if a new auto-paper strategy lane is added.
_MISSING = object()


@dataclass
class FieldMismatch:
    block: str
    field: str
    code_value: Any
    db_value: Any

    def render(self) -> str:
        code_repr = "<missing in code>" if self.code_value is _MISSING else repr(self.code_value)
        db_repr = "<missing in db>" if self.db_value is _MISSING else repr(self.db_value)
        return f"  [{self.block}.{self.field}] code={code_repr} db={db_repr}"


def _diff_rule_block(block: str, code_rules: dict[str, Any], db_rules: dict[str, Any]) -> list[FieldMismatch]:
    mismatches: list[FieldMismatch] = []
    for key in sorted(set(code_rules) | set(db_rules)):
        code_value = code_rules.get(key, _MISSING)
        db_value = db_rules.get(key, _MISSING)
        if code_value != db_value:
            mismatches.append(FieldMismatch(block=block, field=key, code_value=code_value, db_value=db_value))
    return mismatches


def _diff_strategy_rules(code_rules: dict[str, Any], db_rules: dict[str, Any]) -> list[FieldMismatch]:
    mismatches: list[FieldMismatch] = []
    for block in RULE_BLOCK_NAMES:
        mismatches.extend(_diff_rule_block(block, code_rules.get(block, {}), db_rules.get(block, {})))
    return mismatches


def _resolve_database_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    env_value = os.environ.get("POSTGRES_URL")
    if env_value:
        return env_value
    # Matches launcher default (scripts/launch-paper-console.ps1 $DatabasePath).
    default_path = Path(".local_paper_console.db").resolve()
    return f"sqlite:///{default_path.as_posix()}"


def _load_database_revision(database_url: str) -> str | None:
    """Return the applied Alembic revision without importing ORM models."""

    from sqlalchemy import inspect, text

    from services.database import get_engine, reset_database_caches

    os.environ["POSTGRES_URL"] = database_url
    reset_database_caches()
    engine = get_engine(database_url)
    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
    return str(revision) if revision is not None else None


def _load_live_strategy_rules(database_url: str, strategy_key: str) -> dict[str, Any] | None:
    os.environ["POSTGRES_URL"] = database_url

    from services.database import get_session_factory, reset_database_caches
    from services.strategy_library import StrategyRepository

    reset_database_caches()
    with get_session_factory()() as session:
        repo = StrategyRepository(session)
        for item in repo.list_strategies():
            if item.strategy_key == strategy_key:
                return item.rules.model_dump()
    return None


def _load_effective_strategy_rules(
    database_url: str,
    strategy_key: str,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Read the rules actually consumed by the next Paper cycle.

    ConfigSnapshot is authoritative after migration.  The legacy Strategy row
    remains a compatibility fallback for databases that have no active snapshot.
    """

    os.environ["POSTGRES_URL"] = database_url
    from services.database import get_session_factory, reset_database_caches
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    reset_database_caches()
    with get_session_factory()() as session:
        run = next(
            (
                item
                for item in PaperRunRepository(session).list_paper_runs()
                if item.execution_profile.get("auto_paper_runtime_key") == strategy_key
            ),
            None,
        )
        if run is not None and run.paper_run_id is not None:
            active = ConfigSnapshotRepository(session).get_active(run.paper_run_id)
            if active is not None and isinstance(active.config.get("strategy_rules"), dict):
                return dict(active.config["strategy_rules"]), "active_config_snapshot", active.config_hash
    return _load_live_strategy_rules(database_url, strategy_key), "legacy_strategy_row", None


def _find_paper_run_id(database_url: str, strategy_key: str) -> str | None:
    os.environ["POSTGRES_URL"] = database_url

    from services.database import get_session_factory, reset_database_caches
    from services.strategy_library import PaperRunRepository

    reset_database_caches()
    with get_session_factory()() as session:
        repo = PaperRunRepository(session)
        for run in repo.list_paper_runs():
            if run.execution_profile.get("auto_paper_runtime_key") == strategy_key:
                return run.paper_run_id
    return None


def _check_scheduler_heartbeat(path: Path, max_age_seconds: float) -> tuple[bool, str]:
    import json
    from datetime import UTC, datetime

    if not path.exists():
        return False, f"scheduler heartbeat file not found at {path} (scheduler has never run here)"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"scheduler heartbeat file at {path} unreadable: {exc}"

    heartbeat_at = state.get("heartbeat_at")
    if not heartbeat_at:
        return False, f"scheduler heartbeat file at {path} has no heartbeat_at field"
    try:
        heartbeat_time = datetime.fromisoformat(heartbeat_at)
    except ValueError as exc:
        return False, f"scheduler heartbeat_at {heartbeat_at!r} unparseable: {exc}"

    age_seconds = (datetime.now(UTC) - heartbeat_time).total_seconds()
    running = bool(state.get("running"))
    if not running:
        return False, f"scheduler heartbeat reports running=false (last heartbeat {age_seconds:.0f}s ago)"
    if age_seconds > max_age_seconds:
        return False, f"scheduler heartbeat is stale: {age_seconds:.0f}s old (max {max_age_seconds:.0f}s)"
    return True, f"scheduler heartbeat fresh: {age_seconds:.0f}s old, running=true"


def _check_api_reachable(base_url: str, admin_token: str, paper_run_id: str | None, timeout: float) -> tuple[bool, str]:
    import httpx

    health_url = f"{base_url.rstrip('/')}/health"
    try:
        response = httpx.get(health_url, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"GET {health_url} failed: {exc}"
    if response.status_code != 200:
        return False, f"GET {health_url} returned {response.status_code}"

    if paper_run_id is None:
        return True, "API /health reachable; no matching paper_run_id found to probe decision-trace"

    trace_url = f"{base_url.rstrip('/')}/api/v1/execution/paper-runs/{paper_run_id}/decision-trace"
    headers = {"Authorization": f"Bearer {admin_token}"}
    try:
        response = httpx.get(trace_url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"GET {trace_url} failed: {exc}"
    if response.status_code != 200:
        return (
            False,
            f"GET {trace_url} returned {response.status_code} -- the running API process "
            "likely cannot reach the same paper_run_id in its own database (wrong POSTGRES_URL?)",
        )
    return True, f"API reachable and decision-trace for {paper_run_id} returned 200"


def main() -> int:
    from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, resolve_auto_paper_technical_evidence

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-key", default=AUTO_PAPER_TECHNICAL_KEY)
    parser.add_argument(
        "--database-url", default=None, help="Defaults to POSTGRES_URL env, then local sqlite console db."
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8016")
    parser.add_argument("--admin-token", default=None, help="Defaults to shared.config.settings.admin_api_token.")
    parser.add_argument("--skip-api-check", action="store_true")
    parser.add_argument("--skip-heartbeat-check", action="store_true")
    parser.add_argument("--scheduler-state-path", default="logs/scheduler-state.json")
    parser.add_argument("--heartbeat-max-age-seconds", type=float, default=180.0)
    parser.add_argument("--api-timeout-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if args.strategy_key == AUTO_PAPER_TECHNICAL_KEY:
        code_rules, _ = resolve_auto_paper_technical_evidence()
    else:
        print(f"ERROR: no known bootstrap rules constant for strategy_key={args.strategy_key!r}", file=sys.stderr)
        return 2

    database_url = _resolve_database_url(args.database_url)
    print(f"database_url = {database_url}")

    revision = _load_database_revision(database_url)
    if revision != "0010":
        print(
            f"FAIL: database revision is {revision or '<unversioned>'}; expected 0010. "
            "Run scripts/prepare_database.py before reading runtime config."
        )
        return 1
    print("OK: database revision is 0010.")

    effective_rules, config_source, active_config_hash = _load_effective_strategy_rules(database_url, args.strategy_key)
    if effective_rules is None:
        print(
            f"FAIL: no Strategy row with strategy_key={args.strategy_key!r} found in {database_url}. "
            "Has bootstrap_local_paper_runtime() ever run against this database?"
        )
        return 1

    mismatches = _diff_strategy_rules(code_rules, effective_rules)
    exit_code = 0
    if mismatches:
        print(f"FAIL: {len(mismatches)} field mismatch(es) between promoted rules and effective runtime config:")
        for mismatch in mismatches:
            print(mismatch.render())
        print(
            "\nA restart deliberately preserves persisted rules. Run "
            "scripts/migrate_runtime_config_snapshot.py after applying database migration 0010, "
            "then activate the snapshot at the next cycle boundary."
        )
        exit_code = 1
    else:
        print(
            f"OK: effective rules for {args.strategy_key!r} match promoted rules "
            f"(source={config_source}, config_hash={active_config_hash or '<legacy>'})."
        )

    paper_run_id = _find_paper_run_id(database_url, args.strategy_key)
    print(f"paper_run_id = {paper_run_id or '<none found>'}")

    if not args.skip_heartbeat_check:
        healthy, message = _check_scheduler_heartbeat(Path(args.scheduler_state_path), args.heartbeat_max_age_seconds)
        print(f"[{'OK' if healthy else 'WARN'}] scheduler heartbeat: {message}")

    if not args.skip_api_check:
        from shared.config import settings

        admin_token = args.admin_token or settings.admin_api_token
        reachable, message = _check_api_reachable(
            args.api_base_url, admin_token, paper_run_id, args.api_timeout_seconds
        )
        print(f"[{'OK' if reachable else 'WARN'}] live API check: {message}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
