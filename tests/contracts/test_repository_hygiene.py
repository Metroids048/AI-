from __future__ import annotations

import subprocess
from pathlib import Path

FORBIDDEN_TRACKED_SUFFIXES = (".db", ".sqlite", ".sqlite3")
FORBIDDEN_TRACKED_NAMES = {".env"}
FORBIDDEN_SECRET_SUFFIXES = (".pem", ".key")


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_runtime_databases_and_secret_files_are_not_tracked() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for tracked in _tracked_files(root):
        path = Path(tracked)
        suffix = path.suffix.lower()
        if path.name in FORBIDDEN_TRACKED_NAMES:
            offenders.append(tracked)
        if suffix in FORBIDDEN_TRACKED_SUFFIXES:
            offenders.append(tracked)
        if suffix in FORBIDDEN_SECRET_SUFFIXES and path.name != ".env.example":
            offenders.append(tracked)

    assert offenders == []


def test_gitignore_covers_runtime_database_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")

    for required_pattern in (
        "/.dev_ai_quant.db",
        "/.pytest_ai_quant.*.db",
        "/.local/",
        "*.sqlite",
        "*.sqlite3",
    ):
        assert required_pattern in gitignore
