# Agent lessons

Record only recurring, verified lessons that should change future work. Keep each entry concise and periodically delete obsolete items.

## Template

### YYYY-MM-DD — <short lesson>

- Repeated failure: <what happened at least twice>
- Root cause: <verified cause>
- Evidence: <test, log, file, command>
- New rule or automation: <specific prevention>
- Scope: <global project / path / workflow>
- Review date: <date or triggering condition>

---

## 2026-07-26 — Frontend infinite polling loop claimed "complete" 3 times without browser verification

- **Repeated failure**: Claimed "完成" 3 times for frontend fix, but user reported "毫无变化，1s一闪" every time. Browser showed hundreds of API requests per second, console frozen.
- **Root cause**: `useEffect` in `useConsoleData.js` had `state.error` and `state.streamStatus` in dependency array, causing infinite re-render loop. Never actually tested in browser before claiming completion.
- **Evidence**:
  - User screenshot showing network panel with thousands of pending requests
  - `frontend/admin/src/hooks/useConsoleData.js` line 223: `useEffect(..., [symbol, perpSymbol, timeframe, state.error, state.streamStatus])`
  - Fix: Use `useRef` to store state and remove state values from dependency array
- **New rule or automation**:
  - Added AGENTS.md rule 13: "前端功能交付必须浏览器验证（强制执行）"
  - Added AGENTS.md rule 14: "严禁多次声称'完成'后仍需返工"
  - Must run `pnpm dev` and verify in browser before claiming any frontend task complete
  - Must check console for errors and network panel for request frequency
- **Scope**: All frontend changes (React components, hooks, API calls, state management)
- **Review date**: Every frontend delivery until browser verification becomes automatic habit

## 2026-07-16 — Config changes claimed complete but never took effect

- **Repeated failure**: Modified risk parameters in code (42% threshold, 5% risk, 40x leverage), but old config kept running. 225 orders rejected overnight.
- **Root cause**: Changed code but didn't tell user to restart system. Config changes ≠ config active.
- **Evidence**: Database still had old values after "completion"
- **New rule or automation**: Created `config-change-verification.md` requiring explicit restart instruction + user confirmation + database verification
- **Scope**: All config changes (risk params, strategy rules, bootstrap settings)
- **Review date**: After 10 successful config-change deliveries with restart verification
