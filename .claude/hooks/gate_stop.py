"""Stop hook: refuse to end the turn if trading-critical files were edited but no
successful verification was recorded.

This is the mechanical form of AGENTS.md rule 14 ("stop claiming COMPLETE when it
is not"). Exiting 2 sends stderr back to the model and forces it to continue
instead of handing verification back to the operator.

Loop safety: Claude Code sets stop_hook_active=true when it re-enters after a
Stop hook already blocked once. Honouring that flag is what keeps this from
becoming an infinite loop -- we block at most once per turn, then get out of the
way and let the model's own report stand or fall on its merits.
"""

from __future__ import annotations

import sys

from _hooklib import (
    clear_turn_state,
    read_payload,
    touched_entries,
    verification_age_seconds,
)

# A verification older than this is treated as stale: it predates the edits we
# care about, so it proves nothing about the current state of the tree.
MAX_VERIFICATION_AGE_SECONDS = 1800


def main() -> int:
    payload = read_payload()

    # Already blocked once this turn. Let it through to avoid a hard loop.
    if payload.get("stop_hook_active") is True:
        clear_turn_state()
        return 0

    critical = touched_entries("critical")
    if not critical:
        clear_turn_state()
        return 0

    age = verification_age_seconds()
    if age is not None and age <= MAX_VERIFICATION_AGE_SECONDS:
        clear_turn_state()
        return 0

    shown = "\n".join(f"  - {path}" for path in sorted(set(critical))[:12])
    staleness = (
        "从未成功验证" if age is None else f"上次成功验证距今 {int(age)} 秒（超过 {MAX_VERIFICATION_AGE_SECONDS} 秒）"
    )

    # stderr on exit 2 is what the model sees.
    print(
        "本轮改动了交易核心文件，但没有有效的验证记录，不能结束本轮。\n"
        f"{staleness}。\n\n涉及文件：\n{shown}\n\n"
        '请先跑通相关测试（pytest -q -m "not integration"），'
        "把真实输出贴进回复，再结束。不要把验证这一步交回给操作员。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
