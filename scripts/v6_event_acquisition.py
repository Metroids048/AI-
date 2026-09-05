"""Recoverable public-event acquisition primitives for V6.1.

This module is intentionally independent from the trading Runtime.  It provides
small deterministic helpers plus a bounded GDELT transport hierarchy; callers
decide whether a complete history gate has been met.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests  # type: ignore[import-untyped]

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 250
TRANSPORTS = ("curl", "requests", "powershell")
WINDOW_HOURS = (24, 12, 6, 3, 1)
BACKOFF_SECONDS = (2, 5, 15, 30, 60)

QUERY_FAMILIES = {
    "Q01_BITCOIN": "bitcoin OR BTC",
    "Q02_ETHEREUM": "ethereum OR ETH",
    "Q03_CRYPTO_SEC": "cryptocurrency SEC",
    "Q04_BITCOIN_ETF": "bitcoin ETF",
    "Q05_ETHEREUM_ETF": "ethereum ETF",
    "Q06_CRYPTO_REGULATION": "crypto regulation",
    "Q07_CRYPTO_HACK": "crypto hack",
    "Q08_CRYPTO_EXPLOIT": "crypto exploit",
    "Q09_CRYPTO_BANKRUPTCY": "crypto bankruptcy",
    "Q10_EXCHANGE_INCIDENT": "crypto exchange incident",
    "Q11_BINANCE": "Binance",
    "Q12_COINBASE": "Coinbase",
    "Q13_STABLECOIN": "stablecoin",
    "Q14_CRYPTO_DELISTING": "crypto delisting",
    "Q15_CRYPTO_LISTING": "crypto listing",
}


def window_query(query: str, start: datetime, end: datetime) -> str:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(MAX_RECORDS),
        "sort": "datedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    return f"{GDELT_DOC_URL}?{urlencode(params)}"


def adaptive_windows(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Yield the largest currently permitted slice; callers split failures."""

    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(hours=WINDOW_HOURS[0]), end)
        yield cursor, window_end
        cursor = window_end


