# A-001 2026-08-16 真实亏损归因

状态：`A001_PASS_WITH_LIMITATIONS`。本报告只读读取 V2 本地投影与交易所回执记录，不写运行数据库。

## 结论
- CLOSED episodes：3；亏损 episodes：3。
- 当日 local realized PnL 合计：`-90.19345874 USDT`。
- 交易所 fill、保护记录和本地 realized PnL 可核验；但 funding、完整 MFE/MAE、权益基线和全天组合风险时间线未知。
- 没有足够证据支持修改固定 1.5R；没有确认 execution defect。

## Episode
| Episode | Symbol | Direction | Entry | Exit | Realized PnL | Net R | Cause |
|---|---|---|---:|---:|---:|---:|---|
| `0e3814c7-d23a-48a8-bef6-d240fc809b66` | BTC/USDT | long | 2026-08-15 12:15:41.470441 | 2026-08-16 09:57:37.503000 | -10.91071288 | -0.1790236527135291508968747616 | OTHER_EVIDENCED |
| `5a4e07fe-354c-4ec7-8f2c-80a4245a8402` | XRP/USDT | short | 2026-08-16 11:00:46.182472 | 2026-08-16 16:18:29.767000 | -4.0833331 | -1.235576464536431856693294602 | OTHER_EVIDENCED |
| `119d0197-eb5e-4655-a843-001e864b9ce7` | ETH/USDT | short | 2026-08-16 07:16:16.373915 | 2026-08-16 16:27:52.243000 | -75.19941276 | -1.233566538721403329632143953 | OTHER_EVIDENCED |

## Limitations
- No Aug 16 account equity snapshot was available in exchange_account_snapshots.
- Funding income/expense was not present in the local V2 episode contract.
- 1m OHLCV coverage ended around 14:58-14:59 UTC, before the final ETH exit.
- MFE/MAE and full same-direction portfolio risk timeline cannot be reconstructed without complete intraday data.
- Cause labels remain conservative where geometry or funding evidence is missing.
