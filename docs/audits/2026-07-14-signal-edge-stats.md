# Signal Edge Stats Run (2026-07-14)

```text
python scripts/compute_signal_edge_stats.py \
  --strategy-key auto_paper_mature_templates \
  --database-url sqlite:///.local_paper_console.db \
  --days 60 --reuse-stored-data
```

## Result

- `total_trades=17`
- `win_rate=0.3529`
- `average_win=0.025098`
- `average_loss=-0.018187`
- **REJECTED**: 17 trades < required minimum 30

`net_edge_after_cost` therefore continues to use the raw-bar-return proxy until enough real trade-conditioned samples exist. This is fail-closed by design (ADR-063) — not a reason to relax the gate.
