"""Export a redacted 30-day runtime ledger for git-portable review.

Layer: Review / Data (evidence side-path). Does not change Execution behavior.

Reads the hot Paper console DB (default ``.local_paper_console.db``) and writes:

- ``docs/evidence/runtime-ledger/current/manifest.json``
- ``docs/evidence/runtime-ledger/current/SUMMARY.md``
- ``docs/evidence/runtime-ledger/current/ledger.sqlite.gz``

Operator flow (ADR-073): export → commit → pull on another device → import → audit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SOURCE = Path(".local_paper_console.db")
DEFAULT_OUT_DIR = Path("docs/evidence/runtime-ledger/current")
DEFAULT_LOOKBACK_DAYS = 30

# (table, time_column | None for full copy of small reference tables)
LEDGER_TABLES: tuple[tuple[str, str | None], ...] = (
    ("order_executions", "created_at"),
    ("paper_runs", "created_at"),
    ("decision_snapshots", "created_at"),
    ("risk_events", "created_at"),
    ("live_runs", "created_at"),
    ("position_snapshots", "snapshot_time"),
    ("exchange_account_snapshots", "snapshot_time"),
    ("reconciliation_records", "created_at"),
)

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)


def _resolve_sqlite_path(database_url: str | None, source_path: Path) -> Path:
    if database_url:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise SystemExit(f"Only sqlite URLs are supported for export, got: {database_url}")
        return Path(database_url.removeprefix(prefix)).expanduser().resolve()
    return source_path.expanduser().resolve()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                out[key] = "***REDACTED***"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.dumps(_redact(json.loads(stripped)), ensure_ascii=False)
            except json.JSONDecodeError:
                return value
        return value
    return value


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _copy_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> None:
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"missing schema for table {table}")
    dst.execute(row[0])


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _export_table(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    *,
    table: str,
    time_column: str | None,
    since: str,
) -> int:
    if not _table_exists(src, table):
        return 0
    _copy_schema(src, dst, table)
    columns = _column_names(src, table)
    col_sql = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    if time_column is None:
        rows = src.execute(f'SELECT {col_sql} FROM "{table}"').fetchall()
    else:
        rows = src.execute(
            f'SELECT {col_sql} FROM "{table}" WHERE "{time_column}" >= ?',
            (since,),
        ).fetchall()
    if not rows:
        return 0
    redacted_rows: list[tuple[Any, ...]] = []
    for row in rows:
        values = list(row)
        for idx, _col in enumerate(columns):
            values[idx] = _redact(values[idx])
        redacted_rows.append(tuple(values))
    dst.executemany(
        f'INSERT INTO "{table}" ({col_sql}) VALUES ({placeholders})',
        redacted_rows,
    )
    return len(redacted_rows)


def _export_strategies(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    if not _table_exists(src, "strategies"):
        return 0
    _copy_schema(src, dst, "strategies")
    columns = _column_names(src, "strategies")
    col_sql = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    strategy_ids: set[str] = set()
    if _table_exists(dst, "paper_runs"):
        strategy_ids.update(
            str(row[0])
            for row in dst.execute("SELECT DISTINCT strategy_id FROM paper_runs WHERE strategy_id IS NOT NULL")
        )
    if _table_exists(dst, "order_executions"):
        strategy_ids.update(
            str(row[0])
            for row in dst.execute("SELECT DISTINCT strategy_id FROM order_executions WHERE strategy_id IS NOT NULL")
        )
    if _table_exists(dst, "live_runs"):
        strategy_ids.update(
            str(row[0])
            for row in dst.execute("SELECT DISTINCT strategy_id FROM live_runs WHERE strategy_id IS NOT NULL")
        )
    if not strategy_ids:
        rows = src.execute(f"SELECT {col_sql} FROM strategies").fetchall()
    else:
        qmarks = ", ".join("?" for _ in strategy_ids)
        rows = src.execute(
            f"SELECT {col_sql} FROM strategies WHERE id IN ({qmarks})",
            tuple(strategy_ids),
        ).fetchall()
    if not rows:
        return 0
    redacted = [tuple(_redact(v) for v in row) for row in rows]
    dst.executemany(
        f"INSERT INTO strategies ({col_sql}) VALUES ({placeholders})",
        redacted,
    )
    return len(redacted)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_summary(path: Path, *, manifest: dict[str, Any]) -> None:
    counts = manifest["table_counts"]
    lines = [
        "# Runtime Ledger Summary",
        "",
        f"- Exported at: `{manifest['exported_at']}`",
        f"- Window: `{manifest['since']}` → `{manifest['until']}` ({manifest['lookback_days']} days)",
        f"- Source DB: `{manifest['source_db']}`",
        f"- Source SHA256: `{manifest['source_sha256']}`",
        f"- Ledger gzip SHA256: `{manifest['ledger_gz_sha256']}`",
        "",
        "## Row counts",
        "",
        "| table | rows |",
        "| --- | ---: |",
    ]
    for table, count in sorted(counts.items()):
        lines.append(f"| {table} | {count} |")
    lines.extend(
        [
            "",
            "## How to analyze on another device",
            "",
            "```text",
            "agent-python -m scripts.import_runtime_ledger",
            "agent-python -m scripts.audit_decision_funnel "
            "--database-url sqlite:///.local_runtime_ledger.db --lookback-days 30",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def export_runtime_ledger(
    *,
    source_db: Path,
    out_dir: Path,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    if not source_db.is_file():
        raise SystemExit(f"source database not found: {source_db}")

    until = datetime.now(UTC)
    since = until - timedelta(days=lookback_days)
    since_param = since.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    out_dir.mkdir(parents=True, exist_ok=True)
    gz_path = out_dir / "ledger.sqlite.gz"
    manifest_path = out_dir / "manifest.json"
    summary_path = out_dir / "SUMMARY.md"

    with tempfile.TemporaryDirectory(prefix="runtime-ledger-") as tmp:
        tmp_db = Path(tmp) / "ledger.sqlite"
        src = sqlite3.connect(source_db)
        dst = sqlite3.connect(tmp_db)
        try:
            src.execute("PRAGMA query_only = ON")
            counts: dict[str, int] = {}
            for table, time_column in LEDGER_TABLES:
                counts[table] = _export_table(
                    src,
                    dst,
                    table=table,
                    time_column=time_column,
                    since=since_param,
                )
            counts["strategies"] = _export_strategies(src, dst)
            dst.commit()
        finally:
            dst.close()
            src.close()

        with tmp_db.open("rb") as raw, gzip.open(gz_path, "wb", compresslevel=9) as compressed:
            shutil.copyfileobj(raw, compressed)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "exported_at": until.isoformat(),
        "lookback_days": lookback_days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "source_db": source_db.as_posix(),
        "source_sha256": _file_sha256(source_db),
        "ledger_gz_sha256": _file_sha256(gz_path),
        "table_counts": counts,
        "adr": "ADR-073",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_summary(summary_path, manifest=manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="sqlite:///... URL; defaults to --source-db / .local_paper_console.db",
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()
    source = _resolve_sqlite_path(args.database_url, args.source_db)
    manifest = export_runtime_ledger(
        source_db=source,
        out_dir=args.out_dir,
        lookback_days=args.lookback_days,
    )
    print(json.dumps(manifest["table_counts"], indent=2, ensure_ascii=False))
    print(f"wrote {args.out_dir / 'ledger.sqlite.gz'}")
    print(f"wrote {args.out_dir / 'manifest.json'}")
    print(f"wrote {args.out_dir / 'SUMMARY.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
