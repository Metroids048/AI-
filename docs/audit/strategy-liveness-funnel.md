# Strategy Liveness Funnel

- Generated: 2026-07-22T08:48:48.873410+00:00
- Since: 2026-07-15T08:48:47.866655+00:00
- Strategy: all
- Persisted decision snapshots: 1267
- Entry evaluations: 699
- Actual pipeline order: base signal -> multi-timeframe -> ensemble -> meta-label -> LLM -> Gatekeeper -> TradeIntent

## Sequential Funnel

| stage | entered | passed | eliminated | elimination rate |
| --- | ---: | ---: | ---: | ---: |
| base_signal | 699 | 548 | 151 | 21.60% |
| multi_timeframe | 548 | 358 | 190 | 34.67% |
| ensemble | 358 | 132 | 226 | 63.13% |
| meta_label | 132 | 82 | 50 | 37.88% |
| llm_veto | 82 | 80 | 2 | 2.44% |
| gatekeeper | 80 | 10 | 70 | 87.50% |
| trade_intent | 10 | 10 | 0 | 0.00% |

## Core Metrics

| metric | count |
| --- | ---: |
| cycles | 201 |
| symbols_evaluated | 699 |
| raw_long_signals | 585 |
| raw_short_signals | 554 |
| no_base_signal | 151 |
| mtf_evaluated | 548 |
| mtf_pass | 358 |
| mtf_disagreement | 190 |
| ensemble_evaluated | 358 |
| ensemble_pass | 132 |
| ensemble_discard | 226 |
| meta_label_evaluated | 132 |
| meta_label_pass | 82 |
| meta_label_skip | 50 |
| llm_evaluated | 82 |
| llm_pass | 80 |
| llm_veto | 2 |
| risk_evaluated | 80 |
| risk_pass | 10 |
| risk_block | 70 |
| trade_intents | 10 |

## Final Blockers

| blocker | count |
| --- | ---: |
| ENSEMBLE_DISCARD | 226 |
| LLM_VETO | 2 |
| META_LABEL_SKIP | 50 |
| MTF_DISAGREEMENT | 190 |
| NO_BASE_SIGNAL | 151 |
| POST_PIPELINE_NO_INTENT | 19 |
| RISK_BLOCK | 51 |

## Gatekeeper Rejections

| code | count |
| --- | ---: |
| binance_auto_execute_failed | 2 |
| correlated_cluster_exposure_exceeded | 6 |
| max_open_positions_exceeded | 1 |
| net_directional_exposure_exceeded | 12 |
| net_edge_after_cost_negative | 35 |
| validated_edge_stats_missing_or_stale | 1 |

## Terminal Pipeline Statuses

| status | count |
| --- | ---: |
| bet_taken | 80 |
| ensemble_discarded | 226 |
| funding_arbitrage_rejected | 560 |
| meta_label_bet_skipped | 50 |
| multi_timeframe_disagreement | 190 |
| technical_signals_insufficient | 131 |
| universe_status_rejected | 20 |
| unknown | 8 |
| vetoed | 2 |

## Blockers By symbol

