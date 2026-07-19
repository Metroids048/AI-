"""One-shot refactor: move _run_cycle + helpers from PaperRuntimeService → PaperCycleOrchestrator.

Run this ONCE from the project root:
    agent-python scripts/refactor_paper_runtime.py
"""

from __future__ import annotations

from pathlib import Path

SRC = Path("services/execution/paper_runtime.py")
ORCH = Path("services/execution/paper_cycle_orchestrator.py")

# ── line ranges (1-indexed, inclusive) ──────────────────────────────────────
# Imports section of paper_runtime.py: lines 1-64
# Class opening + __init__ + public methods: lines 66-127
# _run_cycle through end of class (line 1804): lines 128-1804
# Module-level helpers excluding _parse_datetime: lines 1806-1873
# _parse_datetime stays in paper_runtime: lines 1875-1882

MOVE_CLASS_START = 128   # _run_cycle first line
MOVE_CLASS_END   = 1804  # last line of _mark_position_as_hedged
MOD_LEVEL_START  = 1806  # _realized_pnl first line
MOD_LEVEL_END    = 1873  # last line before _parse_datetime's blank separator
PARSE_DT_START   = 1875  # _parse_datetime first line

# Methods that STAY in PaperRuntimeService (lines 1227-1260):
STAY_REQUIRE_PAPER_RUN = (1227, 1231)   # _require_paper_run
STAY_STARTING_EQUITY   = (1249, 1255)   # @staticmethod _starting_equity
STAY_INITIAL_EQUITY    = (1257, 1260)   # @staticmethod _initial_equity

