from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from scripts.v6_event_acquisition import (
    acquire_probe_slice,
    adaptive_windows,
    append_manifest,
    canonical_news_record,
    dedupe_records,
    extract_statement_links,
    load_complete_slices,
    resolve_release_timestamp,
    split_window,
    window_query,
)


def test_timeout_split_stops_at_one_hour() -> None:
    start = datetime(2023, 1, 29)
    end = datetime(2023, 1, 30)
    first = split_window(start, end)
    assert first == (start, datetime(2023, 1, 29, 12))
    assert split_window(start, datetime(2023, 1, 29, 1)) is None


def test_saturation_window_query_is_explicit_and_resume_skips_complete(tmp_path: Path) -> None:
    url = window_query("bitcoin", datetime(2023, 1, 29), datetime(2023, 1, 30))
    assert "maxrecords=250" in url
    manifest = tmp_path / "GDELT_ACQUISITION_MANIFEST.jsonl"
    row = {
        "query_family": "Q01",
        "start": "2023-01-29T00:00:00+00:00",
        "end": "2023-01-29T01:00:00+00:00",
        "status": "COMPLETE",
    }
    append_manifest(manifest, row)
    assert ("Q01", row["start"], row["end"]) in load_complete_slices(manifest)


def test_duplicate_urls_and_titles_collapse_to_one_record() -> None:
    rows = [
        {"url": "https://a", "title": "Bitcoin ETF"},
        {"url": "https://a", "title": "other"},
        {"url": "https://b", "title": "Bitcoin ETF"},
    ]
    assert len(dedupe_records(rows)) == 1


def test_fomc_edt_and_est_convert_with_dst() -> None:
    edt = resolve_release_timestamp("For release at 2:00 p.m. EDT", release_date=date(2023, 6, 14))
    est = resolve_release_timestamp("For release at 2:00 p.m. EST", release_date=date(2023, 1, 31))
    assert edt["release_at_utc"].startswith("2023-06-14T18:00:00")
    assert est["release_at_utc"].startswith("2023-01-31T19:00:00")


def test_fomc_missing_release_time_is_unverified() -> None:
    payload = resolve_release_timestamp("FOMC statement", release_date=date(2024, 1, 31))
    assert payload["status"] == "UNVERIFIED_RELEASE_TIMESTAMP"


def test_statement_links_are_extracted_for_scheduled_years() -> None:
    html = '<a href="/newsevents/pressreleases/monetary20230201a.htm">HTML</a>'
    rows = extract_statement_links(html, years=(2023,))
    assert rows == [{"meeting_date": "2023-02-01", "statement_path": "/newsevents/pressreleases/monetary20230201a.htm"}]


def test_network_failure_splits_to_smaller_slices_and_other_slice_continues(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_fetch(url: str, *, timeout: int):
        calls.append((url, timeout))
        if "20230129000000" in url and "20230129120000" not in url:
            return [], {"ok": False, "transport": None, "http_status": None}
        return [{"url": url, "title": "event", "seendate": "20230129120000"}], {
            "ok": True,
            "transport": "requests",
            "http_status": 200,
        }

    monkeypatch.setattr("scripts.v6_event_acquisition.fetch_transport", fake_fetch)
    monkeypatch.setattr("scripts.v6_event_acquisition.time.sleep", lambda _: None)
    rows, manifest = acquire_probe_slice(
        query_family="Q01_BITCOIN",
        query="bitcoin",
        start=datetime(2023, 1, 29, tzinfo=UTC),
        end=datetime(2023, 1, 30, tzinfo=UTC),
        manifest_path=tmp_path / "manifest.jsonl",
        max_attempts=1,
        timeout=1,
    )
    assert rows
    assert any(item["status"] == "NETWORK_FAILED" and item.get("split_for_recovery") for item in manifest)
    assert any(item["status"] == "COMPLETE" for item in manifest)
    assert calls


def test_canonical_record_keeps_seen_time_when_publisher_time_is_missing() -> None:
    record = canonical_news_record(
        {"url": "https://example.test/a", "title": "Headline", "seendate": "20230129123000"},
        query_family="Q01_BITCOIN",
        source_transport="requests",
        retrieved_at=datetime(2023, 1, 29, 13, tzinfo=UTC),
    )
    assert record["gdelt_seen_at"].startswith("2023-01-29T12:30:00")
    assert record["published_at"] is None
    assert record["published_at_quality"] == "gdelt_seen_time_only"


def test_research_window_excludes_final_holdout() -> None:
    start = datetime(2023, 1, 29, tzinfo=UTC)
    end = datetime(2026, 1, 29, tzinfo=UTC)
    assert all(window_end <= end for _, window_end in adaptive_windows(start, end))
