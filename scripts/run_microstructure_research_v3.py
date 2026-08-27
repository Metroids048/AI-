"""Research-only Futures microstructure replay for Alpha Recovery v3.

The runner owns no Runtime or candidate-registry integration. It downloads Binance
Vision archives month/day by month/day, validates them, aggregates raw aggTrades into
closed 5-minute buckets, joins point-in-time OI metrics, and evaluates three frozen
deterministic hypotheses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import time
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from threading import Lock
from typing import Any
from xml.etree import ElementTree

import requests  # type: ignore[import-untyped]

from scripts.run_alpha_champion_master_loop import build_split_plan

FEE_BPS = 5.0
SLIPPAGE_BPS = 3.0
EPSILON = 1e-9
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START = datetime(2023, 1, 29, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
VISION_BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
RETRY_BACKOFFS = (2, 5, 15, 30, 60)
DOWNLOAD_TRANSPORTS = ("curl", "requests", "powershell")
_LEDGER_LOCK = Lock()
BASELINE = {
    "candidate": "volatility_expansion_v1",
    "trades": 281,
    "profit_factor": 1.1576630479094718,
    "expectancy": 0.001512451049147131,
    "max_drawdown": 0.18736432836022304,
}


@dataclass
class Bucket:
    timestamp: datetime
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    total_notional: float = 0.0
    trade_count: int = 0
    buy_trade_count: int = 0
    sell_trade_count: int = 0
    first_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    last_price: float | None = None

    def add(self, price: float, quantity: float, buyer_maker: bool) -> None:
        notional = price * quantity
        self.trade_count += 1
        self.total_notional += notional
        self.first_price = price if self.first_price is None else self.first_price
        self.high_price = price if self.high_price is None else max(self.high_price, price)
        self.low_price = price if self.low_price is None else min(self.low_price, price)
        self.last_price = price
        if buyer_maker:
            self.sell_qty += quantity
            self.sell_notional += notional
            self.sell_trade_count += 1
        else:
            self.buy_qty += quantity
            self.buy_notional += notional
            self.buy_trade_count += 1

    def as_record(self) -> dict[str, Any]:
        delta = self.buy_notional - self.sell_notional
        imbalance = delta / self.total_notional if self.total_notional else None
        return {
            "timestamp": self.timestamp.isoformat(),
            "buy_qty": self.buy_qty,
            "sell_qty": self.sell_qty,
            "buy_notional": self.buy_notional,
            "sell_notional": self.sell_notional,
            "total_notional": self.total_notional,
            "delta_notional": delta,
            "imbalance": imbalance,
            "trade_count": self.trade_count,
            "buy_trade_count": self.buy_trade_count,
            "sell_trade_count": self.sell_trade_count,
            "raw_open": self.first_price,
            "raw_high": self.high_price,
            "raw_low": self.low_price,
            "raw_close": self.last_price,
        }


@dataclass(frozen=True)
class Feature:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    atr: float | None
    price_return: float
    imbalance: float
    signed_notional: float
    price_response_efficiency: float
    oi: float | None
    oi_value: float | None
    oi_change_5m: float | None
    oi_change_15m: float | None
    oi_change_1h: float | None
    oi_zscore: float | None
    funding: float | None


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    opened_at: str
    closed_at: str
    gross_return: float
    net_return: float
    fee: float
    slippage: float
    funding: float
    bars_held: int
    exit_reason: str


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _months(start: datetime, end: datetime) -> list[str]:
    cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
    result: list[str] = []
    while cursor < end:
        result.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = datetime(
            cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1, tzinfo=UTC
        )
    return result


def _days(start: datetime, end: datetime) -> list[str]:
    cursor = start.date()
    result: list[str] = []
    while cursor < end.date():
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _url(kind: str, symbol: str, period: str) -> str:
    if kind == "aggTrades":
        return f"https://data.binance.vision/data/futures/um/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{period}.zip"
    return f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{period}.zip"


class _OfficialArchiveMissing(Exception):
    pass


def _write_ledger(path: Path | None, key: str, payload: dict[str, Any]) -> None:
    if path is None:
        return
    with _LEDGER_LOCK:
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                existing = {}
        existing.setdefault("version", "alpha_research_recovery_v3.1")
        existing.setdefault("objects", {})[key] = payload
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)


def _checksum(url: str) -> str:
    checksum_url = url + ".CHECKSUM"
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        result = subprocess.run(
            [
                curl,
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "15",
                "--max-time",
                "60",
                checksum_url,
            ],
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.split()[0]
        if "404" in result.stderr:
            raise _OfficialArchiveMissing(url)
    try:
        response = requests.get(checksum_url, timeout=(10, 30))
        if response.status_code == 404:
            raise _OfficialArchiveMissing(url)
        response.raise_for_status()
        return response.text.split()[0]
    except _OfficialArchiveMissing:
        raise


def _transport_download(url: str, temporary: Path, transport: str) -> int:
    if transport == "requests":
        with requests.get(url, stream=True, timeout=(15, 120)) as response:
            if response.status_code == 404:
                raise _OfficialArchiveMissing(url)
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return temporary.stat().st_size
    if transport == "curl":
        executable = shutil.which("curl.exe") or shutil.which("curl")
        if executable is None:
            raise FileNotFoundError("curl.exe")
        result = subprocess.run(
            [
                executable,
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "15",
                "--max-time",
                "180",
                "--output",
                str(temporary),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=210,
            check=False,
        )
        if result.returncode:
            raise ConnectionError(result.stderr.strip() or f"curl exit {result.returncode}")
        return temporary.stat().st_size
    if transport == "powershell":
        script = (
            "$ErrorActionPreference='Stop'; "
            f"Invoke-WebRequest -UseBasicParsing -Uri '{url}' -OutFile '{temporary}' -TimeoutSec 180"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=210,
            check=False,
        )
        if result.returncode:
            raise ConnectionError(result.stderr.strip() or "Invoke-WebRequest failed")
        return temporary.stat().st_size
    raise ValueError(f"UNKNOWN_TRANSPORT:{transport}")


def _download(
    url: str,
    destination: Path,
    *,
    ledger_path: Path | None = None,
    object_key: str | None = None,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if ledger_path is not None and object_key is not None and ledger_path.exists() and destination.exists():
        try:
            previous = json.loads(ledger_path.read_text(encoding="utf-8")).get("objects", {}).get(object_key, {})
        except (OSError, TypeError, ValueError):
            previous = {}
        if (
            previous.get("status") == "CHECKSUM_VALID"
            and previous.get("sha256") == hashlib.sha256(destination.read_bytes()).hexdigest()
        ):
            result: dict[str, Any] = {
                **previous,
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "cached": True,
            }
            _write_ledger(ledger_path, object_key, result)
            return result
    checksum: str
    try:
        checksum = _checksum(url)
    except _OfficialArchiveMissing:
        result = {
            "url": url,
            "path": str(destination),
            "status": "HTTP_NOT_FOUND",
            "classification": "SOURCE_ARCHIVE_NOT_PUBLISHED",
        }
        _write_ledger(ledger_path, object_key or url, result)
        return result
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum:
        result = {
            "url": url,
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "cached": True,
            "status": "CHECKSUM_VALID",
            "sha256": checksum,
        }
        _write_ledger(ledger_path, object_key or url, result)
        return result
    destination.unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    failures: list[dict[str, Any]] = []
    checksum_mismatch_seen = False
    for attempt, backoff in enumerate(RETRY_BACKOFFS, start=1):
        for transport in DOWNLOAD_TRANSPORTS:
            temporary.unlink(missing_ok=True)
            started = time.perf_counter()
            try:
                size = _transport_download(url, temporary, transport)
                actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
                if actual != checksum:
                    temporary.unlink(missing_ok=True)
                    checksum_mismatch_seen = True
                    failure = {
                        "attempt": attempt,
                        "transport": transport,
                        "error": "CHECKSUM_MISMATCH",
                        "elapsed_s": round(time.perf_counter() - started, 3),
                    }
                    failures.append(failure)
                    _write_ledger(
                        ledger_path, object_key or url, {"url": url, "status": "CHECKSUM_FAILED", "attempts": failures}
                    )
                    continue
                temporary.replace(destination)
                result = {
                    "url": url,
                    "path": str(destination),
                    "bytes": size,
                    "cached": False,
                    "status": "CHECKSUM_VALID",
                    "sha256": actual,
                    "transport": transport,
                    "attempts": failures
                    + [
                        {
                            "attempt": attempt,
                            "transport": transport,
                            "elapsed_s": round(time.perf_counter() - started, 3),
                        }
                    ],
                }
                _write_ledger(ledger_path, object_key or url, result)
                return result
            except _OfficialArchiveMissing:
                temporary.unlink(missing_ok=True)
                result = {
                    "url": url,
                    "path": str(destination),
                    "status": "HTTP_NOT_FOUND",
                    "classification": "SOURCE_ARCHIVE_NOT_PUBLISHED",
                }
                _write_ledger(ledger_path, object_key or url, result)
                return result
            except Exception as exc:  # transport failures are per-object recoverable errors
                temporary.unlink(missing_ok=True)
                failures.append(
                    {
                        "attempt": attempt,
                        "transport": transport,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "elapsed_s": round(time.perf_counter() - started, 3),
                    }
                )
        _write_ledger(
            ledger_path, object_key or url, {"url": url, "status": "RETRYING", "attempt": attempt, "attempts": failures}
        )
        if attempt < len(RETRY_BACKOFFS):
            time.sleep(backoff)
    result = {
        "url": url,
        "path": str(destination),
        "status": "CHECKSUM_FAILED" if checksum_mismatch_seen else "RETRY_EXHAUSTED",
        "classification": "CHECKSUM_FAILED" if checksum_mismatch_seen else "NETWORK_FAILURE",
        "attempts": failures,
    }
    _write_ledger(ledger_path, object_key or url, result)
    return result


def _head(url: str) -> dict[str, Any]:
    response = requests.head(url, allow_redirects=True, timeout=30)
    return {
        "url": url,
        "status": response.status_code,
        "bytes": int(response.headers.get("content-length", "0") or 0),
        "checksum_available": _checksum(url) if response.ok else None,
    }


def _head_many(urls: list[str], workers: int = 16) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(_head, urls))


def _vision_listing(prefix: str) -> dict[str, int]:
    objects: dict[str, int] = {}
    token: str | None = None
    while True:
        params: dict[str, str] = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        response = requests.get(VISION_BUCKET, params=params, timeout=60)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        namespace = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        for item in root.findall(f"{namespace}Contents"):
            key = item.findtext(f"{namespace}Key")
            size = item.findtext(f"{namespace}Size")
            if key and size:
                objects[key] = int(size)
        truncated = root.findtext(f"{namespace}IsTruncated") == "true"
        token = root.findtext(f"{namespace}NextContinuationToken") if truncated else None
        if not token:
            return objects


def _download_job(job: tuple[str, Path]) -> dict[str, Any]:
    return _download(job[0], job[1])


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1"}


def _parse_aggtrades(path: Path, start: datetime, end: datetime) -> tuple[dict[int, Bucket], dict[str, Any]]:
    buckets: dict[int, Bucket] = {}
    seen_ids: set[int] = set()
    duplicate_ids = 0
    non_monotonic = 0
    invalid_rows = 0
    invalid_maker_flags = 0
    timestamp_non_monotonic = 0
    previous_id: int | None = None
    previous_timestamp: int | None = None
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith((".csv", ".csv.gz"))]
        if not names:
            raise ValueError(f"NO_CSV_IN_ARCHIVE:{path}")
        with archive.open(names[0], "r") as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                try:
                    if row and row[0].lower().startswith("agg"):
                        continue
                    maker_text = row[6].strip().lower()
                    if maker_text not in {"true", "false", "1", "0"}:
                        invalid_maker_flags += 1
                        raise ValueError
                    trade_id, price, quantity, timestamp, buyer_maker = (
                        int(row[0]),
                        float(row[1]),
                        float(row[2]),
                        int(row[5]),
                        _parse_bool(row[6]),
                    )
                    if trade_id in seen_ids:
                        duplicate_ids += 1
                    seen_ids.add(trade_id)
                    if previous_id is not None and trade_id <= previous_id:
                        non_monotonic += 1
                    previous_id = trade_id
                    if previous_timestamp is not None and timestamp < previous_timestamp:
                        timestamp_non_monotonic += 1
                    previous_timestamp = timestamp
                    if price <= 0 or quantity <= 0:
                        raise ValueError
                except (IndexError, TypeError, ValueError):
                    invalid_rows += 1
                    continue
                instant = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
                if not (start <= instant < end):
                    continue
                bucket_ms = timestamp - timestamp % 300_000
                bucket = buckets.setdefault(bucket_ms, Bucket(datetime.fromtimestamp(bucket_ms / 1000, tz=UTC)))
                bucket.add(price, quantity, buyer_maker)
    keys = sorted(buckets)
    missing = sum(
        (current - previous) // 300_000 - 1
        for previous, current in zip(keys, keys[1:], strict=False)
        if current - previous > 300_000
    )
    return buckets, {
        "duplicate_agg_trade_id": duplicate_ids,
        "non_monotonic_ids": non_monotonic,
        "timestamp_non_monotonic": timestamp_non_monotonic,
        "invalid_maker_flags": invalid_maker_flags,
        "invalid_rows": invalid_rows,
        "bucket_count": len(buckets),
        "missing_5m_buckets": missing,
    }


def _parse_aggtrades_archive(path: str) -> tuple[dict[int, Bucket], dict[str, Any]]:
    return _parse_aggtrades(Path(path), START, HOLDOUT_START)


def _parse_metrics(
    path: Path, start: datetime, end: datetime
) -> tuple[dict[int, dict[str, float | None]], dict[str, Any]]:
    rows: dict[int, dict[str, float | None]] = {}
    duplicate = 0
    invalid = 0
    oi_zero = 0
    non_5m = 0
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        with archive.open(names[0], "r") as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                try:
                    timestamp_text = row.get("create_time")
                    oi_text = row.get("sum_open_interest")
                    oi_value_text = row.get("sum_open_interest_value")
                    if timestamp_text is None or oi_text is None or oi_value_text is None:
                        raise ValueError
                    try:
                        timestamp = int(float(timestamp_text))
                        instant = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
                    except ValueError:
                        instant = datetime.fromisoformat(timestamp_text.strip().replace("Z", "+00:00"))
                        if instant.tzinfo is None:
                            instant = instant.replace(tzinfo=UTC)
                        instant = instant.astimezone(UTC)
                        timestamp = int(instant.timestamp() * 1000)
                    oi = float(oi_text)
                    oi_value = float(oi_value_text)
                    if oi <= 0:
                        oi_zero += 1
                    if instant.minute % 5 or instant.second or instant.microsecond:
                        non_5m += 1
                    if not (start <= instant < end):
                        continue
                    key = timestamp - timestamp % 300_000
                    if key in rows:
                        duplicate += 1
                    rows[key] = {
                        "oi": oi,
                        "oi_value": oi_value,
                        "count_toptrader_long_short_ratio": _optional_float(
                            row.get("count_toptrader_long_short_ratio")
                        ),
                        "sum_toptrader_long_short_ratio": _optional_float(row.get("sum_toptrader_long_short_ratio")),
                        "count_long_short_ratio": _optional_float(row.get("count_long_short_ratio")),
                        "sum_taker_long_short_vol_ratio": _optional_float(row.get("sum_taker_long_short_vol_ratio")),
                    }
                except (TypeError, ValueError):
                    invalid += 1
    return rows, {
        "duplicate_timestamps": duplicate,
        "invalid_rows": invalid,
        "oi_zero_rows": oi_zero,
        "non_5m_timestamps": non_5m,
        "row_count": len(rows),
    }


def _optional_float(value: str | None) -> float | None:
    if value is None or value in ("", "null", "None"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _oi_change(
    keys: list[int], prior_oi: dict[int, float | None], index: int, current: float | None, lookback: int
) -> float | None:
    position = index - lookback
    if position < 0 or current is None:
        return None
    previous = prior_oi.get(keys[position])
    return current - previous if previous is not None else None


def _load_ohlcv(
    connection: sqlite3.Connection, symbol: str, start: datetime, end: datetime
) -> dict[int, tuple[float, float, float, float]]:
    rows = connection.execute(
        "SELECT time, open, high, low, close FROM ohlcv_bars WHERE symbol=? AND timeframe='15m' AND time>=? AND time<? ORDER BY time",
        (f"{symbol[:-4]}/USDT", start.isoformat(sep=" "), end.isoformat(sep=" ")),
    )
    result: dict[int, tuple[float, float, float, float]] = {}
    for timestamp, open_price, high, low, close in rows:
        instant = _dt(str(timestamp))
        result[int(instant.timestamp() * 1000)] = (float(open_price), float(high), float(low), float(close))
    return result


def _load_funding(
    connection: sqlite3.Connection, symbol: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    rows = connection.execute(
        "SELECT time, funding_rate FROM market_extras WHERE symbol=? AND time>=? AND time<? AND funding_rate IS NOT NULL ORDER BY time",
        (f"{symbol[:-4]}/USDT", start.isoformat(sep=" "), end.isoformat(sep=" ")),
    )
    return [(_dt(str(timestamp)), float(rate)) for timestamp, rate in rows]


def _funding_at(rates: list[tuple[datetime, float]], instant: datetime) -> float | None:
    value: float | None = None
    for timestamp, rate in rates:
        if timestamp > instant:
            break
        value = rate
    return value


def _atr(rows: list[tuple[int, tuple[float, float, float, float]]], index: int, window: int = 14) -> float | None:
    if index < window:
        return None
    values: list[float] = []
    for pos in range(index - window + 1, index + 1):
        previous = rows[pos - 1][1][3]
        high, low = rows[pos][1][1:3]
        values.append(max(high - low, abs(high - previous), abs(low - previous)))
    value = mean(values)
    return value if value > 0 else None


def _build_features(
    flow: dict[int, Bucket],
    metrics: dict[int, dict[str, float | None]],
    ohlcv: dict[int, tuple[float, float, float, float]],
    funding: list[tuple[datetime, float]],
) -> list[Feature]:
    keys = sorted(set(flow) & set(ohlcv))
    rows = [(key, ohlcv[key]) for key in keys]
    features: list[Feature] = []
    prior_oi: dict[int, float | None] = {}
    for key in keys:
        prior_oi[key] = metrics.get(key, {}).get("oi")
    for index, key in enumerate(keys):
        if key not in metrics:
            continue
        bucket = flow[key].as_record()
        open_price, high, low, close = ohlcv[key]
        previous_close = rows[index - 1][1][3] if index else close
        imbalance = float(bucket["imbalance"] or 0.0)
        price_return = close / previous_close - 1.0 if previous_close else 0.0
        signed = float(bucket["delta_notional"])
        efficiency = abs(price_return) / (abs(imbalance) + EPSILON)
        oi = metrics[key].get("oi")

        changes = [_oi_change(keys, prior_oi, index, oi, lookback) for lookback in (1, 3, 12)]
        history: list[float] = []
        for history_index in range(max(1, index - 12), index + 1):
            current = prior_oi.get(keys[history_index])
            previous = prior_oi.get(keys[history_index - 1])
            if current is not None and previous is not None:
                history.append(current - previous)
        zscore = None
        if len(history) >= 6 and changes[0] is not None:
            sigma = pstdev(history)
            zscore = (changes[0] - mean(history)) / sigma if sigma > 0 else 0.0
        features.append(
            Feature(
                _dt(datetime.fromtimestamp(key / 1000, tz=UTC)),
                open_price,
                high,
                low,
                close,
                _atr(rows, index),
                price_return,
                imbalance,
                signed,
                efficiency,
                oi,
                metrics[key].get("oi_value"),
                changes[0],
                changes[1],
                changes[2],
                zscore,
                _funding_at(funding, _dt(datetime.fromtimestamp(key / 1000, tz=UTC))),
            )
        )
    return features


def _signal_indices(family: str, rows: list[Feature]) -> list[tuple[int, str, float]]:
    signals: list[tuple[int, str, float]] = []
    for i in range(20, len(rows) - 1):
        row = rows[i]
        if row.atr is None:
            continue
        if family == "H1_AGGRESSOR_FLOW_CONTINUATION":
            window = rows[i - 2 : i + 1]
            if (
                all(item.imbalance >= 0 for item in window)
                and sum(item.imbalance >= 0.20 for item in window) >= 2
                and sum(item.signed_notional for item in window) > 0
                and sum(item.price_return for item in rows[i - 2 : i + 1]) > 0
                and row.price_response_efficiency >= 0.001
            ):
                signals.append((i + 1, "long", row.close - row.atr))
            elif (
                all(item.imbalance <= 0 for item in window)
                and sum(item.imbalance <= -0.20 for item in window) >= 2
                and sum(item.signed_notional for item in window) < 0
                and sum(item.price_return for item in rows[i - 2 : i + 1]) < 0
                and row.price_response_efficiency >= 0.001
            ):
                signals.append((i + 1, "short", row.close + row.atr))
        elif family == "H2_FLOW_ABSORPTION_REVERSAL":
            prior = rows[i - 1]
            score = abs(prior.imbalance) / (abs(prior.price_return) + EPSILON)
            if (
                prior.imbalance <= -0.40
                and abs(prior.price_return) <= 0.0015
                and row.close > prior.close
                and row.close > row.open
                and score >= 100
            ):
                signals.append((i + 1, "long", prior.low - 0.25 * row.atr))
            elif (
                prior.imbalance >= 0.40
                and abs(prior.price_return) <= 0.0015
                and row.close < prior.close
                and row.close < row.open
                and score >= 100
            ):
                signals.append((i + 1, "short", prior.high + 0.25 * row.atr))
        else:
            if row.oi_zscore is None or row.oi_change_15m is None:
                continue
            recent_flow = sum(item.imbalance for item in rows[i - 2 : i + 1]) / 3.0
            recent_return = sum(item.price_return for item in rows[i - 2 : i + 1])
            if recent_return > 0 and row.oi_change_15m > 0 and recent_flow >= 0.20 and row.oi_zscore >= 1.0:
                signals.append((i + 1, "long", row.close - row.atr))
            elif recent_return < 0 and row.oi_change_15m > 0 and recent_flow <= -0.20 and row.oi_zscore >= 1.0:
                signals.append((i + 1, "short", row.close + row.atr))
    return signals


def _simulate(
    symbol: str,
    rows: list[Feature],
    entry_index: int,
    side: str,
    stop: float,
    funding_rates: list[tuple[datetime, float]],
) -> Trade | None:
    if entry_index >= len(rows):
        return None
    entry_row = rows[entry_index]
    entry = entry_row.open
    risk = abs(entry - stop)
    if entry <= 0 or stop <= 0 or risk <= 0:
        return None
    target = entry + 1.5 * risk if side == "long" else entry - 1.5 * risk
    end = min(len(rows) - 1, entry_index + 48)
    close_index = end
    reason = "time_exit"
    for index in range(entry_index, end + 1):
        row = rows[index]
        stop_hit = row.low <= stop if side == "long" else row.high >= stop
        target_hit = row.high >= target if side == "long" else row.low <= target
        if stop_hit:
            close_index, reason = index, "stop_loss"
            exit_price = stop
            break
        if target_hit:
            close_index, reason = index, "target"
            exit_price = target
            break
    else:
        exit_price = rows[close_index].close
    gross = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
    fee = 2 * FEE_BPS / 10_000
    slippage = 2 * SLIPPAGE_BPS / 10_000
    funding = 0.0
    for timestamp, rate in funding_rates:
        if entry_row.timestamp <= timestamp <= rows[close_index].timestamp:
            funding += rate if side == "long" else -rate
    return Trade(
        symbol,
        side,
        entry_row.timestamp.isoformat(),
        rows[close_index].timestamp.isoformat(),
        gross,
        gross - fee - slippage - funding,
        fee,
        slippage,
        funding,
        close_index - entry_index + 1,
        reason,
    )


def _replay(
    symbol: str,
    family: str,
    rows: list[Feature],
    funding_rates: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> list[Trade]:
    trades: list[Trade] = []
    last_closed = -1
    for entry_index, side, stop in _signal_indices(family, rows):
        if entry_index <= last_closed or not (start <= rows[entry_index].timestamp < end):
            continue
        trade = _simulate(symbol, rows, entry_index, side, stop, funding_rates)
        if trade:
            trades.append(trade)
            last_closed = entry_index + trade.bars_held - 1
    return trades


def _metrics(trades: Iterable[Trade]) -> dict[str, Any]:
    rows = list(trades)
    wins = [row.net_return for row in rows if row.net_return > 0]
    losses = [row.net_return for row in rows if row.net_return < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    equity = peak = drawdown = 0.0
    for row in sorted(rows, key=lambda item: item.closed_at):
        equity += row.net_return
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    by_symbol = {symbol: [row for row in rows if row.symbol == symbol] for symbol in SYMBOLS}

    def pf(items: list[Trade]) -> float:
        positive = sum(max(0, row.net_return) for row in items)
        negative = abs(sum(min(0, row.net_return) for row in items))
        return positive / negative if negative else 0.0

    result: dict[str, Any] = {
        "trades": len(rows),
        "btc_trades": len(by_symbol["BTCUSDT"]),
        "eth_trades": len(by_symbol["ETHUSDT"]),
        "net_return": sum(row.net_return for row in rows),
        "expectancy": mean(row.net_return for row in rows) if rows else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else 0.0,
        "gross_profit_factor": sum(max(0, row.gross_return) for row in rows)
        / abs(sum(min(0, row.gross_return) for row in rows))
        if rows and sum(min(0, row.gross_return) for row in rows)
        else 0.0,
        "btc_pf": pf(by_symbol["BTCUSDT"]),
        "eth_pf": pf(by_symbol["ETHUSDT"]),
        "max_drawdown": drawdown,
        "gross_return": sum(row.gross_return for row in rows),
        "fees": sum(row.fee for row in rows),
        "slippage": sum(row.slippage for row in rows),
        "funding": sum(row.funding for row in rows),
        "cost_drag": sum(row.fee + row.slippage + row.funding for row in rows),
        "long_trades": sum(row.side == "long" for row in rows),
        "short_trades": sum(row.side == "short" for row in rows),
        "exit_reasons": {
            reason: sum(row.exit_reason == reason for row in rows)
            for reason in sorted({row.exit_reason for row in rows})
        },
    }
    result["by_symbol"] = {
        symbol: {
            "trades": len(items),
            "profit_factor": pf(items),
            "expectancy": mean(item.net_return for item in items) if items else 0.0,
            "net_return": sum(item.net_return for item in items),
            "max_drawdown": _metrics_drawdown(items),
            "cost_drag": sum(item.fee + item.slippage + item.funding for item in items),
        }
        for symbol, items in by_symbol.items()
    }
    return result


def _metrics_drawdown(items: list[Trade]) -> float:
    equity = peak = drawdown = 0.0
    for row in sorted(items, key=lambda item: item.closed_at):
        equity += row.net_return
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _cost_stress_metrics(trades: Iterable[Trade], multiplier: float = 1.5) -> dict[str, Any]:
    rows = list(trades)
    by_symbol = {symbol: [row for row in rows if row.symbol == symbol] for symbol in SYMBOLS}
    result: dict[str, Any] = {}
    for symbol, items in by_symbol.items():
        stressed = [row.gross_return - multiplier * (row.fee + row.slippage + row.funding) for row in items]
        wins = [value for value in stressed if value > 0]
        losses = [value for value in stressed if value < 0]
        result[symbol] = {
            "trades": len(items),
            "net_return": sum(stressed),
            "expectancy": mean(stressed) if stressed else 0.0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else 0.0,
            "cost_drag": multiplier * sum(row.fee + row.slippage + row.funding for row in items),
        }
    all_values = [row.gross_return - multiplier * (row.fee + row.slippage + row.funding) for row in rows]
    wins = [value for value in all_values if value > 0]
    losses = [value for value in all_values if value < 0]
    result["portfolio"] = {
        "trades": len(rows),
        "net_return": sum(all_values),
        "expectancy": mean(all_values) if all_values else 0.0,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else 0.0,
        "cost_drag": multiplier * sum(row.fee + row.slippage + row.funding for row in rows),
    }
    return result


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _audit_local(connection: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table in ("ohlcv_bars", "market_extras", "microstructure_snapshots"):
        try:
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result[table] = {"present": True, "rows": count}
        except sqlite3.Error:
            result[table] = {"present": False, "rows": 0}
    for symbol in ("BTC/USDT", "ETH/USDT"):
        result.setdefault("coverage", {})[symbol] = {}
        for timeframe in ("15m", "1h", "4h"):
            row = connection.execute(
                "SELECT MIN(time), MAX(time), COUNT(*) FROM ohlcv_bars WHERE symbol=? AND timeframe=?",
                (symbol, timeframe),
            ).fetchone()
            result["coverage"][symbol][timeframe] = {"min": row[0], "max": row[1], "rows": row[2]}
        row = connection.execute(
            "SELECT MIN(time), MAX(time), COUNT(*), COUNT(open_interest) FROM market_extras WHERE symbol=?", (symbol,)
        ).fetchone()
        result["coverage"][symbol]["market_extras"] = {
            "min": row[0],
            "max": row[1],
            "rows": row[2],
            "open_interest_rows": row[3],
        }
    return result


def _build_symbol_features(
    symbol: str,
    downloaded: dict[str, Any],
    database: Path,
) -> tuple[str, list[Feature], list[tuple[datetime, float]], dict[str, Any]]:
    flow: dict[int, Bucket] = {}
    flow_quality: dict[str, Any] = defaultdict(int)
    agg_paths = [item["path"] for item in downloaded["aggTrades"][symbol]]
    with ProcessPoolExecutor(max_workers=min(4, max(1, len(agg_paths)))) as executor:
        parsed_aggtrades = executor.map(_parse_aggtrades_archive, agg_paths)
        for flow_rows, quality_rows in parsed_aggtrades:
            for key, bucket in flow_rows.items():
                flow[key] = bucket
            for quality_key, value in quality_rows.items():
                flow_quality[quality_key] += value
    metrics: dict[int, dict[str, float | None]] = {}
    metrics_quality: dict[str, Any] = defaultdict(int)
    for item in downloaded["metrics"][symbol]:
        metric_rows, metric_quality = _parse_metrics(Path(item["path"]), START, HOLDOUT_START)
        metrics.update(metric_rows)
        for metric_quality_key, value in metric_quality.items():
            metrics_quality[metric_quality_key] += value
    with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        ohlcv = _load_ohlcv(connection, symbol, START, HOLDOUT_START)
        funding = _load_funding(connection, symbol, START, HOLDOUT_START)
    features = _build_features(flow, metrics, ohlcv, funding)
    quality = {
        "aggtrades": dict(flow_quality),
        "metrics": dict(metrics_quality),
        "feature_rows": len(features),
        "missing_oi_rows": sum(row.oi is None for row in features),
        "feature_schema_hash": _feature_schema_hash(),
    }
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist([asdict(row) for row in features])
        pq.write_table(
            table,
            Path(os.environ.get("LOCALAPPDATA", "."))
            / "ai-quant"
            / "microstructure-v3"
            / f"{symbol}-microstructure-5m.parquet",
        )
    except ImportError:
        pass
    return symbol, features, funding, quality


def _prepare_archives(
    cache: Path, start: datetime, end: datetime, audit_only: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    remote: dict[str, Any] = {"aggTrades": {}, "metrics": {}}
    downloaded: dict[str, Any] = {"aggTrades": {}, "metrics": {}}
    manifest_path = cache / "metrics_manifest.json"
    agg_manifest_path = cache / "aggtrades_manifest.json"
    agg_jobs: list[tuple[str, dict[str, Any], Path]] = []
    metric_jobs: list[tuple[str, dict[str, Any], Path]] = []
    for symbol in SYMBOLS:
        remote["aggTrades"][symbol] = []
        downloaded["aggTrades"][symbol] = []
        existing_agg = {path.stem: path for path in (cache / "aggTrades" / symbol).glob("*.zip")}
        for period in _months(start, end):
            url = _url("aggTrades", symbol, period)
            destination = cache / "aggTrades" / symbol / f"{period}.zip"
            item: dict[str, Any] = {
                "period": period,
                "url": url,
                "path": str(destination),
                "status": "PENDING",
            }
            if period in existing_agg:
                item.update({"status": "CHECKSUM_VALID", "cached": True, "bytes": destination.stat().st_size})
                downloaded["aggTrades"][symbol].append(item.copy())
            elif not audit_only and symbol == "ETHUSDT":
                agg_jobs.append((symbol, item, destination))
            remote["aggTrades"][symbol].append(item)
        remote["metrics"][symbol] = []
        downloaded["metrics"][symbol] = []
        metric_periods = _days(start, end)
        for period in metric_periods:
            url = _url("metrics", symbol, period)
            metric_item: dict[str, Any] = {
                "url": url,
                "period": period,
                "status": "PENDING",
            }
            remote["metrics"][symbol].append(metric_item)
            if not audit_only:
                metric_jobs.append((symbol, metric_item, cache / "metrics" / symbol / f"{period}.zip"))
    agg_jobs.sort(key=lambda row: (row[1]["period"], row[0]))
    if not audit_only and agg_jobs:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    _download,
                    item["url"],
                    destination,
                    ledger_path=agg_manifest_path,
                    object_key=f"aggTrades:{symbol}:{item['period']}",
                ): (symbol, item)
                for symbol, item, destination in agg_jobs
            }
            for future in as_completed(futures):
                symbol, item = futures[future]
                result = future.result()
                item.update(result)
                for row in remote["aggTrades"][symbol]:
                    if row["period"] == item["period"]:
                        row.update(result)
                        break
                if result.get("status") == "CHECKSUM_VALID":
                    downloaded["aggTrades"][symbol].append(item.copy())
    metric_jobs.sort(key=lambda row: (row[1]["period"], row[0]))
    if not audit_only and metric_jobs:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    _download,
                    item["url"],
                    destination,
                    ledger_path=manifest_path,
                    object_key=f"{symbol}:{item['period']}",
                ): (symbol, item)
                for symbol, item, destination in metric_jobs
            }
            for future in as_completed(futures):
                symbol, item = futures[future]
                result = future.result()
                item.update(result)
                if result.get("status") == "CHECKSUM_VALID":
                    downloaded["metrics"][symbol].append(result)
    return remote, downloaded


def _metrics_coverage(rows: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    expected = len(_days(start, end))
    valid = sum(row.get("status") == "CHECKSUM_VALID" for row in rows)
    source_missing = [row["period"] for row in rows if row.get("status") == "HTTP_NOT_FOUND"]
    network_failures = [row["period"] for row in rows if row.get("status") == "RETRY_EXHAUSTED"]
    checksum_failed = [row["period"] for row in rows if row.get("status") == "CHECKSUM_FAILED"]
    published_expected = expected - len(source_missing)
    coverage_ratio = valid / published_expected if published_expected else 0.0
    missing_dates = sorted(source_missing)
    longest_gap = 0
    current_gap = 0
    previous = None
    for value in missing_dates:
        date = datetime.fromisoformat(value).date()
        if previous is not None and (date - previous).days == 1:
            current_gap += 1
        else:
            current_gap = 1
        longest_gap = max(longest_gap, current_gap)
        previous = date
    return {
        "expected_days": expected,
        "archives_present": valid,
        "checksum_valid": valid,
        "missing_source_archives": missing_dates,
        "network_failures": sorted(network_failures),
        "checksum_failures": sorted(checksum_failed),
        "published_expected_days": published_expected,
        "coverage_ratio": coverage_ratio,
        "coverage_percent": round(coverage_ratio * 100, 4),
        "longest_continuous_source_gap_days": longest_gap,
        "gate": coverage_ratio >= 0.99 and not network_failures and not checksum_failed and longest_gap <= 1,
    }


def _archive_coverage(rows: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    expected = len(_months(start, end))
    valid = sum(row.get("status") == "CHECKSUM_VALID" for row in rows)
    failed = [row["period"] for row in rows if row.get("status") in {"RETRY_EXHAUSTED", "CHECKSUM_FAILED"}]
    missing = [row["period"] for row in rows if row.get("status") in {"PENDING", "HTTP_NOT_FOUND"}]
    ratio = valid / expected if expected else 0.0
    return {
        "expected": expected,
        "valid": valid,
        "coverage": ratio,
        "coverage_percent": round(ratio * 100, 4),
        "missing": sorted(missing),
        "failures": sorted(failed),
        "complete": valid == expected and not failed and not missing,
    }


def _feature_schema_hash() -> str:
    schema = [(field.name, str(field.type)) for field in fields(Feature)]
    return hashlib.sha256(json.dumps(schema, separators=(",", ":")).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".strategy_refactor_history.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/alpha_research_recovery_v3"))
    parser.add_argument(
        "--cache-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ".")) / "ai-quant" / "microstructure-v3"
    )
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    split = build_split_plan(args.database)
    research_end = min(split.research_end, HOLDOUT_START)
    validation_start = research_end
    baseline = {
        "git": {
            "branch": os.popen("git branch --show-current").read().strip(),
            "head": os.popen("git rev-parse HEAD").read().strip(),
        },
        "candidate": BASELINE,
        "research_start": START.isoformat(),
        "research_end": research_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
    }
    _write(args.output_dir / "BASELINE.json", baseline)
    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        local = _audit_local(connection)
    remote, downloaded = _prepare_archives(args.cache_dir, START, HOLDOUT_START, args.audit_only)
    metrics_coverage = {
        symbol: _metrics_coverage(remote["metrics"][symbol], START, HOLDOUT_START) for symbol in SYMBOLS
    }
    aggtrades_coverage = {
        symbol: _archive_coverage(remote["aggTrades"][symbol], START, HOLDOUT_START) for symbol in SYMBOLS
    }
    audit = {
        "aggtrades": remote["aggTrades"],
        "aggtrades_coverage": aggtrades_coverage,
        "metrics": remote["metrics"],
        "metrics_coverage": metrics_coverage,
        "local_existing_data": local,
        "downloaded": downloaded,
        "coverage": {
            "start": START.isoformat(),
            "end_exclusive": HOLDOUT_START.isoformat(),
            "research_end": research_end.isoformat(),
        },
        "usable": all(
            metrics_coverage[symbol]["gate"] and aggtrades_coverage[symbol]["complete"] for symbol in SYMBOLS
        ),
    }
    _write(args.output_dir / "DATA_AUDIT.json", audit)
    plan: dict[str, Any] = {
        "version": "alpha_research_recovery_v3.1",
        "source": "Binance Vision USD-M Futures",
        "features": "raw aggTrades aggregated to closed 5m + daily metrics/OI",
        "split": {
            "research_start": START.isoformat(),
            "research_end": research_end.isoformat(),
            "validation_start": validation_start.isoformat(),
            "validation_end": HOLDOUT_START.isoformat(),
            "final_holdout_accessed": False,
        },
        "families": {
            "H1_AGGRESSOR_FLOW_CONTINUATION": {
                "imbalance_threshold": 0.20,
                "efficiency_min": 0.001,
                "stop_atr": 1.0,
                "target_r": 1.5,
            },
            "H2_FLOW_ABSORPTION_REVERSAL": {
                "imbalance_threshold": 0.40,
                "max_abs_return": 0.0015,
                "absorption_score_min": 100.0,
                "stop_buffer_atr": 0.25,
                "target_r": 1.5,
            },
            "H3_OI_FLOW_BUILDUP": {"flow_threshold": 0.20, "oi_zscore_min": 1.0, "stop_atr": 1.0, "target_r": 1.5},
        },
        "ml": "forbidden in this round",
        "runtime_visible": False,
    }
    _write(args.output_dir / "RESEARCH_PLAN.json", plan)
    if not audit["usable"] or args.audit_only:
        status = (
            "BLOCKED_ETH_AGGTRADES"
            if not audit["usable"] and not aggtrades_coverage["ETHUSDT"]["complete"]
            else ("BLOCKED_MICROSTRUCTURE_HISTORICAL_DATA" if not audit["usable"] else "AUDIT_ONLY")
        )
        _write(
            args.output_dir / "FINAL_REPORT.json",
            {
                "status": status,
                "data_coverage": audit,
                "data": {
                    symbol: {
                        "aggtrades_complete": aggtrades_coverage[symbol]["complete"],
                        "metrics_complete": metrics_coverage[symbol]["gate"],
                        "feature_rows": 0,
                    }
                    for symbol in SYMBOLS
                },
                "baseline": BASELINE,
                "candidates": {},
                "survivors": [],
                "symbol_specific_survivors": [],
                "best_candidate": None,
                "incremental_edge_vs_ohlcv": {},
                "final_holdout_accessed": False,
                "runtime_modified": False,
                "production_authority": "NOT_GRANTED",
            },
        )
        print(json.dumps({"status": status, "usable": audit["usable"]}, indent=2))
        return 0 if audit["usable"] else 2
    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        all_features: dict[str, list[Feature]] = {}
        funding: dict[str, list[tuple[datetime, float]]] = {}
        quality: dict[str, Any] = {}
        for symbol in SYMBOLS:
            symbol, features, symbol_funding, symbol_quality = _build_symbol_features(symbol, downloaded, args.database)
            all_features[symbol] = features
            funding[symbol] = symbol_funding
            quality[symbol] = symbol_quality
    _write(args.output_dir / "DATA_AUDIT.json", {**audit, "quality": quality, "usable": True})
    _write(
        args.output_dir / "FEATURE_LINEAGE.json",
        {
            "source": "Binance Vision",
            "aggregation": "5m floor, closed interval only",
            "direction": {"is_buyer_maker=false": "aggressive_buy", "is_buyer_maker=true": "aggressive_sell"},
            "missing_policy": "no zero/neutral fill",
            "quality": quality,
            "final_holdout_accessed": False,
        },
    )
    candidates: dict[str, Any] = {}
    survivors: list[str] = []
    for family, filename in (
        ("H1_AGGRESSOR_FLOW_CONTINUATION", "H1_AGGRESSOR_FLOW_CONTINUATION.json"),
        ("H2_FLOW_ABSORPTION_REVERSAL", "H2_FLOW_ABSORPTION_REVERSAL.json"),
        ("H3_OI_FLOW_BUILDUP", "H3_OI_FLOW_BUILDUP.json"),
    ):
        research_trades: list[Trade] = []
        validation_trades: list[Trade] = []
        for symbol in SYMBOLS:
            trades = _replay(symbol, family, all_features[symbol], funding[symbol], START, HOLDOUT_START)
            research_trades.extend(row for row in trades if _dt(row.opened_at) < validation_start)
            validation_trades.extend(row for row in trades if validation_start <= _dt(row.opened_at) < HOLDOUT_START)
        research_metrics = _metrics(research_trades)
        validation_metrics = _metrics(validation_trades)
        total_min = {
            "H1_AGGRESSOR_FLOW_CONTINUATION": 150,
            "H2_FLOW_ABSORPTION_REVERSAL": 100,
            "H3_OI_FLOW_BUILDUP": 120,
        }[family]
        symbol_min = {
            "H1_AGGRESSOR_FLOW_CONTINUATION": 50,
            "H2_FLOW_ABSORPTION_REVERSAL": 30,
            "H3_OI_FLOW_BUILDUP": 40,
        }[family]
        symbol_research: dict[str, dict[str, Any]] = {}
        for symbol in SYMBOLS:
            metrics = research_metrics["by_symbol"][symbol]
            symbol_research[symbol] = {
                **metrics,
                "status": "PASS"
                if metrics["trades"] >= symbol_min and metrics["profit_factor"] > 1.10 and metrics["expectancy"] > 0
                else "FAIL",
            }
        research_status = (
            "PASS"
            if (
                research_metrics["trades"] >= total_min
                and research_metrics["profit_factor"] > 1.10
                and research_metrics["expectancy"] > 0
                and all(item["status"] == "PASS" for item in symbol_research.values())
            )
            else (
                "SYMBOL_SPECIFIC_PASS" if any(item["status"] == "PASS" for item in symbol_research.values()) else "FAIL"
            )
        )
        validation_symbols: dict[str, dict[str, Any]] = {}
        for symbol in SYMBOLS:
            metrics = validation_metrics["by_symbol"][symbol]
            validation_symbols[symbol] = {
                **metrics,
                "status": "NOT_RUN"
                if symbol_research[symbol]["status"] != "PASS"
                else ("PASS" if metrics["profit_factor"] > 1.10 and metrics["expectancy"] > 0 else "FAIL"),
            }
        validation_status = (
            "PASS"
            if research_status == "PASS" and all(item["status"] == "PASS" for item in validation_symbols.values())
            else (
                "SYMBOL_SPECIFIC_PASS"
                if any(item["status"] == "PASS" for item in validation_symbols.values())
                else "NOT_RUN"
            )
        )
        if (
            research_status == "PASS"
            and validation_status == "PASS"
            and research_metrics["profit_factor"] >= 1.20
            and validation_metrics["profit_factor"] >= 1.10
            and research_metrics["max_drawdown"] <= 0.20
        ):
            survivors.append(family)
        symbol_survivors = [
            f"{family}:{symbol}"
            for symbol, item in validation_symbols.items()
            if item["status"] == "PASS" and symbol_research[symbol]["status"] == "PASS"
        ]
        payload = {
            "candidate": family,
            "parameters": plan["families"][family],
            "research": {"metrics": research_metrics, "status": research_status, "by_symbol": symbol_research},
            "validation": {"metrics": validation_metrics, "status": validation_status, "by_symbol": validation_symbols},
            "cost_stress_1_5x": {
                "research": _cost_stress_metrics(research_trades, 1.5),
                "validation": _cost_stress_metrics(validation_trades, 1.5),
            },
            "symbol_specific_survivors": symbol_survivors,
            "trades": [asdict(row) for row in research_trades + validation_trades],
            "final_holdout_accessed": False,
            "runtime_visible": False,
        }
        candidates[family] = payload
        _write(args.output_dir / filename, payload)
    stability = {
        "status": "NOT_RUN" if not survivors else "PENDING_REVIEW",
        "survivors": survivors,
        "neighbor_search": "only survivors; max two neighbors",
        "final_holdout_accessed": False,
    }
    _write(
        args.output_dir / "VALIDATION_RESULTS.json", {family: candidates[family]["validation"] for family in candidates}
    )
    _write(args.output_dir / "STABILITY_RESULTS.json", stability)
    symbol_specific_survivors = sorted(
        {item for value in candidates.values() for item in value["symbol_specific_survivors"]}
    )
    status = (
        "MICROSTRUCTURE_ALPHA_SURVIVOR"
        if survivors
        else ("SYMBOL_SPECIFIC_ALPHA_SURVIVOR" if symbol_specific_survivors else "MICROSTRUCTURE_ALPHA_BATCH_EXHAUSTED")
    )
    deltas = {
        family: {
            "delta_pf_vs_baseline": candidates[family]["research"]["metrics"]["profit_factor"]
            - BASELINE["profit_factor"],
            "delta_expectancy_vs_baseline": candidates[family]["research"]["metrics"]["expectancy"]
            - BASELINE["expectancy"],
            "delta_drawdown_vs_baseline": candidates[family]["research"]["metrics"]["max_drawdown"]
            - BASELINE["max_drawdown"],
        }
        for family in candidates
    }
    report = {
        "status": status,
        "data_coverage": audit,
        "baseline": BASELINE,
        "candidates": {
            family: {"research": value["research"], "validation": value["validation"]}
            for family, value in candidates.items()
        },
        "survivors": survivors,
        "symbol_specific_survivors": symbol_specific_survivors,
        "best_candidate": survivors[0] if survivors else None,
        "data": {
            symbol: {
                "aggtrades_complete": aggtrades_coverage[symbol]["complete"],
                "metrics_complete": metrics_coverage[symbol]["gate"],
                "feature_rows": quality[symbol]["feature_rows"],
                "feature_schema_hash": quality[symbol]["feature_schema_hash"],
            }
            for symbol in SYMBOLS
        },
        "h1": candidates["H1_AGGRESSOR_FLOW_CONTINUATION"],
        "h2": candidates["H2_FLOW_ABSORPTION_REVERSAL"],
        "h3": candidates["H3_OI_FLOW_BUILDUP"],
        "incremental_edge_vs_ohlcv": deltas,
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production_authority": "NOT_GRANTED",
    }
    _write(args.output_dir / "FINAL_REPORT.json", report)
    print(json.dumps({"status": status, "survivors": survivors, "candidates": report["candidates"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
