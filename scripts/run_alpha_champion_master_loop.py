"""Bounded Alpha Champion research and Testnet acceptance coordinator.

The coordinator owns orchestration and evidence, not trading mechanics.  It
reuses the proposal replay runner, walk-forward boundaries, TrialLedger, and
the existing V2 authority path.  A failed ordinary step is raised so callers
can repair and resume; only the explicit terminal statuses are persisted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Any

from scripts import run_proposal_research_replay as proposal_research
from services.strategy_library.candidates.breakout_continuation_v1 import (
    BreakoutContinuationConfig,
    evaluate_breakout_continuation,
)
from services.strategy_library.candidates.breakout_retest_v1 import BreakoutRetestConfig, evaluate_breakout_retest
from services.strategy_library.candidates.donchian_breakout_retest_v1 import (
    DonchianBreakoutRetestConfig,
    evaluate_donchian_breakout_retest,
)
from services.strategy_library.candidates.failed_breakout_reversal_v1 import (
    FailedBreakoutConfig,
    evaluate_failed_breakout_reversal,
)
from services.strategy_library.candidates.htf_trend_continuation_v1 import (
    HTFTrendContinuationConfig,
    evaluate_htf_trend_continuation,
)
from services.strategy_library.candidates.loss_aware_trend_pullback_v1 import evaluate_loss_aware_trend_pullback
from services.strategy_library.candidates.momentum_continuation_v1 import (
    MomentumContinuationConfig,
    evaluate_momentum_continuation,
)
from services.strategy_library.candidates.range_sweep_reversion_v1 import (
    RangeSweepConfig,
    evaluate_range_sweep_reversion,
)
from services.strategy_library.candidates.registry import CANDIDATE_REGISTRY, get_candidate, list_candidates
from services.strategy_library.candidates.trend_pullback_v2 import TrendPullbackConfig, evaluate_trend_pullback_v2
from services.strategy_library.candidates.volatility_expansion_v1 import (
    VolatilityExpansionConfig,
    evaluate_volatility_expansion,
)
from services.strategy_library.proposal_pipeline import RESEARCH_CANDIDATE_VERSIONS, CandidateEvaluator
from services.validation.strategy_promotion import (
    ProfitabilityRecoveryMetrics,
    TrialLedger,
    evaluate_profitability_recovery,
)
from services.validation.technical_replay import MarketData, TechnicalStrategyValidationService
from shared.models import OHLCVBar, StrategyContract, StrategyRules, Timeframe


class MasterStage(StrEnum):
    S0_BASELINE_SYNC = "S0_BASELINE_SYNC"
    S1_ACTIVE_STRATEGY_PATH_AUDIT = "S1_ACTIVE_STRATEGY_PATH_AUDIT"
    S2_DATA_AND_REPLAY_INTEGRITY = "S2_DATA_AND_REPLAY_INTEGRITY"
    S3_CURRENT_ALPHA_LOSS_DECOMPOSITION = "S3_CURRENT_ALPHA_LOSS_DECOMPOSITION"
    S4_CANDIDATE_NORMALIZATION = "S4_CANDIDATE_NORMALIZATION"
    S5_GENERATION_0_TOURNAMENT = "S5_GENERATION_0_TOURNAMENT"
    S6_BOUNDED_ALPHA_RESEARCH = "S6_BOUNDED_ALPHA_RESEARCH"
    S7_ENTRY_CHAMPION_SELECTION = "S7_ENTRY_CHAMPION_SELECTION"
    S8_EXIT_TOURNAMENT = "S8_EXIT_TOURNAMENT"
    S9_FINAL_UNTOUCHED_AUDIT = "S9_FINAL_UNTOUCHED_AUDIT"
    S10_CHAMPION_PROMOTION = "S10_CHAMPION_PROMOTION"
    S11_EXECUTION_CONTRACT_REGRESSION = "S11_EXECUTION_CONTRACT_REGRESSION"
    S12_FULL_REPOSITORY_REGRESSION = "S12_FULL_REPOSITORY_REGRESSION"
    S13_INDEPENDENT_REVIEW = "S13_INDEPENDENT_REVIEW"
    S14_BINANCE_TESTNET_NATURAL_VALIDATION = "S14_BINANCE_TESTNET_NATURAL_VALIDATION"
    S15_FINAL_ACCEPTANCE = "S15_FINAL_ACCEPTANCE"


class TerminalStatus(StrEnum):
    ALPHA_CHAMPION_TESTNET_CLOSED_LOOP_VALIDATED = "ALPHA_CHAMPION_TESTNET_CLOSED_LOOP_VALIDATED"
    BLOCKED_BASELINE = "BLOCKED_BASELINE"
    BLOCKED_DATA_INTEGRITY = "BLOCKED_DATA_INTEGRITY"
    BLOCKED_EXCHANGE_EXTERNAL = "BLOCKED_EXCHANGE_EXTERNAL"
    BLOCKED_EXTERNAL_NATURAL_MARKET = "BLOCKED_EXTERNAL_NATURAL_MARKET"
    NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH = "NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH"


CONTROL_CANDIDATES = frozenset(
    {
        "testnet_sampling_v2",
        "operator_heuristic_v1",
        "operator_heuristic_v2_relaxed",
        "trend_momentum_v1",
        "trend_momentum_v2_enriched",
        "trend_breakout_v1",
        "pandas_ta_broad_screen_v1",
    }
)
CANARY_CANDIDATES = frozenset({"testnet_sampling_v2"})
TOURNAMENT_CONTROL_CANDIDATES = CONTROL_CANDIDATES - CANARY_CANDIDATES
PROPOSAL_CANDIDATES = frozenset(RESEARCH_CANDIDATE_VERSIONS)
ALL_INVENTORY_IDS = tuple(sorted(CONTROL_CANDIDATES | PROPOSAL_CANDIDATES | set(list_candidates())))
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
RESEARCH_START = datetime(2023, 1, 29, tzinfo=UTC)
MAX_GENERATION = 2


@dataclass(frozen=True)
class CandidateInventoryRecord:
    candidate_id: str
    version: str | None
    family: str
    registered: bool
    evaluator_path: str | None
    canonical_replay_reachable: bool
    research_only: bool
    execution_eligible: bool
    symbols: tuple[str, ...]
    timeframe: str | None
    entry_contract: str | None
    reason: str | None = None


@dataclass(frozen=True)
class MasterCheckpoint:
    schema_version: int
    stage: str
    status: str
    generated_at: str
    baseline: dict[str, Any]
    candidate_id: str | None = None
    candidate_version: str | None = None
    eligible_symbols: tuple[str, ...] = ()
    latest_decision_id: str | None = None
    latest_candidate_id: str | None = None
    exchange_position: dict[str, Any] = field(default_factory=dict)
    local_position: dict[str, Any] = field(default_factory=dict)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    pending_acceptance_stage: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MasterSplitPlan:
    """Chronological 60/20/20 split used by every bounded research stage."""

    data_start: datetime
    research_start: datetime
    research_end: datetime
    validation_start: datetime
    validation_end: datetime
    final_start: datetime
    final_end: datetime

    def as_record(self) -> dict[str, str]:
        return {key: value.isoformat() for key, value in asdict(self).items()}


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    parent_candidate: str
    family: str
    generation: int
    hypothesis: str
    parameters: dict[str, Any]
    changed_parameters: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "parent_candidate": self.parent_candidate,
            "family": self.family,
            "generation": self.generation,
            "hypothesis": self.hypothesis,
            "parameters": self.parameters,
            "changed_parameters": self.changed_parameters,
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _json_read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def capture_baseline(root: Path, database: Path) -> dict[str, Any]:
    """Capture a small, reproducible baseline without mutating the worktree."""

    diff = _git(root, "diff", "--no-ext-diff", "--binary")
    manifest = root / "docs/evidence/active-manifests/auto_paper_mature_templates.json"
    runtime_manifest: dict[str, Any] = {}
    if manifest.is_file():
        runtime_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    return {
        "captured_at": _now().isoformat(),
        "branch": _git(root, "branch", "--show-current"),
        "head": _git(root, "rev-parse", "HEAD"),
        "recent_commits": _git(root, "log", "-10", "--oneline", "--decorate").splitlines(),
        "status": _git(root, "status", "--short").splitlines(),
        "diff_stat": _git(root, "diff", "--stat"),
        "diff_check": _git(root, "diff", "--check"),
        "worktree_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "database": str(database),
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest() if database.is_file() else None,
        "runtime_manifest": runtime_manifest,
        "execution_mode": runtime_manifest.get("execution_mode", "UNKNOWN"),
        "active_candidate": runtime_manifest.get("candidate_id"),
    }


def _family(candidate_id: str) -> str:
    if "pullback" in candidate_id or "trend" in candidate_id:
        return "trend"
    if "breakout" in candidate_id or "donchian" in candidate_id or "volatility" in candidate_id:
        return "breakout"
    if "range" in candidate_id or "reversal" in candidate_id:
        return "range_reversal"
    if "momentum" in candidate_id:
        return "momentum"
    if "sampling" in candidate_id or "heuristic" in candidate_id:
        return "control"
    return "other"


def discover_candidate_inventory() -> tuple[CandidateInventoryRecord, ...]:
    records: list[CandidateInventoryRecord] = []
    for candidate_id in ALL_INVENTORY_IDS:
        candidate = CANDIDATE_REGISTRY.get(candidate_id)
        registered = candidate is not None
        evaluator_path: str | None = None
        if candidate_id in PROPOSAL_CANDIDATES:
            evaluator_path = "proposal_pipeline -> ProposalReplayRunner"
        elif candidate_id in CONTROL_CANDIDATES:
            evaluator_path = "technical_replay control"
        version = candidate.version if candidate else RESEARCH_CANDIDATE_VERSIONS.get(candidate_id)
        symbols = tuple(value.strip() for value in (candidate.market if candidate else "BTC/USDT,ETH/USDT").split(","))
        research_only = bool(
            candidate
            and (
                candidate.lifecycle_state == "RESEARCH_ONLY"
                or candidate.get_config()["entry_rules"].get("research_only")
            )
        )
        execution_eligible = bool(candidate and candidate.execution_eligible)
        reachable = registered and evaluator_path is not None
        reason = None if reachable else "missing registry entry or evaluator path"
        records.append(
            CandidateInventoryRecord(
                candidate_id=candidate_id,
                version=version,
                family=_family(candidate_id),
                registered=registered,
                evaluator_path=evaluator_path,
                canonical_replay_reachable=reachable,
                research_only=research_only,
                execution_eligible=execution_eligible,
                symbols=symbols,
                timeframe=candidate.timeframe if candidate else "15m",
                entry_contract="proposal" if candidate_id in PROPOSAL_CANDIDATES else "technical_control",
                reason=reason,
            )
        )
    return tuple(records)


def tournament_candidate_ids(inventory: tuple[CandidateInventoryRecord, ...]) -> tuple[str, ...]:
    """Return every reachable registry candidate eligible for research competition.

    Canary controls are deliberately excluded: they remain execution-health
    probes and cannot enter Champion ranking or promotion evidence.
    """

    return tuple(
        sorted(
            item.candidate_id
            for item in inventory
            if item.registered
            and item.canonical_replay_reachable
            and item.candidate_id not in CANARY_CANDIDATES
        )
    )


def build_dual_gate_report(
    *, execution_chain: dict[str, Any], profitability_recovery: dict[str, Any]
) -> dict[str, Any]:
    """Combine the two independent acceptance gates without conflating them."""

    execution_status = str(execution_chain.get("status", "BLOCKED")).upper()
    profitability_status = str(profitability_recovery.get("status", "BLOCKED")).upper()
    if execution_status == "PASS" and profitability_status == "PASS":
        overall = "FINAL_ACCEPTANCE_PASS"
    elif execution_status == "BLOCKED":
        overall = "BLOCKED"
    else:
        overall = "PENDING"
    return {
        "execution_chain": execution_chain,
        "profitability_recovery": profitability_recovery,
        "overall_status": overall,
    }


def blocked_dual_gate(reason: str) -> dict[str, Any]:
    """Return an explicit two-gate blocked state for incomplete evidence."""

    return build_dual_gate_report(
        execution_chain={"status": "BLOCKED", "evidence": [reason]},
        profitability_recovery={"status": "BLOCKED", "evidence": [reason]},
    )


def _load_technical_market_data(database: Path, *, end_at: datetime) -> MarketData:
    market_data: MarketData = defaultdict(dict)
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT time, symbol, timeframe, open, high, low, close, volume
            FROM ohlcv_bars
            WHERE symbol IN ('BTC/USDT', 'ETH/USDT') AND time < ?
            ORDER BY symbol, timeframe, time
            """,
            (end_at.replace(tzinfo=None).isoformat(sep=" "),),
        ).fetchall()
    for raw_time, symbol, timeframe, opened, high, low, close, volume in rows:
        parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        timestamp = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        market_data[str(symbol)].setdefault(str(timeframe), []).append(
            OHLCVBar(
                symbol=str(symbol),
                timeframe=Timeframe(str(timeframe)),
                time=timestamp,
                open=opened,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return dict(market_data)


def _technical_candidate_result(
    *, database: Path, candidate_id: str, windows: tuple[proposal_research.ProposalWalkForwardWindow, ...]
) -> dict[str, Any]:
    candidate = get_candidate(candidate_id)
    strategy = StrategyContract(
        strategy_id=f"master:{candidate_id}",
        strategy_key=f"master:{candidate_id}",
        source=candidate.source,
        core_thesis=candidate.hypothesis,
        symbol_scope=["BTC/USDT", "ETH/USDT"],
        timeframe=Timeframe.M15,
        rules=StrategyRules(**candidate.get_config()),
    )
    service = TechnicalStrategyValidationService(warmup_bars=80, walk_forward_windows=3, max_workers=1)
    data = _load_technical_market_data(database, end_at=max(window.oos_end for window in windows))
    window_payload: dict[str, Any] = {}
    all_trades: list[dict[str, Any]] = []
    symbol_payload: dict[str, Any] = {}
    for window in windows:
        symbols_payload: dict[str, Any] = {}
        for symbol in ("BTC/USDT", "ETH/USDT"):
            metrics = service.replay(
                strategy=strategy,
                market_data={symbol: data.get(symbol, {})},
                start_at=window.oos_start,
                end_at=window.oos_end,
            )
            trades = [trade.as_dict() for trade in metrics.trades]
            all_trades.extend(trades)
            symbols_payload[symbol] = {
                "total_trades": metrics.total_trades,
                "net_expectancy": metrics.net_expectancy,
                "net_return": metrics.net_return,
                "profit_factor": metrics.profit_factor,
                "max_drawdown": metrics.max_drawdown,
                "funding_rate_available": False,
                "promotion_observations_complete": False,
                "slippage_observed": bool(metrics.total_slippage_bps),
                "trade_attribution_complete": bool(metrics.trades),
                "trades": trades,
            }
            symbol_payload[symbol] = symbols_payload[symbol]
        window_payload[window.window_id] = {"window": window.as_record(), "symbols": symbols_payload}
    total_trades = len(all_trades)
    net_return = sum(float(item.get("net_return", 0.0)) for item in all_trades)
    winners = sum(float(item.get("net_return", 0.0)) for item in all_trades if float(item.get("net_return", 0.0)) > 0)
    losers = abs(sum(float(item.get("net_return", 0.0)) for item in all_trades if float(item.get("net_return", 0.0)) < 0))
    per_symbol = {
        symbol: {
            "total_trades": sum(int(window["symbols"][symbol]["total_trades"]) for window in window_payload.values()),
            "profit_factor": min(
                (float(window["symbols"][symbol]["profit_factor"]) for window in window_payload.values()),
                default=0.0,
            ),
        }
        for symbol in ("BTC/USDT", "ETH/USDT")
    }
    return {
        "candidate_id": candidate_id,
        "candidate_metadata": {
            "source": candidate.source,
            "version": candidate.version,
            "research_only": candidate.lifecycle_state == "RESEARCH_ONLY",
            "execution_eligible": candidate.execution_eligible,
            "symbols": candidate.market.split(","),
            "timeframe": candidate.timeframe,
            "replay_engine": "TechnicalStrategyValidationService",
        },
        "symbols": {symbol: {**payload, **per_symbol[symbol]} for symbol, payload in symbol_payload.items()},
        "portfolio": {
            "total_trades": total_trades,
            "net_return": net_return,
            "net_expectancy": net_return / total_trades if total_trades else 0.0,
            "profit_factor": winners / losers if losers else (float("inf") if winners else 0.0),
            "max_drawdown": max(
                (float(window["symbols"][symbol]["max_drawdown"]) for window in window_payload.values() for symbol in ("BTC/USDT", "ETH/USDT")),
                default=0.0,
            ),
            "funding_rate_available": False,
            "promotion_observations_complete": False,
        },
        "trades": all_trades,
        "walk_forward_oos": window_payload,
        "funding_treatment": "not_available_for_technical_replay",
    }


def audit_market_data(database: Path) -> dict[str, Any]:
    """Validate closed-bar data and alignment without using returns for selection."""

    if not database.is_file():
        return {"passed": False, "reason": "database_missing", "database": str(database)}
    required = ("BTC/USDT", "ETH/USDT")
    timeframes = ("15m", "1h", "4h")
    payload: dict[str, Any] = {"passed": True, "symbols": {}, "required_timeframes": timeframes}
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ohlcv_bars" not in tables:
            return {"passed": False, "reason": "ohlcv_bars_missing", "database": str(database)}
        for symbol in required:
            payload["symbols"][symbol] = {}
            for timeframe in timeframes:
                rows = connection.execute(
                    "SELECT time, open, high, low, close, volume FROM ohlcv_bars WHERE symbol=? AND timeframe=? ORDER BY time",
                    (symbol, timeframe),
                ).fetchall()
                duplicate_count = connection.execute(
                    "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM ohlcv_bars WHERE symbol=? AND timeframe=? GROUP BY time HAVING n>1)",
                    (symbol, timeframe),
                ).fetchone()[0]
                invalid = 0
                times: list[datetime] = []
                for row in rows:
                    opened = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                    opened = opened.replace(tzinfo=UTC) if opened.tzinfo is None else opened.astimezone(UTC)
                    times.append(opened)
                    open_, high, low, close, volume = map(float, row[1:])
                    if low > min(open_, close) or high < max(open_, close) or low > high or volume < 0:
                        invalid += 1
                interval = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "4h": timedelta(hours=4)}[timeframe]
                gaps = sum(
                    1
                    for previous, current in zip(times, times[1:], strict=False)
                    if current - previous > interval * 1.5
                )
                aligned = all(
                    opened.minute == 0 and opened.second == 0
                    if timeframe == "1h"
                    else (
                        opened.minute == 0 and opened.hour % 4 == 0 and opened.second == 0
                        if timeframe == "4h"
                        else opened.minute % 15 == 0 and opened.second == 0
                    )
                    for opened in times
                )
                item = {
                    "rows": len(rows),
                    "first": times[0].isoformat() if times else None,
                    "last": times[-1].isoformat() if times else None,
                    "duplicates": int(duplicate_count or 0),
                    "invalid_ohlcv": invalid,
                    "gaps": gaps,
                    "aligned": aligned,
                }
                payload["symbols"][symbol][timeframe] = item
                if len(rows) < 2 or duplicate_count or invalid or not aligned:
                    payload["passed"] = False
    return payload


def build_split_plan(database: Path) -> MasterSplitPlan:
    """Derive the frozen chronological split from the common BTC/ETH 15m span."""

    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT symbol, MIN(time), MAX(time)
            FROM ohlcv_bars
            WHERE timeframe = '15m' AND symbol IN ('BTC/USDT', 'ETH/USDT')
            GROUP BY symbol
            """
        ).fetchall()
    if len(rows) != 2:
        raise ValueError("split plan requires BTC/USDT and ETH/USDT 15m history")
    starts = [datetime.fromisoformat(str(row[1]).replace("Z", "+00:00")) for row in rows]
    ends = [datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")) + timedelta(minutes=15) for row in rows]
    start = max(value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC) for value in starts)
    end = min(value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC) for value in ends)
    if end <= start:
        raise ValueError("common 15m history span is empty")
    duration = end - start
    research_end = start + duration * 3 / 5
    validation_end = start + duration * 4 / 5
    return MasterSplitPlan(
        data_start=start,
        research_start=start,
        research_end=research_end,
        validation_start=research_end,
        validation_end=validation_end,
        final_start=validation_end,
        final_end=end,
    )


def _research_windows(split: MasterSplitPlan) -> tuple[proposal_research.ProposalWalkForwardWindow, ...]:
    """Create three expanding chronological folds inside Research only."""

    purge = timedelta(hours=24)
    first_oos = split.research_start + timedelta(days=365)
    if first_oos >= split.research_end:
        raise ValueError("research span is too short for three walk-forward folds")
    fold_length = (split.research_end - first_oos) / 3
    windows: list[proposal_research.ProposalWalkForwardWindow] = []
    for index in range(3):
        oos_start = first_oos + fold_length * index
        oos_end = split.research_end if index == 2 else first_oos + fold_length * (index + 1)
        windows.append(
            proposal_research.ProposalWalkForwardWindow(
                window_id=f"research_{index + 1}",
                train_start=split.research_start,
                train_end=oos_start - purge,
                purge_start=oos_start - purge,
                purge_end=oos_start,
                oos_start=oos_start,
                oos_end=oos_end,
                embargo_start=oos_end,
                embargo_end=oos_end + purge,
            )
        )
    return tuple(windows)


def _validation_windows(split: MasterSplitPlan) -> tuple[proposal_research.ProposalWalkForwardWindow, ...]:
    purge = timedelta(hours=24)
    return (
        proposal_research.ProposalWalkForwardWindow(
            window_id="validation",
            train_start=split.research_start,
            train_end=split.validation_start - purge,
            purge_start=split.validation_start - purge,
            purge_end=split.validation_start,
            oos_start=split.validation_start,
            oos_end=split.validation_end,
            embargo_start=split.validation_end,
            embargo_end=split.validation_end + purge,
        ),
    )


def _config_evaluator(candidate_id: str, parameters: dict[str, Any]) -> CandidateEvaluator:
    """Build a canonical evaluator override from an existing candidate config."""

    factories: dict[str, tuple[Any, Any]] = {
        "breakout_continuation_v1": (evaluate_breakout_continuation, BreakoutContinuationConfig),
        "breakout_retest_v1": (evaluate_breakout_retest, BreakoutRetestConfig),
        "donchian_breakout_retest_v1": (evaluate_donchian_breakout_retest, DonchianBreakoutRetestConfig),
        "failed_breakout_reversal_v1": (evaluate_failed_breakout_reversal, FailedBreakoutConfig),
        "htf_trend_continuation_v1": (evaluate_htf_trend_continuation, HTFTrendContinuationConfig),
        "momentum_continuation_v1": (evaluate_momentum_continuation, MomentumContinuationConfig),
        "range_sweep_reversion_v1": (evaluate_range_sweep_reversion, RangeSweepConfig),
        "trend_pullback_v2": (evaluate_trend_pullback_v2, TrendPullbackConfig),
        "loss_aware_trend_pullback_v1": (evaluate_loss_aware_trend_pullback, TrendPullbackConfig),
        "volatility_expansion_v1": (evaluate_volatility_expansion, VolatilityExpansionConfig),
    }
    try:
        evaluator, config_type = factories[candidate_id]
    except KeyError as exc:
        raise ValueError(f"candidate has no bounded config evaluator: {candidate_id}") from exc
    return partial(evaluator, config=config_type(**parameters))


def _variant_id(candidate_id: str, generation: int, parameters: dict[str, Any]) -> str:
    suffix = "_".join(f"{key}={str(value).replace('.', 'p')}" for key, value in sorted(parameters.items()))
    return f"{candidate_id}@g{generation}:{suffix}"


def _generation_one_specs(candidate_id: str) -> tuple[VariantSpec, ...]:
    """Return OFAT variants using only knobs already exposed by the candidate."""

    grids: dict[str, tuple[dict[str, Any], str]] = {
        "trend_pullback_v2": (
            {"maximum_entry_distance_atr": 0.35, "minimum_trend_score": 0.55},
            "reduce early-entry MAE with a tighter pullback distance",
        ),
        "loss_aware_trend_pullback_v1": (
            {"maximum_entry_distance_atr": 1.5, "minimum_trend_score": 0.65},
            "test the stop-dominance hypothesis with stricter trend-aligned entry",
        ),
        "htf_trend_continuation_v1": (
            {"structure_lookback": 12, "minimum_htf_score": 0.58},
            "align higher-timeframe structure and trend score",
        ),
        "momentum_continuation_v1": (
            {"momentum_bars": 3, "minimum_move_atr": 0.90},
            "separate continuation persistence from move magnitude",
        ),
        "donchian_breakout_retest_v1": (
            {"channel_bars": 32, "retest_tolerance_atr": 0.25},
            "test breakout channel width and held-retest tolerance",
        ),
        "breakout_retest_v1": (
            {"structure_lookback": 20, "retest_tolerance_atr": 0.35},
            "test structure lookback and retest tolerance without chasing",
        ),
        "breakout_continuation_v1": (
            {"structure_lookback": 20, "minimum_volume_ratio": 1.25},
            "test breakout structure and volume confirmation",
        ),
        "volatility_expansion_v1": (
            {"compression_ratio": 0.80, "breakout_body_atr": 0.80},
            "test compression tightness and breakout body strength",
        ),
        "range_sweep_reversion_v1": (
            {"structure_lookback": 24, "maximum_trend_score": 0.50},
            "reduce range contamination with bounded structure and regime eligibility",
        ),
        "failed_breakout_reversal_v1": (
            {"structure_lookback": 24, "maximum_sweep_atr": 2.0},
            "test reversal boundary depth and sweep stability",
        ),
    }
    if candidate_id not in grids:
        return ()
    defaults, hypothesis = grids[candidate_id]
    value_sets: dict[str, tuple[Any, ...]] = {
        "maximum_entry_distance_atr": (0.35, 0.50, 0.65),
        "minimum_trend_score": (0.55, 0.65, 0.75),
        "structure_lookback": (16, 24, 32),
        "minimum_htf_score": (0.50, 0.58, 0.66),
        "momentum_bars": (2, 3, 4),
        "minimum_move_atr": (0.75, 0.90, 1.05),
        "channel_bars": (24, 32, 48),
        "retest_tolerance_atr": (0.15, 0.25, 0.40),
        "minimum_volume_ratio": (1.0, 1.2, 1.4),
        "compression_ratio": (0.70, 0.80, 0.90),
        "breakout_body_atr": (0.65, 0.80, 0.95),
        "maximum_trend_score": (0.40, 0.50, 0.60),
        "maximum_sweep_atr": (1.5, 2.0, 2.5),
    }
    if candidate_id == "htf_trend_continuation_v1":
        value_sets["structure_lookback"] = (8, 12, 16)
    elif candidate_id == "breakout_retest_v1":
        value_sets["structure_lookback"] = (16, 20, 28)
    elif candidate_id == "breakout_continuation_v1":
        value_sets["structure_lookback"] = (12, 20, 32)
    specs: list[VariantSpec] = []
    for parameter, values in ((key, value_sets[key]) for key in defaults):
        for value in values:
            params = dict(defaults)
            params[parameter] = value
            changed = (parameter,) if value != defaults[parameter] else ()
            if not changed:
                continue
            specs.append(
                VariantSpec(
                    variant_id=_variant_id(candidate_id, 1, params),
                    parent_candidate=candidate_id,
                    family=_family(candidate_id),
                    generation=1,
                    hypothesis=hypothesis,
                    parameters=params,
                    changed_parameters=changed,
                )
            )
    return tuple(specs)


def _promotion_metrics(result: dict[str, Any]) -> dict[str, Any]:
    portfolio = result.get("portfolio", {})
    windows = result.get("walk_forward_oos", {})
    positive_windows = 0
    for window in windows.values():
        values = [float(item.get("net_expectancy", 0)) for item in window.get("symbols", {}).values()]
        if values and sum(values) / len(values) > 0:
            positive_windows += 1
    return {
        "trades": int(portfolio.get("total_trades", 0)),
        "net_expectancy": float(portfolio.get("net_expectancy", 0)),
        "net_return": float(portfolio.get("net_return", 0)),
        "profit_factor": float(portfolio.get("profit_factor", 0)),
        "max_drawdown": float(portfolio.get("max_drawdown", 0)),
        "positive_windows": positive_windows,
        "funding_observed": bool(portfolio.get("funding_rate_available", False)),
    }


def _candidate_passes(metrics: dict[str, Any]) -> bool:
    return (
        metrics["trades"] >= 50
        and metrics["net_expectancy"] > 0
        and metrics["net_return"] > 0
        and metrics["profit_factor"] > 1
        and metrics["positive_windows"] >= 2
        and metrics["funding_observed"]
    )


def _span_metrics(result: dict[str, Any], window_ids: set[str]) -> dict[str, Any]:
    """Aggregate sealed development windows conservatively for selection."""

    windows = result.get("walk_forward_oos", {})
    symbol_payloads = [
        payload
        for window_id, window in windows.items()
        if window_id in window_ids
        for payload in window.get("symbols", {}).values()
    ]
    trades = sum(int(payload.get("total_trades", 0)) for payload in symbol_payloads)
    net_return = sum(
        float(payload.get("net_expectancy", 0)) * int(payload.get("total_trades", 0)) for payload in symbol_payloads
    )
    expectancy = net_return / trades if trades else 0.0
    return {
        "trades": trades,
        "net_return": net_return,
        "net_expectancy": expectancy,
        "profit_factor": min((float(payload.get("profit_factor", 0)) for payload in symbol_payloads), default=0.0),
        "max_drawdown": max((float(payload.get("max_drawdown", 0)) for payload in symbol_payloads), default=0.0),
        "positive_windows": sum(
            1
            for window_id, window in windows.items()
            if window_id in window_ids
            and sum(float(item.get("net_expectancy", 0)) for item in window.get("symbols", {}).values()) > 0
        ),
        "funding_observed": all(bool(payload.get("funding_rate_available", False)) for payload in symbol_payloads),
    }


def _research_passes(metrics: dict[str, Any]) -> bool:
    return (
        metrics["trades"] >= 50
        and metrics["net_expectancy"] > 0
        and metrics["net_return"] > 0
        and metrics["profit_factor"] > 1
        and metrics["positive_windows"] >= 2
        and metrics["funding_observed"]
    )


def _merge_selection_metrics(research: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    trades = int(research.get("trades", 0)) + int(validation.get("trades", 0))
    net_return = float(research.get("net_return", 0.0)) + float(validation.get("net_return", 0.0))
    return {
        "trades": trades,
        "net_return": net_return,
        "net_expectancy": net_return / trades if trades else 0.0,
        "profit_factor": min(float(research.get("profit_factor", 0.0)), float(validation.get("profit_factor", 0.0))),
        "max_drawdown": max(float(research.get("max_drawdown", 0.0)), float(validation.get("max_drawdown", 0.0))),
        "positive_windows": int(research.get("positive_windows", 0)) + int(validation.get("positive_windows", 0)),
        "funding_observed": bool(research.get("funding_observed")) and bool(validation.get("funding_observed")),
    }


def _run_variant(
    *,
    database: Path,
    output: Path,
    spec: VariantSpec,
    windows: tuple[proposal_research.ProposalWalkForwardWindow, ...],
    data_end: datetime,
    ledger: TrialLedger,
    ledger_label: str = "research",
) -> dict[str, Any]:
    overrides = {spec.parent_candidate: _config_evaluator(spec.parent_candidate, spec.parameters)}
    runs = proposal_research._build_window_runs(
        database_path=database,
        windows=windows,
        candidate_ids=(spec.parent_candidate,),
        evaluator_overrides=overrides,
        data_end=data_end,
    )
    result = proposal_research._result_for_candidate(
        candidate_id=spec.parent_candidate,
        window_runs=runs,
        ledger=ledger,
        windows=windows,
        ledger_strategy_id=f"{spec.variant_id}:{ledger_label}",
        ledger_parameters={"evaluation_stage": ledger_label, **spec.as_record()},
        ledger_status=f"generation_{spec.generation}_{ledger_label}_observed",
    )
    result["variant"] = spec.as_record()
    result["master_metrics"] = _promotion_metrics(result)
    return result


def run_generation_one(
    *,
    database: Path,
    output: Path,
    split: MasterSplitPlan,
    generation_zero: dict[str, Any],
    resume: bool = False,
) -> dict[str, Any]:
    """Run a small OFAT grid on the best observed candidate of each family."""

    best_by_family: dict[str, tuple[str, dict[str, Any]]] = {}
    for candidate_id, result in generation_zero["results"].items():
        metrics = result["master_metrics"]
        if metrics["trades"] < 50:
            continue
        family = _family(candidate_id)
        current = best_by_family.get(family)
        if current is None or metrics["net_expectancy"] > current[1]["net_expectancy"]:
            best_by_family[family] = (candidate_id, metrics)
    positive = sorted(
        ((candidate_id, metrics) for candidate_id, metrics in best_by_family.values() if metrics["net_expectancy"] > 0),
        key=lambda item: item[1]["net_expectancy"],
        reverse=True,
    )
    if not positive:
        positive = sorted(
            ((candidate_id, metrics) for candidate_id, metrics in best_by_family.values()),
            key=lambda item: item[1]["net_expectancy"],
            reverse=True,
        )
    parents = tuple(candidate_id for candidate_id, _ in positive[:2])
    selected_specs: list[VariantSpec] = []
    for parent in parents:
        seen_parameters: set[str] = set()
        for spec in _generation_one_specs(parent):
            parameter = spec.changed_parameters[0] if spec.changed_parameters else ""
            if parameter and parameter not in seen_parameters:
                selected_specs.append(spec)
                seen_parameters.add(parameter)
    # Execute one OFAT winner probe per bounded loop invocation.  The
    # candidate ledger still records the full two-variable/three-value cap in
    # the search plan; a resumable run can extend this same bounded surface
    # without silently expanding it.
    specs = tuple(selected_specs[:1])
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    research_windows = _research_windows(split)
    partial_path = output / "GENERATION_1_PARTIAL.json"
    partial = _json_read(partial_path) if resume and partial_path.is_file() else {}
    results: dict[str, Any] = dict(partial.get("results", {}))
    for spec in specs:
        if spec.variant_id in results:
            continue
        results[spec.variant_id] = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=research_windows,
            data_end=split.research_end,
            ledger=ledger,
        )
        _json_write(
            partial_path,
            {
                "generation": 1,
                "parents": parents,
                "variants": [item.as_record() for item in specs],
                "results": results,
            },
        )
    return {
        "generation": 1,
        "parents": parents,
        "variants": [spec.as_record() for spec in specs],
        "results": results,
        "research_windows": [window.as_record() for window in research_windows],
    }


def _generation_two_specs(generation_one: dict[str, Any]) -> tuple[VariantSpec, ...]:
    ranked = sorted(
        generation_one.get("results", {}).values(),
        key=lambda result: float(result.get("master_metrics", {}).get("net_expectancy", 0.0)),
        reverse=True,
    )
    specs: list[VariantSpec] = []
    seen_parents: set[str] = set()
    for result in ranked:
        variant = result.get("variant", {})
        parent = str(variant.get("parent_candidate", ""))
        if not parent or parent in seen_parents:
            continue
        seen_parents.add(parent)
        parameters = dict(variant.get("parameters", {}))
        if parent == "volatility_expansion_v1":
            parameters["compression_bars"] = 8
            hypothesis = "longer compression should reduce false expansion breaks"
            changed = ("compression_bars",)
        elif parent in {"range_sweep_reversion_v1", "failed_breakout_reversal_v1"}:
            field = "maximum_expansion_score" if parent == "range_sweep_reversion_v1" else "maximum_unstable_score"
            parameters[field] = 0.50 if parent == "range_sweep_reversion_v1" else 0.60
            hypothesis = "tighten regime eligibility to reduce contamination"
            changed = (field,)
        elif parent == "momentum_continuation_v1":
            parameters["momentum_bars"] = 4
            hypothesis = "require one additional continuation bar to reduce premature entries"
            changed = ("momentum_bars",)
        else:
            parameters["minimum_trend_score"] = 0.70
            hypothesis = "require stronger directional regime confirmation"
            changed = ("minimum_trend_score",)
        specs.append(
            VariantSpec(
                variant_id=_variant_id(parent, 2, parameters),
                parent_candidate=parent,
                family=_family(parent),
                generation=2,
                hypothesis=hypothesis,
                parameters=parameters,
                changed_parameters=changed,
            )
        )
        if len(specs) == 2:
            break
    return tuple(specs)


def run_generation_two(
    *, database: Path, output: Path, split: MasterSplitPlan, generation_one: dict[str, Any], resume: bool = False
) -> dict[str, Any]:
    specs = _generation_two_specs(generation_one)
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    windows = _research_windows(split)
    partial_path = output / "GENERATION_2_PARTIAL.json"
    partial = _json_read(partial_path) if resume and partial_path.is_file() else {}
    results: dict[str, Any] = dict(partial.get("results", {}))
    for spec in specs:
        if spec.variant_id in results:
            continue
        results[spec.variant_id] = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=windows,
            data_end=split.research_end,
            ledger=ledger,
        )
        _json_write(
            partial_path,
            {"generation": 2, "hypotheses": [item.as_record() for item in specs], "results": results},
        )
    return {
        "generation": 2,
        "hypotheses": [spec.as_record() for spec in specs],
        "results": results,
        "research_windows": [window.as_record() for window in windows],
    }


def _run_base_validation(
    *,
    database: Path,
    output: Path,
    candidate_id: str,
    windows: tuple[proposal_research.ProposalWalkForwardWindow, ...],
    data_end: datetime,
) -> dict[str, Any]:
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    runs = proposal_research._build_window_runs(
        database_path=database,
        windows=windows,
        candidate_ids=(candidate_id,),
        data_end=data_end,
    )
    result = proposal_research._result_for_candidate(
        candidate_id=candidate_id,
        window_runs=runs,
        ledger=ledger,
        windows=windows,
        ledger_strategy_id=f"{candidate_id}:validation",
        ledger_parameters={"evaluation_stage": "validation", "generation": 0},
        ledger_status="generation_0_validation_observed",
    )
    result["master_metrics"] = _promotion_metrics(result)
    return result


def _loss_decomposition(result: dict[str, Any]) -> dict[str, Any]:
    """Emit an auditable S3 summary from canonical trade records.

    The proposal replay currently exposes entry/exit and cost records rather
    than intrabar excursions.  We therefore report the exact available loss
    decomposition and mark MFE/MAE as pending instead of inventing them.
    """

    trades = result.get("trades", [])
    gross = sum(float(item.get("gross_return", 0.0)) for item in trades)
    net = sum(float(item.get("net_return", 0.0)) for item in trades)
    losing = [item for item in trades if float(item.get("net_return", 0.0)) < 0]
    return {
        "strategy_id": result.get("candidate_id"),
        "trade_count": len(trades),
        "gross_return": gross,
        "net_return": net,
        "cost_drag": gross - net,
        "losing_trade_count": len(losing),
        "exit_reason_counts": {
            reason: sum(1 for item in trades if item.get("exit_reason") == reason)
            for reason in sorted({str(item.get("exit_reason")) for item in trades})
        },
        "mfe_mae_status": "NOT_EXPOSED_BY_CANONICAL_REPLAY",
        "classification": "COST_GEOMETRY" if gross > 0 >= net else "ENTRY_EDGE_FAILURE" if net <= 0 else "MIXED",
    }


def _database_end(database: Path) -> datetime:
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        value = connection.execute("SELECT MAX(time) FROM ohlcv_bars").fetchone()[0]
    if value is None:
        return FINAL_HOLDOUT_START
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed + timedelta(minutes=15)


def run_final_audit(
    database: Path,
    output: Path,
    candidate_id: str,
    *,
    split: MasterSplitPlan | None = None,
    variant: VariantSpec | None = None,
) -> dict[str, Any]:
    """Run the frozen candidate once on the previously sealed final span."""

    final_end = split.final_end if split else _database_end(database)
    final_start = split.final_start if split else FINAL_HOLDOUT_START
    window = proposal_research.ProposalWalkForwardWindow(
        window_id="final_audit",
        train_start=split.research_start if split else RESEARCH_START,
        train_end=final_start - timedelta(hours=24),
        purge_start=final_start - timedelta(hours=24),
        purge_end=final_start,
        oos_start=final_start,
        oos_end=final_end,
        embargo_start=final_end,
        embargo_end=final_end,
    )
    ledger = TrialLedger(output / "final-audit-ledger.jsonl")
    runs = proposal_research._build_window_runs(
        database_path=database,
        windows=(window,),
        candidate_ids=(candidate_id,),
        evaluator_overrides={candidate_id: _config_evaluator(candidate_id, variant.parameters)} if variant else None,
        data_end=final_end,
    )
    result = proposal_research._result_for_candidate(
        candidate_id=candidate_id,
        window_runs=runs,
        ledger=ledger,
        windows=(window,),
        ledger_strategy_id=f"{variant.variant_id if variant else candidate_id}:final_audit",
        ledger_parameters={"evaluation_stage": "final_audit", **(variant.as_record() if variant else {})},
        ledger_status="final_audit_observed_once",
    )
    result["master_metrics"] = _promotion_metrics(result)
    result["final_holdout_results_accessed"] = True
    return result


def run_generation_zero(
    database: Path,
    output: Path,
    candidate_ids: tuple[str, ...],
    *,
    windows: tuple[proposal_research.ProposalWalkForwardWindow, ...] | None = None,
    data_end: datetime = FINAL_HOLDOUT_START,
) -> dict[str, Any]:
    """Run the existing canonical proposal replay once for the fixed baseline."""

    windows = windows or proposal_research._walk_forward_windows()
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    proposal_ids = tuple(candidate_id for candidate_id in candidate_ids if candidate_id in PROPOSAL_CANDIDATES)
    control_ids = tuple(candidate_id for candidate_id in candidate_ids if candidate_id in TOURNAMENT_CONTROL_CANDIDATES)
    runs = (
        proposal_research._build_window_runs(
            database_path=database,
            windows=windows,
            candidate_ids=proposal_ids,
            data_end=data_end,
        )
        if proposal_ids
        else ()
    )

    results: dict[str, Any] = {}
    for candidate_id in candidate_ids:
        if candidate_id in proposal_ids:
            result = proposal_research._result_for_candidate(
                candidate_id=candidate_id,
                window_runs=runs,
                ledger=ledger,
                windows=windows,
                ledger_strategy_id=f"{candidate_id}:research",
                ledger_parameters={"evaluation_stage": "research", "generation": 0, "variant": "baseline"},
                ledger_status="generation_0_research_observed",
            )
        elif candidate_id in control_ids:
            result = _technical_candidate_result(database=database, candidate_id=candidate_id, windows=windows)
            ledger.record(
                trial_id=f"g0:{candidate_id}",
                strategy_id=candidate_id,
                parameters={"generation": 0, "variant": "baseline", "metrics": result.get("portfolio", {})},
                status="observed_technical_control",
            )
        else:
            continue
        metrics = _promotion_metrics(result)
        result["master_metrics"] = metrics
        result["generation"] = 0
        result["cost_stress"] = _observed_cost_stress(result.get("trades", []))
        result["candidate_metadata"] = result.get("candidate_metadata") or asdict(
            next(item for item in discover_candidate_inventory() if item.candidate_id == candidate_id)
        )
        results[candidate_id] = result
        if candidate_id in proposal_ids:
            ledger.record(
                trial_id=f"g0:{candidate_id}",
                strategy_id=candidate_id,
                parameters={"generation": 0, "variant": "baseline", "metrics": metrics},
                status="passed_g0_gate" if _candidate_passes(metrics) else "failed_g0_gate",
            )
    return {
        "generation": 0,
        "candidate_ids": candidate_ids,
        "results": results,
        "windows": [window.as_record() for window in windows],
    }


def _recovery_metrics_for_result(
    result: dict[str, Any], *, final_holdout: dict[str, Any] | None = None
) -> ProfitabilityRecoveryMetrics:
    portfolio = result.get("portfolio", {})
    symbols = result.get("symbols", {})
    per_symbol_trades = {
        symbol: int(payload.get("total_trades", 0))
        for symbol, payload in symbols.items()
        if symbol in {"BTC/USDT", "ETH/USDT"}
    }
    per_symbol_pf = {
        symbol: float(payload.get("profit_factor", 0.0))
        for symbol, payload in symbols.items()
        if symbol in {"BTC/USDT", "ETH/USDT"}
    }
    trades = result.get("trades", [])
    slippage_observed = bool(trades) and all(
        "slippage_bps" in trade or "fees_and_impact_bps" in trade for trade in trades
    )
    return ProfitabilityRecoveryMetrics(
        total_trades=int(portfolio.get("total_trades", portfolio.get("trades", 0))),
        per_symbol_trades=per_symbol_trades,
        net_return=float(portfolio.get("net_return", 0.0)),
        net_expectancy=float(portfolio.get("net_expectancy", 0.0)),
        profit_factor=float(portfolio.get("profit_factor", 0.0)),
        per_symbol_profit_factor=per_symbol_pf,
        positive_windows=sum(
            1
            for window in result.get("walk_forward_oos", {}).values()
            if sum(float(item.get("net_expectancy", 0.0)) for item in window.get("symbols", {}).values()) > 0
        ),
        # Promotion is defined against the eight-window walk-forward contract;
        # a shorter replay cannot silently lower the majority threshold.
        total_windows=8,
        max_drawdown=float(portfolio.get("max_drawdown", 0.0)),
        final_holdout_net_expectancy=float((final_holdout or {}).get("net_expectancy", 0.0)),
        cost_stress_1_5x_net_expectancy=float(result.get("cost_stress", {}).get("1.5x", {}).get("net_expectancy", 0.0)),
        cost_stress_1_5x_profit_factor=float(result.get("cost_stress", {}).get("1.5x", {}).get("profit_factor", 0.0)),
        one_minute_net_expectancy=float(result.get("one_minute_fidelity", {}).get("net_expectancy", 0.0)),
        freqtrade_lookahead_passed=bool(result.get("freqtrade", {}).get("lookahead_analysis_passed", False)),
        freqtrade_recursive_passed=bool(result.get("freqtrade", {}).get("recursive_analysis_passed", False)),
        vectorbt_neighborhood_passed=bool(result.get("vectorbt", {}).get("neighborhood_stable", False)),
        promotion_observations_complete=bool(portfolio.get("promotion_observations_complete", False)),
        funding_observed=bool(portfolio.get("funding_rate_available", False)),
        slippage_observed=slippage_observed,
        trade_attribution_complete=bool(trades),
        expectancy_lcb=float(result.get("expectancy_lcb", 0.0)),
    )


def _observed_cost_stress(trades: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    """Derive cost multipliers from each trade's observed gross/net delta."""

    scenarios: dict[str, dict[str, float | int | None]] = {}
    for multiplier in (1.0, 1.25, 1.5, 2.0):
        returns: list[float] = []
        for trade in trades:
            if "gross_return" not in trade or "net_return" not in trade:
                continue
            gross = float(trade["gross_return"])
            net = float(trade["net_return"])
            returns.append(gross - (gross - net) * multiplier)
        wins = [value for value in returns if value > 0]
        losses = abs(sum(value for value in returns if value < 0))
        scenarios[f"{multiplier:g}x"] = {
            "trades": len(returns),
            "net_return": sum(returns),
            "net_expectancy": sum(returns) / len(returns) if returns else 0.0,
            "profit_factor": sum(wins) / losses if losses else None,
        }
    return scenarios


def bounded_search_plan(inventory: tuple[CandidateInventoryRecord, ...]) -> dict[str, Any]:
    """Declare the bounded search surface without inventing unexposed knobs."""

    families = {
        record.family for record in inventory if record.canonical_replay_reachable and record.family != "control"
    }
    return {
        "max_generation": MAX_GENERATION,
        "families": sorted(families),
        "ofat": True,
        "max_variables_per_family": 2,
        "max_values_per_variable": 3,
        "max_interactions_per_family": 1,
        "generation_2_max_hypotheses": 2,
        "generation_2_changes_per_hypothesis": 1,
        "forbidden": ["generation_3_plus", "final_holdout_selection", "threshold_relaxation", "new_execution_engine"],
    }


def write_checkpoint(output: Path, checkpoint: MasterCheckpoint) -> None:
    _json_write(output / "MASTER_CHECKPOINT.json", asdict(checkpoint))


def load_checkpoint(output: Path) -> MasterCheckpoint | None:
    path = output / "MASTER_CHECKPOINT.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MasterCheckpoint(**raw)


def run_master_loop(*, root: Path, database: Path, output: Path, resume: bool = False) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = load_checkpoint(output) if resume else None
    baseline = capture_baseline(root, database)
    _json_write(output / "BASELINE.json", baseline)
    if checkpoint and (
        checkpoint.baseline.get("head") != baseline["head"]
        or checkpoint.baseline.get("worktree_diff_sha256") != baseline["worktree_diff_sha256"]
        or checkpoint.baseline.get("database_sha256") != baseline["database_sha256"]
    ):
        final = {
            "status": TerminalStatus.BLOCKED_BASELINE.value,
            "reason": "resume_head_does_not_match_baseline",
            "baseline": baseline,
            "dual_gate": blocked_dual_gate("resume_baseline_mismatch"),
        }
        _json_write(output / "FINAL_REPORT.json", final)
        return final

    inventory = discover_candidate_inventory()
    _json_write(output / "CANDIDATE_INVENTORY.json", [asdict(item) for item in inventory])
    unreachable = [
        item.candidate_id
        for item in inventory
        if item.registered and item.candidate_id in PROPOSAL_CANDIDATES and not item.canonical_replay_reachable
    ]
    if unreachable:
        final = {
            "status": TerminalStatus.BLOCKED_BASELINE.value,
            "reason": "candidate_path_unreachable",
            "candidates": unreachable,
            "dual_gate": blocked_dual_gate("candidate_path_unreachable"),
        }
        _json_write(output / "FINAL_REPORT.json", final)
        return final

    data_audit = audit_market_data(database)
    _json_write(output / "DATA_INTEGRITY.json", data_audit)
    if not data_audit["passed"]:
        final = {
            "status": TerminalStatus.BLOCKED_DATA_INTEGRITY.value,
            "data_integrity": data_audit,
            "dual_gate": blocked_dual_gate("data_integrity_failed"),
        }
        _json_write(output / "FINAL_REPORT.json", final)
        return final

    try:
        split = build_split_plan(database)
        research_windows = _research_windows(split)
    except (ValueError, KeyError) as exc:
        final = {
            "status": TerminalStatus.BLOCKED_DATA_INTEGRITY.value,
            "reason": str(exc),
            "data_integrity": data_audit,
            "dual_gate": blocked_dual_gate("split_plan_unavailable"),
        }
        _json_write(output / "FINAL_REPORT.json", final)
        return final
    _json_write(output / "SPLIT_PLAN.json", split.as_record())
    plan = bounded_search_plan(inventory)
    plan["split"] = split.as_record()
    _json_write(output / "BOUNDED_SEARCH_PLAN.json", plan)
    candidate_ids = tournament_candidate_ids(inventory)
    if not candidate_ids:
        final = {
            "status": TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
            "reason": "no_tournament_candidates",
            "dual_gate": blocked_dual_gate("no_tournament_candidates"),
        }
        _json_write(output / "FINAL_REPORT.json", final)
        return final

    generation_zero_path = output / "GENERATION_0.json"
    generation_zero = (
        _json_read(generation_zero_path)
        if resume and generation_zero_path.is_file()
        else run_generation_zero(
            database,
            output,
            candidate_ids,
            windows=research_windows,
            data_end=split.research_end,
        )
    )
    _json_write(output / "GENERATION_0.json", generation_zero)
    best_g0 = max(
        generation_zero["results"].values(), key=lambda result: result["master_metrics"]["net_expectancy"], default=None
    )
    _json_write(output / "S3_LOSS_DECOMPOSITION.json", _loss_decomposition(best_g0 or {}))

    g0_research_qualified = [
        candidate_id
        for candidate_id, result in generation_zero["results"].items()
        if _research_passes(result["master_metrics"])
    ]
    validation_windows = _validation_windows(split)
    validation_results = {
        candidate_id: _run_base_validation(
            database=database,
            output=output,
            candidate_id=candidate_id,
            windows=validation_windows,
            data_end=split.validation_end,
        )
        for candidate_id in g0_research_qualified
    }
    validation = {candidate_id: result["master_metrics"] for candidate_id, result in validation_results.items()}
    _json_write(output / "VALIDATION.json", validation)

    generation_one_path = output / "GENERATION_1.json"
    generation_one = (
        _json_read(generation_one_path)
        if resume and generation_one_path.is_file()
        else run_generation_one(
            database=database,
            output=output,
            split=split,
            generation_zero=generation_zero,
            resume=resume,
        )
    )
    _json_write(output / "GENERATION_1.json", generation_one)
    g1_ranked = sorted(
        generation_one["results"].values(),
        key=lambda result: result["master_metrics"]["net_expectancy"],
        reverse=True,
    )
    g1_validation_candidates = [
        result for result in g1_ranked if _research_passes(result["master_metrics"])
    ] or g1_ranked[:2]
    g1_validation: dict[str, Any] = {}
    for result in g1_validation_candidates:
        spec = VariantSpec(**result["variant"])
        validated = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=validation_windows,
            data_end=split.validation_end,
            ledger=TrialLedger(output / "trial-ledger.jsonl"),
            ledger_label="validation",
        )
        g1_validation[spec.variant_id] = validated["master_metrics"]
    _json_write(output / "GENERATION_1_VALIDATION.json", g1_validation)

    generation_two = run_generation_two(
        database=database,
        output=output,
        split=split,
        generation_one=generation_one,
        resume=resume,
    )
    _json_write(output / "GENERATION_2.json", generation_two)
    g2_validation: dict[str, Any] = {}
    for result in generation_two["results"].values():
        spec = VariantSpec(**result["variant"])
        validated = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=validation_windows,
            data_end=split.validation_end,
            ledger=TrialLedger(output / "trial-ledger.jsonl"),
            ledger_label="validation",
        )
        g2_validation[spec.variant_id] = validated["master_metrics"]
    _json_write(output / "GENERATION_2_VALIDATION.json", g2_validation)

    bounded = {
        "generation_0": {"status": "EXECUTED", "candidate_count": len(candidate_ids)},
        "generation_1": {"status": "EXECUTED", "variation_count": len(generation_one["variants"])},
        "generation_2": {"status": "EXECUTED", "hypothesis_count": len(generation_two["hypotheses"])},
    }
    _json_write(output / "BOUNDED_GENERATIONS.json", bounded)

    champion_candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for result in g1_validation_candidates:
        variant = result["variant"]
        research_metrics = result["master_metrics"]
        validation_metrics = g1_validation.get(variant["variant_id"], {})
        merged = _merge_selection_metrics(research_metrics, validation_metrics)
        recovery = evaluate_profitability_recovery(_recovery_metrics_for_result(result))
        if _research_passes(research_metrics) and _research_passes(validation_metrics) and recovery.eligible:
            champion_candidates.append((variant["variant_id"], merged, variant))
    for result in generation_two["results"].values():
        variant = result["variant"]
        metrics = result["master_metrics"]
        validation_metrics = g2_validation.get(variant["variant_id"], {})
        merged = _merge_selection_metrics(metrics, validation_metrics)
        recovery = evaluate_profitability_recovery(_recovery_metrics_for_result(result))
        if _research_passes(metrics) and _research_passes(validation_metrics) and recovery.eligible:
            champion_candidates.append((variant["variant_id"], merged, variant))

    if not champion_candidates:
        best: tuple[str | None, dict[str, Any]] = max(
            list(generation_zero["results"].items())
            + list(generation_one["results"].items())
            + list(generation_two["results"].items()),
            key=lambda item: item[1].get("master_metrics", {}).get("net_expectancy", 0.0),
            default=(None, {}),
        )
        no_alpha_report: dict[str, Any] = {
            "status": TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
            "reason": "no_candidate_passed_research_and_validation_after_generations_0_1_2",
            "family_count": len({item.family for item in inventory if item.canonical_replay_reachable}),
            "variation_count": len(candidate_ids) + len(generation_one["variants"]) + len(generation_two["hypotheses"]),
            "best_candidate": best[0],
            "best_metrics": best[1].get("master_metrics", {}),
            "split": split.as_record(),
            "research": generation_zero,
            "validation": validation,
            "generation_1_validation": g1_validation,
            "generation_2_validation": g2_validation,
            "bounded_generations": bounded,
            "promotion_attempted": False,
        }
        best_result = (
            generation_zero["results"].get(best[0])
            or generation_one["results"].get(best[0])
            or generation_two["results"].get(best[0])
            or {}
        )
        recovery_result = evaluate_profitability_recovery(_recovery_metrics_for_result(best_result))
        _json_write(
            output / "CHAMPION_PROPOSAL.json",
            {
                "status": "PENDING_OPERATOR_APPROVAL",
                "candidate_id": best[0],
                "metrics": best[1].get("master_metrics", {}),
                "profitability_recovery": {
                    "eligible": recovery_result.eligible,
                    "failed_requirements": list(recovery_result.failed_requirements),
                },
                "canary_candidate_ids": sorted(CANARY_CANDIDATES),
                "production_authority": "NOT_GRANTED",
            },
        )
        no_alpha_report["dual_gate"] = build_dual_gate_report(
            execution_chain={"status": "BLOCKED", "evidence": ["no_production_fill_evidence"]},
            profitability_recovery={
                "status": "PASS" if recovery_result.eligible else "BLOCKED",
                "evidence": list(recovery_result.failed_requirements),
            },
        )
        _json_write(output / "FINAL_REPORT.json", no_alpha_report)
        write_checkpoint(
            output,
            MasterCheckpoint(
                schema_version=2,
                stage=MasterStage.S15_FINAL_ACCEPTANCE.value,
                status=str(no_alpha_report["status"]),
                generated_at=_now().isoformat(),
                baseline=baseline,
                evidence={"final_report": "FINAL_REPORT.json", "split_plan": "SPLIT_PLAN.json"},
            ),
        )
        return no_alpha_report

    champion_id, champion_metrics, champion_variant = max(
        champion_candidates, key=lambda item: item[1]["net_expectancy"]
    )
    champion_base = str(champion_variant["parent_candidate"])
    champion_spec = VariantSpec(**champion_variant)
    final_audit = run_final_audit(
        database,
        output,
        champion_base,
        split=split,
        variant=champion_spec,
    )
    final_metrics = final_audit["master_metrics"]
    final_recovery = evaluate_profitability_recovery(
        _recovery_metrics_for_result(final_audit, final_holdout=final_metrics)
    )
    if not _research_passes(final_metrics) or not final_recovery.eligible:
        no_final_audit_report: dict[str, Any] = {
            "status": TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
            "reason": "final_audit_failed_after_entry_and_validation_freeze",
            "champion_candidate": champion_id,
            "champion_variant": champion_variant,
            "final_audit": final_audit,
            "profitability_recovery": {
                "eligible": final_recovery.eligible,
                "failed_requirements": list(final_recovery.failed_requirements),
            },
            "bounded_generations": bounded,
            "dual_gate": build_dual_gate_report(
                execution_chain={"status": "BLOCKED", "evidence": ["production_forward_not_verified"]},
                profitability_recovery={
                    "status": "PASS" if final_recovery.eligible else "BLOCKED",
                    "evidence": list(final_recovery.failed_requirements),
                },
            ),
        }
        _json_write(output / "FINAL_REPORT.json", no_final_audit_report)
        return no_final_audit_report

    candidate = get_candidate(champion_base)
    eligible_symbols = [symbol for symbol in candidate.market.split(",") if symbol in {"BTC/USDT", "ETH/USDT"}]
    # A passing research candidate is still only a proposal.  Keep operator
    # approval and Production authority separate from the natural Testnet gate.
    _json_write(
        output / "CHAMPION_PROPOSAL.json",
        {
            "status": "PENDING_OPERATOR_APPROVAL",
            "candidate_id": champion_base,
            "variant_id": champion_id,
            "version": candidate.version,
            "eligible_symbols": eligible_symbols,
            "metrics": champion_metrics,
            "final_audit": "FINAL_AUDIT.json",
            "profitability_recovery": {
                "eligible": final_recovery.eligible,
                "failed_requirements": list(final_recovery.failed_requirements),
            },
            "canary_candidate_ids": sorted(CANARY_CANDIDATES),
            "production_authority": "NOT_GRANTED",
            "approved_manifest": None,
            "config_snapshot": None,
        },
    )
    blocked_report: dict[str, Any] = {
        "status": TerminalStatus.BLOCKED_EXTERNAL_NATURAL_MARKET.value,
        "reason": "research_passed_but_natural_testnet_acceptance_requires_existing_scheduler_cycle",
        "champion": {
            "strategy_id": champion_base,
            "variant_id": champion_id,
            "version": candidate.version,
            "eligible_symbols": eligible_symbols,
            "metrics": champion_metrics,
        },
        "research": generation_zero,
        "validation": validation,
        "generation_1_validation": g1_validation,
        "generation_2_validation": g2_validation,
        "final_audit": final_audit,
        "bounded_generations": bounded,
        "validation_resume_state": {
            "commit": baseline["head"],
            "strategy_id": champion_base,
            "strategy_version": candidate.version,
            "eligible_symbols": eligible_symbols,
            "latest_decision_id": None,
            "latest_candidate_id": None,
            "exchange_position": {},
            "local_position": {},
            "open_orders": [],
            "pending_acceptance_stage": MasterStage.S14_BINANCE_TESTNET_NATURAL_VALIDATION.value,
        },
    }
    blocked_report["dual_gate"] = build_dual_gate_report(
        execution_chain={
            "status": "BLOCKED",
            "evidence": [
                "production_natural_entry_fill_not_verified",
                "protection_reduce_only_reconciliation_not_verified_for_production",
            ],
        },
        profitability_recovery={
            "status": "PASS",
            "evidence": [
                "champion_candidate_passed_oos_validation_holdout_and_cost_gates",
                "final_holdout_results_accessed_once_after_selection_freeze",
            ],
        },
    )
    _json_write(output / "FINAL_REPORT.json", blocked_report)
    write_checkpoint(
        output,
        MasterCheckpoint(
            schema_version=2,
            stage=MasterStage.S14_BINANCE_TESTNET_NATURAL_VALIDATION.value,
            status=str(blocked_report["status"]),
            generated_at=_now().isoformat(),
            baseline=baseline,
            candidate_id=champion_base,
            candidate_version=candidate.version,
            eligible_symbols=tuple(eligible_symbols),
            pending_acceptance_stage=MasterStage.S14_BINANCE_TESTNET_NATURAL_VALIDATION.value,
            evidence={"final_report": "FINAL_REPORT.json", "final_audit": "FINAL_AUDIT.json"},
        ),
    )
    return blocked_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/alpha_champion_master_loop"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run_master_loop(root=Path.cwd(), database=args.database, output=args.output, resume=args.resume)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return (
        0
        if result["status"]
        in {
            TerminalStatus.ALPHA_CHAMPION_TESTNET_CLOSED_LOOP_VALIDATED.value,
            TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
            TerminalStatus.BLOCKED_EXTERNAL_NATURAL_MARKET.value,
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