| symbol | blocker | count |
| --- | --- | ---: |
| ADA/USDT | ENSEMBLE_DISCARD | 7 |
| ADA/USDT | META_LABEL_SKIP | 1 |
| ADA/USDT | MTF_DISAGREEMENT | 5 |
| ADA/USDT | NO_BASE_SIGNAL | 8 |
| AVAX/USDT | ENSEMBLE_DISCARD | 12 |
| AVAX/USDT | MTF_DISAGREEMENT | 6 |
| AVAX/USDT | NO_BASE_SIGNAL | 2 |
| AVAX/USDT | RISK_BLOCK | 1 |
| BNB/USDT | ENSEMBLE_DISCARD | 2 |
| BNB/USDT | MTF_DISAGREEMENT | 10 |
| BNB/USDT | NO_BASE_SIGNAL | 9 |
| BTC/USDT | ENSEMBLE_DISCARD | 52 |
| BTC/USDT | META_LABEL_SKIP | 22 |
| BTC/USDT | MTF_DISAGREEMENT | 17 |
| BTC/USDT | NO_BASE_SIGNAL | 72 |
| BTC/USDT | POST_PIPELINE_NO_INTENT | 15 |
| BTC/USDT | RISK_BLOCK | 8 |
| DOGE/USDT | ENSEMBLE_DISCARD | 5 |
| DOGE/USDT | MTF_DISAGREEMENT | 8 |
| DOGE/USDT | NO_BASE_SIGNAL | 8 |
| ENA/USDT | ENSEMBLE_DISCARD | 3 |
| ENA/USDT | META_LABEL_SKIP | 1 |
| ENA/USDT | MTF_DISAGREEMENT | 7 |
| ETH/USDT | ENSEMBLE_DISCARD | 50 |
| ETH/USDT | META_LABEL_SKIP | 15 |
| ETH/USDT | MTF_DISAGREEMENT | 38 |
| ETH/USDT | NO_BASE_SIGNAL | 15 |
| ETH/USDT | RISK_BLOCK | 30 |
| FET/USDT | MTF_DISAGREEMENT | 10 |
| HBAR/USDT | MTF_DISAGREEMENT | 9 |
| HBAR/USDT | RISK_BLOCK | 1 |
| HYPE/USDT | ENSEMBLE_DISCARD | 3 |
| HYPE/USDT | LLM_VETO | 1 |
| HYPE/USDT | META_LABEL_SKIP | 2 |
| HYPE/USDT | MTF_DISAGREEMENT | 5 |
| HYPE/USDT | RISK_BLOCK | 1 |
| LINK/USDT | ENSEMBLE_DISCARD | 7 |
| LINK/USDT | LLM_VETO | 1 |
| LINK/USDT | META_LABEL_SKIP | 1 |
| LINK/USDT | MTF_DISAGREEMENT | 3 |
| LINK/USDT | NO_BASE_SIGNAL | 4 |
| LINK/USDT | RISK_BLOCK | 5 |
| ONDO/USDT | ENSEMBLE_DISCARD | 4 |
| ONDO/USDT | MTF_DISAGREEMENT | 5 |
| ONDO/USDT | RISK_BLOCK | 2 |
| PEPE/USDT | ENSEMBLE_DISCARD | 1 |
| PEPE/USDT | META_LABEL_SKIP | 2 |
| PEPE/USDT | MTF_DISAGREEMENT | 8 |
| RENDER/USDT | ENSEMBLE_DISCARD | 3 |
| RENDER/USDT | META_LABEL_SKIP | 1 |
| RENDER/USDT | MTF_DISAGREEMENT | 7 |
| SOL/USDT | ENSEMBLE_DISCARD | 52 |
| SOL/USDT | MTF_DISAGREEMENT | 17 |
| SOL/USDT | NO_BASE_SIGNAL | 20 |
| SOL/USDT | POST_PIPELINE_NO_INTENT | 4 |
| SOL/USDT | RISK_BLOCK | 3 |
| SUI/USDT | ENSEMBLE_DISCARD | 4 |
| SUI/USDT | META_LABEL_SKIP | 1 |
| SUI/USDT | MTF_DISAGREEMENT | 7 |
| TAO/USDT | ENSEMBLE_DISCARD | 7 |
| TAO/USDT | META_LABEL_SKIP | 3 |
| TAO/USDT | MTF_DISAGREEMENT | 1 |
| TON/USDT | MTF_DISAGREEMENT | 11 |
| TRX/USDT | ENSEMBLE_DISCARD | 10 |
| TRX/USDT | MTF_DISAGREEMENT | 8 |
| TRX/USDT | NO_BASE_SIGNAL | 4 |
| XRP/USDT | ENSEMBLE_DISCARD | 4 |
| XRP/USDT | META_LABEL_SKIP | 1 |
| XRP/USDT | MTF_DISAGREEMENT | 8 |
| XRP/USDT | NO_BASE_SIGNAL | 9 |

## Blockers By hour_utc

