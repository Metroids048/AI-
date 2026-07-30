"""Offline technical-strategy replay using the production decision pipeline.

The replay deliberately uses a read-only historical market-data view.  It
reuses the technical signal fusion, multi-timeframe confirmation, stop/take
price calculation, and cost conventions used by Paper execution without
creating orders, snapshots, or changing a strategy's runtime eligibility.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any, cast

from services.data import DataRepository
from services.execution.decision_pipeline import DecisionPipeline, DecisionPipelineResult
from services.execution.exit_ladder import (
    ExitLadderState,
    apply_level_fill,
    close_quantity_for_level,
    initialize_exit_ladder,
    level_hit,
    level_trigger_price,
    next_pending_level,
    next_trailed_stop_price,
)
from services.execution.paper_signal import PaperSignalGenerator
from services.validation.metrics import annualized_sharpe, max_drawdown_from_pnls, profit_factor, win_rate
from services.validation.policy import default_policy
from shared.models import OHLCVBar, StrategyContract, StrategyRules, TradeSide

EXIT_MODE_FIXED_2R = "fixed_2r"
EXIT_MODE_EXIT_LADDER = "exit_ladder"
PipelineFactory = Callable[["HistoricalMarketDataView"], Any]
MarketData = dict[str, dict[str, list[OHLCVBar | dict[str, Any]]]]


@dataclass(frozen=True)
class ReplayTrade:
    symbol: str
    side: TradeSide
    opened_at: datetime
    closed_at: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    take_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    fee_bps: float
    slippage_bps: float
    r_multiple: float
    quantity_fraction: float = 1.0
    ladder_r: float | None = None
    mae_r: float = 0.0
    mfe_r: float = 0.0
    bars_to_mfe: int = 0
    bars_held: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_price": self.stop_price,
            "take_price": self.take_price,
            "exit_reason": self.exit_reason,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "r_multiple": self.r_multiple,
            "quantity_fraction": self.quantity_fraction,
            "ladder_r": self.ladder_r,
            "mae_r": self.mae_r,
            "mfe_r": self.mfe_r,
            "bars_to_mfe": self.bars_to_mfe,
            "bars_held": self.bars_held,
        }


@dataclass(frozen=True)
class ReplayMetrics:
    strategy_key: str
    entry_timeframe: str
    total_trades: int
    signal_count: int
    win_rate: float
    average_win: float
    average_loss: float
    average_r: float
    average_hold_hours: float
    ladder_level_hits: dict[str, int]
    gross_return: float
    net_return: float
    net_expectancy: float
    total_fee_bps: float
    total_slippage_bps: float
    cost_share_of_gross_profit: float | None
    sharpe: float
    profit_factor: float
    max_drawdown: float
    evaluation_start: datetime | None
    evaluation_end: datetime | None
    data_issues: list[str]
    trades: tuple[ReplayTrade, ...]
    exit_mode: str = EXIT_MODE_FIXED_2R

    def as_dict(self, *, include_trades: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_key": self.strategy_key,
            "entry_timeframe": self.entry_timeframe,
            "exit_mode": self.exit_mode,
            "total_trades": self.total_trades,
            "signal_count": self.signal_count,
            "win_rate": self.win_rate,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "average_r": self.average_r,
            "average_hold_hours": self.average_hold_hours,
            "ladder_level_hits": self.ladder_level_hits,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "net_expectancy": self.net_expectancy,
            "total_fee_bps": self.total_fee_bps,
            "total_slippage_bps": self.total_slippage_bps,
            "cost_share_of_gross_profit": self.cost_share_of_gross_profit,
            "sharpe": self.sharpe,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "evaluation_start": self.evaluation_start.isoformat() if self.evaluation_start else None,
            "evaluation_end": self.evaluation_end.isoformat() if self.evaluation_end else None,
            "data_issues": self.data_issues,
            "net_equity_curve": self._equity_curve(),
        }
        if include_trades:
            payload["trades"] = [trade.as_dict() for trade in self.trades]
        return payload

    def _equity_curve(self) -> list[dict[str, Any]]:
        equity = 1.0
        curve = []
        for trade in self.trades:
            equity += trade.net_return
            curve.append({"time": trade.closed_at.isoformat(), "equity": equity})
        return curve


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: str
    start_at: datetime
    end_at: datetime
    baseline: ReplayMetrics
    candidate: ReplayMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "baseline": self.baseline.as_dict(),
            "candidate": self.candidate.as_dict(),
        }


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    failed_reasons: tuple[str, ...]
    baseline_oos_net_expectancy: float
    candidate_oos_net_expectancy: float
    required_relative_improvement: float
    actual_improvement: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "failed_reasons": list(self.failed_reasons),
            "baseline_oos_net_expectancy": self.baseline_oos_net_expectancy,
            "candidate_oos_net_expectancy": self.candidate_oos_net_expectancy,
            "required_relative_improvement": self.required_relative_improvement,
            "actual_improvement": self.actual_improvement,
        }


@dataclass(frozen=True)
class TechnicalStrategyComparisonReport:
    generated_at: datetime
    baseline: ReplayMetrics
    candidate: ReplayMetrics
    baseline_train: ReplayMetrics
    candidate_train: ReplayMetrics
    baseline_oos: ReplayMetrics
    candidate_oos: ReplayMetrics
    signal_density_multiple: float | None
    walk_forward_windows: tuple[WalkForwardWindow, ...]
    promotion: PromotionDecision
    methodology: dict[str, Any]

    def as_dict(self, *, include_trades: bool = False) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "baseline": self.baseline.as_dict(include_trades=include_trades),
            "candidate": self.candidate.as_dict(include_trades=include_trades),
            "baseline_train": self.baseline_train.as_dict(),
            "candidate_train": self.candidate_train.as_dict(),
            "baseline_oos": self.baseline_oos.as_dict(),
            "candidate_oos": self.candidate_oos.as_dict(),
            "signal_density_multiple": self.signal_density_multiple,
            "walk_forward_windows": [window.as_dict() for window in self.walk_forward_windows],
            "promotion": self.promotion.as_dict(),
            "methodology": self.methodology,
        }

    def to_markdown(self) -> str:
        def row(name: str, baseline_value: Any, candidate_value: Any) -> str:
            return f"| {name} | {baseline_value} | {candidate_value} |"

        symbols = self.methodology.get("symbols", [])
        required_timeframes = self.methodology.get("required_timeframes", [])
        issues = [*self.baseline.data_issues, *self.candidate.data_issues]
        lines = [
            "# Top20 1h Baseline vs Current 4h/1h/15m Entry Prescreen",
            "",
            f"- Generated at: {self.generated_at.isoformat()}",
            f"- Evaluation range: {self.baseline.evaluation_start} to {self.baseline.evaluation_end}",
            f"- Symbols ({len(symbols)}): {', '.join(symbols)}",
            f"- Required timeframes: {', '.join(required_timeframes)}",
            f"- Data issues: {len(set(issues)) if issues else 'none'}",
            "- Decision source: production DecisionPipeline over a read-only historical data view.",
            "- LLM veto and market-intelligence votes: disabled for deterministic historical replay.",
            "- Entry-signal prescreen only; no automatic promotion.",
            f"- Exit model: {self.methodology.get('exit_model', 'shared fixed stoploss and fixed 2R takeprofit')}.",
            "",
            "## Comparison",
            "",
            "| Metric | 1h baseline | Current 4h/1h/15m policy |",
            "| --- | ---: | ---: |",
            row("Signals", self.baseline.signal_count, self.candidate.signal_count),
            row("Trades", self.baseline.total_trades, self.candidate.total_trades),
            row("Signal density multiple", "1.00x", self.signal_density_multiple),
            row("Win rate", f"{self.baseline.win_rate:.4f}", f"{self.candidate.win_rate:.4f}"),
            row("Average R", f"{self.baseline.average_r:.4f}", f"{self.candidate.average_r:.4f}"),
            row("Gross return", f"{self.baseline.gross_return:.6f}", f"{self.candidate.gross_return:.6f}"),
            row("Net return", f"{self.baseline.net_return:.6f}", f"{self.candidate.net_return:.6f}"),
            row("Net expectancy", f"{self.baseline.net_expectancy:.6f}", f"{self.candidate.net_expectancy:.6f}"),
            row("Fee cost", f"{self.baseline.total_fee_bps:.2f} bps", f"{self.candidate.total_fee_bps:.2f} bps"),
            row(
                "Slippage cost",
                f"{self.baseline.total_slippage_bps:.2f} bps",
                f"{self.candidate.total_slippage_bps:.2f} bps",
            ),
            row("Sharpe", f"{self.baseline.sharpe:.4f}", f"{self.candidate.sharpe:.4f}"),
            row("Profit factor", f"{self.baseline.profit_factor:.4f}", f"{self.candidate.profit_factor:.4f}"),
            row("Max drawdown", f"{self.baseline.max_drawdown:.4f}", f"{self.candidate.max_drawdown:.4f}"),
            "",
            "## Informational Prescreen Thresholds",
            "",
            f"- Thresholds met: `{self.promotion.allowed}`",
            f"- Failed reasons: {', '.join(self.promotion.failed_reasons) or 'none'}",
            f"- OOS net expectancy: baseline `{self.promotion.baseline_oos_net_expectancy:.6f}`, "
            f"candidate `{self.promotion.candidate_oos_net_expectancy:.6f}`",
            f"- OOS improvement: `{self.promotion.actual_improvement:.6f}`; required `"
            f"{self.promotion.required_relative_improvement:.6f}`",
            "",
            "## Walk-forward Windows",
            "",
        ]
        for window in self.walk_forward_windows:
            lines.append(
                f"- {window.window_id}: {window.start_at.isoformat()} to {window.end_at.isoformat()} | "
                f"baseline net expectancy={window.baseline.net_expectancy:.6f}, "
                f"candidate net expectancy={window.candidate.net_expectancy:.6f}"
            )
        lines.extend(
            [
                "",
                "## Net Return Curve",
                "",
                "| Exit time | Baseline equity | Candidate equity |",
                "| --- | ---: | ---: |",
            ]
        )
        baseline_curve = {item["time"]: item["equity"] for item in self.baseline._equity_curve()}
        candidate_curve = {item["time"]: item["equity"] for item in self.candidate._equity_curve()}
        for point in sorted(set(baseline_curve) | set(candidate_curve)):
            lines.append(f"| {point} | {baseline_curve.get(point, '')} | {candidate_curve.get(point, '')} |")
        if issues:
            lines.extend(["", "## Data Issues", "", *[f"- {issue}" for issue in sorted(set(issues))]])
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ExitPolicy:
    """A named exit-side configuration to A/B against another on the same entry.

    Carries its own ``exit_rules``/``takeprofit_rules`` so a ladder arm remains
    self-contained even when the live runtime config no longer ships a ladder.
    """

    name: str
    exit_mode: str
    exit_rules: dict[str, Any]
    takeprofit_rules: dict[str, Any]

    def __post_init__(self) -> None:
        if self.exit_mode not in {EXIT_MODE_FIXED_2R, EXIT_MODE_EXIT_LADDER}:
            raise ValueError(f"unsupported exit_mode: {self.exit_mode}")


@dataclass(frozen=True)
class ExitPolicyComparisonReport:
    """Fixed-entry, two-exit-policy comparison (no OOS/walk-forward split).

    Deliberately flatter than :class:`TechnicalStrategyComparisonReport`: the
    entry signal is held constant across both arms, so only exit mechanics vary.
    """

    generated_at: datetime
    title: str
    entry_label: str
    policy_a_name: str
    policy_b_name: str
    policy_a: ReplayMetrics
    policy_b: ReplayMetrics
    methodology: dict[str, Any]

    def as_dict(self, *, include_trades: bool = False) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "title": self.title,
            "entry_label": self.entry_label,
            "policy_a_name": self.policy_a_name,
            "policy_b_name": self.policy_b_name,
            "policy_a": self.policy_a.as_dict(include_trades=include_trades),
            "policy_b": self.policy_b.as_dict(include_trades=include_trades),
            "methodology": self.methodology,
        }

    def to_markdown(self) -> str:
        a, b = self.policy_a, self.policy_b
        lines = [
            f"# {self.title}",
            "",
            f"- Generated at: {self.generated_at.isoformat()}",
            f"- Candidate entry: `{self.entry_label}`",
            "- Automatic Paper/Testnet settings: unchanged",
            "- Promotion: not requested; report is evidence only",
            "",
            "## Comparison",
            "",
            f"| Metric | {self.policy_a_name} | {self.policy_b_name} |",
            "| --- | ---: | ---: |",
            f"| Signals | {a.signal_count} | {b.signal_count} |",
            f"| Trade slices | {a.total_trades} | {b.total_trades} |",
            f"| Win rate | {a.win_rate:.4f} | {b.win_rate:.4f} |",
            f"| Net return | {a.net_return:.6f} | {b.net_return:.6f} |",
            f"| Net expectancy | {a.net_expectancy:.6f} | {b.net_expectancy:.6f} |",
            f"| Profit factor | {a.profit_factor:.4f} | {b.profit_factor:.4f} |",
            f"| Max drawdown | {a.max_drawdown:.4f} | {b.max_drawdown:.4f} |",
            f"| Avg hold hours | {a.average_hold_hours:.2f} | {b.average_hold_hours:.2f} |",
            f"| Ladder hits | {a.ladder_level_hits} | {b.ladder_level_hits} |",
            "",
            "## Notes",
            "",
            "- Both arms share the identical entry signal; only exit mechanics differ.",
            f"- `{self.policy_a_name}` exit_mode=`{a.exit_mode}`; `{self.policy_b_name}` exit_mode=`{b.exit_mode}`.",
            "- Failed Validation gates still forbid auto promotion.",
            "",
        ]
        issues = sorted({*a.data_issues, *b.data_issues})
        if issues:
            lines.extend(["## Data Issues", "", *[f"- {issue}" for issue in issues], ""])
        return "\n".join(lines)


def _strategy_with_exit_policy(entry_config: StrategyContract, policy: ExitPolicy) -> StrategyContract:
    """Clone ``entry_config`` keeping entry/stoploss/position rules but swapping
    the exit side to ``policy``. Never mutates the input contract."""

    return entry_config.model_copy(
        update={
            "strategy_id": f"{entry_config.strategy_id}_{policy.name}",
            "strategy_key": f"{entry_config.strategy_key}_{policy.name}",
            "rules": StrategyRules(
                entry_rules=dict(entry_config.rules.entry_rules),
                exit_rules=dict(policy.exit_rules),
                stoploss_rules=dict(entry_config.rules.stoploss_rules),
                takeprofit_rules=dict(policy.takeprofit_rules),
                position_rules=dict(entry_config.rules.position_rules),
            ),
        }
    )


def compare_exit_policies(
    *,
    entry_config: StrategyContract,
    exit_policy_a: ExitPolicy,
    exit_policy_b: ExitPolicy,
    market_data: MarketData,
    symbols: Iterable[str] | None = None,
    date_range: tuple[datetime | None, datetime | None] | None = None,
    title: str = "Exit Policy A vs B (same entry)",
    entry_label: str | None = None,
    warmup_bars: int = 80,
    max_workers: int = 8,
    pipeline_factory: PipelineFactory | None = None,
) -> ExitPolicyComparisonReport:
    """Replay one fixed entry signal under two exit policies and compare them.

    Both arms consume the same ``entry_config`` entry rules and the same
    ``market_data``; only the exit mechanics (exit_mode + exit/takeprofit rules)
    differ. This isolates the effect of the exit policy on the identical entry
    stream. ``date_range`` maps to replay ``(start_at, end_at)``; ``None`` (the
    default) reproduces the per-symbol warmup-start behaviour of :meth:`replay`.
    """

    if symbols is not None:
        wanted = set(symbols)
        market_data = {symbol: frames for symbol, frames in market_data.items() if symbol in wanted}
    start_at, end_at = date_range or (None, None)

    def _replay(policy: ExitPolicy) -> ReplayMetrics:
        service = TechnicalStrategyValidationService(
            pipeline_factory=pipeline_factory,
            warmup_bars=warmup_bars,
            max_workers=max_workers,
            exit_mode=policy.exit_mode,
        )
        return service.replay(
            strategy=_strategy_with_exit_policy(entry_config, policy),
            market_data=market_data,
            start_at=start_at,
            end_at=end_at,
        )

    metrics_a = _replay(exit_policy_a)
    metrics_b = _replay(exit_policy_b)
    return ExitPolicyComparisonReport(
        generated_at=datetime.now(UTC),
        title=title,
        entry_label=entry_label or entry_config.strategy_key,
        policy_a_name=exit_policy_a.name,
        policy_b_name=exit_policy_b.name,
        policy_a=metrics_a,
        policy_b=metrics_b,
        methodology={
            "data_source": "caller-supplied OHLCV market data",
            "symbols": sorted(market_data),
            "entry_config": entry_config.strategy_key,
            "policy_a": {"name": exit_policy_a.name, "exit_mode": exit_policy_a.exit_mode},
            "policy_b": {"name": exit_policy_b.name, "exit_mode": exit_policy_b.exit_mode},
            "warmup_bars": warmup_bars,
            "decision_use": "exit-policy A/B evidence only; no automatic promotion",
            "external_votes": "market intelligence and LLM veto disabled for deterministic replay",
        },
    )


class HistoricalMarketDataView:
    """Read-only DataRepository-shaped view limited to a replay timestamp."""

    def __init__(self, market_data: MarketData) -> None:
        self._bars: dict[tuple[str, str], list[OHLCVBar]] = {}
        self._timestamps: dict[tuple[str, str], list[datetime]] = {}
        for symbol, timeframes in market_data.items():
            for timeframe, values in timeframes.items():
                key = (symbol, timeframe)
                self._bars[key] = sorted(
                    [value if isinstance(value, OHLCVBar) else OHLCVBar(**value) for value in values],
                    key=lambda bar: bar.timestamp,
                )
                self._timestamps[key] = [bar.timestamp for bar in self._bars[key]]
        self.cutoff: datetime | None = None
        self._slice_cache: dict[tuple[str, str, int | None, datetime | None], list[OHLCVBar]] = {}

    def set_cutoff(self, cutoff: datetime) -> None:
        if cutoff != self.cutoff:
            self._slice_cache.clear()
        self.cutoff = cutoff

    def bars(self, *, symbol: str, timeframe: str) -> list[OHLCVBar]:
        return list(self._bars.get((symbol, timeframe), []))

    def list_ohlcv_bars(self, *, symbol: str, timeframe: str, limit: int | None = None, **_: Any) -> list[OHLCVBar]:
        key = (symbol, timeframe)
        cache_key = (symbol, timeframe, limit, self.cutoff)
        cached = self._slice_cache.get(cache_key)
        if cached is not None:
            return cached
        bars = self._bars.get(key, [])
        if self.cutoff is not None:
            bars = bars[: bisect_right(self._timestamps.get(key, []), self.cutoff)]
        result = bars[-limit:] if limit is not None else bars
        self._slice_cache[cache_key] = result
        return result

    def get_latest_market_extras(self, *, symbol: str, **_: Any) -> None:
        """Read-only replay has no point-in-time funding/OI snapshot.

        The production ``DecisionPipeline`` calls this (via ``_latest_funding_bps``)
        while building meta-label features; ``extract_features`` documents that a
        ``None`` funding rate is the correct "no snapshot" fallback (treated as
        ``0.0``), so returning ``None`` keeps the offline replay deterministic
        instead of crashing on a missing repository method.
        """
        return None


@dataclass
class _OpenPosition:
    symbol: str
    side: TradeSide
    opened_at: datetime
    entry_price: float
    stop_price: float
    take_price: float
    fee_bps: float
    slippage_bps: float
    original_stop: float
    remaining_fraction: float = 1.0
    ladder: Any | None = None
    trail_after_r: float | None = None
    mae_r: float = 0.0
    mfe_r: float = 0.0
    bars_to_mfe: int = 0
    bars_held: int = 0


class TechnicalStrategyValidationService:
    """Compare technical templates without mutating execution configuration."""

    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory | None = None,
        warmup_bars: int = 80,
        oos_fraction: float = 0.30,
        walk_forward_windows: int = 3,
        max_workers: int = 4,
        exit_mode: str = EXIT_MODE_FIXED_2R,
    ) -> None:
        if exit_mode not in {EXIT_MODE_FIXED_2R, EXIT_MODE_EXIT_LADDER}:
            raise ValueError(f"unsupported exit_mode: {exit_mode}")
        self._uses_default_pipeline = pipeline_factory is None
        self.pipeline_factory = pipeline_factory or (
            lambda view: DecisionPipeline(data_repo=cast(DataRepository, view))
        )
        self.warmup_bars = warmup_bars
        self.oos_fraction = oos_fraction
        self.walk_forward_windows = walk_forward_windows
        self.max_workers = max_workers
        self.exit_mode = exit_mode

    def replay(
        self,
        *,
        strategy: StrategyContract,
        market_data: MarketData,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> ReplayMetrics:
        replay_strategy = self._offline_strategy(strategy)
        entry_timeframe = self._entry_timeframe(replay_strategy)
        required_timeframes = self._required_timeframes(replay_strategy)
        symbols = sorted(market_data)
        workers = min(max(self.max_workers, 1), len(symbols) or 1)
        if self._uses_default_pipeline and workers > 1:
            payloads = [
                (
                    {symbol: market_data[symbol]},
                    replay_strategy,
                    symbol,
                    entry_timeframe,
                    required_timeframes,
                    start_at,
                    end_at,
                    self.warmup_bars,
                    self.oos_fraction,
                    self.walk_forward_windows,
                    self.exit_mode,
                )
                for symbol in symbols
            ]
            with ProcessPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(_replay_symbol_worker, payloads))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(
                    executor.map(
                        lambda symbol: self._replay_symbol_data(
                            market_data={symbol: market_data[symbol]},
                            strategy=replay_strategy,
                            symbol=symbol,
                            entry_timeframe=entry_timeframe,
                            required_timeframes=required_timeframes,
                            start_at=start_at,
                            end_at=end_at,
                        ),
                        symbols,
                    )
                )
        trades = [trade for symbol_trades, _, _ in results for trade in symbol_trades]
        signal_count = sum(symbol_signals for _, symbol_signals, _ in results)
        issues = [issue for _, _, symbol_issues in results for issue in symbol_issues]

        ordered = tuple(sorted(trades, key=lambda trade: trade.closed_at))
        return self._metrics(
            strategy_key=strategy.strategy_key,
            trades=ordered,
            signal_count=signal_count,
            issues=issues,
            evaluation_start=start_at,
            evaluation_end=end_at,
            entry_timeframe=entry_timeframe,
        )

    def _replay_symbol_data(
        self,
        *,
        market_data: MarketData,
        strategy: StrategyContract,
        symbol: str,
        entry_timeframe: str,
        required_timeframes: set[str],
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> tuple[list[ReplayTrade], int, list[str]]:
        view = HistoricalMarketDataView(market_data)
        missing = [timeframe for timeframe in required_timeframes if not view.bars(symbol=symbol, timeframe=timeframe)]
        if missing:
            return [], 0, [f"{symbol}: missing {timeframe}" for timeframe in missing]
        symbol_start = start_at or self._warmup_start(view=view, symbol=symbol, timeframes=required_timeframes)
        if symbol_start is None:
            return [], 0, [f"{symbol}: insufficient warmup data"]
        pipeline = self.pipeline_factory(view)
        trades, signal_count = self._replay_symbol(
            view=view,
            pipeline=pipeline,
            strategy=strategy,
            symbol=symbol,
            entry_timeframe=entry_timeframe,
            start_at=symbol_start,
            end_at=end_at,
        )
        return trades, signal_count, []

    def compare(
        self,
        *,
        baseline: StrategyContract,
        candidate: StrategyContract,
        market_data: MarketData,
    ) -> TechnicalStrategyComparisonReport:
        view = HistoricalMarketDataView(market_data)
        shared_start, shared_end = self._shared_range(view=view, strategies=(baseline, candidate), symbols=market_data)
        baseline_all = self.replay(
            strategy=baseline,
            market_data=market_data,
            start_at=shared_start,
            end_at=shared_end,
        )
        candidate_all = self.replay(
            strategy=candidate,
            market_data=market_data,
            start_at=shared_start,
            end_at=shared_end,
        )
        split_at = self._split_at(start_at=shared_start, end_at=shared_end)
        baseline_train = self._metrics_for_period(baseline_all, start_at=shared_start, end_at=split_at)
        candidate_train = self._metrics_for_period(candidate_all, start_at=shared_start, end_at=split_at)
        baseline_oos = self._metrics_for_period(baseline_all, start_at=split_at, end_at=shared_end)
        candidate_oos = self._metrics_for_period(candidate_all, start_at=split_at, end_at=shared_end)
        windows = self._walk_forward(
            baseline=baseline_all,
            candidate=candidate_all,
            start_at=shared_start,
            end_at=shared_end,
        )
        density = candidate_all.signal_count / baseline_all.signal_count if baseline_all.signal_count > 0 else None
        promotion = self._promotion_decision(baseline_oos=baseline_oos, candidate_oos=candidate_oos)
        return TechnicalStrategyComparisonReport(
            generated_at=datetime.now(UTC),
            baseline=baseline_all,
            candidate=candidate_all,
            baseline_train=baseline_train,
            candidate_train=candidate_train,
            baseline_oos=baseline_oos,
            candidate_oos=candidate_oos,
            signal_density_multiple=density,
            walk_forward_windows=windows,
            promotion=promotion,
            methodology={
                "data_source": "fixed Top20 Binance USD-M OHLCV supplied by caller",
                "symbols": sorted(market_data),
                "required_timeframes": sorted(
                    self._required_timeframes(baseline) | self._required_timeframes(candidate)
                ),
                "decision_pipeline": "production DecisionPipeline with read-only historical cutoff",
                "warmup_bars": self.warmup_bars,
                "oos_fraction": self.oos_fraction,
                "cost_model": "current strategy core/standard fee and slippage rules, round trip",
                "exit_model": (
                    "exit_ladder partial take-profit + break-even + remainder trail"
                    if self.exit_mode == EXIT_MODE_EXIT_LADDER
                    else "shared fixed stoploss and fixed 2R takeprofit"
                ),
                "exit_mode": self.exit_mode,
                "decision_use": "entry-signal prescreen only; no automatic promotion",
                "prescreen_rule": "OOS net expectancy > 0, >=20% improvement, Validation thresholds",
                "external_votes": "market intelligence and LLM veto disabled for deterministic replay",
            },
        )

    @staticmethod
    def write_audit(report: TechnicalStrategyComparisonReport, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report.to_markdown(), encoding="utf-8")

    def _replay_symbol(
        self,
        *,
        view: HistoricalMarketDataView,
        pipeline: Any,
        strategy: StrategyContract,
        symbol: str,
        entry_timeframe: str,
        start_at: datetime,
        end_at: datetime | None,
    ) -> tuple[list[ReplayTrade], int]:
        bars = view.bars(symbol=symbol, timeframe=entry_timeframe)
        position: _OpenPosition | None = None
        trades: list[ReplayTrade] = []
        signal_count = 0
        risk_prices = PaperSignalGenerator(data_repo=cast(DataRepository, view))
        for index, bar in enumerate(bars):
            if bar.timestamp < start_at or (end_at is not None and bar.timestamp > end_at):
                continue
            view.set_cutoff(bar.timestamp)
            if position is not None:
                closed, position = self._advance_open_position(position=position, bar=bar)
                trades.extend(closed)
                if position is not None:
                    continue
            decision = pipeline.evaluate(
                strategy=strategy,
                symbol=symbol,
                timeframe=entry_timeframe,
                enable_decision_veto=False,
                relaxed_signals=False,
            )
            if (
                not isinstance(decision, DecisionPipelineResult)
                or not decision.should_trade
                or decision.direction is None
            ):
                continue
            signal_count += 1
            # A signal is confirmed on the closed bar; the earliest historical
            # fill is the next bar's open, matching the live scheduler boundary.
            if index + 1 >= len(bars):
                continue
            fill_bar = bars[index + 1]
            if end_at is not None and fill_bar.timestamp > end_at:
                continue
            entry_price = float(fill_bar.open)
            if entry_price <= 0:
                continue
            stop_price, take_price = risk_prices._risk_prices(
                reference_price=fill_bar.open,
                direction=decision.direction,
                strategy=strategy,
                atr=decision.atr,
            )
            fee_bps, slippage_bps = self._round_trip_costs(strategy=strategy, symbol=symbol)
            ladder = None
            trail_after_r = None
            effective_take = float(take_price)
            if self.exit_mode == EXIT_MODE_EXIT_LADDER:
                ladder = initialize_exit_ladder(
                    symbol=symbol,
                    side=decision.direction,
                    entry_price=entry_price,
                    quantity=1.0,
                    stop_price=float(stop_price),
                    takeprofit_rules=dict(strategy.rules.takeprofit_rules),
                )
                if ladder is not None:
                    effective_take = 0.0
                    trail_after_r = None
            position = _OpenPosition(
                symbol=symbol,
                side=decision.direction,
                opened_at=fill_bar.timestamp,
                entry_price=entry_price,
                stop_price=float(stop_price),
                take_price=effective_take,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                original_stop=float(stop_price),
                remaining_fraction=1.0,
                ladder=ladder,
                trail_after_r=trail_after_r,
            )
        if position is not None and position.remaining_fraction > 0:
            latest = next(
                (
                    bar
                    for bar in reversed(bars)
                    if bar.timestamp >= start_at and (end_at is None or bar.timestamp <= end_at)
                ),
                None,
            )
            if latest is not None:
                trades.append(
                    self._close(
                        position=position,
                        closed_at=latest.timestamp,
                        exit_price=float(latest.close),
                        reason="end_of_window",
                        quantity_fraction=position.remaining_fraction,
                    )
                )
        return trades, signal_count

    def _advance_open_position(
        self,
        *,
        position: _OpenPosition,
        bar: OHLCVBar,
    ) -> tuple[list[ReplayTrade], _OpenPosition | None]:
        if position.ladder is None or self.exit_mode != EXIT_MODE_EXIT_LADDER:
            exit_price, exit_reason = self._protective_exit(position=position, bar=bar)
            position = self._record_excursion(
                position=position,
                bar=bar,
                exit_price=exit_price,
                exit_reason=exit_reason,
            )
            if exit_price is None:
                return [], position
            return (
                [
                    self._close(
                        position=position,
                        closed_at=bar.timestamp,
                        exit_price=exit_price,
                        reason=exit_reason,
                        quantity_fraction=position.remaining_fraction,
                    )
                ],
                None,
            )

        closed: list[ReplayTrade] = []
        position = self._record_excursion(position=position, bar=bar)
        ladder = cast(ExitLadderState, position.ladder)
        stop_price = float(ladder.current_stop_price)
        # Stop first (including BE / locked stops after partials).
        if position.side == TradeSide.LONG and float(bar.low) <= stop_price:
            closed.append(
                self._close(
                    position=position,
                    closed_at=bar.timestamp,
                    exit_price=stop_price,
                    reason="stoploss",
                    quantity_fraction=position.remaining_fraction,
                    stop_override=stop_price,
                )
            )
            return closed, None
        if position.side == TradeSide.SHORT and float(bar.high) >= stop_price:
            closed.append(
                self._close(
                    position=position,
                    closed_at=bar.timestamp,
                    exit_price=stop_price,
                    reason="stoploss",
                    quantity_fraction=position.remaining_fraction,
                    stop_override=stop_price,
                )
            )
            return closed, None

        pending = next_pending_level(ladder)
        if pending is not None and level_hit(
            state=ladder, level=pending, bar_high=float(bar.high), bar_low=float(bar.low)
        ):
            trigger = level_trigger_price(ladder, pending)
            qty = close_quantity_for_level(ladder, pending)
            fraction = qty / ladder.original_quantity if ladder.original_quantity else 0.0
            closed.append(
                self._close(
                    position=position,
                    closed_at=bar.timestamp,
                    exit_price=trigger,
                    reason=f"exit_ladder_{pending.r_multiple:g}r",
                    quantity_fraction=fraction,
                    stop_override=float(ladder.initial_stop_price),
                    take_override=trigger,
                    ladder_r=pending.r_multiple,
                )
            )
            ladder = apply_level_fill(ladder, level=pending, trigger_price=trigger, closed_quantity=qty)
            remaining = max(0.0, position.remaining_fraction - fraction)
            if remaining <= 1e-12 or ladder.remaining_quantity <= 0:
                return closed, None
            trail_after = ladder.remainder_trail_after_r if ladder.all_levels_executed else None
            return closed, _OpenPosition(
                symbol=position.symbol,
                side=position.side,
                opened_at=position.opened_at,
                entry_price=position.entry_price,
                stop_price=float(ladder.current_stop_price),
                take_price=0.0,
                fee_bps=position.fee_bps,
                slippage_bps=position.slippage_bps,
                original_stop=float(ladder.initial_stop_price),
                remaining_fraction=remaining,
                ladder=ladder,
                trail_after_r=trail_after,
                mae_r=position.mae_r,
                mfe_r=position.mfe_r,
                bars_to_mfe=position.bars_to_mfe,
                bars_held=position.bars_held,
            )

        # Remainder trail: once all ladder levels filled, ratchet stop to entry after
        # trail_after_r. Shares its math with PaperRuntimeService._apply_trailing_ratchet
        # via next_trailed_stop_price so backtest and live paths ratchet identically.
        if ladder.all_levels_executed and position.trail_after_r is not None:
            initial_distance = abs(position.entry_price - float(ladder.initial_stop_price))
            next_stop = next_trailed_stop_price(
                side=position.side.value,
                entry_price=position.entry_price,
                current_stop_price=float(ladder.current_stop_price),
                initial_distance=initial_distance,
                trail_after_r=position.trail_after_r,
                bar_high=float(bar.high),
                bar_low=float(bar.low),
            )
            if next_stop is not None:
                ladder = ExitLadderState(
                    symbol=ladder.symbol,
                    side=ladder.side,
                    entry_price=ladder.entry_price,
                    original_quantity=ladder.original_quantity,
                    remaining_quantity=ladder.remaining_quantity,
                    initial_stop_price=ladder.initial_stop_price,
                    current_stop_price=next_stop,
                    levels=ladder.levels,
                    remainder_trail_after_r=ladder.remainder_trail_after_r,
                    locked_level1_price=ladder.locked_level1_price,
                )
                position = _OpenPosition(
                    symbol=position.symbol,
                    side=position.side,
                    opened_at=position.opened_at,
                    entry_price=position.entry_price,
                    stop_price=next_stop,
                    take_price=0.0,
                    fee_bps=position.fee_bps,
                    slippage_bps=position.slippage_bps,
                    original_stop=position.original_stop,
                    remaining_fraction=position.remaining_fraction,
                    ladder=ladder,
                    trail_after_r=position.trail_after_r,
                    mae_r=position.mae_r,
                    mfe_r=position.mfe_r,
                    bars_to_mfe=position.bars_to_mfe,
                    bars_held=position.bars_held,
                )
        return closed, position

    @staticmethod
    def _protective_exit(*, position: _OpenPosition, bar: OHLCVBar) -> tuple[float | None, str]:
        if position.side == TradeSide.LONG:
            if float(bar.low) <= position.stop_price:
                return position.stop_price, "stoploss"
            if position.take_price > 0 and float(bar.high) >= position.take_price:
                return position.take_price, "takeprofit"
        else:
            if float(bar.high) >= position.stop_price:
                return position.stop_price, "stoploss"
            if position.take_price > 0 and float(bar.low) <= position.take_price:
                return position.take_price, "takeprofit"
        return None, ""

    @staticmethod
    def _record_excursion(
        *,
        position: _OpenPosition,
        bar: OHLCVBar,
        exit_price: float | None = None,
        exit_reason: str | None = None,
    ) -> _OpenPosition:
        """Track excursions without inventing an intra-bar path on a stop bar."""
        risk = abs(position.entry_price - position.original_stop)
        if risk <= 0:
            return position
        bars_held = position.bars_held + 1
        high, low = float(bar.high), float(bar.low)
        if exit_reason == "stoploss" and exit_price is not None:
            # The replay's stop-first rule means the favorable extreme of this
            # bar may have occurred after the stop; do not count it as MFE.
            high = low = float(exit_price)
        elif exit_reason == "takeprofit" and exit_price is not None:
            # Stop was already ruled out, so the target is the known favorable
            # execution point; use it instead of a possibly later bar extreme.
            high = low = float(exit_price)
        if position.side == TradeSide.LONG:
            favorable = (high - position.entry_price) / risk
            adverse = (low - position.entry_price) / risk
        else:
            favorable = (position.entry_price - low) / risk
            adverse = (position.entry_price - high) / risk
        mfe_r = max(position.mfe_r, favorable)
        return _OpenPosition(
            symbol=position.symbol,
            side=position.side,
            opened_at=position.opened_at,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            take_price=position.take_price,
            fee_bps=position.fee_bps,
            slippage_bps=position.slippage_bps,
            original_stop=position.original_stop,
            remaining_fraction=position.remaining_fraction,
            ladder=position.ladder,
            trail_after_r=position.trail_after_r,
            mae_r=min(position.mae_r, adverse),
            mfe_r=mfe_r,
            bars_to_mfe=bars_held if mfe_r > position.mfe_r else position.bars_to_mfe,
            bars_held=bars_held,
        )

    @staticmethod
    def _close(
        *,
        position: _OpenPosition,
        closed_at: datetime,
        exit_price: float,
        reason: str,
        quantity_fraction: float = 1.0,
        stop_override: float | None = None,
        take_override: float | None = None,
        ladder_r: float | None = None,
    ) -> ReplayTrade:
        fraction = max(0.0, min(1.0, quantity_fraction))
        gross_return = (
            (exit_price - position.entry_price) / position.entry_price
            if position.side == TradeSide.LONG
            else (position.entry_price - exit_price) / position.entry_price
        )
        gross_return *= fraction
        fee_bps = position.fee_bps * fraction
        slippage_bps = position.slippage_bps * fraction
        net_return = gross_return - (fee_bps + slippage_bps) / 10_000
        stop_ref = stop_override if stop_override is not None else position.original_stop
        initial_risk = abs(position.entry_price - stop_ref) / position.entry_price
        return ReplayTrade(
            symbol=position.symbol,
            side=position.side,
            opened_at=position.opened_at,
            closed_at=closed_at,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_price=stop_ref,
            take_price=take_override if take_override is not None else position.take_price,
            exit_reason=reason,
            gross_return=gross_return,
            net_return=net_return,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            r_multiple=(gross_return / fraction / initial_risk) if initial_risk > 0 and fraction > 0 else 0.0,
            quantity_fraction=fraction,
            ladder_r=ladder_r,
            mae_r=position.mae_r,
            mfe_r=position.mfe_r,
            bars_to_mfe=position.bars_to_mfe,
            bars_held=position.bars_held,
        )

    def _metrics(
        self,
        *,
        strategy_key: str,
        trades: Iterable[ReplayTrade],
        signal_count: int,
        issues: list[str],
        evaluation_start: datetime | None,
        evaluation_end: datetime | None,
        entry_timeframe: str,
    ) -> ReplayMetrics:
        ordered = tuple(sorted(trades, key=lambda trade: trade.closed_at))
        net_returns = [trade.net_return for trade in ordered]
        gross_returns = [trade.gross_return for trade in ordered]
        wins = [value for value in net_returns if value > 0]
        losses = [value for value in net_returns if value < 0]
        gross_profit = sum(value for value in gross_returns if value > 0)
        total_cost = sum((trade.fee_bps + trade.slippage_bps) / 10_000 for trade in ordered)
        # Annualize by observed trade frequency, not bar frequency.
        # Using bar frequency (35,040/year for M15) with per-trade returns inflates
        # Sharpe by sqrt(bars_per_year / trades_per_year) — ~9-10x for these strategies.
        if len(ordered) >= 2:
            span_years = (ordered[-1].closed_at - ordered[0].closed_at).total_seconds() / (365.25 * 86400)
            periods_per_year = max(1.0, len(ordered) / span_years) if span_years > 0 else float(len(ordered))
        else:
            periods_per_year = 1.0
        hold_hours = [max(0.0, (trade.closed_at - trade.opened_at).total_seconds() / 3600.0) for trade in ordered]
        ladder_hits: dict[str, int] = {}
        for trade in ordered:
            if trade.exit_reason.startswith("exit_ladder_"):
                ladder_hits[trade.exit_reason] = ladder_hits.get(trade.exit_reason, 0) + 1
        return ReplayMetrics(
            strategy_key=strategy_key,
            entry_timeframe=entry_timeframe,
            total_trades=len(ordered),
            signal_count=signal_count,
            win_rate=win_rate([Decimal(str(value)) for value in net_returns]),
            average_win=mean(wins) if wins else 0.0,
            average_loss=mean(losses) if losses else 0.0,
            average_r=mean([trade.r_multiple for trade in ordered]) if ordered else 0.0,
            average_hold_hours=mean(hold_hours) if hold_hours else 0.0,
            ladder_level_hits=ladder_hits,
            gross_return=sum(gross_returns),
            net_return=sum(net_returns),
            net_expectancy=mean(net_returns) if net_returns else 0.0,
            total_fee_bps=sum(trade.fee_bps for trade in ordered),
            total_slippage_bps=sum(trade.slippage_bps for trade in ordered),
            cost_share_of_gross_profit=total_cost / gross_profit if gross_profit > 0 else None,
            sharpe=annualized_sharpe(net_returns, periods_per_year=periods_per_year),
            profit_factor=profit_factor([Decimal(str(value)) for value in net_returns]),
            max_drawdown=max_drawdown_from_pnls(
                [Decimal(str(value)) for value in net_returns], initial_equity=Decimal("1")
            ),
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            data_issues=list(issues),
            trades=ordered,
            exit_mode=self.exit_mode,
        )

    def _metrics_for_period(self, metrics: ReplayMetrics, *, start_at: datetime, end_at: datetime) -> ReplayMetrics:
        return self._metrics(
            strategy_key=metrics.strategy_key,
            trades=(trade for trade in metrics.trades if start_at <= trade.closed_at <= end_at),
            signal_count=sum(1 for trade in metrics.trades if start_at <= trade.opened_at <= end_at),
            issues=metrics.data_issues,
            evaluation_start=start_at,
            evaluation_end=end_at,
            entry_timeframe=metrics.entry_timeframe,
        )

    def _walk_forward(
        self,
        *,
        baseline: ReplayMetrics,
        candidate: ReplayMetrics,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[WalkForwardWindow, ...]:
        if self.walk_forward_windows <= 0 or end_at <= start_at:
            return ()
        width = (end_at - start_at) / self.walk_forward_windows
        windows: list[WalkForwardWindow] = []
        for index in range(self.walk_forward_windows):
            window_start = start_at + width * index
            window_end = end_at if index == self.walk_forward_windows - 1 else start_at + width * (index + 1)
            windows.append(
                WalkForwardWindow(
                    window_id=f"wf_{index + 1}",
                    start_at=window_start,
                    end_at=window_end,
                    baseline=self._metrics_for_period(baseline, start_at=window_start, end_at=window_end),
                    candidate=self._metrics_for_period(candidate, start_at=window_start, end_at=window_end),
                )
            )
        return tuple(windows)

    def _promotion_decision(self, *, baseline_oos: ReplayMetrics, candidate_oos: ReplayMetrics) -> PromotionDecision:
        actual_improvement = candidate_oos.net_expectancy - baseline_oos.net_expectancy
        required_improvement = max(abs(baseline_oos.net_expectancy) * 0.20, 0.0001)
        failed: list[str] = []
        if candidate_oos.net_expectancy <= 0:
            failed.append("oos_net_expectancy_non_positive")
        if actual_improvement < required_improvement:
            failed.append("oos_improvement_below_20_percent")
        if (
            candidate_oos.sharpe <= default_policy.min_sharpe
            or candidate_oos.profit_factor < default_policy.min_profit_factor
            or candidate_oos.max_drawdown >= default_policy.max_drawdown
            or candidate_oos.net_expectancy <= default_policy.min_expectancy
        ):
            failed.append("validation_thresholds")
        if candidate_oos.data_issues:
            failed.append("data_issues_present")
        return PromotionDecision(
            allowed=not failed,
            failed_reasons=tuple(failed),
            baseline_oos_net_expectancy=baseline_oos.net_expectancy,
            candidate_oos_net_expectancy=candidate_oos.net_expectancy,
            required_relative_improvement=required_improvement,
            actual_improvement=actual_improvement,
        )

    def _shared_range(
        self,
        *,
        view: HistoricalMarketDataView,
        strategies: tuple[StrategyContract, StrategyContract],
        symbols: Iterable[str],
    ) -> tuple[datetime, datetime]:
        starts: list[datetime] = []
        ends: list[datetime] = []
        for symbol in symbols:
            for strategy in strategies:
                for timeframe in self._required_timeframes(strategy):
                    bars = view.bars(symbol=symbol, timeframe=timeframe)
                    if len(bars) < self.warmup_bars:
                        raise ValueError(f"{symbol} {timeframe} has fewer than {self.warmup_bars} bars")
                    starts.append(bars[self.warmup_bars - 1].timestamp)
                    ends.append(bars[-1].timestamp)
        return max(starts), min(ends)

    def _warmup_start(
        self,
        *,
        view: HistoricalMarketDataView,
        symbol: str,
        timeframes: set[str],
    ) -> datetime | None:
        starts = []
        for timeframe in timeframes:
            bars = view.bars(symbol=symbol, timeframe=timeframe)
            if len(bars) < self.warmup_bars:
                return None
            starts.append(bars[self.warmup_bars - 1].timestamp)
        return max(starts)

    def _split_at(self, *, start_at: datetime, end_at: datetime) -> datetime:
        return start_at + (end_at - start_at) * (1 - self.oos_fraction)

    @staticmethod
    def _offline_strategy(strategy: StrategyContract) -> StrategyContract:
        entry_rules = {
            **strategy.rules.entry_rules,
            "market_intelligence_enabled": False,
        }
        return strategy.model_copy(
            update={
                "rules": StrategyRules(
                    entry_rules=entry_rules,
                    exit_rules=strategy.rules.exit_rules,
                    stoploss_rules=strategy.rules.stoploss_rules,
                    takeprofit_rules=strategy.rules.takeprofit_rules,
                    position_rules=strategy.rules.position_rules,
                )
            }
        )

    @staticmethod
    def _entry_timeframe(strategy: StrategyContract) -> str:
        return str(strategy.rules.entry_rules.get("entry_timeframe") or strategy.timeframe.value)

    @classmethod
    def _required_timeframes(cls, strategy: StrategyContract) -> set[str]:
        entry_rules = strategy.rules.entry_rules
        timeframes = {cls._entry_timeframe(strategy)}
        for key in ("state_timeframe", "direction_timeframe"):
            value = entry_rules.get(key)
            if value:
                timeframes.add(str(value))
        return timeframes

    @staticmethod
    def _round_trip_costs(*, strategy: StrategyContract, symbol: str) -> tuple[float, float]:
        rules = strategy.rules.entry_rules
        is_core = symbol.replace(":USDT", "") in {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
        fee = float(rules.get("core_fee_bps" if is_core else "standard_fee_bps", rules.get("fee_bps", 8.0)))
        slippage = float(
            rules.get("core_slippage_bps" if is_core else "standard_slippage_bps", rules.get("slippage_bps", 6.0))
        )
        return 2 * fee, 2 * slippage


def _timeframe_hours(timeframe: str) -> float:
    values = {"1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
    return values.get(timeframe, 1.0)


def _replay_symbol_worker(
    payload: tuple[
        MarketData,
        StrategyContract,
        str,
        str,
        set[str],
        datetime | None,
        datetime | None,
        int,
        float,
        int,
        str,
    ],
) -> tuple[list[ReplayTrade], int, list[str]]:
    (
        market_data,
        strategy,
        symbol,
        entry_timeframe,
        required_timeframes,
        start_at,
        end_at,
        warmup_bars,
        oos_fraction,
        walk_forward_windows,
        exit_mode,
    ) = payload
    service = TechnicalStrategyValidationService(
        warmup_bars=warmup_bars,
        oos_fraction=oos_fraction,
        walk_forward_windows=walk_forward_windows,
        max_workers=1,
        exit_mode=exit_mode,
    )
    return service._replay_symbol_data(
        market_data=market_data,
        strategy=strategy,
        symbol=symbol,
        entry_timeframe=entry_timeframe,
        required_timeframes=required_timeframes,
        start_at=start_at,
        end_at=end_at,
    )
