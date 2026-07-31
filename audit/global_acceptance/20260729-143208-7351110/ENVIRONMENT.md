# Environment

## Audit Context

- Run ID: `20260729-143208-7351110`
- Repository: `C:/Users/win/Desktop/AI--main`
- HEAD: `7351110595bc063f3db69afa1b5554cdb8de7d3a`
- Branch: `fix/v2-production-closure`
- Timezone: `Asia/Shanghai`

## Toolchain

| Command | Result | Status | Evidence |
|---|---|---|---|
| `python --version` | `Python 3.12.10` | PASS | `RAW/10-python-version.command.json` |
| `pip --version` | `pip 26.1.2`, Python 3.12 | PASS | `RAW/11-pip-version.command.json` |
| `node --version` | `v24.16.0` | PASS | `RAW/12-node-version.command.json` |
| `npm --version` | `11.13.0` | PASS via Windows `npm.cmd` shim | `RAW/13-npm-version.command.json` |
| `pnpm --version` | `11.9.0` | PASS with workspace warning | `RAW/14-pnpm-version.command.json` |
| `docker --version` | executable not found | BLOCKED_ENV | `RAW/15-docker-version.command.json` |

The first npm probe used `Start-Process npm` and hit a Windows shim invocation issue.
The retained final record uses `npm.cmd --version`; this is an L1 probe correction
inside the audit directory, not a project change.

## Environment Variable Presence

Values were never read into evidence. Only presence was recorded.

| Name | Present |
|---|---:|
| `AUTOMATED_TRADING_ENGINE` | No |
| `V2_TESTNET_CONTRACT_ENABLED` | No |
| `NATURAL_E2E_ENABLED` | No |
| `BINANCE_API_KEY` | No |
| `BINANCE_API_SECRET` | No |
| `BINANCE_HTTPS_PROXY` | No |
| `HTTPS_PROXY` | No |
| `PAPER_CONSOLE_DISABLE_LIVE_WS` | No |
| `DATABASE_URL` | No |

Evidence: `RAW/16-environment-presence.command.json`.

## Safety Decision

No Scheduler, database mutation, API runtime, frontend runtime, Shadow, Testnet
Contract, Natural E2E, LLM smoke, migration, or order command was run. The Phase 0
dirty-worktree stop condition took precedence.