| hour_utc | blocker | count |
| --- | --- | ---: |
| 2026-07-15T08:00Z | ENSEMBLE_DISCARD | 6 |
| 2026-07-15T08:00Z | MTF_DISAGREEMENT | 12 |
| 2026-07-15T08:00Z | RISK_BLOCK | 6 |
| 2026-07-15T09:00Z | ENSEMBLE_DISCARD | 31 |
| 2026-07-15T09:00Z | LLM_VETO | 1 |
| 2026-07-15T09:00Z | META_LABEL_SKIP | 16 |
| 2026-07-15T09:00Z | MTF_DISAGREEMENT | 34 |
| 2026-07-15T09:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-15T09:00Z | RISK_BLOCK | 9 |
| 2026-07-15T10:00Z | ENSEMBLE_DISCARD | 22 |
| 2026-07-15T10:00Z | LLM_VETO | 1 |
| 2026-07-15T10:00Z | META_LABEL_SKIP | 17 |
| 2026-07-15T10:00Z | MTF_DISAGREEMENT | 31 |
| 2026-07-15T10:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-15T10:00Z | RISK_BLOCK | 7 |
| 2026-07-15T16:00Z | ENSEMBLE_DISCARD | 7 |
| 2026-07-15T16:00Z | META_LABEL_SKIP | 7 |
| 2026-07-15T16:00Z | MTF_DISAGREEMENT | 17 |
| 2026-07-15T16:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-15T16:00Z | RISK_BLOCK | 2 |
| 2026-07-15T17:00Z | ENSEMBLE_DISCARD | 6 |
| 2026-07-15T17:00Z | META_LABEL_SKIP | 8 |
| 2026-07-15T17:00Z | MTF_DISAGREEMENT | 27 |
| 2026-07-15T17:00Z | RISK_BLOCK | 11 |
| 2026-07-16T09:00Z | ENSEMBLE_DISCARD | 21 |
| 2026-07-16T09:00Z | META_LABEL_SKIP | 2 |
| 2026-07-16T09:00Z | MTF_DISAGREEMENT | 23 |
| 2026-07-16T09:00Z | NO_BASE_SIGNAL | 54 |
| 2026-07-16T10:00Z | ENSEMBLE_DISCARD | 1 |
| 2026-07-16T10:00Z | NO_BASE_SIGNAL | 2 |
| 2026-07-17T07:00Z | ENSEMBLE_DISCARD | 13 |
| 2026-07-17T07:00Z | MTF_DISAGREEMENT | 6 |
| 2026-07-17T07:00Z | NO_BASE_SIGNAL | 7 |
| 2026-07-17T08:00Z | ENSEMBLE_DISCARD | 11 |
| 2026-07-17T08:00Z | MTF_DISAGREEMENT | 7 |
| 2026-07-17T08:00Z | NO_BASE_SIGNAL | 4 |
| 2026-07-17T09:00Z | ENSEMBLE_DISCARD | 9 |
| 2026-07-17T09:00Z | MTF_DISAGREEMENT | 7 |
| 2026-07-17T09:00Z | NO_BASE_SIGNAL | 8 |
| 2026-07-17T10:00Z | ENSEMBLE_DISCARD | 3 |
| 2026-07-17T10:00Z | MTF_DISAGREEMENT | 3 |
| 2026-07-17T10:00Z | NO_BASE_SIGNAL | 10 |
| 2026-07-17T10:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-17T10:00Z | RISK_BLOCK | 1 |
| 2026-07-18T05:00Z | ENSEMBLE_DISCARD | 10 |
| 2026-07-18T05:00Z | MTF_DISAGREEMENT | 4 |
| 2026-07-18T05:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-19T10:00Z | ENSEMBLE_DISCARD | 12 |
| 2026-07-19T10:00Z | MTF_DISAGREEMENT | 3 |
| 2026-07-21T03:00Z | ENSEMBLE_DISCARD | 11 |
| 2026-07-21T03:00Z | POST_PIPELINE_NO_INTENT | 6 |
| 2026-07-21T03:00Z | RISK_BLOCK | 3 |
| 2026-07-21T04:00Z | ENSEMBLE_DISCARD | 3 |
| 2026-07-21T04:00Z | POST_PIPELINE_NO_INTENT | 2 |
| 2026-07-21T04:00Z | RISK_BLOCK | 1 |
| 2026-07-21T05:00Z | ENSEMBLE_DISCARD | 8 |
| 2026-07-21T05:00Z | MTF_DISAGREEMENT | 3 |
| 2026-07-21T05:00Z | NO_BASE_SIGNAL | 5 |
| 2026-07-21T05:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-21T06:00Z | ENSEMBLE_DISCARD | 8 |
| 2026-07-21T06:00Z | NO_BASE_SIGNAL | 12 |
| 2026-07-21T06:00Z | RISK_BLOCK | 5 |
| 2026-07-21T07:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-22T02:00Z | ENSEMBLE_DISCARD | 6 |
| 2026-07-22T02:00Z | MTF_DISAGREEMENT | 3 |
| 2026-07-22T02:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-22T03:00Z | ENSEMBLE_DISCARD | 9 |
| 2026-07-22T03:00Z | MTF_DISAGREEMENT | 4 |
| 2026-07-22T03:00Z | NO_BASE_SIGNAL | 5 |
| 2026-07-22T03:00Z | POST_PIPELINE_NO_INTENT | 3 |
| 2026-07-22T03:00Z | RISK_BLOCK | 3 |
| 2026-07-22T04:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-22T05:00Z | ENSEMBLE_DISCARD | 6 |
| 2026-07-22T05:00Z | MTF_DISAGREEMENT | 2 |
| 2026-07-22T05:00Z | NO_BASE_SIGNAL | 2 |
| 2026-07-22T05:00Z | RISK_BLOCK | 1 |
| 2026-07-22T06:00Z | ENSEMBLE_DISCARD | 14 |
| 2026-07-22T06:00Z | MTF_DISAGREEMENT | 4 |
| 2026-07-22T06:00Z | NO_BASE_SIGNAL | 9 |
| 2026-07-22T07:00Z | ENSEMBLE_DISCARD | 4 |
| 2026-07-22T07:00Z | NO_BASE_SIGNAL | 20 |
| 2026-07-22T07:00Z | POST_PIPELINE_NO_INTENT | 2 |
| 2026-07-22T08:00Z | ENSEMBLE_DISCARD | 5 |
| 2026-07-22T08:00Z | NO_BASE_SIGNAL | 10 |
| 2026-07-22T08:00Z | RISK_BLOCK | 2 |

