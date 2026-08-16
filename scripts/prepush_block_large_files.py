"""Reject outgoing Git objects that are too large for the repository policy."""

from __future__ import annotations

import subprocess
import sys

MAX_BLOB_BYTES = 50 * 1024 * 1024


def _run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(args)}")
    return result.stdout


def _incoming_blobs(local_sha: str, remote_sha: str) -> list[tuple[str, str, int]]:
    rev_range = local_sha if remote_sha == "0" * 40 else f"{remote_sha}..{local_sha}"
    objects = _run("git", "rev-list", "--objects", rev_range)
    oversized: list[tuple[str, str, int]] = []
    for line in objects.splitlines():
        object_id, _, path = line.partition(" ")
        if not path:
            continue
        object_type = _run("git", "cat-file", "-t", object_id).strip()
        if object_type != "blob":
            continue
        size = int(_run("git", "cat-file", "-s", object_id).strip())
        if size > MAX_BLOB_BYTES:
            oversized.append((path, object_id, size))
    return oversized


def main() -> int:
    failures: list[tuple[str, str, int]] = []
    for line in sys.stdin:
        fields = line.strip().split()
        if len(fields) != 4:
            continue
        _, local_sha, _, remote_sha = fields
        failures.extend(_incoming_blobs(local_sha, remote_sha))

    if not failures:
        return 0

    print(
        "ERROR: refusing push — outgoing blob exceeds the 50 MiB repository policy.",
        file=sys.stderr,
    )
    for path, object_id, size in failures:
        print(f"  {size / (1024 * 1024):.2f} MiB  {path}  ({object_id})", file=sys.stderr)
    print("Keep generated raw data local or store it outside Git.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
