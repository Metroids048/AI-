"""Pre-commit hook: run the test subset relevant to staged trading-critical files.

Why this exists
---------------
``.pre-commit-config.yaml`` ran ruff and mypy but never pytest, so a commit could
look clean locally and only turn red in CI. That is not hypothetical: revision
0013 shipped while ``verify_runtime_config_sync.CURRENT_SCHEMA_REVISION`` still
said "0012", and an api test asserting ``execution_ready`` had been failing for
several commits. Both were reachable by running pytest before the commit landed.

Scope discipline
----------------
Running the whole suite on every commit costs ~75s and would get disabled within
a week. This hook only fires for paths that can change what the runtime submits
to Binance, and only runs the ``-k`` subset matching those files' module names.

"No tests matched" is reported loudly but does not block: many modules legitimately
have no same-named test file, and blocking there would train people to pass
``--no-verify`` -- the exact bypass this repo forbids. A *failing* matched test
does block.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# pre-commit invokes `language: system` hooks with the interpreter it was
# installed from. Here that is .agent-reach-venv\python.exe, which has no
# pytest -- so trusting sys.executable makes this hook report "tests failed"
# when nothing was ever run. Resolve an interpreter that can actually import
# pytest, and treat "cannot find one" as its own distinct failure.
_REJECTED_INTERPRETER_HINTS = (".agent-reach-venv", "WindowsApps")

# Kept deliberately narrow. Widening this trades commit latency for coverage;
# CI still runs the full suite either way.
CRITICAL_PREFIXES = (
    "services/execution/",
    "services/validation/",
    "services/strategy_library/",
    "apps/api/routers/",
)

TIMEOUT_SECONDS = 420


def _can_import_pytest(candidate: str) -> bool:
    if any(hint in candidate for hint in _REJECTED_INTERPRETER_HINTS):
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [candidate, "-c", "import pytest"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _resolve_pytest_interpreter() -> str | None:
    """Return an interpreter that can import pytest, or None.

    Probed rather than hardcoded so this keeps working if the project grows a
    local venv later. ``QUANT_PYTEST_PYTHON`` is the operator override.
    """

    candidates: list[str] = []

    override = os.environ.get("QUANT_PYTEST_PYTHON")
    if override:
        candidates.append(override)

    for venv in (".venv", "venv"):
        for rel in ("Scripts/python.exe", "bin/python"):
            path = REPO_ROOT / venv / rel
            if path.exists():
                candidates.append(str(path))

    if not any(hint in sys.executable for hint in _REJECTED_INTERPRETER_HINTS):
        candidates.append(sys.executable)

    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            for version in ("-3.12", "-3.11"):
                try:
                    found = subprocess.run(  # noqa: S603 - fixed argv, no shell
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
        if _can_import_pytest(candidate):
            return candidate
    return None


def _is_critical(rel_path: str) -> bool:
    return rel_path.startswith(CRITICAL_PREFIXES)


def _stems(paths: list[str]) -> list[str]:
    """Return de-duplicated module stems worth selecting tests for."""

    stems: list[str] = []
    for raw in paths:
        stem = Path(raw).stem
        if stem in {"__init__", "__main__"}:
            # A package marker's name selects nothing useful; use its package.
            stem = Path(raw).parent.name
        if stem and stem not in stems:
            stems.append(stem)
    return stems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", nargs="*")
    args = parser.parse_args()

    critical = sorted({f.replace("\\", "/") for f in args.filenames if _is_critical(f.replace("\\", "/"))})
    if not critical:
        return 0

    stems = _stems(critical)
    expression = " or ".join(stems)

    print("[pre-commit] trading-critical files staged:")
    for path in critical:
        print(f"  - {path}")

    interpreter = _resolve_pytest_interpreter()
    if interpreter is None:
        # Distinct from a test failure on purpose: blaming the code for a broken
        # environment is how a gate loses credibility and gets bypassed.
        print(
            "[pre-commit] ENVIRONMENT ERROR: no interpreter with pytest was found,\n"
            "            so no test was run and this commit is NOT verified.\n"
            "            Set QUANT_PYTEST_PYTHON to a python.exe that has pytest."
        )
        return 1

    print(f"[pre-commit] interpreter: {interpreter}")
    print(f"[pre-commit] running: pytest -q -m 'not integration' -k '{expression}'")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                interpreter,
                "-m",
                "pytest",
                "-q",
                "-m",
                "not integration",
                "-k",
                expression,
                "--no-header",
            ],
            cwd=REPO_ROOT,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"[pre-commit] FAIL: pytest exceeded {TIMEOUT_SECONDS}s and was killed.")
        return 1
    except OSError as exc:
        print(f"[pre-commit] FAIL: could not start pytest: {exc}")
        return 1

    # pytest exit 5 == no tests collected. Surface it, but do not block the commit.
    if completed.returncode == 5:
        print(
            f"[pre-commit] WARNING: no test matched -k '{expression}'.\n"
            "            These files have no name-matched coverage. Not blocking the\n"
            "            commit, but do not treat this as a verified change."
        )
        return 0

    if completed.returncode != 0:
        print(
            "[pre-commit] FAIL: matched tests did not pass. Fix them before committing.\n"
            "            Do NOT bypass with --no-verify (AGENTS.md rule 11)."
        )
        return 1

    print("[pre-commit] matched tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