def split_window(start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    duration = end - start
    if duration <= timedelta(hours=1):
        return None
    midpoint = start + duration / 2
    return start, midpoint


def _json_response(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    return [row for row in articles if isinstance(row, dict)]


def _curl(url: str, timeout: int) -> tuple[int | None, str]:
    result = subprocess.run(
        ["curl.exe", "--silent", "--show-error", "--max-time", str(timeout), url],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout + 2,
    )
    return (0 if result.returncode == 0 else None), result.stdout


def _powershell(url: str, timeout: int) -> tuple[int | None, str]:
    escaped = url.replace("'", "''")
    command = f"(Invoke-WebRequest -UseBasicParsing -TimeoutSec {timeout} -Uri '{escaped}').Content"
    try:
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout + 2,
        )
    except subprocess.TimeoutExpired:
        return None, ""
    return (0 if result.returncode == 0 else None), result.stdout


def fetch_transport(url: str, *, timeout: int = 15) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    for transport in TRANSPORTS:
        try:
            if transport == "curl":
                status, raw = _curl(url, timeout)
            elif transport == "requests":
                response = requests.get(url, timeout=timeout)
                status, raw = response.status_code, response.text
                response.raise_for_status()
            else:
                status, raw = _powershell(url, timeout)
            rows = _json_response(raw)
            return rows, {"transport": transport, "http_status": status, "ok": True, "record_count": len(rows)}
        except json.JSONDecodeError:
            errors.append(f"{transport}:JSONDecodeError")
            return [], {
                "transport": transport,
                "http_status": status if "status" in locals() else None,
                "ok": False,
                "parse_failed": True,
                "errors": errors,
            }
        except Exception as exc:  # noqa: BLE001 - record each transport failure
            errors.append(f"{transport}:{type(exc).__name__}")
    return [], {"transport": None, "http_status": None, "ok": False, "errors": errors}


def acquire_probe_slice(
    *,
    query_family: str,
    query: str,
    start: datetime,
    end: datetime,
    manifest_path: Path,
    max_attempts: int = 5,
    timeout: int = 15,
    backoff_seconds: tuple[int, ...] = BACKOFF_SECONDS,
    split_network_failures: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Acquire one bounded slice, splitting saturation and failures into smaller slices."""

    completed = load_complete_slices(manifest_path)
    queue: list[tuple[datetime, datetime]] = [(start, end)]
    records: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    while queue:
        slice_start, slice_end = queue.pop(0)
        key = (query_family, slice_start.isoformat(), slice_end.isoformat())
        if key in completed:
            continue
        url = window_query(query, slice_start, slice_end)
        append_manifest(
            manifest_path,
            _manifest_line(
                query_family=query_family,
                start=slice_start,
                end=slice_end,
                transport=None,
                attempts=0,
                http_status=None,
                record_count=0,
                saturated=False,
                status="PENDING",
            ),
        )
        rows: list[dict[str, Any]] = []
        meta: dict[str, Any] = {"ok": False}
        attempts_used = 0
        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            rows, meta = fetch_transport(url, timeout=timeout)
            if meta.get("ok"):
                break
            if attempt < max_attempts and backoff_seconds:
                time.sleep(backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)])
        if not meta.get("ok"):
            row = _manifest_line(
                query_family=query_family,
                start=slice_start,
                end=slice_end,
                transport=meta.get("transport"),
                attempts=attempts_used,
                http_status=meta.get("http_status"),
                record_count=0,
                saturated=False,
                status="PARSE_FAILED" if meta.get("parse_failed") else "NETWORK_FAILED",
            )
            if split_network_failures and split_window(slice_start, slice_end) is not None:
                midpoint = slice_start + (slice_end - slice_start) / 2
                row["split_for_recovery"] = True
                queue[0:0] = [(slice_start, midpoint), (midpoint, slice_end)]
            append_manifest(manifest_path, row)
            manifest_rows.append(row)
            continue
        if len(rows) >= MAX_RECORDS and split_window(slice_start, slice_end) is not None:
            midpoint = slice_start + (slice_end - slice_start) / 2
            row = _manifest_line(
                query_family=query_family,
                start=slice_start,
                end=slice_end,
                transport=meta.get("transport"),
                attempts=attempts_used,
                http_status=meta.get("http_status"),
                record_count=len(rows),
                saturated=True,
                status="SATURATED",
            )
            append_manifest(manifest_path, row)
            manifest_rows.append(row)
            queue[0:0] = [(slice_start, midpoint), (midpoint, slice_end)]
            continue
        row = _manifest_line(
            query_family=query_family,
            start=slice_start,
            end=slice_end,
            transport=meta.get("transport"),
            attempts=attempts_used,
            http_status=meta.get("http_status"),
            record_count=len(rows),
            saturated=False,
            status="COMPLETE",
        )
        append_manifest(manifest_path, row)
        manifest_rows.append(row)
        records.extend(rows)
    return records, manifest_rows


def _manifest_line(
    *,
    query_family: str,
    start: datetime,
    end: datetime,
    transport: str | None,
    attempts: int,
    http_status: int | None,
    record_count: int,
    saturated: bool,
    status: str,
    response: str = "",
) -> dict[str, Any]:
    return {
        "query_family": query_family,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "window_seconds": int((end - start).total_seconds()),
        "transport": transport,
        "attempts": attempts,
        "http_status": http_status,
        "record_count": record_count,
        "saturated": saturated,
        "status": status,
        "response_hash": hashlib.sha256(response.encode("utf-8")).hexdigest() if response else None,
    }


def append_manifest(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_canonical_records(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {str(item.get("record_id")) for item in load_manifest(path) if item.get("record_id")}
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            record_id = str(row.get("record_id") or "")
            if not record_id or record_id in existing:
                continue
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            existing.add(record_id)


def load_complete_slices(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    completed: set[tuple[str, str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "COMPLETE":
            completed.add((str(row.get("query_family")), str(row.get("start")), str(row.get("end"))))
    return completed


def dedupe_records(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "").strip()
        title = re.sub(r"[^a-z0-9]+", " ", str(row.get("title") or "").lower()).strip()
        if not url and not title:
            continue
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        result.append(row)
    return result


def canonical_news_record(
    row: dict[str, Any],
    *,
    query_family: str,
    source_transport: str | None,
    retrieved_at: datetime,
) -> dict[str, Any]:
    """Map DOC/GKG-like rows to the frozen point-in-time news schema."""

    url = str(row.get("url") or row.get("DocumentIdentifier") or "").strip()
    title = str(row.get("title") or row.get("PAGE_TITLE") or "").strip()
    seen_raw = row.get("seendate") or row.get("DATE") or row.get("gdelt_seen_at")
    seen_at = _parse_timestamp(str(seen_raw)) if seen_raw else None
    published_raw = row.get("published_at") or row.get("publishdate") or row.get("published")
    published_at = _parse_timestamp(str(published_raw)) if published_raw else None
    domain = str(row.get("domain") or row.get("sourcecountry") or "").strip() or None
    raw_payload = json.dumps(row, sort_keys=True, ensure_ascii=False)
    return {
        "record_id": hashlib.sha256(f"{url}|{title}|{seen_raw}".encode()).hexdigest(),
        "source": "GDELT",
        "source_transport": source_transport,
        "query_family": query_family,
        "gdelt_seen_at": seen_at.isoformat() if seen_at else None,
        "published_at": published_at.isoformat() if published_at else None,
        "published_at_quality": "exact" if published_at else "gdelt_seen_time_only",
        "title": title,
        "title_hash": hashlib.sha256(title.lower().encode("utf-8")).hexdigest() if title else None,
        "url": url,
        "url_hash": hashlib.sha256(url.encode("utf-8")).hexdigest() if url else None,
        "domain": domain,
        "language": row.get("language"),
        "themes": row.get("themes") or row.get("V2Themes") or [],
        "organizations": row.get("organizations") or row.get("V2Organizations") or [],
        "retrieved_at": retrieved_at.astimezone(UTC).isoformat(),
        "raw_record_hash": hashlib.sha256(raw_payload.encode("utf-8")).hexdigest(),
    }


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def resolve_release_timestamp(text: str, *, release_date: date) -> dict[str, Any]:
    match = re.search(r"For release at\s+(\d{1,2}:\d{2}\s*[ap]\.m\.)\s+(EST|EDT)", text, re.I)
    if not match:
        return {"status": "UNVERIFIED_RELEASE_TIMESTAMP", "release_date": release_date.isoformat()}
    clock = re.sub(r"\s+", " ", match.group(1).lower()).strip().replace("a.m.", "AM").replace("p.m.", "PM")
    parsed = datetime.strptime(clock, "%I:%M %p").replace(tzinfo=ZoneInfo("America/New_York"))
    release = datetime.combine(release_date, parsed.timetz())
    return {
        "status": "VERIFIED",
        "release_date": release_date.isoformat(),
        "release_clock_text": match.group(1),
        "timezone_text": match.group(2).upper(),
        "release_at_utc": release.astimezone(UTC).isoformat(),
    }


def extract_statement_links(calendar_html: str, *, years: Iterable[int] = (2023, 2024, 2025)) -> list[dict[str, Any]]:
    wanted = "|".join(str(year) for year in years)
    links = sorted(set(re.findall(rf"/newsevents/pressreleases/monetary((?:{wanted})\d{{4}})a\.htm", calendar_html)))
    result: list[dict[str, Any]] = []
    for stamp in links:
        result.append(
            {
                "meeting_date": datetime.strptime(stamp, "%Y%m%d").date().isoformat(),
                "statement_path": f"/newsevents/pressreleases/monetary{stamp}a.htm",
            }
        )
    return result


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
