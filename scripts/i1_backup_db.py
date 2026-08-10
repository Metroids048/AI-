"""I-1 steps 8-9: full DB backup taken AFTER writer-zero, with SHA256 + size on disk.

Refuses to run unless the writer-zero preconditions still hold, so a backup can never
be taken while something is still writing. Verifies the copy byte-for-byte by hash and
records a manifest; the backup is the only rollback path for M-02 (D-A forbids a
legacy reader fallback).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DB = ".local_paper_console.db"
OUT_DIR = Path("artifacts/t0-i1-symbol-canonical-20260809")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def writers_present() -> tuple[int, int]:
    """Return (non-expired lease count, live scheduler/API process count)."""
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        leases = con.execute("SELECT COUNT(*) FROM scheduler_leases WHERE expires_at > ?", (now,)).fetchone()[0]
    finally:
        con.close()
    ps = (
        "@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and "
        "$_.CommandLine -match 'run-local-paper-scheduler|apps\\.api\\.local_server' }).Count"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=False
    ).stdout.strip()
    return leases, int(out or 0)


def main() -> int:
    leases, procs = writers_present()
    print(f"non-expired leases = {leases}   live scheduler/API processes = {procs}")
    if leases or procs:
        print("ABORT: writers still present; a backup here could be an inconsistent snapshot.")
        return 1

    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.exists(DB + suffix):
            print(f"ABORT: {DB + suffix} exists; DB is not cleanly closed.")
            return 1

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    dest = f"{DB}.backup_i1_{stamp}"
    if os.path.exists(dest):
        print(f"ABORT: {dest} already exists; refusing to overwrite.")
        return 1

    src_size = os.path.getsize(DB)
    print(f"\nhashing source ({src_size} bytes) ...")
    src_hash = sha256(DB)
    print(f"  source sha256 = {src_hash}")

    print(f"copying -> {dest}")
    shutil.copy2(DB, dest)

    dst_size = os.path.getsize(dest)
    print(f"hashing copy ({dst_size} bytes) ...")
    dst_hash = sha256(dest)
    print(f"  copy   sha256 = {dst_hash}")

    ok = src_hash == dst_hash and src_size == dst_size
    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": "I-1 step 8-9 backup taken after writer-zero",
        "source": {"path": DB, "size_bytes": src_size, "sha256": src_hash},
        "backup": {"path": dest, "size_bytes": dst_size, "sha256": dst_hash},
        "verified": ok,
        "writer_zero_at_backup": {"non_expired_leases": leases, "live_processes": procs},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "STEP08_BACKUP_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(dest + ".sha256").write_text(f"{dst_hash}  {dest}\n", encoding="utf-8")

    print(f"\nwritten -> {out}")
    print(f"written -> {dest}.sha256")
    print(f"\nBACKUP_VERIFIED = {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
