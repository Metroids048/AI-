"""Shared helpers for this repo's Claude Code hooks.

Why Python instead of the bash+jq scripts in the original proposal: this repo is
developed on Windows where ``jq`` is not installed. A jq-based hook fails
silently, which is worse than no hook at all -- the gate looks armed but never
fires. Python 3.11+ is already a hard requirement of the project, so it is the
one interpreter guaranteed to exist.

Hook I/O contract (Claude Code):
- payload arrives as JSON on stdin
- to inject context, print JSON with hookSpecificOutput.additionalContext
- to block an action, write the reason to stderr and exit 2
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Interpreters that must never be used to run the test suite. The agent
# sandbox venv shadows `python` on PATH but has no pytest, so a naive
# `python -m pytest` from a hook reports "No module named pytest" and the
# gate degrades to a no-op.
_REJECTED_INTERPRETER_HINTS = (".agent-reach-venv", "WindowsApps")


def project_dir() -> Path:
    """Return the repo root, preferring the value Claude Code injects."""

    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir).resolve()
    return Path(__file__).resolve().parent.parent.parent


def read_payload() -> dict[str, Any]:
    """Parse the hook payload from stdin, tolerating empty or malformed input.

    A hook must never crash the session because of unexpected input, so this
    degrades to an empty dict rather than raising.
    """

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def emit_context(event_name: str, context: str) -> None:
    """Emit additionalContext for the given hook event and exit successfully."""

    payload = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def block(reason: str) -> None:
    """Block the pending action: reason goes to stderr, exit code 2."""

    sys.stderr.write(reason.rstrip() + "\n")
    sys.stderr.flush()
    raise SystemExit(2)


def _interpreter_is_usable(candidate: str) -> bool:
    if any(hint in candidate for hint in _REJECTED_INTERPRETER_HINTS):
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            [candidate, "-c", "import pytest"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def resolve_test_interpreter() -> str | None:
    """Find an interpreter that can actually import pytest.

    Probed rather than hardcoded so this keeps working if the project later
    grows a local venv. Returns None when no candidate qualifies, which callers
    must surface instead of pretending the suite passed.
    """

    root = project_dir()
    candidates: list[str] = []

    override = os.environ.get("CLAUDE_HOOK_PYTHON")
    if override:
        candidates.append(override)

    for venv in (".venv", "venv", ".env"):
        win = root / venv / "Scripts" / "python.exe"
        posix = root / venv / "bin" / "python"
        candidates.extend(str(path) for path in (win, posix) if path.exists())

    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            for version in ("-3.12", "-3.11"):
                try:
                    found = subprocess.run(  # noqa: S603
                        [launcher, version, "-c", "import sys; print(sys.executable)"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    continue
                if found.returncode == 0 and found.stdout.strip():
                    candidates.append(found.stdout.strip())

    for name in ("python3", "python"):
        found_path = shutil.which(name)
        if found_path:
            candidates.append(found_path)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _interpreter_is_usable(candidate):
            return candidate
    return None


def state_dir() -> Path:
    """Return (creating if needed) the directory holding hook state files."""

    path = project_dir() / ".claude" / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Per-turn state. These files are deliberately scoped to a single turn: the Stop
# gate clears them once it has made its decision, so a later turn is never
# blocked by an older turn's edits.
_TOUCHED_PREFIX = "touched_"
_VERIFIED_MARKER = "last_verification_ts"


def record_touched(kind: str, path: str) -> None:
    """Append a file path to the per-turn ledger for the given category."""

    target = state_dir() / f"{_TOUCHED_PREFIX}{kind}.log"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"{path}\n")


def touched_entries(kind: str) -> list[str]:
    """Return the paths recorded for a category this turn."""

    target = state_dir() / f"{_TOUCHED_PREFIX}{kind}.log"
    if not target.exists():
        return []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip()]


def record_verification() -> None:
    """Stamp 'verification succeeded just now'.

    Only ever called on a genuinely green run. A failing run must leave the old
    timestamp untouched so the Stop gate still sees the tree as unverified.
    """

    (state_dir() / _VERIFIED_MARKER).write_text(str(int(time.time())), encoding="utf-8")


def verification_age_seconds() -> float | None:
    """Seconds since the last successful verification, or None if never."""

    marker = state_dir() / _VERIFIED_MARKER
    if not marker.exists():
        return None
    try:
        stamped = int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return max(0.0, time.time() - stamped)


def clear_turn_state() -> None:
    """Drop the per-turn ledgers once the Stop gate has ruled on them."""

    directory = state_dir()
    for leftover in directory.glob(f"{_TOUCHED_PREFIX}*.log"):
        with contextlib.suppress(OSError):
            leftover.unlink()
    with contextlib.suppress(OSError):
        (directory / _VERIFIED_MARKER).unlink()
