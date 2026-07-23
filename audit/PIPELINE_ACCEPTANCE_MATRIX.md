# Pipeline Acceptance Matrix

| 通道 | 允许 | 必须证明 | 当前 |
|---|---|---|---|
| Synthetic Execution | mock/paper adapter | intent、risk、normalizer、ack、reconcile 完整 | NOT_RUN |
| Deterministic Replay | 固定 fixture、LLM mock | 同输入同计数/reason/payload hash | NOT_RUN |
| Full Shadow Round Trip | 实时行情、shadow only | entry/position/exit 全闭环 | NOT_RUN |
| Mainnet/Testnet acceptance | 禁止 | 无 | NOT_RUN/BLOCKED BY SCOPE |
