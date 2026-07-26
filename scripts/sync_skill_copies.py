"""Keep per-tool skill copies identical to their canonical source.

Cursor, Claude Code, and the tool-neutral `.agents` layout each discover skills
only under their own directory, so a shared skill has to exist as N copies on
disk. Maintaining those by hand is what produced the drift this script exists to
prevent: three byte-identical copies of `verify-work/SKILL.md` that nothing kept
in step, where editing one and forgetting the others silently gives each tool a
different definition of "verified".

Canonical source is `.agents/skills/<name>/` because that directory is not owned
by any single tool.

Usage:
    python scripts/sync_skill_copies.py --check   # verify, non-zero on drift (CI)
    python scripts/sync_skill_copies.py           # rewrite mirrors from canonical
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CANONICAL_ROOT = REPO_ROOT / ".agents" / "skills"
MIRROR_ROOTS = (
    REPO_ROOT / ".claude" / "skills",
    REPO_ROOT / ".cursor" / "skills",
)

# Skills that must exist identically for every tool. Add a name here only when
# the skill is genuinely tool-neutral.
SHARED_SKILLS = ("verify-work",)


def _canonical_files(skill: str) -> list[Path]:
    source_dir = CANONICAL_ROOT / skill
    if not source_dir.is_dir():
        return []
    return sorted(path for path in source_dir.rglob("*") if path.is_file())


def _drifted_pairs(skill: str) -> list[tuple[Path, Path, str]]:
    """Return (source, target, reason) for every mirror that is out of sync."""

    problems: list[tuple[Path, Path, str]] = []
    for source in _canonical_files(skill):
        relative = source.relative_to(CANONICAL_ROOT)
        for mirror_root in MIRROR_ROOTS:
            target = mirror_root / relative
            if not target.exists():
                problems.append((source, target, "missing"))
            elif not filecmp.cmp(source, target, shallow=False):
                problems.append((source, target, "content differs"))
    return problems


def _sync(skill: str) -> int:
    written = 0
    for source in _canonical_files(skill):
        relative = source.relative_to(CANONICAL_ROOT)
        for mirror_root in MIRROR_ROOTS:
            target = mirror_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of rewriting mirrors",
    )
    args = parser.parse_args()

    missing_canonical = [skill for skill in SHARED_SKILLS if not _canonical_files(skill)]
    if missing_canonical:
        for skill in missing_canonical:
            print(f"FAIL: no canonical source at .agents/skills/{skill}/", file=sys.stderr)
        return 2

    if args.check:
        drift: list[tuple[Path, Path, str]] = []
        for skill in SHARED_SKILLS:
            drift.extend(_drifted_pairs(skill))
        if drift:
            print("FAIL: per-tool skill copies have drifted from canonical source:", file=sys.stderr)
            for source, target, reason in drift:
                print(
                    f"  {target.relative_to(REPO_ROOT).as_posix()} <- "
                    f"{source.relative_to(REPO_ROOT).as_posix()} ({reason})",
                    file=sys.stderr,
                )
            print("\nRun: python scripts/sync_skill_copies.py", file=sys.stderr)
            return 1
        print(f"OK: all mirrors match canonical source for {', '.join(SHARED_SKILLS)}.")
        return 0

    total = sum(_sync(skill) for skill in SHARED_SKILLS)
    print(f"OK: wrote {total} mirrored file(s) from .agents/skills/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
