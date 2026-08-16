# Runtime-Parity + P1 动态保护审计（2026-08-16）

## 目标

在继续 funding 或入场信号实验前，验证 P2-A 静态 Policy A replay 是否遗漏了运行时 P1 动态止损保护，并将同一批 30 笔真实 `testnet_sampling_v2` 交易拆成 R0/R1/R2/R3。

## 方法

- 只读打开 `.local_paper_console.db`（SQLite `mode=ro`），没有改执行链、风控参数、Gatekeeper 或 funding 输入。
- Cohort 固定复用 `docs/audits/2026-08-16-p2a-actual-decomposition.json` 的 30 个 position。
- R0：既有 P2-A Policy A replay，7 天 1m 窗口、模型手续费/滑点。
- R1：真实 entry VWAP + protection record 中实际提交的静态 stop/target，截到真实 close，保守 stop-first。
- R2：R1 加入 runtime P1：`MFE>=0.60R -> +0.05R`、`MFE>=1.00R -> +0.40R`，按 symbol tick rounding；本 bar 先判断既有保护，P1 更新下一根 bar 生效。
- R3：V2 `reduce_only` exchange fills，分别算 gross 与扣 entry/exit commission 后 net；不加入 funding。

## 结果

| 阶段 | 均值 | PF（USDT gross / net） |
|---|---:|---:|
| R0 静态 replay | `0.047839R` net | `1.112299 / 0.700681` |
| R1 静态 runtime 几何 | `0.291423R` gross | `1.000553` |
| R2 P1 parity | `0.249455R` gross | `1.431105` |
| R3 真实 exchange | `0.073741R` gross；`-0.154600R` net | `0.723774 / 0.486882` |

- R1 -> R2（P1 动态保护）变化：`-0.041968R`。
- R2 -> R3（真实成交/退出执行缺口）：`-0.404055R`。
- 30 笔中模拟 P1 触发 21 笔，其中 12 笔最终没有达到静态 target。
- 历史 30 笔 protection event 中明确的 `ProfitProtectionStopTightened` 为 `0`；保护记录上的 `policy=P1` 不视为 replacement 证据。

## 判定

`P1_NOT_PRIMARY_EXPLANATION / CONTINUE_RUNTIME_ATTRIBUTION`

P1 的 parity 影响约 `-0.042R`，远小于 R2 到真实 exchange 的 `-0.404R` 缺口；当前证据不支持先改 funding 或入场信号。下一步应继续核对真实 exit order identity、保护单价格与 fill price/滑点之间的差异，以及 replay 对真实成交时序的语义；funding 仍保持 `INSUFFICIENT_DATA`，不作为单变量实验。

证据：

- `docs/audits/2026-08-16-runtime-p1-parity.json`
- `docs/audits/2026-08-16-runtime-p1-parity.md`
- `scripts/audit_runtime_p1_parity.py`
