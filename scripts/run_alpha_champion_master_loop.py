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
from decimal import Decimal
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
    stationary_cluster_bootstrap_lcb,
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
    FINALIST_FROZEN_PENDING_EXPENSIVE_VALIDATION = "FINALIST_FROZEN_PENDING_EXPENSIVE_VALIDATION"
    BOUNDED_SEARCH_INCOMPLETE = "BOUNDED_SEARCH_INCOMPLETE"
    NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH = "NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH"


class TournamentDisposition(StrEnum):
    """Finite audit states for registry candidates in this replay."""

    REACHABLE_TOURNAMENT_CANDIDATE = "REACHABLE_TOURNAMENT_CANDIDATE"
    SUPERSEDED = "SUPERSEDED"
    UNIMPLEMENTED_DESIGN_STUB = "UNIMPLEMENTED_DESIGN_STUB"
    UNCLASSIFIED_UNREACHABLE = "UNCLASSIFIED_UNREACHABLE"
    NOT_REGISTERED = "NOT_REGISTERED"


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
EXPLICIT_TOURNAMENT_EXCLUSIONS: dict[str, tuple[TournamentDisposition, str | None, str]] = {
    "trend_pullback_v1": (
        TournamentDisposition.SUPERSEDED,
        "trend_pullback_v2",
        "historical research config superseded by the canonical trend_pullback_v2 proposal evaluator",
    ),
    "aggressive_multi_regime_v1": (
        TournamentDisposition.UNIMPLEMENTED_DESIGN_STUB,
        None,
        "registry design stub has no canonical CandidateEvaluator or proposal-pipeline entry",
    ),
}
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
    tournament_disposition: str = TournamentDisposition.UNCLASSIFIED_UNREACHABLE.value
    eligible_for_tournament: bool = False
    superseded_by: str | None = None
    exclusion_reason: str | None = None


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
        if reachable:
            disposition = TournamentDisposition.REACHABLE_TOURNAMENT_CANDIDATE
            superseded_by = None
            exclusion_reason = None
            eligible_for_tournament = candidate_id not in CANARY_CANDIDATES
        elif candidate_id in EXPLICIT_TOURNAMENT_EXCLUSIONS:
            disposition, superseded_by, exclusion_reason = EXPLICIT_TOURNAMENT_EXCLUSIONS[candidate_id]
            eligible_for_tournament = False
        elif registered:
            disposition = TournamentDisposition.UNCLASSIFIED_UNREACHABLE
            superseded_by = None
            exclusion_reason = None
            eligible_for_tournament = False
        else:
            disposition = TournamentDisposition.NOT_REGISTERED
            superseded_by = None
            exclusion_reason = None
            eligible_for_tournament = False
        reason = None if reachable else (exclusion_reason or "missing registry entry or evaluator path")
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
                tournament_disposition=disposition.value,
                eligible_for_tournament=eligible_for_tournament,
                superseded_by=superseded_by,
                exclusion_reason=exclusion_reason,
            )
        )
    return tuple(records)


def _has_valid_tournament_disposition(item: CandidateInventoryRecord) -> bool:
    if not item.registered:
        return True
    if item.canonical_replay_reachable:
        return (
            item.tournament_disposition == TournamentDisposition.REACHABLE_TOURNAMENT_CANDIDATE.value
            and item.eligible_for_tournament
        )
    if item.eligible_for_tournament:
        return False
    if item.tournament_disposition == TournamentDisposition.SUPERSEDED.value:
        return bool(item.superseded_by and item.exclusion_reason)
    if item.tournament_disposition == TournamentDisposition.UNIMPLEMENTED_DESIGN_STUB.value:
        return bool(item.exclusion_reason)
    return False


def unclassified_unreachable_candidates(
    inventory: tuple[CandidateInventoryRecord, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.candidate_id
            for item in inventory
            if item.registered and not item.canonical_replay_reachable and not _has_valid_tournament_disposition(item)
        )
    )


