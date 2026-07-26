"""SessionStart hook: re-inject the non-negotiable constraints.

Runs on startup, resume, and -- critically -- after context compaction. A long
task that compacts loses the detail it read from AGENTS.md early on, which is the
mechanical reason "the config keeps not taking effect": nothing was disabled, the
model simply no longer has the text. Re-injecting on `compact` closes that gap.

Only stable constraints belong here. Anything long or volatile stays in the files
this points at, so the injected block does not itself become stale.
"""

from __future__ import annotations

from pathlib import Path

from _hooklib import emit_context, project_dir

CONSTRAINTS = """=== 本仓库强制约束（每次会话启动/恢复/上下文压缩后重新注入）===

1. Exchange-First 不变量：Binance Testnet 是执行事实源，本地 SQLite 只是投影。
   任何"自动开平仓已完成"的结论必须有真实 Testnet 订单 ID + 成交证据；
   本地 Paper 记录、mock 调用、验收单都不算证据。
2. 交付前必须真实执行验证命令并贴出逐字输出（ruff / mypy / pytest）。
   禁止用"应该没问题""理论上可以工作"替代真实数字。
3. 改动 services/execution、services/validation、services/strategy_library
   之后，禁止仅凭代码审查声称完成。
4. 前端改动必须实际启动服务并在浏览器验证（控制台无报错、API 请求频率正常）。
5. 配置类改动（风控阈值/仓位/杠杆）若需重启才生效，自己执行重启并确认新值
   已生效，不要把这一步丢回给用户。
6. 同一任务已声称"完成"但被打回 1 次以上时，停止再次声称完成；
   先按 docs/AGENT_LESSONS.md 的格式写清上次到底漏检了什么。
7. 禁止创建或切换 git 分支/worktree；禁止 git commit --no-verify。
8. 高风险操作（风控阈值、交易所凭据、mainnet 开关、删除迁移、准入门槛数值）
   必须先暂停并取得用户明确同意。"""

_MAX_LESSON_LINES = 40
_MAX_STATE_LINES = 20


def _tail(path: Path, limit: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(unavailable)"
    return "\n".join(lines[-limit:]) if lines else "(empty)"


def _head(path: Path, limit: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(unavailable)"
    return "\n".join(lines[:limit]) if lines else "(empty)"


def main() -> int:
    root = project_dir()
    lessons = _tail(root / "docs" / "AGENT_LESSONS.md", _MAX_LESSON_LINES)
    state = _head(root / "CURRENT_STATE.md", _MAX_STATE_LINES)

    context = (
        f"{CONSTRAINTS}\n\n"
        f"=== docs/AGENT_LESSONS.md（最近 {_MAX_LESSON_LINES} 行实战教训）===\n"
        f"{lessons}\n\n"
        f"=== CURRENT_STATE.md（开头 {_MAX_STATE_LINES} 行；数值可能过期，"
        f"权威来源永远是代码 + 运行时状态）===\n"
        f"{state}"
    )
    emit_context("SessionStart", context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