## Blockers By regime

| regime | blocker | count |
| --- | --- | ---: |
| low_volatility | ENSEMBLE_DISCARD | 201 |
| low_volatility | LLM_VETO | 1 |
| low_volatility | META_LABEL_SKIP | 48 |
| low_volatility | MTF_DISAGREEMENT | 164 |
| low_volatility | NO_BASE_SIGNAL | 129 |
| low_volatility | POST_PIPELINE_NO_INTENT | 19 |
| low_volatility | RISK_BLOCK | 45 |
| normal_volatility | ENSEMBLE_DISCARD | 25 |
| normal_volatility | LLM_VETO | 1 |
| normal_volatility | META_LABEL_SKIP | 2 |
| normal_volatility | MTF_DISAGREEMENT | 26 |
| normal_volatility | NO_BASE_SIGNAL | 2 |
| normal_volatility | RISK_BLOCK | 6 |
| unknown | NO_BASE_SIGNAL | 20 |

## Blockers By direction

| direction | blocker | count |
| --- | --- | ---: |
| long | ENSEMBLE_DISCARD | 104 |
| long | LLM_VETO | 2 |
| long | META_LABEL_SKIP | 28 |
| long | MTF_DISAGREEMENT | 51 |
| long | POST_PIPELINE_NO_INTENT | 17 |
| long | RISK_BLOCK | 45 |
| mixed | ENSEMBLE_DISCARD | 35 |
| mixed | MTF_DISAGREEMENT | 27 |
| none | NO_BASE_SIGNAL | 151 |
| short | ENSEMBLE_DISCARD | 87 |
| short | META_LABEL_SKIP | 22 |
| short | MTF_DISAGREEMENT | 112 |
| short | POST_PIPELINE_NO_INTENT | 2 |
| short | RISK_BLOCK | 6 |
