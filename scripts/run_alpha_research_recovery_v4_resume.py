"""Research-only external-context overlay for the corrected V4 baseline.

The runner deliberately consumes only the immutable 281-row Champion research
ledger recovered by V4.1. It never creates a trade, changes strategy geometry,
touches runtime code, or reads the sealed final holdout window.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BASELINE_ROOT = Path("artifacts/alpha_research_recovery_v4_1")
DEFAULT_OUTPUT = Path("artifacts/alpha_research_recovery_v4_resume")
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
BASELINE_EXPECTED = {
    "trades": 281,
    "btc_trades": 132,
    "eth_trades": 149,
    "profit_factor": 1.1576630479094718,
    "expectancy": 0.001512451049147131,
    "max_drawdown": 0.18736432836022304,
}
ALT_SYMBOLS = ("BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "TRXUSDT")
SPOT_SYMBOLS = ALT_SYMBOLS + ("BTCUSDT", "ETHUSDT")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def _json_hash(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode())


def parse_dt(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _epoch_datetime(raw: str) -> datetime:
    """Binance archives contain both millisecond and microsecond epochs."""
    epoch = int(raw)
    divisor = 1_000_000 if epoch >= 10**15 else 1_000
    return datetime.fromtimestamp(epoch / divisor, tz=UTC)


def load_parquet_rows(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        import sys

        sys.path.insert(0, str(Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"))
        import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist()


def _metrics(rows: Iterable[dict[str, Any]]) -> dict[str, float | int]:
    returns = [float(row["net_r"]) for row in rows]
    wins = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    equity = peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
    return {
        "trades": len(returns),
        "profit_factor": wins / losses if losses else (9.99 if wins else 0.0),
        "expectancy": statistics.fmean(returns) if returns else 0.0,
        "max_drawdown": max_dd,
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        "net_return": sum(returns),
    }


def _event_id_hash(rows: Iterable[dict[str, Any]]) -> str:
    keys = sorted(
        f"{row.get('symbol','')}|{row.get('direction','')}|{row.get('signal_time','')}|{row.get('proposal_id','')}"
        for row in rows
    )
    return sha256_bytes("\n".join(keys).encode())


def baseline_lock(*, baseline_root: Path = BASELINE_ROOT) -> dict[str, Any]:
    baseline = json.loads((baseline_root / "BASELINE.json").read_text(encoding="utf-8"))
    rows = load_parquet_rows(baseline_root / "BASELINE_EVENT_LEDGER.parquet")
    actual = _metrics(rows)
    btc = sum(row.get("symbol") == "BTC/USDT" for row in rows)
    eth = sum(row.get("symbol") == "ETH/USDT" for row in rows)
    expected = baseline.get("metrics", BASELINE_EXPECTED)
    deltas = {
        "profit_factor": abs(float(actual["profit_factor"]) - float(expected["profit_factor"])),
        "expectancy": abs(float(actual["expectancy"]) - float(expected["expectancy"])),
        "max_drawdown": abs(float(actual["max_drawdown"]) - float(expected["max_drawdown"])),
    }
    locked = (
        len(rows) == BASELINE_EXPECTED["trades"]
        and btc == BASELINE_EXPECTED["btc_trades"]
        and eth == BASELINE_EXPECTED["eth_trades"]
        and deltas["profit_factor"] <= 1e-6
        and deltas["expectancy"] <= 1e-9
        and deltas["max_drawdown"] <= 1e-6
        and all(parse_dt(row["signal_time"]) < FINAL_HOLDOUT_START for row in rows)
    )
    historical = json.loads((baseline_root / "HISTORICAL_BASELINE_PROVENANCE.json").read_text(encoding="utf-8"))
    split_plan = historical.get("window_definitions_rebuilt", [])
    replay_contract = {
        "evaluator_path": historical.get("evaluation_path", "proposal_pipeline -> ProposalReplayRunner"),
        "scope": historical.get("scope", "Champion Research OOS aggregate"),
        "purge": "24h",
        "overlay_semantics": "KEEP or REJECT existing baseline events only",
    }
    lock = {
        "status": "BASELINE_LOCKED" if locked else "BLOCKED_BASELINE_REPRODUCTION",
        "metrics": {**actual, "btc_trades": btc, "eth_trades": eth},
        "expected": BASELINE_EXPECTED,
        "deltas": deltas,
        "final_holdout_accessed": False,
        "hashes": {
            "event_id_hash": _event_id_hash(rows),
            "database_hash": historical.get("database_hash"),
            "strategy_hash": sha256_file(Path("services/strategy_library/candidates/volatility_expansion_v1.py")),
            "config_hash": _json_hash(historical.get("parameters", {}).get("current_default_config", {})),
            "split_hash": _json_hash(split_plan),
            "window_hash": _json_hash(split_plan),
            "replay_contract_hash": _json_hash(replay_contract),
        },
        "replay_contract": replay_contract,
        "ledger": str(baseline_root / "BASELINE_EVENT_LEDGER.parquet"),
    }
    return {"lock": lock, "rows": rows}


def _month_range(start: datetime, end: datetime) -> list[tuple[int, int]]:
    current = datetime(start.year, start.month, 1, tzinfo=UTC)
    result: list[tuple[int, int]] = []
    while current < end:
        result.append((current.year, current.month))
        current = datetime(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1, tzinfo=UTC)
    return result


def _download(url: str, *, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "alpha-research-v4-resume/1.0"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.75 * (attempt + 1))
    raise last_error if last_error is not None else OSError("download failed")


def fetch_binance_spot(*, output: Path, start: datetime, end: datetime) -> dict[str, Any]:
    cache = output / "source_cache" / "binance_spot_1h"
    cache.mkdir(parents=True, exist_ok=True)
    closes: dict[str, dict[datetime, float]] = defaultdict(dict)
    audit: dict[str, Any] = {"source": "Binance Vision spot monthly klines", "symbols": {}, "status": "PASS"}
    jobs = [(symbol, year, month) for symbol in SPOT_SYMBOLS for year, month in _month_range(start - timedelta(days=35), end)]

    def fetch_one(job: tuple[str, int, int]) -> tuple[str, str, bytes | None, str | None]:
        symbol, year, month = job
        name = f"{symbol}-1h-{year:04d}-{month:02d}.zip"
        path = cache / name
        if path.is_file():
            return symbol, name, path.read_bytes(), None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return symbol, name, _download(
                    f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{urllib.parse.quote(name)}"
                ), None
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
        return symbol, name, None, type(last_error).__name__ if last_error else "UNKNOWN"

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fetch_one, job) for job in jobs]
        for future in as_completed(futures):
            symbol, name, blob, error = future.result()
            if blob is None:
                audit.setdefault("symbols", {}).setdefault(symbol, {}).setdefault("missing_archives", []).append(f"{name}:{error}")
                continue
            path = cache / name
            try:
                if not path.is_file():
                    path.write_bytes(blob)
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    member = archive.namelist()[0]
                    for raw in archive.read(member).decode("utf-8").splitlines():
                        fields = next(csv.reader([raw]))
                        if not fields or not fields[0].isdigit():
                            continue
                        timestamp = _epoch_datetime(fields[0])
                        if start - timedelta(days=35) <= timestamp <= end:
                            closes[symbol][timestamp] = float(fields[4])
                audit.setdefault("symbols", {}).setdefault(symbol, {}).setdefault("downloaded", 0)
                audit["symbols"][symbol]["downloaded"] += 1
            except (OSError, zipfile.BadZipFile, ValueError) as exc:
                audit.setdefault("symbols", {}).setdefault(symbol, {}).setdefault("missing_archives", []).append(f"{name}:{type(exc).__name__}")
    for symbol in SPOT_SYMBOLS:
        details = audit.setdefault("symbols", {}).setdefault(symbol, {})
        downloaded = int(details.get("downloaded", 0))
        missing = details.get("missing_archives", [])
        observed = len(closes[symbol])
        audit["symbols"][symbol] = {"archives": downloaded, "missing_archives": missing, "rows": observed}
        if missing or observed == 0:
            audit["status"] = "BLOCKED_SOURCE_DATA"
    (output / "H1_BINANCE_SPOT_AUDIT.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return {"closes": closes, "audit": audit}


def _asof_hour(closes: dict[datetime, float], timestamp: datetime) -> datetime | None:
    key = timestamp.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return key if key in closes else None


def build_breadth_features(closes: dict[str, dict[datetime, float]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous_breadth: float | None = None
    for row in sorted(rows, key=lambda item: parse_dt(item["signal_time"])):
        signal = parse_dt(row["signal_time"])
        hour = signal.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        returns_1h: list[float] = []
        returns_4h: list[float] = []
        for symbol in ALT_SYMBOLS:
            series = closes.get(symbol, {})
            if hour not in series or hour - timedelta(hours=1) not in series:
                continue
            returns_1h.append(series[hour] / series[hour - timedelta(hours=1)] - 1.0)
            if hour - timedelta(hours=4) in series:
                returns_4h.append(series[hour] / series[hour - timedelta(hours=4)] - 1.0)
        btc = closes.get("BTCUSDT", {})
        eth = closes.get("ETHUSDT", {})
        btc_return = btc[hour] / btc[hour - timedelta(hours=1)] - 1.0 if hour in btc and hour - timedelta(hours=1) in btc else None
        eth_return = eth[hour] / eth[hour - timedelta(hours=1)] - 1.0 if hour in eth and hour - timedelta(hours=1) in eth else None
        breadth = sum(value > 0 for value in returns_1h) / len(returns_1h) if returns_1h else None
        result.append(
            {
                **row,
                "asof_hour": hour.isoformat(),
                "breadth_1h": breadth,
                "breadth_4h": sum(value > 0 for value in returns_4h) / len(returns_4h) if returns_4h else None,
                "median_alt_return_1h": statistics.median(returns_1h) if returns_1h else None,
                "median_alt_return_4h": statistics.median(returns_4h) if returns_4h else None,
                "cross_section_dispersion": statistics.pstdev(returns_1h) if len(returns_1h) > 1 else 0.0 if returns_1h else None,
                "alt_minus_btc_return": statistics.median(returns_1h) - btc_return if returns_1h and btc_return is not None else None,
                "alt_minus_eth_return": statistics.median(returns_1h) - eth_return if returns_1h and eth_return is not None else None,
                "breadth_acceleration": breadth - previous_breadth if breadth is not None and previous_breadth is not None else None,
            }
        )
        if breadth is not None:
            previous_breadth = breadth
    return result


def _quantile_bucket(values: list[float], value: float | None) -> str | None:
    if value is None or not values:
        return None
    ordered = sorted(values)
    rank = sum(item <= value for item in ordered) / len(ordered)
    return f"Q{min(4, max(1, math.ceil(rank * 4)))}"


def attribution(rows: list[dict[str, Any]], feature: str, *, direction_adjusted: bool = False) -> dict[str, Any]:
    values = [float(row[feature]) * (1.0 if row["direction"] == "long" else -1.0) if direction_adjusted else float(row[feature]) for row in rows if row.get(feature) is not None]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(feature)
        adjusted = float(value) * (1.0 if row["direction"] == "long" else -1.0) if value is not None and direction_adjusted else float(value) if value is not None else None
        bucket = _quantile_bucket(values, adjusted) if adjusted is not None else None
        if bucket:
            buckets[bucket].append(dict(row))
    return {bucket: _metrics(items) for bucket, items in sorted(buckets.items())}


def _source_summary(rows: list[dict[str, Any]], *, source: str, feature: str) -> dict[str, Any]:
    valid = [row for row in rows if row.get(feature) is not None]
    attribution_rows = attribution(valid, feature, direction_adjusted=True)
    baseline = _metrics(valid)
    ordered = [attribution_rows[key]["expectancy"] for key in ("Q1", "Q2", "Q3", "Q4") if key in attribution_rows]
    monotonic = len(ordered) >= 3 and (all(a <= b for a, b in zip(ordered, ordered[1:], strict=False)) or all(a >= b for a, b in zip(ordered, ordered[1:], strict=False)))
    best_bucket = max(attribution_rows, key=lambda key: float(attribution_rows[key]["expectancy"])) if attribution_rows else None
    quantiles = [float(item[feature]) * (1.0 if item["direction"] == "long" else -1.0) for item in valid]
    high_buckets = {"Q3", "Q4"} if not ordered or ordered[-1] >= ordered[0] else {"Q1", "Q2"}
    filtered = [row for row in valid if _quantile_bucket(quantiles, float(row[feature]) * (1.0 if row["direction"] == "long" else -1.0)) in high_buckets]
    filtered_metrics = _metrics(filtered)
    research_pass = monotonic and len(filtered) >= 80 and filtered_metrics["profit_factor"] > BASELINE_EXPECTED["profit_factor"] and filtered_metrics["expectancy"] > BASELINE_EXPECTED["expectancy"]
    return {
        "source": source,
        "feature": feature,
        "available_trades": len(valid),
        "missing_trades": len(rows) - len(valid),
        "baseline_direction_adjusted": baseline,
        "per_symbol": {symbol: _metrics([row for row in valid if row.get("symbol") == symbol]) for symbol in ("BTC/USDT", "ETH/USDT")},
        "quartile_attribution": attribution_rows,
        "monotonic_relationship": monotonic,
        "candidate_overlay": {"rule": "Q3_or_Q4", "selected_bucket": best_bucket if monotonic else None, "filtered_metrics": filtered_metrics},
        "research_gate": "PASS" if research_pass else "FAIL",
        "validation": "NOT_RUN",
    }


def fetch_dvol(*, output: Path, start: datetime, end: datetime) -> dict[str, Any]:
    audit: dict[str, Any] = {"source": "Deribit DVOL", "symbols": {}, "status": "PASS"}
    values: dict[str, dict[datetime, float]] = {}
    for currency in ("BTC", "ETH"):
        try:
            series: dict[datetime, float] = {}
            cursor = start
            while cursor < end:
                window_end = min(cursor + timedelta(days=30), end)
                params = urllib.parse.urlencode({"currency": currency, "start_timestamp": int(cursor.timestamp() * 1000), "end_timestamp": int(window_end.timestamp() * 1000), "resolution": 3600})
                url = f"https://www.deribit.com/api/v2/public/get_volatility_index_data?{params}"
                payload = json.loads(_download(url).decode("utf-8"))
                for item in payload.get("result", {}).get("data", []):
                    timestamp = datetime.fromtimestamp(item[0] / 1000, tz=UTC)
                    if start <= timestamp < end:
                        series[timestamp] = float(item[4])
                cursor = window_end
            values[currency] = series
            audit["symbols"][currency] = {"rows": len(series), "first": min(series).isoformat() if series else None, "last": max(series).isoformat() if series else None}
            if not series:
                audit["status"] = "BLOCKED_SOURCE_DATA"
        except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            values[currency] = {}
            audit["symbols"][currency] = {"rows": 0, "error": type(exc).__name__}
            audit["status"] = "BLOCKED_SOURCE_DATA"
    (output / "H2_DVOL_AUDIT.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return {"values": values, "audit": audit}


def fetch_fear_greed(*, output: Path) -> dict[str, Any]:
    audit: dict[str, Any] = {"source": "Alternative.me Fear & Greed", "status": "PASS", "publication_policy": "D value usable from D+1 UTC day"}
    try:
        payload = json.loads(_download("https://api.alternative.me/fng/?limit=0&format=json").decode("utf-8"))
        raw = payload.get("data", [])
        values: dict[datetime, float] = {}
        for item in raw:
            day = datetime.fromtimestamp(int(item["timestamp"]), tz=UTC).date()
            values[datetime(day.year, day.month, day.day, tzinfo=UTC)] = float(item["value"])
        audit["rows"] = len(values)
        audit["first"] = min(values).isoformat() if values else None
        audit["last"] = max(values).isoformat() if values else None
        if not values:
            audit["status"] = "BLOCKED_SOURCE_DATA"
    except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        values = {}
        audit["status"] = "BLOCKED_SOURCE_DATA"
        audit["error"] = type(exc).__name__
    (output / "H3_FEARGREED_AUDIT.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return {"values": values, "audit": audit}


def build_report(*, output: Path = DEFAULT_OUTPUT, baseline_root: Path = BASELINE_ROOT) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    locked = baseline_lock(baseline_root=baseline_root)
    lock = locked["lock"]
    (output / "BASELINE_LOCK.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    if lock["status"] != "BASELINE_LOCKED":
        report = {"status": "BLOCKED_BASELINE_REPRODUCTION", "baseline_lock": lock, "final_holdout_accessed": False, "runtime_modified": False, "production_authority": "NOT_GRANTED"}
        (output / "FINAL_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    rows = locked["rows"]
    start = min(parse_dt(row["signal_time"]) for row in rows)
    end = max(parse_dt(row["signal_time"]) for row in rows) + timedelta(days=1)
    h1 = fetch_binance_spot(output=output, start=start, end=end)
    breadth_rows = build_breadth_features(h1["closes"], rows)
    h1_summary = _source_summary(breadth_rows, source="H1_ALTCOIN_MARKET_BREADTH_V1", feature="breadth_1h")
    h1_summary["additional_features"] = {
        feature: attribution(breadth_rows, feature, direction_adjusted=True)
        for feature in ("breadth_4h", "median_alt_return_1h", "median_alt_return_4h", "cross_section_dispersion", "alt_minus_btc_return", "alt_minus_eth_return", "breadth_acceleration")
    }
    h2 = fetch_dvol(output=output, start=start - timedelta(hours=2), end=end)
    dvol_rows: list[dict[str, Any]] = []
    for row in rows:
        series = h2["values"].get("BTC" if row["symbol"] == "BTC/USDT" else "ETH", {})
        hour = parse_dt(row["signal_time"]).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        item = dict(row)
        item["dvol_level"] = series.get(hour)
        for offset, name in ((1, "dvol_change_1h"), (6, "dvol_change_6h"), (24, "dvol_change_24h")):
            previous = series.get(hour - timedelta(hours=offset))
            item[name] = (series[hour] - previous) if hour in series and previous is not None else None
        history = [value for timestamp, value in series.items() if hour - timedelta(days=30) <= timestamp <= hour]
        item["dvol_30d_percentile"] = sum(value <= series[hour] for value in history) / len(history) if history and hour in series else None
        item["dvol_rising"] = series.get(hour, 0.0) > series.get(hour - timedelta(hours=1), series.get(hour, 0.0)) if hour in series else None
        dvol_rows.append(item)
    h2_summary = _source_summary(dvol_rows, source="H2_DERIBIT_DVOL_V1", feature="dvol_level")
    h2_summary["additional_features"] = {
        feature: attribution(dvol_rows, feature, direction_adjusted=True)
        for feature in ("dvol_change_1h", "dvol_change_6h", "dvol_change_24h", "dvol_30d_percentile")
    }
    h2_summary["source_status"] = "BLOCKED_SOURCE_DATA" if h2["audit"]["status"] != "PASS" or h2_summary["missing_trades"] else "PASS"
    h3 = fetch_fear_greed(output=output)
    fg_rows: list[dict[str, Any]] = []
    for row in rows:
        day = parse_dt(row["signal_time"]).date() - timedelta(days=1)
        item = dict(row)
        day_key = datetime(day.year, day.month, day.day, tzinfo=UTC)
        values = h3["values"]
        item["fear_greed_level"] = values.get(day_key)
        for offset, name in ((1, "fear_greed_change_1d"), (3, "fear_greed_change_3d"), (7, "fear_greed_change_7d")):
            previous_day = day_key - timedelta(days=offset)
            item[name] = values[day_key] - values[previous_day] if day_key in values and previous_day in values else None
        change = item.get("fear_greed_change_1d")
        item["fear_greed_trend"] = "IMPROVING" if change is not None and change > 0 else "DETERIORATING" if change is not None and change < 0 else "FLAT" if change is not None else None
        fg_rows.append(item)
    h3_summary = _source_summary(fg_rows, source="H3_FEAR_GREED_CONTEXT_V1", feature="fear_greed_level")
    h3_summary["additional_features"] = {
        feature: attribution(fg_rows, feature, direction_adjusted=True)
        for feature in ("fear_greed_change_1d", "fear_greed_change_3d", "fear_greed_change_7d")
    }
    h3_summary["source_status"] = "BLOCKED_SOURCE_DATA" if h3["audit"]["status"] != "PASS" or h3_summary["missing_trades"] else "PASS"
    h1_summary["source_status"] = "BLOCKED_SOURCE_DATA" if h1["audit"]["status"] != "PASS" or h1_summary["missing_trades"] else "PASS"
    source_statuses = [h1_summary["source_status"], h2_summary["source_status"], h3_summary["source_status"]]
    if "BLOCKED_SOURCE_DATA" in source_statuses:
        status = "BLOCKED_SOURCE_DATA"
    elif any(summary["research_gate"] == "PASS" for summary in (h1_summary, h2_summary, h3_summary)):
        status = "EXTERNAL_CONTEXT_ALPHA_SURVIVOR"
    else:
        status = "EXTERNAL_CONTEXT_ALPHA_BATCH_EXHAUSTED"
    report = {
        "status": status,
        "baseline": lock,
        "H1_ALTCOIN_MARKET_BREADTH_V1": h1_summary,
        "H2_DERIBIT_DVOL_V1": h2_summary,
        "H3_FEAR_GREED_CONTEXT_V1": h3_summary,
        "combined": {"status": "NOT_RUN", "reason": "requires at least two independent Research + Validation PASS sources"},
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production_authority": "NOT_GRANTED",
        "runtime_monitoring": "READ_ONLY_NOT_COLLECTED",
    }
    (output / "FINAL_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--baseline-root", type=Path, default=BASELINE_ROOT)
    args = parser.parse_args()
    report = build_report(output=args.output_dir, baseline_root=args.baseline_root)
    print(json.dumps({"status": report["status"], "baseline": report["baseline"]["metrics"] if "baseline" in report else report["baseline_lock"]["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
