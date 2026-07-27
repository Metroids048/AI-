"""Verify the local Paper scheduler, universe, and automatic order path."""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.data.universe import (
    AUTO_PAPER_RESEARCH_SYMBOLS,
    AUTO_SIMULATION_EXECUTION_SYMBOLS,
    execution_scope_hash,
)
from services.execution.gateway import probe_testnet_account
from shared.config import settings

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".local_paper_console.db"
STATE_PATH = ROOT / "logs" / "scheduler-state.json"
AUTO_STRATEGY_KEYS = (
    "auto_paper_mature_templates",
    "signal_observation_technical",
)
ACTIVE_ORDER_STATUSES = {"accepted", "filled", "submitted", "open"}

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_current_sampling_gateway_order(
    row,
    context: dict,
    *,
    now: datetime,
    build_id: str,
    max_age: timedelta = timedelta(hours=24),
) -> bool:
    created_at = _parse_datetime(row["created_at"])
    return bool(
        row["gateway_order_id"]
        and str(row["execution_status"]).lower() in ACTIVE_ORDER_STATUSES
        and row["symbol"] in AUTO_SIMULATION_EXECUTION_SYMBOLS
        and created_at is not None
        and timedelta(0) <= now - created_at <= max_age
        and context.get("execution_kind") == "strategy_trade"
        and context.get("strategy_lane") == "signal_observation"
        and context.get("runtime_build_id") == build_id
        and context.get("execution_scope_hash") == execution_scope_hash()
    )


