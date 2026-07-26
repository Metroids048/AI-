"""Self-test for the hook scripts: feed each one a realistic payload on stdin and
assert the contract Claude Code relies on (exit code + parseable stdout JSON).

Run this after editing any hook. A hook that crashes or emits malformed JSON fails
silently in a live session, which is strictly worse than having no hook at all --
that was the concrete reason the original jq-based draft could not be used on this
machine (no jq binary present).

    python .claude/hooks/selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HOOKS_DIR.parent.parent


def run_hook(script: str, payload: dict) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=600,
    )
    return proc.returncode, proc.stdout, proc.stderr


def expect_json_stdout(name: str, stdout: str, event: str) -> list[str]:
    failures: list[str] = []
    if not stdout.strip():
        return [f"{name}: stdout was empty, expected JSON"]
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return [f"{name}: stdout is not valid JSON ({exc})"]
    specific = parsed.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        failures.append(f"{name}: missing hookSpecificOutput object")
    elif specific.get("hookEventName") != event:
        failures.append(f"{name}: hookEventName={specific.get('hookEventName')!r}, expected {event!r}")
    elif not str(specific.get("additionalContext") or "").strip():
        failures.append(f"{name}: additionalContext was empty")
    return failures


def main() -> int:
    failures: list[str] = []
    checks = 0

    # 1. SessionStart must emit injectable context.
    checks += 1
    code, out, err = run_hook("inject_context.py", {"hook_event_name": "SessionStart", "source": "compact"})
    if code != 0:
        failures.append(f"inject_context: exit {code} (expected 0), stderr={err[:200]}")
    failures.extend(expect_json_stdout("inject_context", out, "SessionStart"))

    # 2. Bash guard must BLOCK --no-verify.
    checks += 1
    code, _, err = run_hook(
        "guard_bash.py",
        {"tool_name": "Bash", "tool_input": {"command": 'git commit --no-verify -m "x"'}},
    )
    if code != 2:
        failures.append(f"guard_bash(--no-verify): exit {code} (expected 2)")
    if "no-verify" not in err:
        failures.append("guard_bash(--no-verify): stderr did not explain the block")

    # 3. Bash guard must BLOCK a mainnet flip.
    checks += 1
    code, _, err = run_hook(
        "guard_bash.py",
        {"tool_name": "Bash", "tool_input": {"command": "LIVE_TRADING_ENABLED=true python -m apps.api"}},
    )
    if code != 2:
        failures.append(f"guard_bash(live flip): exit {code} (expected 2)")

    # 4. Bash guard must ALLOW an ordinary command.
    checks += 1
    code, _, err = run_hook(
        "guard_bash.py",
        {"tool_name": "Bash", "tool_input": {"command": "git status"}},
    )
    if code != 0:
        failures.append(f"guard_bash(benign): exit {code} (expected 0), stderr={err[:200]}")

    # 5. Guard must not fire on a *quoted mention* inside a grep/read command.
    checks += 1
    code, _, _ = run_hook(
        "guard_bash.py",
        {"tool_name": "Bash", "tool_input": {"command": "grep -rn 'live_trading_enabled' services/"}},
    )
    if code != 0:
        failures.append(f"guard_bash(grep mention): exit {code} (expected 0 -- read-only mention must not block)")

    # 6. PostToolUse on a non-critical path must stay silent.
    checks += 1
    code, out, _ = run_hook(
        "verify_critical_edit.py",
        {"tool_name": "Edit", "tool_input": {"file_path": str(REPO_ROOT / "README.md")}},
    )
    if code != 0:
        failures.append(f"verify_critical_edit(readme): exit {code} (expected 0)")
    if out.strip():
        failures.append("verify_critical_edit(readme): expected no output for a non-critical file")

    # 7. PostToolUse on a frontend path must emit a reminder without running pytest.
    checks += 1
    code, out, _ = run_hook(
        "verify_critical_edit.py",
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(REPO_ROOT / "frontend/admin/src/pages/PaperConsole.jsx")},
        },
    )
    if code != 0:
        failures.append(f"verify_critical_edit(frontend): exit {code} (expected 0)")
    failures.extend(expect_json_stdout("verify_critical_edit(frontend)", out, "PostToolUse"))

    # 8. Stop gate must pass when no critical file was touched this turn.
    checks += 1
    from _hooklib import clear_turn_state  # noqa: PLC0415

    clear_turn_state()
    code, _, _ = run_hook("gate_stop.py", {"hook_event_name": "Stop", "stop_hook_active": False})
    if code != 0:
        failures.append(f"gate_stop(clean): exit {code} (expected 0)")

    # 9. Stop gate must BLOCK when a critical file was touched with no verification.
    checks += 1
    from _hooklib import record_touched  # noqa: PLC0415

    clear_turn_state()
    record_touched("critical", "services/execution/paper_signal.py")
    code, _, err = run_hook("gate_stop.py", {"hook_event_name": "Stop", "stop_hook_active": False})
    if code != 2:
        failures.append(f"gate_stop(unverified): exit {code} (expected 2)")
    if "paper_signal.py" not in err:
        failures.append("gate_stop(unverified): stderr did not name the touched file")

    # 10. Stop gate must NOT loop: stop_hook_active short-circuits.
    checks += 1
    record_touched("critical", "services/execution/paper_signal.py")
    code, _, _ = run_hook("gate_stop.py", {"hook_event_name": "Stop", "stop_hook_active": True})
    if code != 0:
        failures.append(f"gate_stop(stop_hook_active): exit {code} (expected 0 -- must not loop)")

    clear_turn_state()

    # 11. Malformed stdin must never crash a hook (fail-open).
    for script in ("inject_context.py", "guard_bash.py", "verify_critical_edit.py", "gate_stop.py"):
        checks += 1
        proc = subprocess.run(
            [sys.executable, str(HOOKS_DIR / script)],
            input="not json at all",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )
        if proc.returncode not in (0, 2):
            failures.append(f"{script}(garbage stdin): exit {proc.returncode}, stderr={proc.stderr[:200]}")

    print(f"ran {checks} checks")
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all hook contract checks passed")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HOOKS_DIR))
    raise SystemExit(main())
