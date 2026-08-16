# Testnet Sampling R2 Recovery

## Scope

Restore the established `TESTNET_CANARY` / `testnet_sampling_v2` sampling
contract after the R2 cost gate introduced by `39e6524` made normal candidates
mathematically unreachable. No mainnet action, manual entry, database fill
fabrication, or account-risk-control bypass was used.

## Change

`services/automated_trading/application/cycle_service.py` now resolves an
explicit R2 policy. The exact Canary sampling contract is `DIAGNOSTIC`; it
retains the complete R2 payload but does not inject the failed payoff into the
blocking entry runtime. Production and every other enabled R2 caller remains
`BLOCKING`. Regression coverage is in
`tests/services/test_automated_trading_cycle.py`.

## Natural Testnet Evidence

- Scheduler cycle: `03e04f46-2062-488f-9dfb-9279c12955ab`
- Decision: `3722e97c-df9f-432f-9211-0a50e48698f5`
- Candidate: BTC/USDT long, `testnet_sampling_v2`, closed at
  `2026-08-15T12:15:00Z`
- R2: `cost_R=0.4567504299539455684085232320`,
  `theoretical_net_payoff=0.7161484552155143291301732696`, `status=REJECT`,
  `policy=DIAGNOSTIC`, `would_block=true`, `enforced=false`
- Binance Testnet entry: order `28541964139`, `FILLED`, quantity `0.2764`,
  average fill `62964.998`
- Active reduce-only stop/target: `1000000167954341` / `1000000167954361`
- Local and exchange BTC positions: `0.2764`; reconciliation mismatch set was
  empty and runtime reconciliation was `healthy`.

This is a non-promotable Testnet sampling observation, not production-strategy
evidence. The runtime remains responsible for normal reduce-only exit handling.

## Verification

- `agent-python -m pytest tests/services/test_automated_trading_cycle.py tests/services/test_risk_controls.py -q`: `29 passed`
- `agent-python -m pytest -q -m "not integration"`: `1620 passed, 5 skipped, 2 deselected`
- `agent-python -m mypy`: `Success: no issues found in 250 source files`
- `agent-python -m ruff check .`: one pre-existing unrelated `C416` at
  `scripts/verify_gate17_e2e.py:77`
- `git diff --check`: clean apart from existing CRLF conversion warnings.
