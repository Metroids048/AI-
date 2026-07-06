from __future__ import annotations

import re
from pathlib import Path

LOCAL_WINDOWS_PATH = re.compile(r"C:\\Users\\Windows11\\Desktop\\量化项目")


def _portable_markdown_files(root: Path) -> list[Path]:
    files = [root / "README.md", root / "AGENTS.md"]
    files.extend((root / "docs").rglob("*.md"))
    files.extend((root / "services").rglob("README.md"))
    files.extend((root / "research_source").rglob("README.md"))
    return sorted(path for path in files if path.exists())


def test_user_facing_markdown_does_not_link_to_local_windows_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in _portable_markdown_files(root):
        text = path.read_text(encoding="utf-8")
        if LOCAL_WINDOWS_PATH.search(text):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []
