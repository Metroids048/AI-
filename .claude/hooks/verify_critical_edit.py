"""PostToolUse(Edit|Write|MultiEdit) hook: run the real test subset after an edit
to a trading-critical file, and feed the real output back to the model.

This is the mechanical replacement for AGENTS.md rule 12. Rule 12 asks the model
to remember to run tests and paste true output; this hook removes the choice --
the output lands in context whether or not the model wanted to look at it.

It also records which critical paths were touched and when a verification last
ran, which is the state gate_stop.py reads to decide whether the turn may end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _hooklib import (
    emit_context,
    project_dir,
    read_payload,
    record_touched,
    record_verification,
    resolve_test_interpreter,
)

# Editing anything under these prefixes can change what the runtime submits to
# Binance, so an edit here must be backed by a real test run.
CRITICAL_PREFIXES = (
    "services/execution/",
    "services/validation/",
    "services/strategy_library/",
    "apps/api/routers/",
)

FRONTEND_PREFIX = "frontend/admin/src/"

# A targeted -k subset should finish in seconds. If it does not, something is
# wrong (a hung fixture, a real network call) and that is itself worth surfacing
# rather than stalling the session.
PYTEST_TIMEOUT_SECONDS = 300


def _relative_path(raw: str) -> str | None:
    if not raw:
        return None
    try:
        return Path(raw).resolve().relative_to(project_dir()).as_posix()
    except (ValueError, OSError):
        return None


def _run_targeted_tests(stem: str) -> tuple[str, bool]:
    """Run the -k subset matching this module's name. Returns (output, ran)."""

    python = resolve_test_interpreter()
    if python is None:
        return (
            "跳过自动测试：找不到装有 pytest 的解释器。请设置环境变量 CLAUDE_HOOK_PYTHON 指向正确的 python.exe。",
            False,
        )
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                python,
                "-m",
                "pytest",
                "-q",
                "-m",
                "not integration",
                "-k",
                stem,
                "--no-header",
            ],
            cwd=project_dir(),
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return (f"自动测试超时（>{PYTEST_TIMEOUT_SECONDS}s），已终止。这本身需要排查。", False)
    except OSError as exc:
        return (f"自动测试无法启动：{exc}", False)

    stream = (completed.stdout or "") + (completed.stderr or "")
    lines = [line for line in stream.splitlines() if line.strip()]
    tail = "\n".join(lines[-25:]) if lines else "(pytest 无输出)"

    # "no tests ran" is not a pass. Say so explicitly, because a green-looking
    # empty run is exactly how a missing-coverage edit slips through.
    if "no tests ran" in stream:
        return (
            f"{tail}\n\n注意：-k '{stem}' 没有匹配到任何测试。这个改动目前没有对应的测试覆盖，不能当作已验证。",
            False,
        )
    return tail, completed.returncode == 0


def main() -> int:
    payload = read_payload()
    raw_path = str(payload.get("tool_input", {}).get("file_path") or "")
    rel = _relative_path(raw_path)
    if rel is None:
        return 0

    if rel.startswith(FRONTEND_PREFIX):
        record_touched("frontend", rel)
        emit_context(
            "PostToolUse",
            f"[hook] 已编辑前端文件 {rel}。\n"
            "AGENTS.md 规则 13：交付前必须实际跑 npm --workspace frontend/admin run test，"
            "并启动 dev server 在浏览器里验证（控制台无报错、API 请求频率正常），"
            "不能只凭代码审查声称完成。",
        )
        return 0

    if not rel.startswith(CRITICAL_PREFIXES):
        return 0

    record_touched("critical", rel)
    stem = Path(rel).stem
    output, passed = _run_targeted_tests(stem)
    if passed:
        record_verification()

    verdict = "全部通过" if passed else "未通过或未覆盖 —— 必须先处理，不得声称完成"
    emit_context(
        "PostToolUse",
        f"[hook] 已编辑交易核心文件 {rel}，自动运行 pytest -k '{stem}' 的真实输出如下"
        f"（不是模型自述）：\n\n{output}\n\n判定：{verdict}。",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
