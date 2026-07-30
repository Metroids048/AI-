"""Freeze the pre-refactor BTC/ETH strategy baseline without changing runtime.

The artifact is intentionally fail-closed.  It first proves that BTC/ETH have
continuous 1m/5m/15m/1h/4h history for the required 42 calendar months.  When
coverage is insufficient it still writes an immutable evidence package, but it
does not run a partial replay or inspect Final Holdout results.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from sqlalchemy import create_engine, select

from services.data.repository import DataRepository, ohlcv_bars
from services.execution.decision_pipeline import DecisionPipeline, DecisionPipelineResult
from services.execution.signal_edge_stats import strategy_rules_hash
from services.strategy_library.candidates.registry import get_candidate
from services.validation.metrics import bootstrap_ci
from services.validation.technical_replay import (
    EXIT_MODE_FIXED_2R,
    MarketData,
    ReplayMetrics,
    TechnicalStrategyValidationService,
)
from shared.models import OHLCVBar, StrategyContract, StrategyRules, Timeframe

SYMBOLS = ("BTC/USDT", "ETH/USDT")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
}
ACTIVE_MANIFEST = Path("docs/evidence/active-manifests/auto_paper_mature_templates.json")
DEFAULT_OUTPUT = Path("artifacts/strategy_refactor/baseline")
SOURCE_EXTENSIONS = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ps1",
    ".css",
    ".html",
    ".sql",
    ".md",
}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "artifacts",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "logs",
    "site-packages",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@dataclass(frozen=True)
class BarCoverage:
    """Coverage and content identity for one symbol/timeframe series."""

    symbol: str
    timeframe: str
    first_open: datetime | None
    last_open: datetime | None
    latest_closed_at: datetime | None
    bar_count: int
    gap_count: int
    missing_bar_count: int
    largest_gap_seconds: int
    data_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "first_open": _iso(self.first_open),
            "last_open": _iso(self.last_open),
            "latest_closed_at": _iso(self.latest_closed_at),
            "bar_count": self.bar_count,
            "gap_count": self.gap_count,
            "missing_bar_count": self.missing_bar_count,
            "largest_gap_seconds": self.largest_gap_seconds,
            "data_hash": self.data_hash,
        }


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value is not None else None


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_number(value: Any) -> str:
    if value is None:
        return "null"
    decimal = Decimal(str(value))
    return format(decimal.normalize(), "f")


def subtract_calendar_months(value: datetime, months: int) -> datetime:
    """Subtract whole calendar months while clamping the day when required."""

    if months < 0:
        raise ValueError("months must be non-negative")
    zero_based = value.year * 12 + (value.month - 1) - months
    year, month_index = divmod(zero_based, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _floor_4h(value: datetime) -> datetime:
    aware = _aware(value)
    return aware.replace(hour=(aware.hour // 4) * 4, minute=0, second=0, microsecond=0)


def latest_common_closed_4h_boundary(coverages: list[BarCoverage]) -> datetime:
    """Return the latest 4h boundary available in every required series."""

    by_key = {(item.symbol, item.timeframe): item for item in coverages}
    required = [(symbol, timeframe) for symbol in SYMBOLS for timeframe in TIMEFRAMES]
    missing = [f"{symbol}|{timeframe}" for symbol, timeframe in required if (symbol, timeframe) not in by_key]
    empty = [
        f"{symbol}|{timeframe}"
        for symbol, timeframe in required
        if (symbol, timeframe) in by_key and by_key[(symbol, timeframe)].latest_closed_at is None
    ]
    if missing or empty:
        raise ValueError(f"required series unavailable: {', '.join([*missing, *empty])}")
    slowest = min(cast(datetime, by_key[(symbol, timeframe)].latest_closed_at) for symbol, timeframe in required)
    return _floor_4h(slowest)


def assess_data_coverage(
    coverages: list[BarCoverage],
    *,
    cutoff: datetime | None,
) -> dict[str, Any]:
    """Apply the 12m train + eight 3m OOS + 6m holdout coverage gate."""

    cutoff = _aware(cutoff) if cutoff is not None else None
    final_holdout_start = subtract_calendar_months(cutoff, 6) if cutoff is not None else None
    required_history_start = (
        subtract_calendar_months(final_holdout_start, 36) if final_holdout_start is not None else None
    )
    by_key = {(item.symbol, item.timeframe): item for item in coverages}
    series: dict[str, dict[str, Any]] = {}
    sufficient = True
    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            key = f"{symbol}|{timeframe}"
            item = by_key.get((symbol, timeframe))
            starts_before = bool(
                item is not None
                and item.first_open is not None
                and required_history_start is not None
                and _aware(item.first_open) <= required_history_start
            )
            reaches_cutoff = bool(
                item is not None
                and item.latest_closed_at is not None
                and cutoff is not None
                and _aware(item.latest_closed_at) >= cutoff
            )
            present = bool(item is not None and item.bar_count > 0)
            continuous = bool(present and item is not None and item.gap_count == 0)
            passed = starts_before and reaches_cutoff and continuous and present
            sufficient = sufficient and passed
            series[key] = {
                "present": present,
                "starts_before_required_history": starts_before,
                "reaches_cutoff": reaches_cutoff,
                "continuous": continuous,
                "passed": passed,
                "coverage": item.as_dict() if item is not None else None,
            }
    return {
        "status": "SUFFICIENT" if sufficient else "DATA_COVERAGE_INSUFFICIENT",
        "cutoff": _iso(cutoff),
        "final_holdout_start": _iso(final_holdout_start),
        "required_history_start": _iso(required_history_start),
        "required_total_months": 42,
        "training_months": 12,
        "oos_window_months": 3,
        "oos_window_count": 8,
        "final_holdout_months": 6,
        "series": series,
    }


def _iter_source_files(root: Path) -> list[Path]:
    files = {
        path
        for path in root.rglob("*")
        if (
            path.is_file()
            and path.suffix.lower() in SOURCE_EXTENSIONS
            and not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
        )
    }
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def source_tree_manifest(root: Path) -> dict[str, Any]:
    """Hash current files by normalized path and content, independent of Git."""

    root = root.resolve()
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_bytes(path.read_bytes()),
        }
        for path in _iter_source_files(root)
    ]
    combined = "".join(f"{item['path']}\t{item['sha256']}\n" for item in entries).encode()
    return {
        "algorithm": "sha256(sorted(relative_path<TAB>content_sha256<LF>))",
        "scope": "all source/config/document files under source_root",
        "included_extensions": sorted(SOURCE_EXTENSIONS),
        "excluded_path_parts": sorted(EXCLUDED_PARTS),
        "file_count": len(entries),
        "source_tree_hash": _sha256_bytes(combined),
        "files": entries,
    }


def _scan_series(
    database_url: str,
    *,
    symbol: str,
    timeframe: str,
    closed_through: datetime,
) -> BarCoverage:
    delta = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    latest_open = _aware(closed_through) - delta
    digest = hashlib.sha256()
    first_open: datetime | None = None
    previous_open: datetime | None = None
    last_open: datetime | None = None
    bar_count = 0
    gap_count = 0
    missing_bar_count = 0
    largest_gap_seconds = 0
    engine = create_engine(database_url)
    statement = (
        select(
            ohlcv_bars.c.time,
            ohlcv_bars.c.open,
            ohlcv_bars.c.high,
            ohlcv_bars.c.low,
            ohlcv_bars.c.close,
            ohlcv_bars.c.volume,
        )
        .where(
            ohlcv_bars.c.symbol == symbol,
            ohlcv_bars.c.timeframe == timeframe,
            ohlcv_bars.c.time <= latest_open,
        )
        .order_by(ohlcv_bars.c.time)
    )
    try:
        with engine.connect() as connection:
            rows = connection.execution_options(stream_results=True).execute(statement)
            for row in rows:
                opened_at = _aware(row.time)
                if first_open is None:
                    first_open = opened_at
                if previous_open is not None:
                    gap_seconds = int((opened_at - previous_open).total_seconds())
                    expected_seconds = TIMEFRAME_SECONDS[timeframe]
                    if gap_seconds > expected_seconds:
                        gap_count += 1
                        missing_bar_count += max(0, round(gap_seconds / expected_seconds) - 1)
                        largest_gap_seconds = max(largest_gap_seconds, gap_seconds)
                canonical = "\t".join(
                    (
                        opened_at.isoformat(),
                        _canonical_number(row.open),
                        _canonical_number(row.high),
                        _canonical_number(row.low),
                        _canonical_number(row.close),
                        _canonical_number(row.volume),
                    )
                )
                digest.update((canonical + "\n").encode())
                previous_open = opened_at
                last_open = opened_at
                bar_count += 1
    finally:
        engine.dispose()
    return BarCoverage(
        symbol=symbol,
        timeframe=timeframe,
        first_open=first_open,
        last_open=last_open,
        latest_closed_at=last_open + delta if last_open is not None else None,
        bar_count=bar_count,
        gap_count=gap_count,
        missing_bar_count=missing_bar_count,
        largest_gap_seconds=largest_gap_seconds,
        data_hash=digest.hexdigest(),
    )


def _load_coverages(database_url: str, *, closed_through: datetime) -> list[BarCoverage]:
    return [
        _scan_series(
            database_url,
            symbol=symbol,
            timeframe=timeframe,
            closed_through=closed_through,
        )
        for symbol in SYMBOLS
        for timeframe in TIMEFRAMES
    ]


def _active_strategy(source_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = source_root / ACTIVE_MANIFEST
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    candidate_id = str(manifest["candidate_id"])
    candidate = get_candidate(candidate_id)
    config = candidate.get_config()
    rules = StrategyRules(**config)
    payload = {
        "manifest_path": ACTIVE_MANIFEST.as_posix(),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "manifest": manifest,
        "candidate_id": candidate_id,
        "candidate_version": candidate.version,
        "strategy_rules": rules.model_dump(mode="json"),
        "current_rules_hash": strategy_rules_hash(rules),
        "manifest_rules_hash_matches_current_config": (str(manifest.get("rules_hash")) == strategy_rules_hash(rules)),
    }
    return payload, config


def _combined_data_hash(coverages: list[BarCoverage]) -> str:
    lines = "".join(
        f"{item.symbol}|{item.timeframe}\t{item.data_hash}\n"
        for item in sorted(coverages, key=lambda value: (value.symbol, value.timeframe))
    )
    return _sha256_bytes(lines.encode())


def _load_replay_market_data(
    database_url: str,
    *,
    start_at: datetime,
    end_at: datetime,
) -> MarketData:
    market_data: MarketData = {}
    engine = create_engine(database_url)
    try:
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            repository = DataRepository(session)
            for symbol in SYMBOLS:
                market_data[symbol] = {
                    timeframe: cast(
                        list[OHLCVBar | dict[str, Any]],
                        repository.list_ohlcv_bars(
                            symbol=symbol,
                            timeframe=timeframe,
                            start_at=start_at,
                            end_at=end_at - timedelta(seconds=TIMEFRAME_SECONDS[timeframe]),
                        ),
                    )
                    for timeframe in ("15m", "1h", "4h")
                }
    finally:
        engine.dispose()
    return market_data


class _CountingDecisionPipeline:
    def __init__(self, view: Any, terminal_reasons: Counter[str]) -> None:
        self._delegate = DecisionPipeline(data_repo=cast(DataRepository, view))
        self._terminal_reasons = terminal_reasons

    def evaluate(self, **kwargs: Any) -> DecisionPipelineResult:
        result = self._delegate.evaluate(**kwargs)
        self._terminal_reasons[result.reason] += 1
        return result


def _metric_payload(metrics: ReplayMetrics) -> tuple[dict[str, Any], dict[str, Any]]:
    returns = [float(trade.net_return) for trade in metrics.trades]
    if len(metrics.trades) >= 2:
        span_years = (metrics.trades[-1].closed_at - metrics.trades[0].closed_at).total_seconds() / (365.25 * 86400)
        periods_per_year = max(1.0, len(metrics.trades) / span_years) if span_years > 0 else float(len(metrics.trades))
    else:
        periods_per_year = 1.0
    sharpe_ci, expectancy_ci = bootstrap_ci(
        returns,
        periods_per_year=periods_per_year,
        n_resamples=1000,
        confidence=0.90,
        seed=42,
    )
    has_wins = any(value > 0 for value in returns)
    has_losses = any(value < 0 for value in returns)
    average_profit_loss_ratio = (
        metrics.average_win / abs(metrics.average_loss)
        if has_wins and has_losses and metrics.average_loss != 0
        else None
    )
    payload = metrics.as_dict(include_trades=False)
    payload.update(
        {
            "average_profit_loss_ratio": average_profit_loss_ratio,
            "profit_factor_defined": has_wins and has_losses,
            "profit_factor_current_legacy_value": metrics.profit_factor,
            "zero_return_trades_count_in_win_rate_denominator": sum(1 for value in returns if value == 0),
        }
    )
    ci = {
        "method": "legacy_iid_percentile_bootstrap",
        "final_promotion_eligible": False,
        "confidence": 0.90,
        "resamples": 1000,
        "seed": 42,
        "sharpe": list(sharpe_ci),
        "net_expectancy": list(expectancy_ci),
    }
    return payload, ci


def _run_legacy_replay(
    *,
    database_url: str,
    config: dict[str, Any],
    active_strategy: dict[str, Any],
    coverage: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    required_start = datetime.fromisoformat(coverage["required_history_start"])
    holdout_start = datetime.fromisoformat(coverage["final_holdout_start"])
    market_data = _load_replay_market_data(
        database_url,
        start_at=required_start - timedelta(days=15),
        end_at=holdout_start,
    )
    strategy = StrategyContract(
        strategy_id=str(active_strategy["manifest"]["strategy_key"]),
        strategy_key=str(active_strategy["manifest"]["strategy_key"]),
        source="golden_baseline:current_active_manifest",
        core_thesis="Immutable pre-refactor replay; never execution eligible.",
        symbol_scope=list(SYMBOLS),
        timeframe=Timeframe.M15,
        rules=StrategyRules(**config),
    )

    def replay(symbol_data: MarketData) -> tuple[ReplayMetrics, Counter[str]]:
        terminal_reasons: Counter[str] = Counter()
        service = TechnicalStrategyValidationService(
            pipeline_factory=lambda view: _CountingDecisionPipeline(view, terminal_reasons),
            warmup_bars=80,
            max_workers=1,
            exit_mode=EXIT_MODE_FIXED_2R,
        )
        result = service.replay(
            strategy=strategy,
            market_data=symbol_data,
            start_at=required_start,
            end_at=holdout_start,
        )
        return result, terminal_reasons

    metrics_payload: dict[str, Any] = {"status": "AVAILABLE"}
    ci_payload: dict[str, Any] = {}
    for symbol in SYMBOLS:
        symbol_metrics, _ = replay({symbol: market_data[symbol]})
        metrics_payload[symbol], ci_payload[symbol] = _metric_payload(symbol_metrics)
    portfolio_metrics, terminal_reasons = replay(market_data)
    metrics_payload["portfolio"], ci_payload["portfolio"] = _metric_payload(portfolio_metrics)
    trades = [trade.as_dict() for trade in portfolio_metrics.trades]
    funnel_counts = {
        "status": "AVAILABLE",
        "scope": "portfolio replay terminal outcomes",
        "evaluated_decisions": sum(terminal_reasons.values()),
        "terminal_reason_counts": dict(sorted(terminal_reasons.items())),
        "candidate_signal_count": portfolio_metrics.signal_count,
        "note": (
            "The legacy replay exposes terminal DecisionPipeline reasons, not a V2 per-stage "
            "funnel. Counts are frozen as-is and are not reconstructed from future logic."
        ),
    }
    return metrics_payload, trades, funnel_counts, ci_payload


def write_immutable_artifacts(destination: Path, artifacts: dict[str, bytes]) -> None:
    """Atomically create a baseline directory and refuse every overwrite."""

    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"immutable baseline already exists: {destination}")
    for relative_name in artifacts:
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact path must stay below baseline directory: {relative_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".strategy-baseline-", dir=destination.parent))
    try:
        for relative_name, content in artifacts.items():
            target = temporary / relative_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _auxiliary_git_sha(source_root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def generate_golden_baseline(
    *,
    database_url: str,
    output_dir: Path,
    source_root: Path,
    observed_at: datetime,
    auxiliary_git_sha: str | None = None,
) -> dict[str, Any]:
    """Generate and atomically freeze the current legacy baseline evidence."""

    observed_at = _aware(observed_at)
    source_root = source_root.resolve()
    initial_coverages = _load_coverages(database_url, closed_through=observed_at)
    try:
        cutoff = latest_common_closed_4h_boundary(initial_coverages)
    except ValueError:
        cutoff = None
    coverages = _load_coverages(database_url, closed_through=cutoff) if cutoff is not None else initial_coverages
    coverage = assess_data_coverage(coverages, cutoff=cutoff)
    tree = source_tree_manifest(source_root)
    active_strategy, config = _active_strategy(source_root)
    config_hash = _sha256_bytes(_json_bytes(active_strategy))
    data_hash = _combined_data_hash(coverages)
    status = coverage["status"]

    if status == "SUFFICIENT":
        metrics, trades, funnel_counts, current_ci = _run_legacy_replay(
            database_url=database_url,
            config=config,
            active_strategy=active_strategy,
            coverage=coverage,
        )
    else:
        metrics = {
            "status": "UNAVAILABLE",
            "reason": status,
            "BTC/USDT": None,
            "ETH/USDT": None,
            "portfolio": None,
        }
        trades = []
        funnel_counts = {
            "status": "UNAVAILABLE",
            "reason": status,
            "terminal_reason_counts": {},
        }
        current_ci = {
            "status": "UNAVAILABLE",
            "reason": status,
            "method": "legacy_iid_percentile_bootstrap",
            "final_promotion_eligible": False,
        }

    costs = {
        "source": "current active strategy rules",
        "entry_rules": {
            key: config["entry_rules"].get(key)
            for key in (
                "core_fee_bps",
                "core_slippage_bps",
                "standard_fee_bps",
                "standard_slippage_bps",
            )
        },
        "funding": "not modeled by legacy technical replay",
        "spread": "not modeled separately by legacy technical replay",
        "latency": "not modeled by legacy technical replay",
        "partial_fill": "not modeled by fixed_2r legacy replay",
        "exit_model": "fixed_2r",
    }
    manifest = {
        "schema_version": 1,
        "status": status,
        "generated_at": observed_at.isoformat(),
        "observed_at": observed_at.isoformat(),
        "cutoff": _iso(cutoff),
        "final_holdout_start": coverage["final_holdout_start"],
        "final_holdout_end": _iso(cutoff),
        "holdout_results_accessed": False,
        "holdout_policy": (
            "frozen_by_range_and_hash_only; no metrics computed"
            if cutoff is not None
            else "not_frozen; required series unavailable"
        ),
        "auxiliary_git_sha": auxiliary_git_sha or _auxiliary_git_sha(source_root),
        "source_tree_hash": tree["source_tree_hash"],
        "data_hash": data_hash,
        "config_hash": config_hash,
        "active_strategy": {
            "strategy_key": active_strategy["manifest"]["strategy_key"],
            "candidate_id": active_strategy["candidate_id"],
            "candidate_version": active_strategy["candidate_version"],
            "eligible_symbols": active_strategy["manifest"].get("eligible_symbols", []),
            "manifest_rules_hash_matches_current_config": active_strategy["manifest_rules_hash_matches_current_config"],
        },
        "execution_eligible": False,
        "artifact_files": [
            "BASELINE_MANIFEST.json",
            "source_tree_manifest.json",
            "data_manifest.json",
            "config_manifest.json",
            "cost_model.json",
            "metrics.json",
            "current_ci.json",
            "funnel_counts.json",
            "trades.jsonl",
            "README.md",
        ],
    }
    data_manifest = {
        "database_url_redacted": _redact_database_url(database_url),
        "combined_data_hash": data_hash,
        "coverage_gate": coverage,
        "series": [item.as_dict() for item in coverages],
    }
    trades_bytes = b"".join(_json_bytes(trade) for trade in trades)
    readme = (
        "# Immutable Strategy Golden Baseline\n\n"
        f"- Status: `{status}`\n"
        f"- Cutoff: `{_iso(cutoff)}`\n"
        f"- Final Holdout: `{coverage['final_holdout_start']}` to `{_iso(cutoff)}`\n"
        "- Holdout results accessed: `false`\n"
        "- Runtime/execution eligibility: `false`\n\n"
        "`auxiliary_git_sha` is the repository HEAD observed during generation and is "
        "informational only; the path-plus-content `source_tree_hash` is authoritative.\n\n"
        "This directory is created atomically and the generator refuses to overwrite it. "
        "When status is `DATA_COVERAGE_INSUFFICIENT`, empty trades and unavailable metrics "
        "are evidence of the coverage gate; they are not zero-performance claims.\n"
    ).encode()
    write_immutable_artifacts(
        output_dir,
        {
            "BASELINE_MANIFEST.json": _json_bytes(manifest),
            "source_tree_manifest.json": _json_bytes(tree),
            "data_manifest.json": _json_bytes(data_manifest),
            "config_manifest.json": _json_bytes(active_strategy),
            "cost_model.json": _json_bytes(costs),
            "metrics.json": _json_bytes(metrics),
            "current_ci.json": _json_bytes(current_ci),
            "funnel_counts.json": _json_bytes(funnel_counts),
            "trades.jsonl": trades_bytes,
            "README.md": readme,
        },
    )
    return manifest


def _redact_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return f"sqlite:///{Path(database_url.removeprefix('sqlite:///')).name}"
    if "@" in database_url:
        return f"{database_url.split('://', 1)[0]}://<redacted>@{database_url.rsplit('@', 1)[1]}"
    return "<redacted>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default="sqlite:///./.local_paper_console.db")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--observed-at", type=datetime.fromisoformat, default=None)
    args = parser.parse_args()
    result = generate_golden_baseline(
        database_url=args.database_url,
        output_dir=args.output_dir,
        source_root=args.source_root,
        observed_at=args.observed_at or datetime.now(UTC),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
