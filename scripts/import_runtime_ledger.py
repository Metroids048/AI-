"""Materialize the committed runtime ledger for local analysis.

Layer: Review / Data (evidence side-path).

Reads ``docs/evidence/runtime-ledger/current/ledger.sqlite.gz`` and writes a
gitignored working copy ``.local_runtime_ledger.db`` that existing auditors can
open via ``--database-url sqlite:///.local_runtime_ledger.db``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from pathlib import Path

DEFAULT_LEDGER_GZ = Path("docs/evidence/runtime-ledger/current/ledger.sqlite.gz")
DEFAULT_MANIFEST = Path("docs/evidence/runtime-ledger/current/manifest.json")
DEFAULT_TARGET = Path(".local_runtime_ledger.db")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_runtime_ledger(
    *,
    ledger_gz: Path,
    target_db: Path,
    manifest_path: Path | None = None,
    require_manifest_hash: bool = True,
) -> dict[str, object]:
    if not ledger_gz.is_file():
        raise SystemExit(f"ledger not found: {ledger_gz}. Run export on the trading machine and git pull first.")

    actual_hash = _sha256(ledger_gz)
    if require_manifest_hash and manifest_path is not None and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest.get("ledger_gz_sha256")
        if expected and expected != actual_hash:
            raise SystemExit(f"ledger hash mismatch: manifest={expected} file={actual_hash}. Re-export or git pull.")

    target_db.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_db.with_suffix(target_db.suffix + ".tmp")
    with gzip.open(ledger_gz, "rb") as compressed, tmp_path.open("wb") as raw:
        shutil.copyfileobj(compressed, raw)
    tmp_path.replace(target_db)
    return {
        "target_db": target_db.as_posix(),
        "ledger_gz": ledger_gz.as_posix(),
        "ledger_gz_sha256": actual_hash,
        "bytes": target_db.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-gz", type=Path, default=DEFAULT_LEDGER_GZ)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET)
    parser.add_argument(
        "--skip-hash-check",
        action="store_true",
        help="Do not compare ledger gzip SHA256 against manifest.json",
    )
    args = parser.parse_args()
    result = import_runtime_ledger(
        ledger_gz=args.ledger_gz,
        target_db=args.target_db,
        manifest_path=None if args.skip_hash_check else args.manifest,
        require_manifest_hash=not args.skip_hash_check,
    )
    print(json.dumps(result, indent=2))
    print(
        "Analyze with:\n"
        "  agent-python -m scripts.audit_decision_funnel "
        f"--database-url sqlite:///{Path(str(result['target_db'])).as_posix()} --lookback-days 30"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