def _detect_orphan_bootstrap_configs(source: str | None = None) -> list[tuple[str, str, str]]:
    """Scan bootstrap.py for RULES dicts whose bootstrap function is not wired into
    bootstrap_local_paper_runtime(). Returns list of (rules_name, fn_name, reason).
    Pass ``source`` directly in tests to avoid filesystem dependency."""
    if source is None:
        bootstrap_path = Path(__file__).resolve().parents[1] / "services" / "execution" / "bootstrap.py"
        try:
            source = bootstrap_path.read_text(encoding="utf-8")
        except OSError as exc:
            return [("(read error)", "", str(exc))]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [("(parse error)", "", str(exc))]

    # Module-level assignments ending in _RULES (handles both bare and annotated assignments)
    rules_names: set[str] = set()
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id.endswith("_RULES")
        ):
            rules_names.add(stmt.targets[0].id)
        elif (
            isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id.endswith("_RULES")
        ):
            rules_names.add(stmt.target.id)

    # Functions that reference each RULES dict
    rules_to_bootstrap_fns: dict[str, set[str]] = {name: set() for name in rules_names}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fn_name = node.name
        if not fn_name.startswith("bootstrap_") or fn_name == "bootstrap_local_paper_runtime":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in rules_names:
                rules_to_bootstrap_fns[child.id].add(fn_name)

    # Functions called from bootstrap_local_paper_runtime
    auto_boot_calls: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "bootstrap_local_paper_runtime":
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    auto_boot_calls.add(child.func.id)

    # Configs intentionally excluded (docstring contains "deliberately NOT wired")
    intentionally_excluded: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node) or ""
            if "deliberately NOT wired" in docstring or "deliberately not wired" in docstring.lower():
                # Find which RULES this function references
                for rules_name, fns in rules_to_bootstrap_fns.items():
                    if node.name in fns:
                        intentionally_excluded.add(rules_name)

    orphans: list[tuple[str, str, str]] = []
    for rules_name, fns in sorted(rules_to_bootstrap_fns.items()):
        if rules_name in intentionally_excluded or not fns:
            continue
        unwired = {fn for fn in fns if fn not in auto_boot_calls}
        if unwired and not any(fn in auto_boot_calls for fn in fns):
            reason = "bootstrap function exists but not called from bootstrap_local_paper_runtime()"
            for fn in sorted(unwired):
                orphans.append((rules_name, fn, reason))
    return orphans


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append((name, passed, detail))

    if not DB_PATH.exists():
        record("本地数据库", False, f"缺少 {DB_PATH}")
        return _report(checks)
    if not STATE_PATH.exists():
        record("Scheduler 状态文件", False, f"缺少 {STATE_PATH}")
        return _report(checks)

    state = _json_object(STATE_PATH.read_text(encoding="utf-8"))

    # Celery / Redis infrastructure check (Section 3.1)
    redis_ok = False
    redis_detail = "not checked"
    try:
        import redis as _redis  # type: ignore[import-untyped]

        _r = _redis.from_url(
            str(settings.redis_url or "redis://localhost:6379/0"),
            socket_connect_timeout=2,
        )
        _r.ping()
        redis_ok = True
        redis_detail = f"connected to {settings.redis_url}"
    except ImportError:
        redis_detail = "redis package not installed (pip install redis)"
    except Exception as exc:
        redis_detail = f"unreachable: {type(exc).__name__}({exc})"
    record("Redis 连接", redis_ok, redis_detail)

    celery_ok = False
    celery_detail = "not checked"
    try:
        from apps.api.celery_app import celery_app  # noqa: PLC0415

        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.ping()
        if active:
            celery_ok = True
            celery_detail = f"{len(active)} worker(s) responding"
        else:
            celery_detail = "no workers responded (inprocess scheduler may be used — check RUNTIME_SCHEDULER_MODE)"
    except Exception as exc:
        celery_detail = f"probe failed: {type(exc).__name__}"
    record("Celery Workers", celery_ok, celery_detail)

    heartbeat_at = _parse_datetime(state.get("heartbeat_at"))
    last_cycle_at = _parse_datetime(state.get("last_auto_cycle_at"))
    now = datetime.now(UTC)
    heartbeat_age = (now - heartbeat_at).total_seconds() if heartbeat_at else None
    cycle_age = (now - last_cycle_at).total_seconds() if last_cycle_at else None
    expected_symbols = list(AUTO_PAPER_RESEARCH_SYMBOLS)

    record("Scheduler 进程", bool(state.get("running")), f"running={state.get('running')}")
    record(
        "Scheduler 心跳",
        heartbeat_age is not None and heartbeat_age <= 120,
        f"age_seconds={heartbeat_age:.1f}" if heartbeat_age is not None else "heartbeat_at 缺失",
    )
    record(
        "自动周期",
        cycle_age is not None and cycle_age <= 600,
        f"age_seconds={cycle_age:.1f}" if cycle_age is not None else "last_auto_cycle_at 缺失",
    )
    record("Scheduler 异常", not state.get("scheduler_error"), str(state.get("scheduler_error") or "无"))
    record("ExchangeInfo", bool(state.get("exchange_info_ready")), f"ready={state.get('exchange_info_ready')}")
    record("行情新鲜度", bool(state.get("data_fresh")), f"data_fresh={state.get('data_fresh')}")
    coverage = int(state.get("top20_coverage_count") or 0)
    record("固定币种覆盖", coverage == len(expected_symbols), f"{coverage}/{len(expected_symbols)}")

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        paper_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_runs)").fetchall()}
        position_columns = {row[1] for row in connection.execute("PRAGMA table_info(position_snapshots)").fetchall()}
        record(
            "数据库 Schema",
            {"paper_status", "execution_profile", "paper_metrics_summary"}.issubset(paper_columns)
            and {"hedge_group_id", "is_hedge_leg"}.issubset(position_columns),
            "PaperRun 与 hedge 列齐全",
        )

        placeholders = ",".join("?" for _ in AUTO_STRATEGY_KEYS)
        runs = connection.execute(
            f"""
            SELECT p.paper_run_id, p.paper_status, p.candidate_symbols,
                   p.execution_profile, p.paper_metrics_summary, s.strategy_key
            FROM paper_runs p
            JOIN strategies s ON s.id = p.strategy_id
            WHERE s.strategy_key IN ({placeholders})
            ORDER BY s.strategy_key
            """,
            AUTO_STRATEGY_KEYS,
        ).fetchall()
        statuses = {row["strategy_key"]: row["paper_status"] for row in runs}
        record(
            "自动 PaperRun 数量",
            len(runs) == len(AUTO_STRATEGY_KEYS),
            f"found={len(runs)} expected={len(AUTO_STRATEGY_KEYS)}",
        )
        record(
            "自动 PaperRun 状态",
            len(runs) == len(AUTO_STRATEGY_KEYS) and all(row["paper_status"] == "running" for row in runs),
            json.dumps(statuses, ensure_ascii=False, sort_keys=True),
        )
        simulation_profiles_ok = True
        for row in runs:
            profile = _json_object(row["execution_profile"])
            if row["strategy_key"] == "auto_paper_mature_templates":
                simulation_profiles_ok = simulation_profiles_ok and (
                    profile.get("execution_mode") == "local_paper" and not bool(profile.get("mirror_to_gateway"))
                )
            else:
                mode = profile.get("execution_mode")
                mirror = bool(profile.get("mirror_to_gateway"))
                simulation_profiles_ok = simulation_profiles_ok and (
                    (mode == "local_paper" and not mirror)
                    or (
                        mode == "binance_testnet"
                        and mirror
                        and bool(profile.get("cost_gate_verified"))
                        and profile.get("acceptance_symbols") == expected_symbols
                        and profile.get("acceptance_scope_hash") == execution_scope_hash()
                    )
                )
        record(
            "Paper 执行隔离配置",
            len(runs) == len(AUTO_STRATEGY_KEYS) and simulation_profiles_ok,
            "主策略保持 Paper-only；观察通道仅可在 Top3 验收后镜像",
        )

        run_coverage_ok = True
        cycle_metrics_ok = True
        for row in runs:
            candidates = json.loads(row["candidate_symbols"] or "[]")
            metrics = _json_object(row["paper_metrics_summary"])
            scanned = metrics.get("last_scanned_symbols") or []
            run_coverage_ok = run_coverage_ok and candidates == expected_symbols
            cycle_metrics_ok = cycle_metrics_ok and scanned == expected_symbols and bool(metrics.get("last_cycle_at"))
        record("PaperRun Top3 配置", run_coverage_ok, ", ".join(expected_symbols))
        record("PaperRun 实际扫描", cycle_metrics_ok, "两个自动 Run 均已扫描固定 Top3")

        automatic_rows = connection.execute(
            """
            SELECT execution_status, rejection_codes, entry_context, symbol, created_at, gateway_order_id
            FROM order_executions
            WHERE gateway_order_id IS NOT NULL AND gateway_order_id != ''
            ORDER BY created_at DESC
            """
        ).fetchall()
        automatic_orders = []
        for row in automatic_rows:
            context = _json_object(row["entry_context"])
            if _is_current_sampling_gateway_order(
                row,
                context,
                now=now,
                build_id=settings.app_build_id,
            ):
                automatic_orders.append((row, context))
        record("当前采样策略网关订单", bool(automatic_orders), f"count={len(automatic_orders)}（24h/current build）")

        active_orders = automatic_orders
        active_detail = (
            f"{active_orders[0][0]['symbol']} gateway_order_id={active_orders[0][0]['gateway_order_id']}"
            if active_orders
            else "没有已接受/成交且含 Binance gateway_order_id 的自动订单"
        )
        record("自动开单", bool(active_orders), active_detail)

        edge_units_ok = True
        if automatic_orders:
            _, context = automatic_orders[0]
            values = (
                context.get("meta_label_win_rate"),
                context.get("meta_label_average_win"),
                context.get("meta_label_average_loss"),
                context.get("round_trip_fee_rate"),
                context.get("round_trip_slippage_rate"),
            )
            numeric_values = [value for value in values if isinstance(value, (int, float))]
            edge_units_ok = len(numeric_values) == len(values) and all(
                0 <= float(value) <= 1 for value in numeric_values
            )
        record("净期望单位", edge_units_ok, "胜率/盈亏/成本均为分数收益率，不是 R 倍数")
    finally:
        connection.close()

    record(
        "主网保护",
        not settings.live_trading_enabled and settings.binance_use_testnet,
        "LIVE=false, simulation=true",
    )
    try:
        account = probe_testnet_account(order_limit=30)
        gateway_ids = {str(order.order_id) for order in account.recent_orders}
        matched_id = str(active_orders[0][0]["gateway_order_id"]) if active_orders else ""
        record(
            "Binance 模拟账户连接",
            bool(account.connected) and account.api_backend in {"demo", "testnet", "testnet-fallback"},
            f"connected={account.connected} backend={account.api_backend}",
        )
        record(
            "Binance 自动订单回读",
            not matched_id or matched_id in gateway_ids,
            f"gateway_order_id={matched_id or 'none（当前未要求自动镜像）'}",
        )
    except Exception as exc:  # noqa: BLE001 - verification must show the external probe failure
        record("Binance 模拟账户连接", False, str(exc))
        record("Binance 自动订单回读", False, "账户探针失败")

    orphans = _detect_orphan_bootstrap_configs()
    return _report(checks, orphans)