def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    n = len(lines)
    print(f"Read {n} lines from {SRC}")

    # ── 1. Build imports for orchestrator (same as paper_runtime, minus self-import) ──
    import_lines = []
    for line in lines[0:64]:   # lines 1-64
        # drop the line that imports PaperCycleOrchestrator (will be the class itself)
        if "from services.execution.paper_cycle_orchestrator import" in line:
            continue
        import_lines.append(line)

    # ── 2. Extract the class body that moves (lines 128-1804, 0-indexed 127-1803) ──
    moving_class_body = lines[MOVE_CLASS_START - 1 : MOVE_CLASS_END]

    # Patch _run_cycle: replace `self._require_paper_run(paper_run_id)` with inline
    patched = []
    for line in moving_class_body:
        if "paper_run = self._require_paper_run(paper_run_id)" in line:
            indent = " " * (len(line) - len(line.lstrip()))
            patched.append(f"{indent}paper_run = self.paper_repo.get_paper_run(paper_run_id)\n")
            patched.append(f"{indent}if paper_run is None:\n")
            patched.append(f'{indent}    raise ValueError("paper run not found")\n')
        elif "self._initial_equity(paper_run)" in line:
            # inline _initial_equity since it stays in PaperRuntimeService
            line = line.replace(
                "self._initial_equity(paper_run)",
                "float(paper_run.execution_profile.get(\"account_equity\") or 10_000.0)",
            )
            patched.append(line)
        else:
            patched.append(line)
    moving_class_body = patched

    # ── 3. Extract module-level helpers that move (lines 1806-1873) ──
    moving_mod_helpers = lines[MOD_LEVEL_START - 1 : MOD_LEVEL_END]

    # ── 4. Build new paper_cycle_orchestrator.py ──────────────────────────────
    orch_header = [
        '"""Autonomous paper-trading cycle orchestration — owns the full cycle logic."""\n',
        "\n",
    ]

    orch_constructor = '''\

class PaperCycleOrchestrator:
    """Owns the complete paper-runtime cycle: signal → gatekeeper → execution → metrics."""

    def __init__(
        self,
        *,
        data_repo: "DataRepository",
        execution_repo: "ExecutionRepository",
        paper_repo: "PaperRunRepository",
        strategy_repo: "StrategyRepository",
        gatekeeper: "ExecutionGatekeeperService",
        gateway: "ExchangeGateway | None" = None,
        exchange_execution: "PaperExchangeExecutionService",
        order_lifecycle: "PaperOrderLifecycleService",
        signal_generator: "PaperSignalGenerator",
        decision_snapshot_repo: "DecisionSnapshotRepository",
        review_repo: "ReviewRepository | None" = None,
    ) -> None:
        self.data_repo = data_repo
        self.execution_repo = execution_repo
        self.paper_repo = paper_repo
        self.strategy_repo = strategy_repo
        self.gatekeeper = gatekeeper
        self.gateway = gateway
        self.exchange_execution = exchange_execution
        self.order_lifecycle = order_lifecycle
        self.signal_generator = signal_generator
        self.decision_snapshot_repo = decision_snapshot_repo
        self.review_repo = review_repo

    def run_cycle(
        self,
        *,
        paper_run_id: str,
        request: "PaperRuntimeCycleRequest",
    ) -> "PaperRuntimeCycleResult":
        return self._run_cycle(paper_run_id, request)

'''

    orch_content = (
        orch_header
        + import_lines
        + [orch_constructor]
        + moving_class_body
        + ["\n"]
        + moving_mod_helpers
    )
    ORCH.write_text("".join(orch_content), encoding="utf-8")
    orch_lines = ORCH.read_text(encoding="utf-8").splitlines()
    print(f"Wrote {len(orch_lines)} lines to {ORCH}")

    # ── 5. Build new slim paper_runtime.py ────────────────────────────────────
    # Keep: imports (without PaperCycleOrchestrator import) + add updated import
    # Keep: lines 66-127 (class header, __init__, get_runtime_status, run_cycle)
    # Keep: lines 1227-1231 (_require_paper_run)
    # Keep: lines 1249-1260 (_starting_equity, _initial_equity)
    # Keep: lines 1875-1882 (_parse_datetime)
    # Update: __init__ to inject all deps into PaperCycleOrchestrator

    # Build new paper_runtime.py
    new_runtime_lines = []

    # 5a. New docstring + imports
    new_runtime_lines.append('"""Autonomous paper-runtime cycles over validation-admitted strategies."""\n')
    new_runtime_lines.append("\n")
    new_runtime_lines.append("from __future__ import annotations\n")
    new_runtime_lines.append("\n")
    new_runtime_lines.append("from datetime import UTC, datetime\n")
    new_runtime_lines.append("from typing import Any\n")
    new_runtime_lines.append("\n")
    new_runtime_lines.append("from services.data import DataRepository\n")
    new_runtime_lines.append("from services.execution.gatekeeper import ExecutionGatekeeperService\n")
    new_runtime_lines.append("from services.execution.gateway import ExchangeGateway\n")
    new_runtime_lines.append("from services.execution.paper_cycle_orchestrator import PaperCycleOrchestrator\n")
    new_runtime_lines.append("from services.execution.paper_exchange_execution import PaperExchangeExecutionService\n")
    new_runtime_lines.append("from services.execution.paper_order_lifecycle import PaperOrderLifecycleService\n")
    new_runtime_lines.append("from services.execution.paper_signal import PaperSignalGenerator\n")
    new_runtime_lines.append("from services.strategy_library import (\n")
    new_runtime_lines.append("    AgentTaskRepository,\n")
    new_runtime_lines.append("    DecisionSnapshotRepository,\n")
    new_runtime_lines.append("    ExecutionRepository,\n")
    new_runtime_lines.append("    NotificationRepository,\n")
    new_runtime_lines.append("    PaperRunRepository,\n")
    new_runtime_lines.append("    ReviewRepository,\n")
    new_runtime_lines.append("    StrategyRepository,\n")
    new_runtime_lines.append(")\n")
    new_runtime_lines.append("from shared.models import (\n")
    new_runtime_lines.append("    PaperRun,\n")
    new_runtime_lines.append("    PaperRuntimeCycleRequest,\n")
    new_runtime_lines.append("    PaperRuntimeCycleResult,\n")
    new_runtime_lines.append("    PaperRuntimeStatus,\n")
    new_runtime_lines.append("    StrategyContract,\n")
    new_runtime_lines.append(")\n")
    new_runtime_lines.append("\n")
    new_runtime_lines.append("\n")

    # 5b. Class definition with updated __init__
    new_runtime_lines.append("class PaperRuntimeService:\n")
    new_runtime_lines.append('    """Public gateway for paper-runtime cycles — delegates to PaperCycleOrchestrator."""\n')
    new_runtime_lines.append("\n")
    new_runtime_lines.append("    def __init__(\n")
    new_runtime_lines.append("        self,\n")
    new_runtime_lines.append("        *,\n")
    new_runtime_lines.append("        data_repo: DataRepository,\n")
    new_runtime_lines.append("        execution_repo: ExecutionRepository,\n")
    new_runtime_lines.append("        paper_repo: PaperRunRepository,\n")
    new_runtime_lines.append("        strategy_repo: StrategyRepository,\n")
    new_runtime_lines.append("        agent_repo: AgentTaskRepository | None = None,\n")
    new_runtime_lines.append("        review_repo: ReviewRepository | None = None,\n")
    new_runtime_lines.append("        notification_repo: NotificationRepository | None = None,\n")
    new_runtime_lines.append("        gatekeeper: ExecutionGatekeeperService,\n")
    new_runtime_lines.append("        gateway: ExchangeGateway | None = None,\n")
    new_runtime_lines.append("    ) -> None:\n")
    new_runtime_lines.append("        self.data_repo = data_repo\n")
    new_runtime_lines.append("        self.execution_repo = execution_repo\n")
    new_runtime_lines.append("        self.paper_repo = paper_repo\n")
    new_runtime_lines.append("        self.strategy_repo = strategy_repo\n")
    new_runtime_lines.append("        self.review_repo = review_repo\n")
    new_runtime_lines.append("        self.gatekeeper = gatekeeper\n")
    new_runtime_lines.append("        self.gateway = gateway\n")
    new_runtime_lines.append("        exchange_execution = PaperExchangeExecutionService(\n")
    new_runtime_lines.append("            execution_repo=execution_repo,\n")
    new_runtime_lines.append("            gateway=gateway,\n")
    new_runtime_lines.append("            review_repo=review_repo,\n")
    new_runtime_lines.append("        )\n")
    new_runtime_lines.append("        order_lifecycle = PaperOrderLifecycleService(execution_repo=execution_repo)\n")
    new_runtime_lines.append("        decision_snapshot_repo = DecisionSnapshotRepository(execution_repo.session)\n")
    new_runtime_lines.append("        signal_generator = PaperSignalGenerator(\n")
    new_runtime_lines.append("            data_repo=data_repo,\n")
    new_runtime_lines.append("            execution_repo=execution_repo,\n")
    new_runtime_lines.append("            agent_repo=agent_repo,\n")
    new_runtime_lines.append("            strategy_repo=strategy_repo,\n")
    new_runtime_lines.append("            review_repo=review_repo,\n")
    new_runtime_lines.append("            notification_repo=notification_repo,\n")
    new_runtime_lines.append("        )\n")
    new_runtime_lines.append("        self.cycle_orchestrator = PaperCycleOrchestrator(\n")
    new_runtime_lines.append("            data_repo=data_repo,\n")
    new_runtime_lines.append("            execution_repo=execution_repo,\n")
    new_runtime_lines.append("            paper_repo=paper_repo,\n")
    new_runtime_lines.append("            strategy_repo=strategy_repo,\n")
    new_runtime_lines.append("            gatekeeper=gatekeeper,\n")
    new_runtime_lines.append("            gateway=gateway,\n")
    new_runtime_lines.append("            exchange_execution=exchange_execution,\n")
    new_runtime_lines.append("            order_lifecycle=order_lifecycle,\n")
    new_runtime_lines.append("            signal_generator=signal_generator,\n")
    new_runtime_lines.append("            decision_snapshot_repo=decision_snapshot_repo,\n")
    new_runtime_lines.append("            review_repo=review_repo,\n")
    new_runtime_lines.append("        )\n")
    new_runtime_lines.append("        # Preserve accessible service references for external callers\n")
    new_runtime_lines.append("        self.exchange_execution = exchange_execution\n")
    new_runtime_lines.append("        self.order_lifecycle = order_lifecycle\n")
    new_runtime_lines.append("        self.decision_snapshot_repo = decision_snapshot_repo\n")
    new_runtime_lines.append("        self.signal_generator = signal_generator\n")
    new_runtime_lines.append("\n")

    # 5c. get_runtime_status (lines 106-123, 0-indexed 105-122)
    new_runtime_lines.extend(lines[105:123])
    new_runtime_lines.append("\n")

    # 5d. run_cycle (lines 125-126, 0-indexed 124-125)
    new_runtime_lines.extend(lines[124:126])
    new_runtime_lines.append("\n")

    # 5e. _require_paper_run (lines 1227-1231, 0-indexed 1226-1231)
    new_runtime_lines.extend(lines[1226:1232])
    new_runtime_lines.append("\n")

    # 5f. _starting_equity (lines 1249-1255, 0-indexed 1248-1255)
    new_runtime_lines.extend(lines[1248:1256])
    new_runtime_lines.append("\n")

    # 5g. _initial_equity (lines 1257-1259, 0-indexed 1256-1260)
    new_runtime_lines.extend(lines[1256:1260])
    new_runtime_lines.append("\n")
    new_runtime_lines.append("\n")

    # 5h. _parse_datetime module-level function (lines 1875-1882, 0-indexed 1874-1881)
    new_runtime_lines.extend(lines[1874:])

    SRC.write_text("".join(new_runtime_lines), encoding="utf-8")
    runtime_lines = SRC.read_text(encoding="utf-8").splitlines()
    print(f"Wrote {len(runtime_lines)} lines to {SRC}")
    print("Done. Run: pytest -q && mypy")


if __name__ == "__main__":
    main()
