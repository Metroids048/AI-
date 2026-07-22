# Manual Trade Miss Analysis

- Generated: 2026-07-22T06:19:23.874187+00:00
- Database: C:/Users/win/Desktop/AI--main/.local_runtime_ledger.db
- Market database: C:/Users/win/Desktop/AI--main/.local_paper_console.db
- Decision lookback: 90 minutes
- Outcome window: 24 hours
- Decision reconstruction uses only snapshots at or before entry; MFE/MAE uses later bars and is outcome evidence only.

## BTC/USDT 3bd168d2-c3d0-4130-a716-2059129d23c7

- Side: short
- Entry time (UTC): 2026-07-21T12:14:25+00:00
- Entry price: 66350.030408
- Quantity: 0.4318
- Gateway status: filled
- Prior decision time (UTC): 2026-07-21T12:02:41.881776+00:00
- Decision age (minutes): 11.718637066666668
- Pipeline status: ensemble_discarded
- Final blocker: ENSEMBLE_DISCARD
- Classification: INSUFFICIENT_EVIDENCE
- Market regime: low_volatility
- MTF status: state_confirmation_disagreed
- Theoretical stop:
- Theoretical take-profit:
- MFE fraction: 0.007010251617668132
- MAE fraction: 0.008255754950399402
- Reached 1R:
- Reached 2R:
- Signals: `[{"confidence":0.18418688442346937,"entry":null,"leverage":null,"reason":"macd_bearish_cross","received_at":null,"side":"short","signal_time":"2026-07-21T10:45:00Z","source":"technical_macd","stoploss":null,"symbol":"BTC/USDT","takeprofits":[]}]`
- Ensemble: `{"correlation_matrix_ref":"layered_regime_entry:allowed_direction=none:{'fusion_method': 'layered_regime_entry', 'min_direction_sources': 3, 'allowed_direction': 'none', 'correlation_threshold': 0.75, 'min_history': 200, 'input_count': 1, 'eligible_count': 0, 'kept_count': 0}","created_at":"2026-07-21T12:02:42.296092","ensemble_id":"5fa2622a-092c-49ac-b40f-f67dfc3b1ba7","ensemble_status":"discarded_low_confidence","fused_confidence":null,"fused_direction":null,"fusion_method":"layered_regime_entry","raw_votes":[],"strategy_refs":["technical_macd:macd_bearish_cross"]}`
- LLM veto: `null`

Evidence gaps:
- persisted theoretical stop/take-profit is unavailable

## ETH/USDT a2e7c962-715c-42d1-931b-dd6dc95ab28a

- Side: short
- Entry time (UTC): 2026-07-21T12:20:03+00:00
- Entry price:
- Quantity: 14.768
- Gateway status: new
- Prior decision time (UTC): 2026-07-21T12:17:03.170750+00:00
- Decision age (minutes): 2.997154166666667
- Pipeline status: ensemble_discarded
- Final blocker: ENSEMBLE_DISCARD
- Classification: INSUFFICIENT_EVIDENCE
- Market regime: low_volatility
- MTF status: state_confirmation_disagreed
- Theoretical stop:
- Theoretical take-profit:
- MFE fraction:
- MAE fraction:
- Reached 1R:
- Reached 2R:
- Signals: `[{"confidence":0.66197183098592,"entry":null,"leverage":null,"reason":"bearish_engulfing","received_at":null,"side":"short","signal_time":"2026-07-21T12:15:00Z","source":"price_action_engulfing","stoploss":null,"symbol":"ETH/USDT","takeprofits":[]},{"confidence":0.6068610634648387,"entry":null,"leverage":null,"reason":"fvg_bearish_gap_fill_rejection","received_at":null,"side":"short","signal_time":"2026-07-21T12:15:00Z","source":"technical_fvg","stoploss":null,"symbol":"ETH/USDT","takeprofits":[]}]`
- Ensemble: `{"correlation_matrix_ref":"layered_regime_entry:allowed_direction=none:{'fusion_method': 'layered_regime_entry', 'min_direction_sources': 3, 'allowed_direction': 'none', 'correlation_threshold': 0.75, 'min_history': 200, 'input_count': 2, 'eligible_count': 0, 'kept_count': 0}","created_at":"2026-07-21T12:17:04.048978","ensemble_id":"bb597a1e-cf6c-4ae5-a1b9-b3673bb82794","ensemble_status":"discarded_low_confidence","fused_confidence":null,"fused_direction":null,"fusion_method":"layered_regime_entry","raw_votes":[],"strategy_refs":["price_action_engulfing:bearish_engulfing","technical_fvg:fvg_bearish_gap_fill_rejection"]}`
- LLM veto: `null`

Evidence gaps:
- entry fill price is unavailable
- persisted theoretical stop/take-profit is unavailable
