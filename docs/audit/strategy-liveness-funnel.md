# Strategy Liveness Funnel

- Generated: 2026-07-23T08:15:26.728623+00:00
- Since: 2026-07-16T08:15:26.198293+00:00
- Strategy: all
- Persisted decision snapshots: 597
- Entry evaluations: 584
- Actual pipeline order: base signal -> multi-timeframe -> ensemble -> meta-label -> LLM -> Gatekeeper -> TradeIntent

## Sequential Funnel

| stage | entered | passed | eliminated | elimination rate |
| --- | ---: | ---: | ---: | ---: |
| base_signal | 584 | 402 | 182 | 31.16% |
| multi_timeframe | 402 | 314 | 88 | 21.89% |
| ensemble | 314 | 83 | 231 | 73.57% |
| meta_label | 83 | 81 | 2 | 2.41% |
| llm_veto | 81 | 81 | 0 | 0.00% |
| gatekeeper | 81 | 13 | 68 | 83.95% |
| trade_intent | 13 | 13 | 0 | 0.00% |

## Core Metrics

| metric | count |
| --- | ---: |
| cycles | 238 |
| symbols_evaluated | 584 |
| raw_long_signals | 273 |
| raw_short_signals | 295 |
| no_base_signal | 182 |
| mtf_evaluated | 402 |
| mtf_pass | 314 |
| mtf_disagreement | 88 |
| ensemble_evaluated | 314 |
| ensemble_pass | 83 |
| ensemble_discard | 231 |
| meta_label_evaluated | 83 |
| meta_label_pass | 81 |
| meta_label_skip | 2 |
| llm_evaluated | 81 |
| llm_pass | 81 |
| llm_veto | 0 |
| risk_evaluated | 81 |
| risk_pass | 13 |
| risk_block | 68 |
| trade_intents | 13 |

## Final Blockers

| blocker | count |
| --- | ---: |
| ENSEMBLE_DISCARD | 231 |
| META_LABEL_SKIP | 2 |
| MTF_DISAGREEMENT | 88 |
| NO_BASE_SIGNAL | 182 |
| POST_PIPELINE_NO_INTENT | 33 |
| RISK_BLOCK | 35 |

## Gatekeeper Rejections

| code | count |
| --- | ---: |
| binance_auto_execute_failed | 17 |
| correlated_cluster_exposure_exceeded | 10 |
| max_open_positions_exceeded | 1 |
| net_directional_exposure_exceeded | 15 |
| validated_edge_stats_missing_or_stale | 1 |

## Terminal Pipeline Statuses

| status | count |
| --- | ---: |
| bet_taken | 81 |
| ensemble_discarded | 231 |
| meta_label_bet_skipped | 2 |
| multi_timeframe_disagreement | 88 |
| technical_signals_insufficient | 162 |
| universe_status_rejected | 20 |
| unknown | 13 |

## Blockers By symbol

| symbol | blocker | count |
| --- | --- | ---: |
| ADA/USDT | ENSEMBLE_DISCARD | 1 |
| ADA/USDT | MTF_DISAGREEMENT | 1 |
| ADA/USDT | NO_BASE_SIGNAL | 8 |
| AVAX/USDT | ENSEMBLE_DISCARD | 7 |
| AVAX/USDT | MTF_DISAGREEMENT | 1 |
| AVAX/USDT | NO_BASE_SIGNAL | 2 |
| BNB/USDT | MTF_DISAGREEMENT | 2 |
| BNB/USDT | NO_BASE_SIGNAL | 8 |
| BTC/USDT | ENSEMBLE_DISCARD | 64 |
| BTC/USDT | MTF_DISAGREEMENT | 26 |
| BTC/USDT | NO_BASE_SIGNAL | 79 |
| BTC/USDT | POST_PIPELINE_NO_INTENT | 25 |
| BTC/USDT | RISK_BLOCK | 6 |
| DOGE/USDT | ENSEMBLE_DISCARD | 1 |
| DOGE/USDT | MTF_DISAGREEMENT | 1 |
| DOGE/USDT | NO_BASE_SIGNAL | 8 |
| ETH/USDT | ENSEMBLE_DISCARD | 73 |
| ETH/USDT | META_LABEL_SKIP | 2 |
| ETH/USDT | MTF_DISAGREEMENT | 40 |
| ETH/USDT | NO_BASE_SIGNAL | 27 |
| ETH/USDT | POST_PIPELINE_NO_INTENT | 2 |
| ETH/USDT | RISK_BLOCK | 26 |
| LINK/USDT | ENSEMBLE_DISCARD | 3 |
| LINK/USDT | MTF_DISAGREEMENT | 3 |
| LINK/USDT | NO_BASE_SIGNAL | 4 |
| SOL/USDT | ENSEMBLE_DISCARD | 80 |
| SOL/USDT | MTF_DISAGREEMENT | 8 |
| SOL/USDT | NO_BASE_SIGNAL | 34 |
| SOL/USDT | POST_PIPELINE_NO_INTENT | 6 |
| SOL/USDT | RISK_BLOCK | 3 |
| TRX/USDT | ENSEMBLE_DISCARD | 2 |
| TRX/USDT | MTF_DISAGREEMENT | 4 |
| TRX/USDT | NO_BASE_SIGNAL | 4 |
| XRP/USDT | MTF_DISAGREEMENT | 2 |
| XRP/USDT | NO_BASE_SIGNAL | 8 |