def _report(checks: list[tuple[str, bool, str]], orphan_warnings: list[tuple[str, str, str]] | None = None) -> int:
    print("=" * 72)
    print("AI Quant 本地 Paper 自动开单验收")
    print(f"时间: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print("=" * 72)
    for name, passed, detail in checks:
        color = GREEN if passed else RED
        label = "PASS" if passed else "FAIL"
        print(f"{color}[{label}]{RESET} {name}: {detail}")
    if orphan_warnings:
        print("-" * 72)
        print(f"{YELLOW}[WARNING] 发现 {len(orphan_warnings)} 个孤儿配置 — bootstrap 函数已定义但未接入调度链路{RESET}")
        for rules_name, fn_name, reason in orphan_warnings:
            print(f"{YELLOW}  • {rules_name} → {fn_name}{RESET}")
            print(f"    {reason}")
        print(
            f"{YELLOW}  建议：确认是需要接线到 bootstrap_local_paper_runtime()"
            f" 还是应删除，否则下次重启仍不会扫描。{RESET}"
        )
    passed_count = sum(1 for _, passed, _ in checks if passed)
    print("-" * 72)
    if passed_count == len(checks):
        print(f"{GREEN}GREEN: {passed_count}/{len(checks)} checks passed{RESET}")
        return 0
    print(f"{RED}RED: {passed_count}/{len(checks)} checks passed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
