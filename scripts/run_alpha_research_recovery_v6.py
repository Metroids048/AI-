"""Public-news event-alpha data gate for ALPHA_RESEARCH_RECOVERY_V6.

V6 starts with a source audit.  GDELT is queried in narrow historical windows
and Federal Reserve FOMC pages are read without inferring directional surprise.
If point-in-time history cannot be proven, the runner stops before clustering,
labeling, replay, or any Runtime interaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests  # type: ignore[import-untyped]

from scripts.v6_event_acquisition import (
    QUERY_FAMILIES,
    acquire_probe_slice,
    append_canonical_records,
    canonical_news_record,
    dedupe_records,
    extract_statement_links,
    load_manifest,
    resolve_release_timestamp,
    strip_html,
)

STATUS_BLOCKED = "BLOCKED_EVENT_HISTORICAL_DATA"
RESEARCH_START = datetime(2023, 1, 29, tzinfo=UTC)
FINAL_HOLDOUT_START = datetime(2026, 1, 29, tzinfo=UTC)
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
WINDOW_DAYS = 7
PROBE_WINDOWS = 1
GDELT_TIMEOUT_SECONDS = 15
SCHEDULED_FOMC_DATES = {
    "2023-02-01",
    "2023-03-22",
    "2023-05-03",
    "2023-06-14",
    "2023-07-26",
    "2023-09-20",
    "2023-11-01",
    "2023-12-13",
    "2024-01-31",
    "2024-03-20",
    "2024-05-01",
    "2024-06-12",
    "2024-07-31",
    "2024-09-18",
    "2024-11-07",
    "2024-12-18",
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
}

_NOT_RUN_ARTIFACTS = (
    "EVENT_CLUSTER_LEDGER.parquet",
    "LABEL_QA.json",
    "H1_REGULATORY_ETF.json",
    "H2_SECURITY_SHOCK.json",
    "H3_ATTENTION_MOMENTUM.json",
    "FOMC_ATTRIBUTION.json",
    "LATENCY_STRESS.json",
    "VALIDATION_RESULTS.json",
)


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def iter_windows(start: datetime, end: datetime, *, days: int = WINDOW_DAYS) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    step = timedelta(days=days)
    while cursor < end:
        window_end = min(cursor + step, end)
        yield cursor, window_end
        cursor = window_end


def _window_query(start: datetime, end: datetime) -> str:
    params = {
        "query": "(bitcoin OR ethereum OR crypto OR cryptocurrency OR SEC OR ETF OR exchange OR hack OR exploit OR bankruptcy OR regulation OR stablecoin OR Binance OR Coinbase)",
        "mode": "artlist",
        "format": "json",
        "maxrecords": "250",
        "sort": "datedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    return f"{GDELT_DOC_URL}?{urlencode(params)}"


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def audit_gdelt(
    *,
    probe_windows: int = PROBE_WINDOWS,
    manifest_path: Path | None = None,
    query_families: Iterable[str] | None = None,
    max_attempts: int = 5,
    timeout: int = GDELT_TIMEOUT_SECONDS,
    split_network_failures: bool = True,
) -> dict[str, Any]:
    manifest_path = manifest_path or Path("artifacts/alpha_research_recovery_v6/GDELT_ACQUISITION_MANIFEST.jsonl")
    families = tuple(query_families or QUERY_FAMILIES)
    raw_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    windows = list(iter_windows(RESEARCH_START, FINAL_HOLDOUT_START, days=1))[: max(1, probe_windows)]
    for family in families:
        query = QUERY_FAMILIES[family]
        for start, end in windows:
            rows, outcomes = acquire_probe_slice(
                query_family=family,
                query=query,
                start=start,
                end=end,
                manifest_path=manifest_path,
                max_attempts=max_attempts,
                timeout=timeout,
                split_network_failures=split_network_failures,
            )
            raw_rows.extend([{**row, "_query_family": family} for row in rows])
            manifest_rows.extend(outcomes)
            probes.append(
                {"query_family": family, "start": start.isoformat(), "end": end.isoformat(), "outcomes": outcomes}
            )
    deduped = dedupe_records(raw_rows)
    retrieved_at = datetime.now(UTC)
    canonical = [
        canonical_news_record(
            row,
            query_family=str(row.get("_query_family") or (families[0] if families else "")),
            source_transport=None,
            retrieved_at=retrieved_at,
        )
        for row in deduped
    ]
    canonical_path = manifest_path.parent / "NEWS_RECORDS.jsonl"
    append_canonical_records(canonical_path, canonical)
    existing_canonical = []
    if canonical_path.exists():
        for line in canonical_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                existing_canonical.append(value)
    manifest_rows = load_manifest(manifest_path)
    seen_times: list[datetime] = []
    for row in existing_canonical:
        parsed = _parse_gdelt_time(str(row.get("gdelt_seen_at") or ""))
        if parsed is not None:
            seen_times.append(parsed)
    months = {item.strftime("%Y-%m") for item in seen_times if RESEARCH_START <= item < FINAL_HOLDOUT_START}
    expected_months: set[str] = set()
    year, month = RESEARCH_START.year, RESEARCH_START.month
    while (year, month) < (FINAL_HOLDOUT_START.year, FINAL_HOLDOUT_START.month):
        expected_months.add(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    leaf_failures = [
        item for item in manifest_rows if item.get("status") == "NETWORK_FAILED" and not item.get("split_for_recovery")
    ]
    unresolved_saturated = [item for item in manifest_rows if item.get("status") == "SATURATED"]
    transport_counts: dict[str, int] = {}
    for item in manifest_rows:
        transport = item.get("transport")
        if transport:
            transport_counts[str(transport)] = transport_counts.get(str(transport), 0) + 1
    usable_probe = bool(deduped and probes and not leaf_failures)
    history_proven = len(months) == len(expected_months) and not unresolved_saturated and not leaf_failures
    return {
        "source": "GDELT_DOC_2.0",
        "status": "PASS" if history_proven else "PROBE_ONLY" if usable_probe else "BLOCKED",
        "event_count": len(existing_canonical),
        "records": len(existing_canonical),
        "first_event": min(seen_times).isoformat() if seen_times else None,
        "last_event": max(seen_times).isoformat() if seen_times else None,
        "coverage_start": min(seen_times).isoformat() if seen_times else None,
        "coverage_end": max(seen_times).isoformat() if seen_times else None,
        "months": f"{len(months)}/{len(expected_months)}",
        "months_with_records": sorted(months),
        "expected_months": sorted(expected_months),
        "network_failed_slices": len(leaf_failures),
        "saturated_unresolved": len(unresolved_saturated),
        "transport_counts": transport_counts,
        "timestamp_coverage": "full" if history_proven else "probe_only",
        "publication_timestamp_available": any(row.get("published_at") for row in canonical),
        "duplicate_rate": round(1 - len(deduped) / len(raw_rows), 6) if raw_rows else 0.0,
        "usable": history_proven,
        "probe_windows": probes,
        "history_scope": {"start": RESEARCH_START.isoformat(), "end_exclusive": FINAL_HOLDOUT_START.isoformat()},
        "manifest_path": str(manifest_path),
        "records_path": str(canonical_path),
    }


def _parse_gdelt_time(value: str) -> datetime | None:
    text = value.strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def _fetch_statement_html(url: str) -> tuple[str, str]:
    """Fetch official HTML with a small transport fallback for intermittent TLS errors."""

    try:
        response = requests.get(url, timeout=GDELT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.text, "requests"
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["curl.exe", "--silent", "--show-error", "--max-time", str(GDELT_TIMEOUT_SECONDS), url],
            capture_output=True,
            text=True,
            check=False,
            timeout=GDELT_TIMEOUT_SECONDS + 2,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout, "curl"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "", ""


def audit_fomc() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": "FEDERAL_RESERVE_FOMC",
        "event_count": 0,
        "first_event": None,
        "last_event": None,
        "timestamp_coverage": "unavailable",
        "missing_periods": [],
        "duplicate_rate": 0.0,
        "publication_timestamp_available": False,
        "updated_article_rate": 0.0,
        "usable": False,
        "directional_alpha": False,
    }
    try:
        response = requests.get(FOMC_CALENDAR_URL, timeout=GDELT_TIMEOUT_SECONDS)
        response.raise_for_status()
        text = response.text
        years = sorted({int(year) for year in re.findall(r"\b(2023|2024|2025)\b", text)})
        payload["calendar_years_seen"] = years
        payload["calendar_retrieved"] = True
        statements = extract_statement_links(text, years=(2023, 2024, 2025))
        ledger: list[dict[str, Any]] = []
        for item in statements:
            statement_url = f"https://www.federalreserve.gov{item['statement_path']}"
            entry: dict[str, Any] = {
                **item,
                "statement_url": statement_url,
                "calendar_found": True,
                "scheduled_meeting": item["meeting_date"] in SCHEDULED_FOMC_DATES,
                "statement_found": False,
                "release_time_verified": False,
            }
            try:
                raw_statement, transport = _fetch_statement_html(statement_url)
                if not raw_statement:
                    raise RuntimeError("STATEMENT_FETCH_FAILED")
                statement_text = strip_html(raw_statement)
                resolved = resolve_release_timestamp(
                    statement_text, release_date=datetime.fromisoformat(item["meeting_date"]).date()
                )
                entry.update(resolved)
                entry["statement_found"] = True
                entry["release_time_verified"] = resolved["status"] == "VERIFIED"
                entry["source_transport"] = transport
                entry["source_hash"] = hashlib.sha256(raw_statement.encode("utf-8")).hexdigest()
                entry["release_text_hash"] = hashlib.sha256(statement_text.encode("utf-8")).hexdigest()
            except Exception as exc:  # noqa: BLE001 - preserve per-meeting evidence
                entry["error_type"] = type(exc).__name__
                entry["error"] = str(exc)[:240]
            ledger.append(entry)
        scheduled = [item for item in ledger if item.get("scheduled_meeting")]
        payload["scheduled_meetings"] = len(scheduled)
        payload["extraordinary_events"] = [item for item in ledger if not item.get("scheduled_meeting")]
        payload["verified_release_times"] = sum(bool(item.get("release_time_verified")) for item in scheduled)
        payload["coverage"] = f"{payload['verified_release_times']}/{payload['scheduled_meetings']}"
        payload["ledger"] = ledger
        payload["event_count"] = len(scheduled)
        release_times = [str(item["release_at_utc"]) for item in scheduled if item.get("release_at_utc")]
        payload["first_event"] = min(release_times) if release_times else None
        payload["last_event"] = max(release_times) if release_times else None
        payload["publication_timestamp_available"] = payload["verified_release_times"] > 0
        payload["usable"] = (
            payload["scheduled_meetings"] == len(SCHEDULED_FOMC_DATES)
            and payload["verified_release_times"] == payload["scheduled_meetings"]
        )
        payload["missing_periods"] = [] if payload["usable"] else ["unverified_statement_release_timestamp"]
    except Exception as exc:  # noqa: BLE001 - source audit records the failure
        payload["calendar_retrieved"] = False
        payload["error_type"] = type(exc).__name__
        payload["error"] = str(exc)[:240]
    return payload


def _write_empty_cluster_ledger(path: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError:
        fallback = Path.home() / ".agent-reach-venv" / "Lib" / "site-packages"
        if not fallback.exists():
            return
        import sys

        sys.path.insert(0, str(fallback))
        import pyarrow as pa
        import pyarrow.parquet as pq
    schema = pa.schema(
        [
            ("event_cluster_id", pa.string()),
            ("first_published_at", pa.string()),
            ("source_count", pa.int64()),
            ("unique_domains", pa.list_(pa.string())),
            ("event_category", pa.string()),
            ("entities", pa.list_(pa.string())),
            ("directional_label", pa.string()),
            ("confidence", pa.float64()),
        ]
    )
    pq.write_table(pa.Table.from_pylist([], schema=schema), path)


def write_blocked_artifacts(*, output_dir: Path, audits: list[dict[str, Any]], blocker: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gdelt = next((item for item in audits if item.get("source") == "GDELT_DOC_2.0"), audits[0] if audits else {})
    fomc = next(
        (item for item in audits if item.get("source") == "FEDERAL_RESERVE_FOMC"), audits[1] if len(audits) > 1 else {}
    )
    _dump(
        output_dir / "EVENT_DATA_AUDIT.json",
        {
            "status": STATUS_BLOCKED,
            "gdelt": gdelt,
            "fomc": fomc,
            "sources": audits,
            "event_research_ready": False,
            "blocker": blocker,
        },
    )
    if fomc.get("ledger"):
        _dump(output_dir / "FOMC_EVENT_LEDGER.json", {"source": fomc.get("source"), "events": fomc["ledger"]})
    for name in _NOT_RUN_ARTIFACTS:
        if name.endswith(".json"):
            _dump(
                output_dir / name,
                {
                    "status": "NOT_RUN",
                    "reason": STATUS_BLOCKED,
                    "final_holdout_accessed": False,
                    "runtime_modified": False,
                    "production_authority": "NOT_GRANTED",
                },
            )
    _write_empty_cluster_ledger(output_dir / "EVENT_CLUSTER_LEDGER.parquet")
    _dump(
        output_dir / "RESEARCH_PLAN.json",
        {
            "version": "alpha_research_recovery_v6",
            "name": "PUBLIC_NEWS_EVENT_ALPHA",
            "sources": ["GDELT_DOC_2.0", "FEDERAL_RESERVE_FOMC"],
            "research_start": RESEARCH_START.isoformat(),
            "final_holdout_start": FINAL_HOLDOUT_START.isoformat(),
            "runtime_frozen": True,
            "status": STATUS_BLOCKED,
            "blocker": blocker,
            "hypotheses": ["REGULATORY_ETF_EVENT_V1", "SECURITY_EXCHANGE_SHOCK_V1", "NEWS_ATTENTION_MOMENTUM_V1"],
        },
    )
    report = {
        "status": STATUS_BLOCKED,
        "data": {"sources": audits, "events": sum(int(item.get("event_count", 0)) for item in audits), "clusters": 0},
        "label_qa": {"status": "NOT_RUN"},
        "h1": {"status": "NOT_RUN"},
        "h2": {"status": "NOT_RUN"},
        "h3": {"status": "NOT_RUN"},
        "fomc": {"status": "ATTRIBUTION_NOT_RUN"},
        "latency": {"status": "NOT_RUN", "scenarios_minutes": [15, 30, 60]},
        "survivor": None,
        "runtime_during_v6": {"status": "READ_ONLY_OBSERVATION_NOT_STARTED"},
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production": "NOT_GRANTED",
        "blocker": blocker,
    }
    _dump(output_dir / "FINAL_REPORT.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/alpha_research_recovery_v6"))
    parser.add_argument("--probe-windows", type=int, default=PROBE_WINDOWS)
    parser.add_argument("--query-family", action="append", choices=tuple(QUERY_FAMILIES), dest="query_families")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=GDELT_TIMEOUT_SECONDS)
    parser.add_argument("--expand-network-failures", action="store_true")
    args = parser.parse_args()
    manifest = args.output_dir / "GDELT_ACQUISITION_MANIFEST.jsonl"
    gdelt = audit_gdelt(
        probe_windows=max(1, args.probe_windows),
        manifest_path=manifest,
        query_families=args.query_families,
        max_attempts=max(1, min(args.max_attempts, 5)),
        timeout=max(1, args.timeout),
        split_network_failures=args.expand_network_failures,
    )
    fomc = audit_fomc()
    gdelt_pass = bool(gdelt.get("usable"))
    if not gdelt_pass:
        blocker = (
            "GDELT_HISTORICAL_SOURCE_UNAVAILABLE"
            if gdelt.get("status") == "BLOCKED"
            else "GDELT_FULL_HISTORY_NOT_PROVEN"
        )
        report = write_blocked_artifacts(output_dir=args.output_dir, audits=[gdelt, fomc], blocker=blocker)
        print(
            json.dumps(
                {"status": report["status"], "blocker": blocker, "gdelt": gdelt, "fomc": fomc},
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2

    # The original V6 clustering/replay stage is intentionally not reimplemented here;
    # data recovery is reported separately so a ready source cannot be mistaken for
    # completed H1/H2/H3 evidence.
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _dump(
        output_dir / "EVENT_DATA_AUDIT.json",
        {"status": "EVENT_DATA_ACQUISITION_RECOVERED", "gdelt": gdelt, "fomc": fomc, "event_research_ready": True},
    )
    _dump(output_dir / "FOMC_EVENT_LEDGER.json", {"source": fomc.get("source"), "events": fomc.get("ledger", [])})
    report = {
        "status": "EVENT_DATA_ACQUISITION_RECOVERED",
        "v6_resumed": False,
        "v6_resume_reason": "ORIGINAL_V6_RESEARCH_STAGE_NOT_PRESENT_IN_RUNNER",
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production": "NOT_GRANTED",
    }
    _dump(output_dir / "FINAL_REPORT.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
