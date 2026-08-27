from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.run_microstructure_research_v3 as v3
from scripts.run_microstructure_research_v3 import Bucket, Feature, _signal_indices


def _feature(
    timestamp: datetime,
    imbalance: float,
    price_return: float,
    *,
    oi_change_15m: float | None = None,
    oi_zscore: float | None = None,
) -> Feature:
    return Feature(
        timestamp,
        100.0,
        101.0,
        99.0,
        100.0 * (1 + price_return),
        1.0,
        price_return,
        imbalance,
        imbalance * 1_000,
        abs(price_return) / (abs(imbalance) + 1e-9),
        100.0,
        100.0,
        1.0,
        oi_change_15m,
        oi_change_15m,
        oi_zscore,
        0.0,
    )


def test_buyer_maker_direction_semantics() -> None:
    bucket = Bucket(datetime(2024, 1, 1, tzinfo=UTC))
    bucket.add(100.0, 2.0, False)
    bucket.add(100.0, 1.0, True)
    record = bucket.as_record()
    assert record["buy_qty"] == 2.0
    assert record["sell_qty"] == 1.0
    assert record["delta_notional"] == 100.0


def test_h1_signal_requires_next_bar_entry_and_future_rows_are_irrelevant() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [_feature(start + timedelta(minutes=5 * index), 0.25, 0.001) for index in range(24)]
    signals = _signal_indices("H1_AGGRESSOR_FLOW_CONTINUATION", rows)
    assert signals
    assert all(entry_index > 0 for entry_index, _, _ in signals)
    changed = rows[:]
    changed[-1] = _feature(changed[-1].timestamp, -0.99, -0.2)
    assert [
        signal for signal in _signal_indices("H1_AGGRESSOR_FLOW_CONTINUATION", changed) if signal[0] < len(rows) - 1
    ] == [signal for signal in signals if signal[0] < len(rows) - 1]


def test_missing_oi_does_not_generate_h3_signal() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [
        _feature(start + timedelta(minutes=5 * index), 0.3, 0.001, oi_change_15m=None, oi_zscore=None)
        for index in range(24)
    ]
    assert _signal_indices("H3_OI_FLOW_BUILDUP", rows) == []


def test_tls_first_attempt_fail_curl_fallback_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = b"valid archive"
    monkeypatch.setattr(v3, "_checksum", lambda _url: hashlib.sha256(payload).hexdigest())
    calls: list[str] = []

    def fake_transport(_url: str, part: Path, transport: str) -> int:
        calls.append(transport)
        if transport == "curl":
            raise ConnectionError("TLS EOF")
        part.write_bytes(payload)
        return len(payload)

    monkeypatch.setattr(v3, "_transport_download", fake_transport)
    monkeypatch.setattr(v3.time, "sleep", lambda _seconds: None)
    result = v3._download("https://example.test/archive.zip", tmp_path / "archive.zip")
    assert result["status"] == "CHECKSUM_VALID"
    assert result["transport"] == "requests"
    assert calls[:2] == ["curl", "requests"]
    assert (tmp_path / "archive.zip").read_bytes() == payload
    assert not (tmp_path / "archive.zip.part").exists()


def test_partial_download_is_never_valid_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = b"complete archive"
    monkeypatch.setattr(v3, "_checksum", lambda _url: hashlib.sha256(payload).hexdigest())

    def interrupted(_url: str, part: Path, _transport: str) -> int:
        part.write_bytes(b"partial")
        raise ConnectionError("connection reset")

    monkeypatch.setattr(v3, "_transport_download", interrupted)
    monkeypatch.setattr(v3.time, "sleep", lambda _seconds: None)
    result = v3._download("https://example.test/archive.zip", tmp_path / "archive.zip")
    assert result["status"] == "RETRY_EXHAUSTED"
    assert not (tmp_path / "archive.zip").exists()
    assert not (tmp_path / "archive.zip.part").exists()


def test_checksum_mismatch_rejects_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(v3, "_checksum", lambda _url: "0" * 64)
    monkeypatch.setattr(v3, "_transport_download", lambda _url, part, _transport: part.write_bytes(b"bad") or 3)
    monkeypatch.setattr(v3.time, "sleep", lambda _seconds: None)
    result = v3._download("https://example.test/archive.zip", tmp_path / "archive.zip")
    assert result["status"] == "CHECKSUM_FAILED"
    assert not (tmp_path / "archive.zip").exists()


def test_existing_valid_archive_is_not_redownloaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    destination = tmp_path / "archive.zip"
    destination.write_bytes(b"cached")
    monkeypatch.setattr(v3, "_checksum", lambda _url: hashlib.sha256(b"cached").hexdigest())
    monkeypatch.setattr(v3, "_transport_download", lambda *_args: (_ for _ in ()).throw(AssertionError("redownloaded")))
    result = v3._download("https://example.test/archive.zip", destination)
    assert result["status"] == "CHECKSUM_VALID"
    assert result["cached"] is True


def test_404_is_source_missing_without_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def missing(_url: str) -> str:
        nonlocal calls
        calls += 1
        raise v3._OfficialArchiveMissing(_url)

    monkeypatch.setattr(v3, "_checksum", missing)
    result = v3._download("https://example.test/archive.zip", tmp_path / "archive.zip")
    assert result["status"] == "HTTP_NOT_FOUND"
    assert result["classification"] == "SOURCE_ARCHIVE_NOT_PUBLISHED"
    assert calls == 1


def test_btc_failure_does_not_block_eth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(v3, "SYMBOLS", ("BTCUSDT", "ETHUSDT"))
    monkeypatch.setattr(v3, "_days", lambda _start, _end: ["2023-01-29", "2023-01-30"])

    def fake_download(url: str, destination: Path, **_kwargs: object) -> dict[str, object]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "BTCUSDT" in url:
            return {"url": url, "path": str(destination), "status": "RETRY_EXHAUSTED"}
        destination.write_bytes(b"ok")
        return {"url": url, "path": str(destination), "status": "CHECKSUM_VALID"}

    monkeypatch.setattr(v3, "_download", fake_download)
    remote, downloaded = v3._prepare_archives(tmp_path, v3.START, v3.HOLDOUT_START, False)
    assert all(row["status"] == "RETRY_EXHAUSTED" for row in remote["metrics"]["BTCUSDT"])
    assert len(downloaded["metrics"]["ETHUSDT"]) == 2


def test_future_holdout_date_is_not_in_daily_targets() -> None:
    periods = v3._days(v3.START, v3.HOLDOUT_START)
    assert "2026-01-28" in periods
    assert "2026-01-29" not in periods


def test_source_gap_is_reported_and_never_filled() -> None:
    rows = [
        {"period": "2023-01-29", "status": "CHECKSUM_VALID"},
        {"period": "2023-01-30", "status": "HTTP_NOT_FOUND"},
        {"period": "2023-01-31", "status": "CHECKSUM_VALID"},
    ]
    result = v3._metrics_coverage(rows, v3.START, v3.START + timedelta(days=3))
    assert result["missing_source_archives"] == ["2023-01-30"]
    assert result["archives_present"] == 2


def test_metrics_parser_accepts_binance_iso_create_time(tmp_path: Path) -> None:
    archive = tmp_path / "metrics.zip"
    import zipfile

    payload = (
        b"create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        b"2023-01-29 00:00:00,BTCUSDT,10,20,1,1,1,1\n"
    )
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("BTCUSDT-metrics.csv", payload)
    rows, quality = v3._parse_metrics(archive, v3.START, v3.START + timedelta(days=1))
    assert len(rows) == 1
    assert quality["invalid_rows"] == 0
