"""PreToolUse(Bash) hook: refuse the two command shapes that bypass review.

Exit code 2 blocks the call and returns stderr to the model as feedback.

Scope is deliberately narrow. This guard exists for actions where the damage is
done the moment the command runs -- skipping pre-commit, or flipping the live /
testnet boundary. Everything else is left to the normal permission flow, because
a guard that fires on ordinary commands gets routed around.
"""

from __future__ import annotations

import re

from _hooklib import block, read_payload

# --no-verify / -n on a commit skips the hooks that are the only automated gate
# before a push. AGENTS.md rule 11 forbids it outright.
_NO_VERIFY = re.compile(r"\bgit\b[^|;&]*\bcommit\b[^|;&]*(--no-verify|(?<!\w)-n(?!\w))")

# Flipping either of these moves real money. AGENTS.md rule 8 requires explicit
# operator consent first, so the model must not set them inline.
_LIVE_BOUNDARY = re.compile(
    r"(LIVE_TRADING_ENABLED\s*=\s*(true|1)"
    r"|BINANCE_USE_TESTNET\s*=\s*(false|0))",
    re.IGNORECASE,
)

# `git commit -am` style short flags bundle -a and -m, not -n; the -n pattern
# above uses word boundaries so it does not fire on those.


def main() -> int:
    payload = read_payload()
    command = str(payload.get("tool_input", {}).get("command") or "")
    if not command:
        return 0

    if _NO_VERIFY.search(command):
        block(
            "已拦截：禁止用 --no-verify / -n 跳过 pre-commit（AGENTS.md 规则 11）。\n"
            "如果钩子报错，请修复它报出的问题本身，而不是绕过检查。"
        )

    if _LIVE_BOUNDARY.search(command):
        block(
            "已拦截：这是切换实盘/Testnet 边界的高危操作（AGENTS.md 规则 8）。\n"
            "必须先向用户说明影响并取得明确同意，不要在命令里直接设置。"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
