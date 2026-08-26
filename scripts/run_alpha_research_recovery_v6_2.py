"""Probe GDELT's official daily raw archives for V6 compatibility.

The probe downloads six representative GKG/Event ZIPs, validates transport and
ZIP integrity, inspects the contemporaneous GKG schema, and then removes the raw
bytes. It never calls DOC, requires BigQuery, loads the Final Holdout, or touches
the trading Runtime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests  # type: ignore[import-untyped]

PROBE_DATES = ("20230129", "20230701", "20240101", "20240701", "20250101", "20250701")
RAW_BASE = "https://data.gdeltproject.org"
GKG_2_FIELDS = (
    "GKGRECORDID", "DATE", "SourceCollectionIdentifier", "SourceCommonName", "DocumentIdentifier",
    "Counts", "V2Counts", "Themes", "V2Themes", "Locations", "V2Locations", "Persons", "V2Persons",
    "Organizations", "V2Organizations", "Tone", "EnhancedDates", "GCAM", "SharingImage", "RelatedImages",
    "SocialImageEmbeds", "SocialVideoEmbeds", "Quotations", "AllNames", "Amounts", "TranslationInfo", "Extras",
)
GKG_1_FIELDS = (
    "DATE", "NUMARTS", "COUNTS", "THEMES", "LOCATIONS", "PERSONS",
    "ORGANIZATIONS", "TONE", "CAMEOEVENTIDS", "SOURCES", "SOURCEURLS",
)
EVENT_REQUIRED_FIELDS = {
    "GLOBALEVENTID": 0,
    "SQLDATE": 1,
    "EventCode": 26,
    "QuadClass": 29,
    "GoldsteinScale": 30,
    "NumMentions": 31,
    "NumSources": 32,
    "NumArticles": 33,
    "AvgTone": 34,
    "DATEADDED": 56,
    "SOURCEURL": 57,
}
KEYWORDS = ("bitcoin", "btc", "ethereum", "eth", "cryptocurrency", "binance", "coinbase", "sec", "etf", "hack", "exploit", "bankruptcy", "stablecoin", "listing", "delisting")
FILTER_FIELDS = (
    "THEMES", "V1THEMES", "V2Themes", "Organizations", "ORGANIZATIONS",
    "V2Organizations", "DocumentIdentifier", "SourceCommonName", "SOURCES",
    "SOURCEURLS", "Extras",
)
STATUS_NETWORK = "BLOCKED_GDELT_RAW_ARCHIVE_NETWORK"
STATUS_SCHEMA = "BLOCKED_GDELT_RAW_SCHEMA_FOR_V6"
STATUS_TIMESTAMP = "BLOCKED_EVENT_TIMESTAMP_RESOLUTION"
STATUS_RECOVERED = "EVENT_DATA_ACQUISITION_RECOVERED"
FOMC_LEDGER_PATH = Path("artifacts/alpha_research_recovery_v6_1/FOMC_EVENT_LEDGER.json")


def archive_url(day: str, kind: str) -> str:
    if kind == "gkg":
        return f"{RAW_BASE}/gkg/{day}.gkg.csv.zip"
    if kind == "events":
        return f"{RAW_BASE}/events/{day}.export.CSV.zip"
    raise ValueError(kind)


def _curl_bytes(url: str, timeout: int) -> bytes:
    result = subprocess.run(
        ["curl.exe", "--silent", "--show-error", "--location", "--max-time", str(timeout), url],
        capture_output=True,
        check=False,
        timeout=timeout + 2,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl_exit_{result.returncode}")
    return result.stdout


def _powershell_bytes(url: str, timeout: int) -> bytes:
    escaped = url.replace("'", "''")
    command = f"$b=(Invoke-WebRequest -UseBasicParsing -TimeoutSec {timeout} -Uri '{escaped}').Content; [Convert]::ToBase64String($b)"
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout + 2,
    )
    if result.returncode != 0:
        raise RuntimeError(f"powershell_exit_{result.returncode}")
    return base64.b64decode(result.stdout.strip())


def fetch_archive(url: str, *, timeout: int = 15) -> tuple[bytes, dict[str, Any]]:
    errors: list[str] = []
    observed_statuses: list[int] = []
    for transport in ("curl", "requests", "powershell"):
        started = time.monotonic()
        status: int | None = None
        try:
            if transport == "curl":
                data = _curl_bytes(url, timeout)
                status = 200
            elif transport == "requests":
                response = requests.get(url, timeout=timeout)
                status = response.status_code
                response.raise_for_status()
                data = response.content
            else:
                data, status = _powershell_bytes(url, timeout), 200
            if status is not None:
                observed_statuses.append(status)
            if not data:
                raise RuntimeError(f"empty_body_http_{status}")
            return data, {
                "ok": True,
                "transport": transport,
                "http_status": status,
                "bytes": len(data),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        except Exception as exc:  # noqa: BLE001 - preserve per-archive evidence
            if status is not None and status not in observed_statuses:
                observed_statuses.append(status)
            errors.append(f"{transport}:{exc}")
    status = next((value for value in observed_statuses if value != 200), observed_statuses[-1] if observed_statuses else None)
    return b"", {"ok": False, "transport": None, "http_status": status, "observed_http_statuses": observed_statuses, "bytes": 0, "errors": errors}


def inspect_zip(data: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            bad = archive.testzip()
            members = archive.namelist()
            return {"zip_ok": bad is None, "bad_member": bad, "members": members, "member_count": len(members)}
    except (OSError, zipfile.BadZipFile) as exc:
        return {"zip_ok": False, "bad_member": None, "members": [], "member_count": 0, "error_type": type(exc).__name__}


def inspect_events(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "GDELT_EVENT_2.0_58COL_TSV",
        "field_count_expected": 58,
        "field_presence": dict.fromkeys(EVENT_REQUIRED_FIELDS, False),
        "sample_rows": 0,
        "malformed_rows": 0,
        "numeric_date_values": 0,
        "timestamp_values": 0,
        "source_url_values": 0,
    }
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        member = next((name for name in archive.namelist() if name.lower().endswith((".csv", ".tsv", ".txt"))), None)
        if member is None:
            result["error"] = "EVENT_DATA_MEMBER_NOT_FOUND"
            return result
        with archive.open(member) as raw:
            reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            for index, line in enumerate(reader):
                if index >= 10000:
                    break
                values = line.rstrip("\r\n").split("\t")
                if not any(values):
                    continue
                if len(values) < result["field_count_expected"]:
                    result["malformed_rows"] += 1
                    continue
                result["sample_rows"] += 1
                for name, position in EVENT_REQUIRED_FIELDS.items():
                    result["field_presence"][name] |= bool(values[position])
                if values[1].isdigit() and len(values[1]) == 8:
                    result["numeric_date_values"] += 1
                if values[56].isdigit() and len(values[56]) >= 8:
                    result["timestamp_values"] += 1
                if values[57].startswith(("http://", "https://")):
                    result["source_url_values"] += 1
    result["required_fields_present"] = all(result["field_presence"].values()) and result["malformed_rows"] == 0
    result["category_semantics_pass"] = result["required_fields_present"]
    return result


def reuse_fomc_ledger(path: Path = FOMC_LEDGER_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        events = payload.get("events", [])
        scheduled_verified = [
            event for event in events
            if event.get("scheduled_meeting") is True
            and event.get("status") == "VERIFIED"
            and event.get("release_time_verified") is True
        ]
        coverage = f"{len(scheduled_verified)}/24"
        status = "REUSED" if len(scheduled_verified) == 24 else "UNVERIFIED"
        return {"status": status, "ledger": str(path), "coverage": coverage, "event_count": len(events)}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "UNAVAILABLE", "ledger": str(path), "coverage": "0/24", "error": type(exc).__name__}


def inspect_gkg(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "UNKNOWN",
        "field_count_expected": 0,
        "fields": [],
        "field_presence": {},
        "sample_rows": 0,
        "keyword_matches": 0,
        "timestamp_values": 0,
        "minute_timestamp_values": 0,
        "non_midnight_timestamp_values": 0,
        "timestamp_resolution": "UNKNOWN",
    }
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        member = next((name for name in archive.namelist() if name.lower().endswith((".csv", ".tsv", ".txt"))), None)
        if member is None:
            result["error"] = "GKG_DATA_MEMBER_NOT_FOUND"
            return result
        with archive.open(member) as raw:
            reader = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            first_values: list[str] | None = None
            fields: tuple[str, ...] | None = None
            for index, line in enumerate(reader):
                if index >= 10000:
                    break
                values = line.rstrip("\r\n").split("\t")
                if not values or not any(values):
                    continue
                if first_values is None:
                    first_values = values
                    upper = {value.strip().upper() for value in values}
                    if "DATE" in upper and ("THEMES" in upper or "V2THEMES" in upper):
                        if "NUMARTS" in upper and "SOURCEURLS" in upper:
                            fields = GKG_1_FIELDS
                            result["schema"] = "GKG_1.0_daily_tsv"
                        else:
                            fields = GKG_2_FIELDS
                            result["schema"] = "GKG_2.x_daily_tsv"
                        result["field_count_expected"] = len(fields)
                        result["fields"] = list(fields)
                        result["field_presence"] = dict.fromkeys(fields, False)
                        continue
                    if len(values) >= len(GKG_2_FIELDS):
                        fields = GKG_2_FIELDS
                        result["schema"] = "GKG_2.x_daily_tsv_unheaded"
                    elif len(values) >= len(GKG_1_FIELDS):
                        fields = GKG_1_FIELDS
                        result["schema"] = "GKG_1.0_daily_tsv_unheaded"
                    else:
                        result["error"] = "GKG_HEADER_OR_FIELD_COUNT_NOT_RECOGNIZED"
                        return result
                    result["field_count_expected"] = len(fields)
                    result["fields"] = list(fields)
                    result["field_presence"] = dict.fromkeys(fields, False)
                if fields is None or len(values) < len(fields):
                    continue
                result["sample_rows"] += 1
                row = dict(zip(fields, values, strict=False))
                for field in fields:
                    result["field_presence"][field] |= bool(row.get(field))
                text = " ".join(row.get(field, "") for field in FILTER_FIELDS if field in row).lower()
                if any(keyword in text for keyword in KEYWORDS):
                    result["keyword_matches"] += 1
                stamp = row.get("DATE", "")
                if stamp.isdigit() and len(stamp) >= 8:
                    result["timestamp_values"] += 1
                    if len(stamp) >= 12:
                        result["minute_timestamp_values"] += 1
                    if len(stamp) >= 14 and stamp[8:14] != "000000":
                        result["non_midnight_timestamp_values"] += 1
    canonical_requirements = {
        "DATE": ("DATE",),
        "THEMES": ("THEMES", "V1THEMES", "V2Themes"),
        "ORGANIZATIONS": ("ORGANIZATIONS", "Organizations", "V2Organizations"),
        "PERSONS": ("PERSONS", "Persons", "V2Persons"),
        "TONE": ("TONE", "Tone"),
        "SOURCES": ("SOURCES", "SourceCommonName"),
        "SOURCEURLS": ("SOURCEURLS", "DocumentIdentifier"),
    }
    result["canonical_field_presence"] = {
        canonical: any(result["field_presence"].get(alias, False) for alias in aliases)
        for canonical, aliases in canonical_requirements.items()
    }
    result["required_fields_present"] = all(result["canonical_field_presence"].values())
    result["filter_has_matches"] = result["keyword_matches"] > 0
    result["minute_timestamp_pass"] = result["sample_rows"] > 0 and result["minute_timestamp_values"] == result["sample_rows"] and result["non_midnight_timestamp_values"] > 0
    if result["timestamp_values"] == 0:
        result["timestamp_resolution"] = "MISSING"
    elif result["minute_timestamp_values"] == result["timestamp_values"]:
        result["timestamp_resolution"] = "MINUTE_OR_BETTER"
    else:
        result["timestamp_resolution"] = "DAY_ONLY"
    return result


def run_probe(*, output_dir: Path, timeout: int = 15) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    gkg_ok = events_ok = 0
    schemas: list[dict[str, Any]] = []
    event_schemas: list[dict[str, Any]] = []
    for day in PROBE_DATES:
        for kind in ("gkg", "events"):
            url = archive_url(day, kind)
            data, meta = fetch_archive(url, timeout=timeout)
            integrity = inspect_zip(data) if data else {"zip_ok": False, "members": [], "member_count": 0}
            item = {"date": day, "kind": kind, "url": url, **meta, **integrity}
            if kind == "gkg" and data and integrity.get("zip_ok"):
                schema = inspect_gkg(data)
                item["schema_probe"] = schema
                schemas.append(schema)
                gkg_ok += 1
            elif kind == "gkg" and data and not integrity.get("zip_ok"):
                item["schema_probe"] = {"status": "ZIP_INTEGRITY_FAILED"}
            if kind == "events" and data and integrity.get("zip_ok"):
                event_schema = inspect_events(data)
                item["event_schema_probe"] = event_schema
                event_schemas.append(event_schema)
                events_ok += 1
            records.append(item)
    network_pass = gkg_ok == len(PROBE_DATES) and events_ok == len(PROBE_DATES)
    schema_pass = (
        bool(schemas)
        and bool(event_schemas)
        and all(item.get("required_fields_present") and item.get("filter_has_matches") for item in schemas)
        and all(item.get("required_fields_present") and item.get("category_semantics_pass") for item in event_schemas)
    )
    timestamp_pass = bool(schemas) and all(item.get("minute_timestamp_pass") for item in schemas)
    if not network_pass:
        status = STATUS_NETWORK
    elif not schema_pass:
        status = STATUS_SCHEMA
    elif not timestamp_pass:
        status = STATUS_TIMESTAMP
    else:
        status = STATUS_RECOVERED
    report = {
        "status": status,
        "probe_dates": list(PROBE_DATES),
        "gkg_files_ok": gkg_ok,
        "event_files_ok": events_ok,
        "network_pass": network_pass,
        "schema_pass": schema_pass,
        "historical_timestamp_resolution": "MINUTE_OR_BETTER" if timestamp_pass else "INSUFFICIENT",
        "timestamp_pass": timestamp_pass,
        "category_semantics": "PASS" if schema_pass else "FAIL",
        "estimated_historical_download": "NOT_ESTIMATED_PROBE_ONLY",
        "records": records,
        "fomc": reuse_fomc_ledger(),
        "v6_resumed": False,
        "final_holdout_accessed": False,
        "runtime_modified": False,
        "production": "NOT_GRANTED",
        "raw_archives_retained": False,
        "probe_completed_at": datetime.now(UTC).isoformat(),
    }
    (output_dir / "RAW_ARCHIVE_PROBE.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/alpha_research_recovery_v6_2"))
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()
    report = run_probe(output_dir=args.output_dir, timeout=max(1, args.timeout))
    print(json.dumps({key: report[key] for key in ("status", "gkg_files_ok", "event_files_ok", "schema_pass", "timestamp_pass", "fomc", "v6_resumed")}, indent=2, ensure_ascii=False))
    return 0 if report["status"] == STATUS_RECOVERED else 2


if __name__ == "__main__":
    raise SystemExit(main())
