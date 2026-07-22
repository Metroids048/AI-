# Runtime Ledger Summary

- Exported at: `2026-07-22T00:43:44.256501+00:00`
- Window: `2026-06-22T00:43:44.256501+00:00` → `2026-07-22T00:43:44.256501+00:00` (30 days)
- Source DB: `C:/Users/Windows11/Desktop/量化项目/.local_paper_console.db`
- Source SHA256: `b1e861ae594295be71f31dfe24a1e415b67623f720976fe61567ce4ab0ac678f`
- Ledger gzip SHA256: `0c41e8b2bb819e02d1ed308c8ae9db98badbe9f748888a8a9380ee3774e52525`

## Row counts

| table | rows |
| --- | ---: |
| decision_snapshots | 9702 |
| exchange_account_snapshots | 15730 |
| live_runs | 1 |
| order_executions | 253 |
| paper_runs | 7 |
| position_snapshots | 27845 |
| reconciliation_records | 0 |
| risk_events | 3575 |
| strategies | 7 |

## How to analyze on another device

```text
agent-python -m scripts.import_runtime_ledger
agent-python -m scripts.audit_decision_funnel --database-url sqlite:///.local_runtime_ledger.db --lookback-days 30
```
