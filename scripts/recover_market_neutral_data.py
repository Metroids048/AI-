"""Recover the fixed Market Neutral V1 datasets from official Binance sources.

Research-only.  Downloads are resumable, checksum-validated and written outside
the repository under %LOCALAPPDATA%\\ai-quant\\market-neutral-v1.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

import requests

UNIVERSE = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
START = datetime(2023, 1, 29, tzinfo=UTC)
END = datetime(2026, 1, 29, tzinfo=UTC)
VISION = "https://data.binance.vision/data"
FAPI = "https://fapi.binance.com"
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ai-quant/market-neutral-v1"
MANIFEST = Path("artifacts/market_neutral_research_v1/MARKET_NEUTRAL_ACQUISITION_MANIFEST.jsonl")


def _months() -> list[str]:
    out: list[str] = []
    cursor = datetime(2023, 1, 1, tzinfo=UTC)
    while cursor < END:
        out.append(cursor.strftime("%Y-%m"))
        cursor = datetime(
            cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1, tzinfo=UTC
        )
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(url: str, timeout: int = 90) -> bytes:
    request = Request(url, headers={"User-Agent": "ai-quant-market-neutral-v1/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except (OSError, URLError):
        try:
            result = subprocess.run(
                ["curl.exe", "-fsSL", "--max-time", str(timeout), url], capture_output=True, check=True
            )
            return result.stdout
        except (OSError, subprocess.CalledProcessError):
            response = requests.get(url, timeout=timeout, headers={"User-Agent": "ai-quant-market-neutral-v1/1.0"})
            response.raise_for_status()
            return response.content


def _load_manifest() -> dict[str, dict[str, object]]:
    if not MANIFEST.exists():
        return {}
    rows: dict[str, dict[str, object]] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            rows[str(item["key"])] = item
    return rows


def _save_manifest(rows: dict[str, dict[str, object]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="manifest-", suffix=".jsonl", dir=MANIFEST.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key in sorted(rows):
                handle.write(json.dumps(rows[key], ensure_ascii=True, sort_keys=True) + "\n")
        os.replace(name, MANIFEST)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _archive_task(dataset: str, symbol: str, month: str) -> dict[str, object]:
    filename = f"{symbol}-1h-{month}.zip"
    url = f"{VISION}/{'spot' if dataset == 'spot' else 'futures/um'}/monthly/{dataset if dataset == 'spot' else dataset + 'Klines' if dataset in {'markPrice', 'indexPrice', 'premiumIndex'} else 'klines'}/{symbol}/1h/{filename}"
    if dataset == "spot":
        url = f"{VISION}/spot/monthly/klines/{symbol}/1h/{filename}"
    elif dataset == "perp":
        url = f"{VISION}/futures/um/monthly/klines/{symbol}/1h/{filename}"
    key = f"{dataset}:{symbol}:{month}"
    target = ROOT / dataset / symbol / "1h" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "key": key,
        "source": "binance_vision",
        "dataset": dataset,
        "symbol": symbol,
        "start": month,
        "end": month,
        "status": "PENDING",
        "attempts": 0,
        "bytes": 0,
        "checksum": None,
        "rows": None,
        "error": None,
        "url": url,
    }
    for attempt in range(1, 4):
        record["attempts"] = attempt
        try:
            if target.exists() and target.stat().st_size > 0 and ZipFile(target).testzip() is None:
                checksum = _sha256(target)
                record.update(
                    status="CHECKSUM_VALID", bytes=target.stat().st_size, checksum=checksum, rows=_zip_rows(target)
                )
                return record
            payload = _request(url + ".CHECKSUM")
            expected = payload.decode("ascii", errors="strict").strip().split()[0].lower()
            archive = _request(url)
            actual = hashlib.sha256(archive).hexdigest()
            if expected != actual:
                raise ValueError(f"checksum mismatch expected={expected} actual={actual}")
            part = target.with_suffix(target.suffix + ".part")
            part.write_bytes(archive)
            with ZipFile(part) as zf:
                zf.testzip()
            os.replace(part, target)
            target.with_suffix(target.suffix + ".sha256").write_text(actual + "\n", encoding="ascii")
            record.update(status="COMPLETE", bytes=len(archive), checksum=actual, rows=_zip_rows(target))
            return record
        except HTTPError as exc:
            record.update(status="HTTP_NOT_FOUND" if exc.code == 404 else "NETWORK_FAILED", error=f"HTTP {exc.code}")
            if exc.code == 404:
                return record
        except Exception as exc:  # bounded retry is intentional for source recovery
            record.update(
                status="CHECKSUM_FAILED" if "checksum" in str(exc).lower() else "NETWORK_FAILED", error=str(exc)[:400]
            )
        if attempt < 3:
            time.sleep((2, 5, 15)[attempt - 1])
    return record


def _zip_rows(path: Path) -> int:
    try:
        with ZipFile(path) as archive:
            member = next(item for item in archive.infolist() if not item.is_dir())
            with archive.open(member) as stream:
                return max(0, sum(1 for _ in TextIOWrapper(stream, encoding="utf-8")) - 1)
    except (BadZipFile, StopIteration, OSError):
        return 0


def _funding(symbol: str) -> dict[str, object]:
    key = f"funding:{symbol}:2023-01-29:2026-01-29"
    target = ROOT / "funding" / f"{symbol}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    points: dict[int, dict[str, object]] = {}
    cursor = start_ms
    attempts = 0
    error: str | None = None
    while cursor < end_ms:
        query = f"symbol={symbol}&startTime={cursor}&endTime={end_ms - 1}&limit=1000"
        for attempt in range(1, 6):
            attempts += 1
            try:
                rows = json.loads(_request(f"{FAPI}/fapi/v1/fundingRate?{query}"))
                if not rows:
                    cursor = end_ms
                    break
                latest = cursor
                for row in rows:
                    ts = int(row["fundingTime"])
                    if start_ms <= ts < end_ms:
                        points[ts] = {
                            "fundingTime": ts,
                            "fundingRate": row.get("fundingRate"),
                            "markPrice": row.get("markPrice", ""),
                        }
                        latest = max(latest, ts)
                cursor = end_ms if len(rows) < 1000 or latest <= cursor else latest + 1
                break
            except Exception as exc:
                error = str(exc)[:400]
                if attempt == 5:
                    cursor = end_ms
                else:
                    time.sleep((2, 5, 15, 30, 60)[attempt - 1])
    with target.open("w", encoding="utf-8") as handle:
        for ts in sorted(points):
            handle.write(json.dumps(points[ts], ensure_ascii=True, sort_keys=True) + "\n")
    return {
        "key": key,
        "source": "fapi_public_fundingRate",
        "dataset": "funding",
        "symbol": symbol,
        "start": START.isoformat(),
        "end": END.isoformat(),
        "status": "COMPLETE" if points and error is None else "NETWORK_FAILED" if error else "COMPLETE",
        "attempts": attempts,
        "bytes": target.stat().st_size,
        "checksum": _sha256(target),
        "rows": len(points),
        "error": error,
        "url": f"{FAPI}/fapi/v1/fundingRate",
    }


def main() -> int:
    rows = _load_manifest()
    tasks = [
        (dataset, symbol, month)
        for dataset in ("spot", "perp", "markPrice", "indexPrice", "premiumIndex")
        for symbol in UNIVERSE
        for month in _months()
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_archive_task, *task): task for task in tasks}
        for future in as_completed(futures):
            result = future.result()
            rows[str(result["key"])] = result
            _save_manifest(rows)
            print(json.dumps(result, ensure_ascii=True), flush=True)
    for symbol in UNIVERSE:
        result = _funding(symbol)
        rows[str(result["key"])] = result
        _save_manifest(rows)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    info = json.loads(_request(f"{FAPI}/fapi/v1/exchangeInfo"))
    snapshot = {
        "source": "fapi_public_exchangeInfo",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "symbols": [row for row in info.get("symbols", []) if row.get("symbol") in UNIVERSE],
        "checksum": hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest(),
    }
    (ROOT / "trading_rules").mkdir(parents=True, exist_ok=True)
    (ROOT / "trading_rules" / "exchangeInfo.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    rows["trading_rules:exchangeInfo"] = {
        "key": "trading_rules:exchangeInfo",
        "source": "fapi_public_exchangeInfo",
        "dataset": "trading_rules",
        "symbol": "UNIVERSE",
        "start": "current",
        "end": "current",
        "status": "COMPLETE",
        "attempts": 1,
        "bytes": (ROOT / "trading_rules" / "exchangeInfo.json").stat().st_size,
        "checksum": snapshot["checksum"],
        "rows": len(snapshot["symbols"]),
        "error": None,
    }
    _save_manifest(rows)
    print(json.dumps({"status": "RECOVERY_COMPLETE", "manifest": str(MANIFEST), "objects": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