def tournament_candidate_ids(inventory: tuple[CandidateInventoryRecord, ...]) -> tuple[str, ...]:
    """Return every reachable registry candidate eligible for research competition.

    Canary controls are deliberately excluded: they remain execution-health
    probes and cannot enter Champion ranking or promotion evidence.
    """

    return tuple(
        sorted(
            item.candidate_id
            for item in inventory
            if item.registered and item.canonical_replay_reachable and item.eligible_for_tournament
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


def _load_funding_points(database: Path, *, end_at: datetime) -> dict[str, tuple[tuple[datetime, Decimal], ...]]:
    """Load point-in-time funding observations for the technical replay lane."""

    points: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "market_extras" not in tables:
            return {}
        rows = connection.execute(
            """
            SELECT time, symbol, funding_rate
            FROM market_extras
            WHERE symbol IN ('BTC/USDT', 'ETH/USDT')
              AND time <= ?
              AND funding_rate IS NOT NULL
            ORDER BY symbol, time
            """,
            (end_at.replace(tzinfo=None).isoformat(sep=" "),),
        ).fetchall()
    for raw_time, symbol, raw_rate in rows:
        parsed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        timestamp = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        points[str(symbol)].append((timestamp, Decimal(str(raw_rate))))
    return {symbol: tuple(values) for symbol, values in points.items()}


def _apply_funding_cost(
    trade: dict[str, Any], funding_points: tuple[tuple[datetime, Decimal], ...]
) -> tuple[dict[str, Any], bool]:
    """Apply signed point-in-time funding without changing the replay geometry."""

    opened_at = datetime.fromisoformat(str(trade["opened_at"]).replace("Z", "+00:00"))
    closed_at = datetime.fromisoformat(str(trade["closed_at"]).replace("Z", "+00:00"))
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=UTC)
    if not funding_points or not any(observed_at <= opened_at for observed_at, _ in funding_points):
        return {**trade, "funding_cost": None, "funding_evidence": "MISSING"}, False
    side_sign = Decimal("1") if str(trade.get("side")) == "long" else Decimal("-1")
    filled_fraction = Decimal(str(trade.get("quantity_fraction") or "1"))
    funding_cost = sum(
        (
            rate * side_sign * filled_fraction
            for observed_at, rate in funding_points
            if opened_at < observed_at <= closed_at
        ),
        Decimal("0"),
    )
    enriched = dict(trade)
    enriched["net_return_before_funding"] = float(trade.get("net_return", 0.0))
    enriched["funding_cost"] = float(funding_cost)
    enriched["net_return"] = float(Decimal(str(trade.get("net_return", 0.0))) - funding_cost)
    enriched["funding_evidence"] = "POINT_IN_TIME_OBSERVED"
    return enriched, True


def _replay_trade_metrics(trades: list[dict[str, Any]], *, raw_metrics: Any | None = None) -> dict[str, Any]:
    """Recompute post-funding metrics while retaining raw replay diagnostics."""

    returns = [float(item.get("net_return", 0.0)) for item in trades]
    wins = [value for value in returns if value > 0]
    losses = abs(sum(value for value in returns if value < 0))
    equity = 1.0
    peak = equity
    max_drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    payload = {
        "total_trades": len(trades),
        "net_return": sum(returns),
        "net_expectancy": sum(returns) / len(returns) if returns else 0.0,
        "profit_factor": sum(wins) / losses if losses else (float("inf") if wins else 0.0),
        "max_drawdown": max_drawdown,
        "funding_rate_available": bool(trades)
        and all(item.get("funding_evidence") == "POINT_IN_TIME_OBSERVED" for item in trades),
        "promotion_observations_complete": False,
        "slippage_observed": False,
        "trade_attribution_complete": bool(trades),
        "trades": trades,
    }
    if raw_metrics is not None:
        payload.update(
            {
                "signal_count": raw_metrics.signal_count,
                "win_rate": raw_metrics.win_rate,
                "average_win": raw_metrics.average_win,
                "average_loss": raw_metrics.average_loss,
                "average_r": raw_metrics.average_r,
                "average_hold_hours": raw_metrics.average_hold_hours,
                "gross_return": raw_metrics.gross_return,
                "total_fee_bps": raw_metrics.total_fee_bps,
                "total_slippage_bps": raw_metrics.total_slippage_bps,
                "cost_share_of_gross_profit": raw_metrics.cost_share_of_gross_profit,
                "data_issues": raw_metrics.data_issues,
            }
        )
    return payload


def _technical_candidate_result(
    *,
    database: Path,
    candidate_id: str,
    windows: tuple[proposal_research.ProposalWalkForwardWindow, ...],
    market_data: MarketData | None = None,
    funding_points: dict[str, tuple[tuple[datetime, Decimal], ...]] | None = None,
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
    replay_end = max(window.oos_end for window in windows)
    data = market_data if market_data is not None else _load_technical_market_data(database, end_at=replay_end)
    observed_funding = (
        funding_points if funding_points is not None else _load_funding_points(database, end_at=replay_end)
    )
    window_payload: dict[str, Any] = {}
    all_trades: list[dict[str, Any]] = []
    symbol_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for window in windows:
        symbols_payload: dict[str, Any] = {}
        for symbol in ("BTC/USDT", "ETH/USDT"):
            metrics = service.replay(
                strategy=strategy,
                market_data={symbol: data.get(symbol, {})},
                start_at=window.oos_start,
                end_at=window.oos_end,
            )
            trades = []
            for trade in metrics.trades:
                enriched, _ = _apply_funding_cost(trade.as_dict(), observed_funding.get(symbol, ()))
                trades.append(enriched)
            all_trades.extend(trades)
            symbol_trades[symbol].extend(trades)
            symbols_payload[symbol] = _replay_trade_metrics(trades, raw_metrics=metrics)
        window_payload[window.window_id] = {"window": window.as_record(), "symbols": symbols_payload}
    symbol_payload = {symbol: _replay_trade_metrics(trades) for symbol, trades in symbol_trades.items()}
    portfolio = _replay_trade_metrics(all_trades)
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
        "symbols": symbol_payload,
        "portfolio": portfolio,
        "trades": all_trades,
        "walk_forward_oos": window_payload,
        "funding_treatment": "point_in_time_market_extras",
        "cost_evidence": {
            "funding": "POINT_IN_TIME_OBSERVED" if portfolio["funding_rate_available"] else "MISSING",
            "commission": "CONFIGURED_BINANCE_TAKER_BPS",
            "slippage": "CONFIGURED_REPLAY_BPS",
            "slippage_observed": False,
            "promotion_observations_complete": False,
        },
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
    """Cheap screening gate; expensive promotion evidence is intentionally absent."""

    return (
        metrics["trades"] >= 50
        and metrics["net_expectancy"] > 0
        and metrics["net_return"] > 0
        and metrics["profit_factor"] > 1
        and metrics["positive_windows"] >= 2
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
    """Validation gate after screening; still before holdout/expensive checks."""

    return _candidate_passes(metrics) and metrics["funding_observed"]


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
        by_parameter: dict[str, list[VariantSpec]] = {}
        for spec in _generation_one_specs(parent):
            parameter = spec.changed_parameters[0] if spec.changed_parameters else ""
            if parameter:
                by_parameter.setdefault(parameter, []).append(spec)
        for parameter in list(by_parameter)[:2]:
            selected_specs.extend(by_parameter[parameter][:2])
    # Execute the complete pre-declared OFAT surface.  Each exposed variable
    # contributes its bounded non-default alternatives; no ad-hoc grid is
    # created here and the parent/family cap above remains the sole selector.
    specs = tuple(selected_specs)
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    research_windows = _research_windows(split)
    partial_path = output / "GENERATION_1_PARTIAL.json"
    partial = _json_read(partial_path) if resume and partial_path.is_file() else {}
    planned_ids = {spec.variant_id for spec in specs}
    results: dict[str, Any] = {
        variant_id: result for variant_id, result in partial.get("results", {}).items() if variant_id in planned_ids
    }
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
    planned_id_list = [spec.variant_id for spec in specs]
    completed_ids = sorted(set(results).intersection(planned_id_list))
    return {
        "generation": 1,
        "parents": parents,
        "variants": [spec.as_record() for spec in specs],
        "results": results,
        "research_windows": [window.as_record() for window in research_windows],
        "planned_variant_ids": planned_id_list,
        "completed_variant_ids": completed_ids,
        "complete": set(completed_ids) == set(planned_ids),
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
    planned_ids = {spec.variant_id for spec in specs}
    results: dict[str, Any] = {
        variant_id: result for variant_id, result in partial.get("results", {}).items() if variant_id in planned_ids
    }
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
    planned_id_list = [spec.variant_id for spec in specs]
    completed_ids = sorted(set(results).intersection(planned_id_list))
    return {
        "generation": 2,
        "hypotheses": [spec.as_record() for spec in specs],
        "results": results,
        "research_windows": [window.as_record() for window in windows],
        "planned_hypothesis_ids": planned_id_list,
        "completed_hypothesis_ids": completed_ids,
        "complete": set(completed_ids) == set(planned_ids),
    }


def _run_base_validation(
    *,
    database: Path,
    output: Path,
    candidate_id: str,
    windows: tuple[proposal_research.ProposalWalkForwardWindow, ...],
    data_end: datetime,
    market_data: MarketData | None = None,
    funding_points: dict[str, tuple[tuple[datetime, Decimal], ...]] | None = None,
) -> dict[str, Any]:
    if candidate_id in TOURNAMENT_CONTROL_CANDIDATES:
        result = _technical_candidate_result(
            database=database,
            candidate_id=candidate_id,
            windows=windows,
            market_data=market_data,
            funding_points=funding_points,
        )
        result["evaluation_stage"] = "validation"
        result["master_metrics"] = _promotion_metrics(result)
        return result
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
    if candidate_id in TOURNAMENT_CONTROL_CANDIDATES:
        result = _technical_candidate_result(
            database=database,
            candidate_id=candidate_id,
            windows=(window,),
        )
        result["evaluation_stage"] = "final_audit"
        result["final_holdout_results_accessed"] = True
        result["master_metrics"] = _promotion_metrics(result)
        return result

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
    resume: bool = False,
) -> dict[str, Any]:
    """Run the existing canonical proposal replay once for the fixed baseline."""

    windows = windows or proposal_research._walk_forward_windows()
    proposal_ids = tuple(candidate_id for candidate_id in candidate_ids if candidate_id in PROPOSAL_CANDIDATES)
    control_ids = tuple(candidate_id for candidate_id in candidate_ids if candidate_id in TOURNAMENT_CONTROL_CANDIDATES)
    partial_path = output / "GENERATION_0_PARTIAL.json"
    partial = _json_read(partial_path) if partial_path.is_file() else {}
    results: dict[str, Any] = dict(partial.get("results", {}))
    pending_proposal_ids = tuple(candidate_id for candidate_id in proposal_ids if candidate_id not in results)
    pending_control_ids = tuple(candidate_id for candidate_id in control_ids if candidate_id not in results)
    ledger = TrialLedger(output / "trial-ledger.jsonl")
    runs = (
        proposal_research._build_window_runs(
            database_path=database,
            windows=windows,
            candidate_ids=pending_proposal_ids,
            data_end=data_end,
        )
        if pending_proposal_ids
        else ()
    )
    inventory_by_id = {item.candidate_id: item for item in discover_candidate_inventory()}
    technical_data = (
        _load_technical_market_data(database, end_at=max(window.oos_end for window in windows))
        if pending_control_ids
        else {}
    )
    funding_points = (
        _load_funding_points(database, end_at=max(window.oos_end for window in windows)) if pending_control_ids else {}
    )
    for candidate_id in candidate_ids:
        if candidate_id in results:
            continue
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
            result = _technical_candidate_result(
                database=database,
                candidate_id=candidate_id,
                windows=windows,
                market_data=technical_data,
                funding_points=funding_points,
            )
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
        result["candidate_metadata"] = result.get("candidate_metadata") or asdict(inventory_by_id[candidate_id])
        results[candidate_id] = result
        if candidate_id in proposal_ids:
            ledger.record(
                trial_id=f"g0:{candidate_id}",
                strategy_id=candidate_id,
                parameters={"generation": 0, "variant": "baseline", "metrics": metrics},
                status="passed_g0_gate" if _candidate_passes(metrics) else "failed_g0_gate",
            )
        _json_write(
            partial_path,
            {
                "generation": 0,
                "candidate_ids": candidate_ids,
                "completed_candidate_ids": sorted(results),
                "windows": [window.as_record() for window in windows],
                "results": results,
            },
        )
    return {
        "generation": 0,
        "candidate_ids": candidate_ids,
        "results": results,
        "windows": [window.as_record() for window in windows],
    }


def _recovery_metrics_for_result(
    result: dict[str, Any],
    *,
    final_holdout: dict[str, Any] | None = None,
    expensive_evidence: dict[str, Any] | None = None,
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
    cost_evidence = result.get("cost_evidence", {})
    if "slippage_observed" in cost_evidence:
        slippage_observed = bool(cost_evidence["slippage_observed"])
    else:
        slippage_observed = bool(portfolio.get("slippage_observed", False))
    evidence = expensive_evidence or result.get("expensive_validation", {})
    one_minute = evidence.get("one_minute_fidelity", {})
    freqtrade = evidence.get("freqtrade", {})
    vectorbt = evidence.get("vectorbt", {})
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
        cost_stress_1_5x_profit_factor=float(result.get("cost_stress", {}).get("1.5x", {}).get("profit_factor") or 0.0),
        one_minute_net_expectancy=float(one_minute.get("net_expectancy", 0.0)),
        freqtrade_lookahead_passed=bool(freqtrade.get("lookahead_analysis_passed", False)),
        freqtrade_recursive_passed=bool(freqtrade.get("recursive_analysis_passed", False)),
        vectorbt_neighborhood_passed=bool(vectorbt.get("neighborhood_stable", False)),
        promotion_observations_complete=bool(portfolio.get("promotion_observations_complete", False)),
        funding_observed=bool(portfolio.get("funding_rate_available", False)),
        slippage_observed=slippage_observed,
        trade_attribution_complete=bool(trades),
        expectancy_lcb=float(evidence.get("expectancy_lcb", result.get("expectancy_lcb", 0.0))),
    )


def _merge_research_validation_result(
    research_result: dict[str, Any], validation_result: dict[str, Any] | None
) -> dict[str, Any]:
    """Freeze research and validation evidence into one post-cost selection record."""

    if not validation_result:
        return dict(research_result)
    merged = dict(research_result)
    research_trades = list(research_result.get("trades", []))
    validation_trades = list(validation_result.get("trades", []))
    merged_trades = research_trades + validation_trades
    merged["trades"] = merged_trades
    merged["portfolio"] = _replay_trade_metrics(merged_trades)
    research_portfolio = research_result.get("portfolio", {})
    validation_portfolio = validation_result.get("portfolio", {})
    merged["portfolio"]["funding_rate_available"] = bool(
        research_portfolio.get("funding_rate_available", False)
        and validation_portfolio.get("funding_rate_available", False)
    )
    merged["portfolio"]["promotion_observations_complete"] = bool(
        research_portfolio.get("promotion_observations_complete", False)
        and validation_portfolio.get("promotion_observations_complete", False)
    )
    merged["portfolio"]["slippage_observed"] = bool(
        research_portfolio.get("slippage_observed", False) and validation_portfolio.get("slippage_observed", False)
    )
    merged["symbols"] = {}
    for symbol in ("BTC/USDT", "ETH/USDT"):
        symbol_trades = [trade for trade in merged_trades if trade.get("symbol") == symbol]
        merged["symbols"][symbol] = _replay_trade_metrics(symbol_trades)
    merged["walk_forward_oos"] = {
        **research_result.get("walk_forward_oos", {}),
        **validation_result.get("walk_forward_oos", {}),
    }
    merged["cost_stress"] = _observed_cost_stress(merged_trades)
    merged["validation_result"] = validation_result
    merged["master_metrics"] = _promotion_metrics(merged)
    return merged


def _run_expensive_validations(*, candidate_id: str, result: dict[str, Any], output: Path) -> dict[str, Any]:
    """Run or explicitly account for finalist-only expensive evidence.

    Candidate-specific 1m/Freqtrade/vectorbt adapters require sealed research
    inputs that are not present in every registry entry.  We persist an
    explicit NOT_RUN/UNAVAILABLE record instead of treating absent fields as a
    pass.  Bootstrap LCB is computed from the frozen finalist trades when
    enough observations exist.
    """

    returns = tuple(Decimal(str(item.get("net_return", 0.0))) for item in result.get("trades", []))
    if returns:
        bootstrap = stationary_cluster_bootstrap_lcb((returns,), n_resamples=200, seed=17)
        expectancy_lcb = bootstrap.expectancy_lcb
        bootstrap_evidence: dict[str, Any] = {
            "status": "COMPLETED",
            "method": bootstrap.method,
            "sample_size": bootstrap.sample_size,
            "expectancy_lcb": expectancy_lcb,
        }
    else:
        expectancy_lcb = 0.0
        bootstrap_evidence = {"status": "UNAVAILABLE", "reason": "no_finalist_trades"}
    evidence = {
        "candidate_id": candidate_id,
        "one_minute_fidelity": {
            "status": "NOT_RUN",
            "reason": "candidate_specific_1m_fidelity_adapter_not_configured",
            "net_expectancy": 0.0,
        },
        "freqtrade": {
            "status": "NOT_RUN",
            "reason": "candidate_specific_freqtrade_spec_not_configured",
            "lookahead_analysis_passed": False,
            "recursive_analysis_passed": False,
        },
        "vectorbt": {
            "status": "NOT_RUN",
            "reason": "candidate_specific_vectorbt_spec_not_configured",
            "neighborhood_stable": False,
        },
        "bootstrap": bootstrap_evidence,
        "expectancy_lcb": expectancy_lcb,
    }
    safe_id = candidate_id.replace("/", "_").replace(":", "_")
    _json_write(output / f"EXPENSIVE_VALIDATION_{safe_id}.json", evidence)
    return evidence


def _expensive_validation_pending(evidence: dict[str, Any]) -> bool:
    return any(
        str(evidence.get(engine, {}).get("status", "NOT_RUN")).upper() in {"NOT_RUN", "UNAVAILABLE"}
        for engine in ("one_minute_fidelity", "freqtrade", "vectorbt")
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
        key = f"{multiplier:.1f}x" if multiplier.is_integer() else f"{multiplier:.2f}".rstrip("0").rstrip(".") + "x"
        scenarios[key] = {
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


def _stage_leaderboard_metrics(result: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize one research/validation result for the combined evidence table."""

    result = result or {}
    metrics = dict(result.get("master_metrics", {}))
    symbols = result.get("symbols", {})
    metrics.setdefault("funding_observed", bool(result.get("portfolio", {}).get("funding_rate_available", False)))
    cost_stress = result.get("cost_stress", {})
    stress = cost_stress.get("1.5x", {})
    return {
        "btc_trades": int(symbols.get("BTC/USDT", {}).get("total_trades", 0)),
        "eth_trades": int(symbols.get("ETH/USDT", {}).get("total_trades", 0)),
        "trades": int(metrics.get("trades", result.get("portfolio", {}).get("total_trades", 0))),
        "profit_factor": float(metrics.get("profit_factor", result.get("portfolio", {}).get("profit_factor", 0.0))),
        "net_expectancy": float(metrics.get("net_expectancy", result.get("portfolio", {}).get("net_expectancy", 0.0))),
        "net_return": float(metrics.get("net_return", result.get("portfolio", {}).get("net_return", 0.0))),
        "max_drawdown": float(metrics.get("max_drawdown", result.get("portfolio", {}).get("max_drawdown", 0.0))),
        "positive_windows": int(metrics.get("positive_windows", 0)),
        "cost_stress_1_5x_net_expectancy": float(stress.get("net_expectancy", 0.0)),
        "cost_stress_1_5x_profit_factor": float(stress.get("profit_factor") or 0.0),
        "funding_observed": bool(
            metrics.get("funding_observed", result.get("portfolio", {}).get("funding_rate_available", False))
        ),
        "pass": bool(_research_passes(metrics)) if metrics else False,
    }


def _build_research_validation_leaderboard(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose both selection stages without collapsing validation into research."""

    leaderboard: list[dict[str, Any]] = []
    for record in records:
        research = record.get("research")
        validation = record.get("validation")
        research_metrics = _stage_leaderboard_metrics(research)
        validation_metrics = _stage_leaderboard_metrics(validation)
        variant = record.get("variant") or {}
        leaderboard.append(
            {
                "candidate_id": record.get("candidate_id"),
                "variant_id": record.get("variant_id"),
                "parent": variant.get("parent_candidate", record.get("candidate_id")),
                "generation": int(record.get("generation", 0)),
                "family": record.get("family"),
                "changed_parameters": list(variant.get("changed_parameters", ())),
                "parameters": variant.get("parameters", record.get("candidate_parameters", {})),
                "research": research_metrics,
                "validation": validation_metrics,
                "research_status": "PASS" if research_metrics["pass"] else "FAIL" if research else "NOT_RUN",
                "validation_status": "PASS" if validation_metrics["pass"] else "FAIL" if validation else "NOT_RUN",
                "combined": {
                    "finalist_eligible": bool(research_metrics["pass"] and validation_metrics["pass"]),
                },
            }
        )
    return leaderboard


def _bounded_search_execution(
    *,
    planned_generation_1: list[str],
    executed_generation_1: list[str],
    validated_generation_1: list[str],
    planned_generation_2: list[str],
    executed_generation_2: list[str],
    validated_generation_2: list[str],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the fail-closed evidence for the declared bounded surface."""

    planned_generation_1 = list(dict.fromkeys(planned_generation_1))
    executed_generation_1 = list(dict.fromkeys(executed_generation_1))
    validated_generation_1 = list(dict.fromkeys(validated_generation_1))
    planned_generation_2 = list(dict.fromkeys(planned_generation_2))
    executed_generation_2 = list(dict.fromkeys(executed_generation_2))
    validated_generation_2 = list(dict.fromkeys(validated_generation_2))
    missing_generation_1 = sorted(set(planned_generation_1) - set(executed_generation_1))
    missing_generation_2 = sorted(set(planned_generation_2) - set(executed_generation_2))
    missing_validation_1 = sorted(set(planned_generation_1) - set(validated_generation_1))
    missing_validation_2 = sorted(set(planned_generation_2) - set(validated_generation_2))
    exhausted = (
        not missing_generation_1 and not missing_generation_2 and not missing_validation_1 and not missing_validation_2
    )
    return {
        "planned_generation_1_variants": planned_generation_1,
        "executed_generation_1_variants": executed_generation_1,
        "validated_generation_1_variants": validated_generation_1,
        "planned_generation_2_hypotheses": planned_generation_2,
        "executed_generation_2_hypotheses": executed_generation_2,
        "validated_generation_2_hypotheses": validated_generation_2,
        "missing_generation_1": missing_generation_1,
        "missing_generation_2": missing_generation_2,
        "missing_generation_1_validation": missing_validation_1,
        "missing_generation_2_validation": missing_validation_2,
        "search_surface_exhausted": exhausted,
        "entries": entries,
    }


def _search_surface_exhausted(execution: dict[str, Any]) -> bool:
    return bool(execution.get("search_surface_exhausted", False))


def _no_alpha_allowed(execution: dict[str, Any], *, finalist_count: int, final_holdout_accessed: bool) -> bool:
    """No-Alpha is legal only after every declared stage is complete and holdout is untouched."""

    return _search_surface_exhausted(execution) and finalist_count == 0 and not final_holdout_accessed


def write_checkpoint(output: Path, checkpoint: MasterCheckpoint) -> None:
    _json_write(output / "MASTER_CHECKPOINT.json", asdict(checkpoint))


def load_checkpoint(output: Path) -> MasterCheckpoint | None:
    path = output / "MASTER_CHECKPOINT.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MasterCheckpoint(**raw)


def _generation_one_payload_complete(payload: dict[str, Any]) -> bool:
    planned = {str(item.get("variant_id")) for item in payload.get("variants", [])}
    completed = set(payload.get("results", {}))
    return bool(payload.get("complete", False)) and planned == completed == set(
        payload.get("planned_variant_ids", planned)
    )


def _generation_two_payload_complete(payload: dict[str, Any]) -> bool:
    planned = {str(item.get("variant_id")) for item in payload.get("hypotheses", [])}
    completed = set(payload.get("results", {}))
    return bool(payload.get("complete", False)) and planned == completed == set(
        payload.get("planned_hypothesis_ids", planned)
    )


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
    unreachable = unclassified_unreachable_candidates(inventory)
    if unreachable and database.is_file():
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
            resume=resume,
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
    validation_controls = tuple(
        candidate_id for candidate_id in g0_research_qualified if candidate_id in TOURNAMENT_CONTROL_CANDIDATES
    )
    validation_market_data = (
        _load_technical_market_data(database, end_at=split.validation_end) if validation_controls else None
    )
    validation_funding_points = (
        _load_funding_points(database, end_at=split.validation_end) if validation_controls else None
    )
    validation_results = {
        candidate_id: _run_base_validation(
            database=database,
            output=output,
            candidate_id=candidate_id,
            windows=validation_windows,
            data_end=split.validation_end,
            market_data=validation_market_data,
            funding_points=validation_funding_points,
        )
        for candidate_id in g0_research_qualified
    }
    validation = {candidate_id: result["master_metrics"] for candidate_id, result in validation_results.items()}
    _json_write(output / "VALIDATION.json", validation)

    generation_one_path = output / "GENERATION_1.json"
    generation_one = _json_read(generation_one_path) if resume and generation_one_path.is_file() else {}
    if not _generation_one_payload_complete(generation_one):
        generation_one = run_generation_one(
            database=database,
            output=output,
            split=split,
            generation_zero=generation_zero,
            resume=resume,
        )
    if not _generation_one_payload_complete(generation_one):
        incomplete = {
            "status": TerminalStatus.BOUNDED_SEARCH_INCOMPLETE.value,
            "reason": "generation_1_surface_not_exhausted",
            "generation": 1,
            "planned_variant_ids": generation_one.get("planned_variant_ids", []),
            "completed_variant_ids": generation_one.get("completed_variant_ids", []),
            "missing_generation_1": sorted(
                set(generation_one.get("planned_variant_ids", []))
                - set(generation_one.get("completed_variant_ids", []))
            ),
            "final_holdout_results_accessed": False,
            "dual_gate": blocked_dual_gate("bounded_search_incomplete"),
        }
        _json_write(output / "BOUNDED_SEARCH_EXECUTION.json", incomplete)
        _json_write(output / "FINAL_REPORT.json", incomplete)
        return incomplete
    _json_write(output / "GENERATION_1.json", generation_one)
    g1_ranked = sorted(
        generation_one["results"].values(),
        key=lambda result: result["master_metrics"]["net_expectancy"],
        reverse=True,
    )
    g1_validation_candidates = g1_ranked
    g1_validation: dict[str, Any] = {}
    g1_validation_partial_path = output / "GENERATION_1_VALIDATION_PARTIAL.json"
    g1_validation_partial = (
        _json_read(g1_validation_partial_path) if resume and g1_validation_partial_path.is_file() else {}
    )
    planned_g1_validation_ids = {str(result["variant"]["variant_id"]) for result in g1_validation_candidates}
    g1_validation_results: dict[str, Any] = {
        variant_id: result
        for variant_id, result in g1_validation_partial.get("results", {}).items()
        if variant_id in planned_g1_validation_ids
    }
    for result in g1_validation_candidates:
        spec = VariantSpec(**result["variant"])
        if spec.variant_id in g1_validation_results:
            continue
        validated = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=validation_windows,
            data_end=split.validation_end,
            ledger=TrialLedger(output / "trial-ledger.jsonl"),
            ledger_label="validation",
        )
        g1_validation_results[spec.variant_id] = validated
        _json_write(
            g1_validation_partial_path,
            {
                "generation": 1,
                "results": g1_validation_results,
                "planned_variant_ids": sorted(planned_g1_validation_ids),
            },
        )
    g1_validation = {variant_id: result["master_metrics"] for variant_id, result in g1_validation_results.items()}
    _json_write(output / "GENERATION_1_VALIDATION.json", g1_validation)

    generation_two_path = output / "GENERATION_2.json"
    generation_two = _json_read(generation_two_path) if resume and generation_two_path.is_file() else {}
    if not _generation_two_payload_complete(generation_two):
        generation_two = run_generation_two(
            database=database,
            output=output,
            split=split,
            generation_one=generation_one,
            resume=resume,
        )
    if not _generation_two_payload_complete(generation_two):
        incomplete = {
            "status": TerminalStatus.BOUNDED_SEARCH_INCOMPLETE.value,
            "reason": "generation_2_surface_not_exhausted",
            "generation": 2,
            "planned_hypothesis_ids": generation_two.get("planned_hypothesis_ids", []),
            "completed_hypothesis_ids": generation_two.get("completed_hypothesis_ids", []),
            "missing_generation_2": sorted(
                set(generation_two.get("planned_hypothesis_ids", []))
                - set(generation_two.get("completed_hypothesis_ids", []))
            ),
            "final_holdout_results_accessed": False,
            "dual_gate": blocked_dual_gate("bounded_search_incomplete"),
        }
        _json_write(output / "BOUNDED_SEARCH_EXECUTION.json", incomplete)
        _json_write(output / "FINAL_REPORT.json", incomplete)
        return incomplete
    _json_write(output / "GENERATION_2.json", generation_two)
    g2_validation: dict[str, Any] = {}
    g2_validation_partial_path = output / "GENERATION_2_VALIDATION_PARTIAL.json"
    g2_validation_partial = (
        _json_read(g2_validation_partial_path) if resume and g2_validation_partial_path.is_file() else {}
    )
    planned_g2_validation_ids = {str(result["variant"]["variant_id"]) for result in generation_two["results"].values()}
    g2_validation_results: dict[str, Any] = {
        variant_id: result
        for variant_id, result in g2_validation_partial.get("results", {}).items()
        if variant_id in planned_g2_validation_ids
    }
    for result in generation_two["results"].values():
        spec = VariantSpec(**result["variant"])
        if spec.variant_id in g2_validation_results:
            continue
        validated = _run_variant(
            database=database,
            output=output,
            spec=spec,
            windows=validation_windows,
            data_end=split.validation_end,
            ledger=TrialLedger(output / "trial-ledger.jsonl"),
            ledger_label="validation",
        )
        g2_validation_results[spec.variant_id] = validated
        _json_write(
            g2_validation_partial_path,
            {
                "generation": 2,
                "results": g2_validation_results,
                "planned_hypothesis_ids": sorted(planned_g2_validation_ids),
            },
        )
    g2_validation = {variant_id: result["master_metrics"] for variant_id, result in g2_validation_results.items()}
    _json_write(output / "GENERATION_2_VALIDATION.json", g2_validation)

    leaderboard_records: list[dict[str, Any]] = []
    for candidate_id, research_result in generation_zero["results"].items():
        candidate = get_candidate(candidate_id)
        leaderboard_records.append(
            {
                "candidate_id": candidate_id,
                "variant_id": f"{candidate_id}@g0:baseline",
                "generation": 0,
                "family": _family(candidate_id),
                "research": research_result,
                "validation": validation_results.get(candidate_id),
                "candidate_parameters": candidate.get_config(),
            }
        )
    for result in generation_one["results"].values():
        variant = result["variant"]
        leaderboard_records.append(
            {
                "candidate_id": variant["parent_candidate"],
                "variant_id": variant["variant_id"],
                "generation": 1,
                "family": variant["family"],
                "variant": variant,
                "research": result,
                "validation": g1_validation_results.get(variant["variant_id"]),
            }
        )
    for result in generation_two["results"].values():
        variant = result["variant"]
        leaderboard_records.append(
            {
                "candidate_id": variant["parent_candidate"],
                "variant_id": variant["variant_id"],
                "generation": 2,
                "family": variant["family"],
                "variant": variant,
                "research": result,
                "validation": g2_validation_results.get(variant["variant_id"]),
            }
        )
    research_validation_leaderboard = _build_research_validation_leaderboard(leaderboard_records)
    _json_write(output / "RESEARCH_VALIDATION_LEADERBOARD.json", research_validation_leaderboard)
    bounded_execution = _bounded_search_execution(
        planned_generation_1=list(generation_one.get("planned_variant_ids", [])),
        executed_generation_1=list(generation_one.get("results", {})),
        validated_generation_1=list(g1_validation_results),
        planned_generation_2=list(generation_two.get("planned_hypothesis_ids", [])),
        executed_generation_2=list(generation_two.get("results", {})),
        validated_generation_2=list(g2_validation_results),
        entries=research_validation_leaderboard,
    )
    _json_write(output / "BOUNDED_SEARCH_EXECUTION.json", bounded_execution)
    bounded = {
        "generation_0": {"status": "EXECUTED", "candidate_count": len(candidate_ids)},
        "generation_1": {
            "status": "EXECUTED",
            "variation_count": len(generation_one["variants"]),
            "planned": len(bounded_execution["planned_generation_1_variants"]),
            "executed": len(bounded_execution["executed_generation_1_variants"]),
            "validated": len(bounded_execution["validated_generation_1_variants"]),
        },
        "generation_2": {
            "status": "EXECUTED",
            "hypothesis_count": len(generation_two["hypotheses"]),
            "planned": len(bounded_execution["planned_generation_2_hypotheses"]),
            "executed": len(bounded_execution["executed_generation_2_hypotheses"]),
            "validated": len(bounded_execution["validated_generation_2_hypotheses"]),
        },
        "search_surface_exhausted": bounded_execution["search_surface_exhausted"],
    }
    _json_write(output / "BOUNDED_GENERATIONS.json", bounded)

    if not _search_surface_exhausted(bounded_execution):
        incomplete = {
            "status": TerminalStatus.BOUNDED_SEARCH_INCOMPLETE.value,
            "reason": "declared_bounded_surface_not_exhausted",
            "bounded_search_execution": bounded_execution,
            "research_validation_leaderboard": research_validation_leaderboard,
            "final_holdout_results_accessed": False,
            "dual_gate": blocked_dual_gate("bounded_search_incomplete"),
        }
        _json_write(output / "FINAL_REPORT.json", incomplete)
        return incomplete

    finalist_candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for candidate_id in g0_research_qualified:
        research_result = generation_zero["results"].get(candidate_id)
        validation_result = validation_results.get(candidate_id)
        if not research_result or not validation_result:
            continue
        research_metrics = research_result["master_metrics"]
        validation_metrics = validation_result["master_metrics"]
        if not (_research_passes(research_metrics) and _research_passes(validation_metrics)):
            continue
        candidate = get_candidate(candidate_id)
        baseline_variant = {
            "variant_id": f"{candidate_id}@g0:baseline",
            "parent_candidate": candidate_id,
            "family": _family(candidate_id),
            "generation": 0,
            "hypothesis": "sealed generation-0 baseline",
            "parameters": candidate.get_config(),
            "changed_parameters": (),
        }
        finalist_candidates.append(
            (
                baseline_variant["variant_id"],
                _merge_selection_metrics(research_metrics, validation_metrics),
                baseline_variant,
            )
        )
    for result in g1_validation_candidates:
        variant = result["variant"]
        research_metrics = result["master_metrics"]
        validation_metrics = g1_validation.get(variant["variant_id"], {})
        merged = _merge_selection_metrics(research_metrics, validation_metrics)
        if _research_passes(research_metrics) and _research_passes(validation_metrics):
            finalist_candidates.append((variant["variant_id"], merged, variant))
    for result in generation_two["results"].values():
        variant = result["variant"]
        metrics = result["master_metrics"]
        validation_metrics = g2_validation.get(variant["variant_id"], {})
        merged = _merge_selection_metrics(metrics, validation_metrics)
        if _research_passes(metrics) and _research_passes(validation_metrics):
            finalist_candidates.append((variant["variant_id"], merged, variant))

    if not finalist_candidates:
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
            "bounded_search_execution": bounded_execution,
            "research_validation_leaderboard": research_validation_leaderboard,
            "final_holdout_results_accessed": False,
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
                "status": TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
                "candidate_id": best[0],
                "metrics": best[1].get("master_metrics", {}),
                "profitability_recovery": {
                    "eligible": recovery_result.eligible,
                    "failed_requirements": list(recovery_result.failed_requirements),
                },
                "canary_candidate_ids": sorted(CANARY_CANDIDATES),
                "production_authority": "NOT_GRANTED",
                "bounded_search_execution": "BOUNDED_SEARCH_EXECUTION.json",
                "research_validation_leaderboard": "RESEARCH_VALIDATION_LEADERBOARD.json",
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
        finalist_candidates, key=lambda item: item[1]["net_expectancy"]
    )
    champion_base = str(champion_variant["parent_candidate"])
    champion_spec = VariantSpec(**champion_variant)
    champion_research_result = generation_zero["results"].get(champion_base)
    champion_validation_result = validation_results.get(champion_base)
    if champion_research_result is None:
        champion_research_result = generation_one["results"].get(champion_id)
        champion_validation_result = g1_validation_results.get(champion_id)
    if champion_research_result is None:
        champion_research_result = generation_two["results"].get(champion_id)
        champion_validation_result = g2_validation_results.get(champion_id)
    if champion_research_result is None:
        raise RuntimeError(f"finalist result missing for {champion_id}")
    merged_champion_result = _merge_research_validation_result(champion_research_result, champion_validation_result)
    expensive_evidence = _run_expensive_validations(
        candidate_id=champion_id,
        result=merged_champion_result,
        output=output,
    )
    merged_champion_result["expensive_validation"] = expensive_evidence
    if _expensive_validation_pending(expensive_evidence):
        pending_report: dict[str, Any] = {
            "status": TerminalStatus.FINALIST_FROZEN_PENDING_EXPENSIVE_VALIDATION.value,
            "reason": "candidate_specific_expensive_validation_spec_required",
            "finalist": {
                "strategy_id": champion_base,
                "variant_id": champion_id,
                "variant": champion_variant,
                "metrics": champion_metrics,
            },
            "expensive_validation": expensive_evidence,
            "final_holdout_results_accessed": False,
            "production_authority": "NOT_GRANTED",
            "dual_gate": build_dual_gate_report(
                execution_chain={"status": "BLOCKED", "evidence": ["production_forward_not_verified"]},
                profitability_recovery={
                    "status": "PENDING",
                    "evidence": [
                        "finalist_frozen",
                        "expensive_validation_spec_required",
                        "final_holdout_locked",
                    ],
                },
            ),
        }
        _json_write(output / "FINALIST_FROZEN.json", pending_report)
        _json_write(output / "FINAL_REPORT.json", pending_report)
        write_checkpoint(
            output,
            MasterCheckpoint(
                schema_version=2,
                stage=MasterStage.S9_FINAL_UNTOUCHED_AUDIT.value,
                status=str(pending_report["status"]),
                generated_at=_now().isoformat(),
                baseline=baseline,
                candidate_id=champion_base,
                candidate_version=get_candidate(champion_base).version,
                pending_acceptance_stage=MasterStage.S9_FINAL_UNTOUCHED_AUDIT.value,
                evidence={
                    "final_report": "FINAL_REPORT.json",
                    "finalist_frozen": "FINALIST_FROZEN.json",
                    "expensive_validation": f"EXPENSIVE_VALIDATION_{champion_id.replace('/', '_').replace(':', '_')}.json",
                },
            ),
        )
        return pending_report

    final_audit = run_final_audit(
        database,
        output,
        champion_base,
        split=split,
        variant=champion_spec,
    )
    _json_write(output / "FINAL_AUDIT.json", final_audit)
    final_metrics = final_audit["master_metrics"]
    final_recovery = evaluate_profitability_recovery(
        _recovery_metrics_for_result(
            merged_champion_result,
            final_holdout=final_metrics,
            expensive_evidence=expensive_evidence,
        )
    )
    if not final_recovery.eligible:
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
            "status": "PENDING",
            "evidence": [
                "research_and_validation_evidence_frozen",
                "final_holdout_results_accessed_once_after_selection_freeze",
                "production_forward_evidence_required_before_profitability_recovery_pass",
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
            TerminalStatus.FINALIST_FROZEN_PENDING_EXPENSIVE_VALIDATION.value,
            TerminalStatus.NO_PROMOTABLE_ALPHA_AFTER_BOUNDED_SEARCH.value,
            TerminalStatus.BLOCKED_EXTERNAL_NATURAL_MARKET.value,
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
