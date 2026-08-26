from __future__ import annotations

import io
import zipfile
from pathlib import Path

from scripts.run_alpha_research_recovery_v6_2 import (
    PROBE_DATES,
    STATUS_NETWORK,
    archive_url,
    inspect_events,
    inspect_gkg,
    inspect_zip,
    run_probe,
)


def _zip_bytes(name: str, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


def test_archive_urls_are_official_and_probe_is_six_by_two() -> None:
    assert archive_url("20230129", "gkg") == "https://data.gdeltproject.org/gkg/20230129.gkg.csv.zip"
    assert archive_url("20230129", "events") == "https://data.gdeltproject.org/events/20230129.export.CSV.zip"
    assert len(PROBE_DATES) == 6


def test_zip_integrity_and_gkg_schema_probe() -> None:
    fields = [
        "GKGRECORDID", "20230129123000", "1", "Example", "https://example.test/a",
        "", "", "bitcoin", "bitcoin", "", "", "Alice", "Alice", "Binance", "Binance",
        "1,2,3", "", "", "", "", "", "", "", "", "", "", "<PAGE_TITLE>Bitcoin</PAGE_TITLE>",
    ]
    data = _zip_bytes("20230129.gkg.csv", ("\t".join(fields) + "\n").encode())
    assert inspect_zip(data)["zip_ok"] is True
    result = inspect_gkg(data)
    assert result["required_fields_present"] is True
    assert result["filter_has_matches"] is True
    assert result["minute_timestamp_pass"] is True


def test_gkg_1_0_header_and_day_date_remain_day_level() -> None:
    header = (
        "DATE\tNUMARTS\tCOUNTS\tTHEMES\tLOCATIONS\tPERSONS\tORGANIZATIONS\t"
        "TONE\tCAMEOEVENTIDS\tSOURCES\tSOURCEURLS\n"
    )
    row = "20250701\t1\t\tbitcoin#1\t\tAlice\tBinance\t0,0,0\t\texample.com\thttps://example.test/item\n"
    result = inspect_gkg(_zip_bytes("20250701.gkg.csv", (header + row).encode()))
    assert result["schema"] == "GKG_1.0_daily_tsv"
    assert result["required_fields_present"] is True
    assert result["filter_has_matches"] is True
    assert result["timestamp_values"] == 1
    assert result["minute_timestamp_values"] == 0
    assert result["non_midnight_timestamp_values"] == 0
    assert result["timestamp_resolution"] == "DAY_ONLY"
    assert result["minute_timestamp_pass"] is False


def test_event_archive_probe_checks_58_column_semantics() -> None:
    fields = [""] * 58
    fields[0] = "123456789"
    fields[1] = "20250701"
    fields[26] = "190"
    fields[29] = "3"
    fields[30] = "1.5"
    fields[31] = "2"
    fields[32] = "1"
    fields[33] = "2"
    fields[34] = "0.1"
    fields[56] = "20250701123000"
    fields[57] = "https://example.test/event"
    result = inspect_events(_zip_bytes("20250701.export.CSV", ("\t".join(fields) + "\n").encode()))
    assert result["sample_rows"] == 1
    assert result["required_fields_present"] is True
    assert result["category_semantics_pass"] is True
    assert result["timestamp_values"] == 1
    assert result["source_url_values"] == 1


def test_probe_preserves_network_block_and_does_not_claim_v6(monkeypatch, tmp_path: Path) -> None:
    def fail_fetch(url: str, *, timeout: int):
        return b"", {"ok": False, "transport": None, "http_status": None, "bytes": 0, "errors": ["test"]}

    monkeypatch.setattr("scripts.run_alpha_research_recovery_v6_2.fetch_archive", fail_fetch)
    report = run_probe(output_dir=tmp_path, timeout=1)
    assert report["status"] == STATUS_NETWORK
    assert report["gkg_files_ok"] == 0
    assert report["event_files_ok"] == 0
    assert report["v6_resumed"] is False
    assert report["final_holdout_accessed"] is False
    assert report["runtime_modified"] is False
    assert report["production"] == "NOT_GRANTED"
    assert report["raw_archives_retained"] is False
    assert report["fomc"]["status"] == "REUSED"
    assert report["fomc"]["coverage"] == "24/24"
    assert (tmp_path / "RAW_ARCHIVE_PROBE.json").exists()
