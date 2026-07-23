# Isolated Test Design

本轮只设计，不执行下单。

## A Synthetic Execution

固定 BTC/ETH 合法 TradeIntent -> schema validation -> 固定仓位/杠杆 profile -> risk validation -> order builder/normalizer -> paper/testnet adapter mock -> acknowledgement -> position reconciliation。禁止绕过 intent、risk、normalizer、adapter、reconciliation；验收是每一段都有显式 accepted/rejected/error reason，且不触碰 mainnet。

## B Deterministic Replay

保存固定 market fixture（BTC/ETH、时间戳、OHLCV、策略版本、配置 hash）重复执行 strategy -> ensemble -> LLM mock -> MTF -> gatekeeper -> TradeIntent。验收是每次的 stage counts、reason codes、payload hash 完全一致；随机时间、网络行情和真实 LLM 均禁止。

## C Full Shadow Round Trip

实时行情只进入 shadow order/simulated position，执行 entry -> position state -> exit condition -> exit intent -> simulated exit。验收覆盖恢复、重复周期、保护价、退出原因和数量守恒；不得调用 gateway 的 POST。
