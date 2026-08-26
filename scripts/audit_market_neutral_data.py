"""Audit the fixed Market Neutral V1 data contract from the recovered cache."""

from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zipfile import ZipFile

UNIVERSE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
START = datetime(2023, 1, 29, tzinfo=UTC)
END = datetime(2026, 1, 29, tzinfo=UTC)
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ai-quant/market-neutral-v1"
OUTPUT_DIR = Path("artifacts/market_neutral_research_v1")
INTERVAL = timedelta(hours=1)


def _ts(raw: str) -> datetime:
    value = int(float(raw))
    divisor = 1_000_000 if value >= 100_000_000_000_000 else 1_000
    return datetime.fromtimestamp(value / divisor, tz=UTC)


def _zip_timestamps(path: Path) -> list[datetime]:
    with ZipFile(path) as archive:
        member = next(item for item in archive.infolist() if not item.is_dir())
        with archive.open(member) as stream:
            reader = csv.reader(line.decode("utf-8") for line in stream)
            result: list[datetime] = []
            for row in reader:
                if not row or row[0].lower().endswith("open_time"):
                    continue
                try:
                    result.append(_ts(row[0]))
                except (TypeError, ValueError, OSError):
                    continue
            return result


def _series_audit(dataset: str, symbol: str) -> dict[str, Any]:
    paths = sorted((ROOT / dataset / symbol / "1h").glob("*.zip"))
    timestamps: list[datetime] = []
    invalid = 0
    for path in paths:
        try:
            timestamps.extend(_zip_timestamps(path))
        except Exception:
            invalid += 1
    filtered = sorted(ts for ts in timestamps if START <= ts < END)
    unique = sorted(set(filtered))
    duplicates = len(filtered) - len(unique)
    expected = int((END - START) / INTERVAL)
    expected_set = {START + i * INTERVAL for i in range(expected)}
    missing = sorted(expected_set - set(unique))
    return {
        "status": "PASS" if len(unique) >= expected * 0.995 and not invalid else "PARTIAL",
        "root": str(ROOT / dataset / symbol / "1h"),
        "expected_bars": expected,
        "actual_bars": len(unique),
        "start": unique[0].isoformat() if unique else None,
        "end": unique[-1].isoformat() if unique else None,
        "missing_intervals": [item.isoformat() for item in missing[:100]],
        "missing_count": len(missing),
        "duplicate": duplicates,
        "invalid_rows_or_archives": invalid,
        "coverage_ratio": len(unique) / expected if expected else 0.0,
        "archive_count": len(paths),
    }


def _funding_audit(symbol: str) -> dict[str, Any]:
    path = ROOT / "funding" / f"{symbol}.jsonl"
    points: list[int] = []
    invalid = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                points.append(int(item["fundingTime"]))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                invalid += 1
    points = sorted(points)
    unique = sorted(set(points))
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    scoped = [item for item in unique if start_ms <= item < end_ms]
    cadence_ms = 8 * 60 * 60 * 1000
    gaps = [b - a for a, b in zip(scoped, scoped[1:], strict=False) if abs((b - a) - cadence_ms) > 60_000]
    return {
        "status": "PASS" if len(scoped) >= 3200 and not gaps and invalid == 0 else "PARTIAL",
        "root": str(path),
        "expected_records": 3288,
        "actual_records": len(scoped),
        "start": datetime.fromtimestamp(scoped[0] / 1000, tz=UTC).isoformat() if scoped else None,
        "end": datetime.fromtimestamp(scoped[-1] / 1000, tz=UTC).isoformat() if scoped else None,
        "missing_intervals": len(gaps),
        "duplicate": len(points) - len(unique),
        "invalid_rows": invalid,
        "coverage_ratio": len(scoped) / 3288,
    }


def _write(name: str, payload: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT_DIR / f".{name}.tmp"
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temp.replace(OUTPUT_DIR / name)


def main() -> int:
    series = {
        dataset: {symbol: _series_audit(dataset, symbol) for symbol in UNIVERSE}
        for dataset in ("spot", "perp", "markPrice", "indexPrice", "premiumIndex")
    }
    funding = {symbol: _funding_audit(symbol) for symbol in UNIVERSE}
    rules_path = ROOT / "trading_rules" / "exchangeInfo.json"
    rules_ready = rules_path.exists() and bool(json.loads(rules_path.read_text(encoding="utf-8")).get("symbols"))
    requirements = {
        "spot_1h": all(item["coverage_ratio"] >= 0.995 for item in series["spot"].values()),
        "perpetual_1h": all(item["coverage_ratio"] >= 0.995 for item in series["perp"].values()),
        "mark_index_premium": all(
            item["coverage_ratio"] >= (0.99 if dataset == "premiumIndex" else 0.995)
            for dataset in ("markPrice", "indexPrice", "premiumIndex")
            for item in series[dataset].values()
        ),
        "funding_history": all(item["status"] == "PASS" for item in funding.values()),
        "trading_rules": rules_ready,
    }
    ready = all(requirements.values())
    audit = {
        "schema": "MARKET_NEUTRAL_DATA_AUDIT_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "DATA_READY" if ready else "BLOCKED_MARKET_NEUTRAL_DATA",
        "universe": UNIVERSE,
        "required_history": {"start": START.date().isoformat(), "end": END.date().isoformat(), "interval": "1h"},
        "required_inputs": [
            "spot_1h",
            "perpetual_1h",
            "mark_price",
            "index_price",
            "premium_index",
            "funding_rate",
            "trading_rules",
            "fee_assumptions",
        ],
        "missing_required_inputs": {key: not value for key, value in requirements.items()},
        "evidence": {
            "series": series,
            "funding": funding,
            "trading_rules": {"status": "PASS" if rules_ready else "MISSING", "path": str(rules_path)},
            "fee_assumptions": {
                "maker_bps": 2.0,
                "taker_bps": 5.0,
                "slippage_bps": 3.0,
                "status": "ASSUMED_RESEARCH_INPUT",
            },
        },
        "final_holdout": {"start": "2026-01-29", "end": "sealed", "accessed": False},
        "runtime_modified": False,
        "terminal": "NONE",
        "blocking_reasons": [] if ready else [key for key, value in requirements.items() if not value],
    }
    _write("DATA_AUDIT.json", audit)
    _write("MARKET_NEUTRAL_DATA_AUDIT.json", audit)
    print(json.dumps({"status": audit["status"], "requirements": requirements, "root": str(ROOT)}, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