## Blockers By hour_utc

| hour_utc | blocker | count |
| --- | --- | ---: |
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
| 2026-07-22T08:00Z | ENSEMBLE_DISCARD | 6 |
| 2026-07-22T08:00Z | NO_BASE_SIGNAL | 10 |
| 2026-07-22T08:00Z | POST_PIPELINE_NO_INTENT | 1 |
| 2026-07-22T08:00Z | RISK_BLOCK | 5 |
| 2026-07-22T09:00Z | ENSEMBLE_DISCARD | 8 |
| 2026-07-22T09:00Z | MTF_DISAGREEMENT | 1 |
| 2026-07-22T09:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-22T09:00Z | POST_PIPELINE_NO_INTENT | 8 |
| 2026-07-22T09:00Z | RISK_BLOCK | 10 |
| 2026-07-23T01:00Z | ENSEMBLE_DISCARD | 7 |
| 2026-07-23T01:00Z | MTF_DISAGREEMENT | 5 |
| 2026-07-23T01:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-23T01:00Z | POST_PIPELINE_NO_INTENT | 3 |
| 2026-07-23T02:00Z | ENSEMBLE_DISCARD | 20 |
| 2026-07-23T02:00Z | MTF_DISAGREEMENT | 7 |
| 2026-07-23T02:00Z | NO_BASE_SIGNAL | 1 |
| 2026-07-23T02:00Z | POST_PIPELINE_NO_INTENT | 2 |
| 2026-07-23T03:00Z | ENSEMBLE_DISCARD | 11 |
| 2026-07-23T03:00Z | NO_BASE_SIGNAL | 21 |
| 2026-07-23T05:00Z | ENSEMBLE_DISCARD | 8 |
| 2026-07-23T05:00Z | MTF_DISAGREEMENT | 2 |
| 2026-07-23T05:00Z | NO_BASE_SIGNAL | 4 |
| 2026-07-23T05:00Z | RISK_BLOCK | 2 |
| 2026-07-23T06:00Z | ENSEMBLE_DISCARD | 19 |
| 2026-07-23T06:00Z | MTF_DISAGREEMENT | 4 |
| 2026-07-23T06:00Z | NO_BASE_SIGNAL | 5 |
| 2026-07-23T06:00Z | RISK_BLOCK | 4 |
| 2026-07-23T07:00Z | ENSEMBLE_DISCARD | 3 |
| 2026-07-23T07:00Z | NO_BASE_SIGNAL | 1 |

## Blockers By regime

| regime | blocker | count |
| --- | --- | ---: |
| low_volatility | ENSEMBLE_DISCARD | 221 |
| low_volatility | META_LABEL_SKIP | 2 |
| low_volatility | MTF_DISAGREEMENT | 84 |
| low_volatility | NO_BASE_SIGNAL | 160 |
| low_volatility | POST_PIPELINE_NO_INTENT | 33 |
| low_volatility | RISK_BLOCK | 35 |
| normal_volatility | ENSEMBLE_DISCARD | 10 |
| normal_volatility | MTF_DISAGREEMENT | 4 |
| normal_volatility | NO_BASE_SIGNAL | 2 |
| unknown | NO_BASE_SIGNAL | 20 |

## Blockers By direction

| direction | blocker | count |
| --- | --- | ---: |
| long | ENSEMBLE_DISCARD | 82 |
| long | META_LABEL_SKIP | 2 |
| long | MTF_DISAGREEMENT | 27 |
| long | POST_PIPELINE_NO_INTENT | 28 |
| long | RISK_BLOCK | 33 |
| mixed | ENSEMBLE_DISCARD | 33 |
| mixed | MTF_DISAGREEMENT | 8 |
| none | NO_BASE_SIGNAL | 182 |
| short | ENSEMBLE_DISCARD | 116 |
| short | MTF_DISAGREEMENT | 53 |
| short | POST_PIPELINE_NO_INTENT | 5 |
| short | RISK_BLOCK | 2 |
