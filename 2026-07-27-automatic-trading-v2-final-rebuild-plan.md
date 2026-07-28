# èªå¨å¼å¹³åé¾è·¯ V2 ä¸æ¬¡æ§éæå®æ½æ»æ¹æ¡

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Every checkbox is a separate verification step. Do not batch unrelated tasks. Do not weaken existing tests to fit the implementation.
>
> ç®æ ä»åºï¼`Metroids048/AI-`
> æ¥æï¼2026-07-27
> å»ºè®®ä¿å­è·¯å¾ï¼`docs/superpowers/plans/2026-07-27-automatic-trading-v2-final-rebuild.md`

**Goal:** å¨ä¸å¼æ¾ Mainnet çåæä¸ï¼éå»ºä¸æ¡å¯ä¸ãçå®ãå¯è§å¯ãå¯æ¢å¤ç Binance USDT-M Testnet èªå¨äº¤æé¾è·¯ï¼é­å K çº¿ä¸å®æ¶è¡æ â ç¡®å®æ§åé â æè¾¹çç AI å®¡é â å¥åºé£æ§ â Binance çå®æäº¤ â æ¬å°æäº¤æå½± â Binance çå®ä¿æ¤å â èªç¶éåº/æ­¢æ/æ­¢ç â ReduceOnly çå®å¹³ä» â æ¬å°ä¸äº¤æææç»ä¸è´ã

**Architecture:** å»ç»ç°æ `paper_*` æ··åé¾è·¯ï¼ä¸åç»§ç»­å¨ä¸¤ä¸ªåè¡çº§æä»¶ä¸­å å åæ¯ãæ°å¢ç¬ç«ç `services/automated_trading/` åç´æ¨¡åï¼ä»¥äºä»¶é©±å¨ç¶ææºãä¸å¯åäº¤ææåæ§ãäº¤æææå¨å¿«ç§åååå¥è Scheduler ä¸ºæ ¸å¿ãæ§é¾è·¯ä»ä¿ç Local Paper ååå²è¯»åè½åï¼V2 å¨ Shadow æ¨¡å¼éªè¯ååå¾å¯ä¸ Testnet åå¥æï¼å®æåæ¢åå é¤æ§äº¤ææåå¥å¥å£ã

**Tech Stack:** Python 3ãPydanticãSQLAlchemy/AlembicãBinance USDT-M TestnetãFastAPIãReact/ViteãPytestãç°æ Scheduler/Lease/Fencing åºç¡è®¾æ½ã

---

# 0. ä¸ºä»ä¹å¿é¡»æ V2 éå»ºï¼èä¸è½ç»§ç»­å±é¨ä¿®è¡¥

## 0.1 å·²ç»éè¿æµè¯å¤ç°ççå®ç¼ºé·

å½åå³é®æºç å·²ç»éè¿å¤±è´¥æµè¯å¤ç°è¿ä»¥ä¸é®é¢ï¼

1. æ²¡æç¡®è®¤äº¤æææäº¤ï¼ä¹å¯è½åå»º `MANAGED_STRATEGY` æ¬å°ä»ä½ã
2. Binance Gateway ç¼ºå¤±æ¶ï¼æ¬å°è®¢åä»å¯è½ä¿æ `accepted`ã
3. å¯¹è´¦å¤±è´¥æ¶ï¼é»è®¤ç©ºé»æ­éåå¯è½è¢«è§£éä¸ºâåè®¸å¼ä»âã
4. Entry Kill Switch ä¼é»æ­¢ ReduceOnly éé£é©éåºã
5. è¯·æ± Testnet ä½è¿è¡æ¡ä»¶æªæ­¦è£æ¶ï¼Orchestrator å¯è½ç»§ç»­èµ°æ¬å° fill/open-positionã
6. è¿å»çâExchange-First å·²éè¿âä¸»è¦æ¯ Fake Binance Adapter æ Exchange Emulatorï¼ç½ç»è®¢åæ°ä¸º 0ã
7. è¿å»çâèªç¶ç­ç¥å·²éè¿âæåä»ç¶çææå¼ä»ä½ï¼æªè¯æèªç¶èªå¨å¹³ä»ã
8. å½å `paper_cycle_orchestrator.py` è¶è¿ 11 ä¸å­èï¼`paper_exchange_execution.py` è¶è¿ 6 ä¸å­èï¼èè´£ç»§ç»­å å ä¼ä½¿ä»»ä½ä¿®å¤é½äº§çæ°çéå¼åæ¯ã
9. åç«¯éè¿å¤ä¸ª API æ¼è£ç¶æï¼å¹¶ç¨ PaperRun åç§°ååéæ°éçæµâå½åèªå¨è¿è¡å®ä¾âï¼ä¸æ¯ä»ä¸ä¸ªæå¨ Runtime Contract è¯»åã
10. `binance_simulation_first`ã`mirror_to_gateway`ãæ¬å° PaperãTestnet Acceptanceãèªç¶ç­ç¥æ§è¡ç­æ¦å¿µæ··å¨åä¸æ¨¡åä¸­ï¼å¯¼è´âæ¨¡ææäº¤ââéåè®¢åââçå®æäº¤âè¯­ä¹ä¸å¯ä¸ã

## 0.2 å·²ç»ç¡®è®¤ä¸æ¯å½åé®é¢çåå®¹

ä»¥ä¸åå®¹ä¸å¾åæ¬¡ä½ä¸ºæ¬è½®ä¿®æ¹ç®æ ï¼é¤éæ°çå¤±è´¥æµè¯éæ°è¯æå­å¨é®é¢ï¼

- å½åçæ¬ CloseOnly å·²ä¸å¼ä»æå°åä¹éé¢è¡¥é½åæ¯åå¼ï¼ä¸å¾å­æ§å¤æ­åæ¬¡ä¿®æ¹ã
- ä½¿ç¨é­å K çº¿æ¬èº«ä¸æ¯ Bugï¼é®é¢æ¯æ²¡æå³ç­æ¼æãå®æ¶æäº¤åä»·æ ¼å¿«ç§åæ§è¡ä»·æ ¼æ¼ç§»æ£æ¥ã
- AI ä¸åºç´æ¥çæä»»æè®¢åä»·æ ¼åæ°éï¼API ç¨éä¸º 0 åºéè¿å¯è§å¯çè°ç¨é¾è§£å³ï¼èä¸æ¯è®© AI è·å¾æ éäº¤ææéã
- Testnet Acceptance åºå®å¾è¿ååªè½è¯æåºç¡è®¾æ½è¿æ¥ï¼ä¸è½ä½ä¸ºèªç¶ç­ç¥é­ç¯è¯æã

## 0.3 æ¬æ¬¡éæ©çéææ¹å¼

### æ¹æ¡ Aï¼ç»§ç»­ä¿®æ¹ç°æ `paper_*` æä»¶

æç»ãåå ï¼

- ç¶æè¯­ä¹æ··ä¹±å·²ç»æ¯æ¶æé®é¢ï¼
- æ¯æ¬¡ä¿®è¡¥é½ä¼è§¦ç¢°å¤ä¸ªå±äº«ç¶æï¼
- æ§æµè¯å¤§éä¾èµæ··åæ¨¡å¼ï¼å®¹æä¸ºäºå¼å®¹èä¿çéè¯¯åæ¯ï¼
- æ æ³å¯é è¯æåªæ¡è·¯å¾æ¯çå® Testnetï¼åªæ¡è·¯å¾æ¯æ¬å°æ¨¡æã

### æ¹æ¡ Bï¼å¨åæä»¶åå¤§è§æ¨¡éæ

ä¸æ¨èãåå ï¼

- æ¹å¨é¢è¿å¤§ï¼æ§ä»£ç åæ°ä»£ç å¨åä¸æä»¶ååæ¶å­å¨ï¼
- å¾é¾å¨å®æ½æé´ç»´æå¯è¿è¡åºçº¿ï¼
- Agent å®¹æç»§ç»­å¤ç¨æ§ç§æå½æ°ï¼å½¢æåæ°åæ§é¾è·¯ã

### æ¹æ¡ Cï¼å¹¶è¡å»ºç« V2 åç´é¾è·¯ï¼Shadow éªè¯åä¸æ¬¡åæ¢

**æ¬æ¹æ¡éç¨ã**

å³é®ååï¼

- æ°é¾è·¯ä½¿ç¨æ°åãæ°è¡¨ãæ° APIãæ°è¿è¡æ è¯ï¼
- æ§é¾è·¯å¨åæ¢ååªæ¥åå®å¨è¡¥ä¸ï¼ä¸åå¢å ä¸å¡åè½ï¼
- Shadow é¶æ®µ V2 åªäº§çå³ç­åè®¢åè®¡åï¼ä¸åéäº¤ææè®¢åï¼
- Active é¶æ®µåªæ V2 æ¥æ Testnet ä¸åæéï¼
- åæ»åªå³é­ V2 Entryï¼ä¸éæ°æ¿æ´»æ§åå¥èï¼
- å·²æå¼ç V2 ä»ä½å§ç»ç± V2 Recovery/Exit è·¯å¾ç®¡çå°å¹³ä»ï¼ä¸è½è½¬äº¤æ§ç³»ç»ã

---

# 1. èå´ãéç®æ åä¸å¯åçº¦æ

## 1.1 æ¬è½®èå´

ä»æ¯æï¼

- Binance USDT-M Testnetï¼
- BTC/USDTãETH/USDTï¼
- ååæä»æ¨¡å¼ï¼
- èªå¨æ¹åäº¤æï¼
- åçä»æ¯æ Market EntryãMarket ReduceOnly Exitï¼
- äº¤æææ­¢æä¸æ­¢çä¿æ¤ï¼
- Local Paper ç¬ç«æ¨¡æï¼
- Testnet Sampling ç¬ç«éæ ·ï¼
- Production Candidate ç¬ç«ç ç©¶/éªè¯ï¼
- AI å¸åºå®¡éååéå®¡éï¼
- Runtime Truth API ä¸åç«¯å¯è§å¯é¡µé¢ã

## 1.2 æ¬è½®æç¡®ä¸å

- ä¸å¼æ¾ Binance Mainnetï¼
- ä¸æ¯æå¤äº¤ææï¼
- ä¸æ¯æ Hedge Mode ååæä»ï¼
- ä¸æ¯æèªå¨æ¥ç®¡æ æ³ç¡®è®¤å½å±çäººå·¥ä»ä½ï¼
- ä¸å¨ V2 é¦çæ¯æéä»·å¥åºãå°å±±ãTWAPï¼
- ä¸è®© LLM ç´æ¥çæ quantityãleverageãstop priceãtake-profit priceï¼
- ä¸è®© AI é»æ­¢ç¡¬æ­¢æãä¿æ¤å¤±è´¥ç´§æ¥å¹³ä»ææ¸ç®é²æ¤ï¼
- ä¸æ Testnet Sampling çäº¤æç»æåå¥æ­£å¼ç­ç¥æåè¯æ®ï¼
- ä¸è¿ç§»æ§å¹½çµåä¸º V2 Managed Positionï¼
- ä¸åæ¶è¿è¡ä¸¤ä¸ª Testnet è®¢ååå¥èã

## 1.3 å¨å±ä¸å¯åçº¦æ

1. `BINANCE_TESTNET` æ¨¡å¼æ²¡æçå®æäº¤åæ§æ¶ï¼æ°æ®åºä¸­ä¸å¾å­å¨ V2 Managed Positionã
2. æ¬å° `INTENT_CREATED`ã`SUBMITTING`ã`ACKNOWLEDGED` é½ä¸ç­äºæäº¤ã
3. `FILLED` å¿é¡»æ `exchange_order_id`ãè³å°ä¸ä¸ª `trade_id`ãæ­£ç `filled_quantity`ãæ­£ç `average_fill_price`ã
4. V2 æ¬å°ä»ä½åªæ¯äº¤æææäº¤äºå®çæå½±ï¼ä¸æ¯ç¬ç«çç¸ã
5. å¯¹è´¦ç¶æä¸æ¯ `HEALTHY` æ¶ï¼ç¦æ­¢æææ°å¢ Entryã
6. å¯¹è´¦å¼å¸¸ä¸å¾é»æ­¢ ReduceOnly éé£é©éåºã
7. ç¡¬éåºä¸ä¾èµç­ç¥ ManifestãLLMãMetaLabelãNet Edge æä¿¡å·æ°æ®æ°é²åº¦ã
8. å¥åºæ­¢ææ­¢ççç»å¯¹ä»·æ ¼å¿é¡»å¨çå®æäº¤åï¼ä»¥ `average_fill_price` éç®ã
9. æ¬å° Protection åªæåå¾äº¤ææä¿æ¤è®¢å ID åæè½æ è®°ä¸º `ACTIVE`ã
10. æ æ³ç¡®è®¤è®¢åæ¯å¦å·²æäº¤æ¶è¿å¥ `EXCHANGE_UNKNOWN`ï¼åæ Client Order ID å¯¹è´¦ï¼ç¦æ­¢ç²ç®éå¤ä¸åã
11. Scheduler Fencing Token å¿é¡»ç»å®å° CycleãIntent åè®¢åæäº¤ã
12. æ¯æ ¹è¢«è¯ä¼°çé­åå³ç­ K çº¿å¿é¡»äº§çä¸æ¡ç»æ Decision Funnel è®°å½ã
13. API æªæ¥éææ°æ®ç¼ºå¤±æ¶è¿å `null/UNAVAILABLE`ï¼ä¸å¾ç¨ `0`ãåä½é¢æé»è®¤å¨çº¿ç¶æä»£æ¿ã
14. Mainnet éç½®ä¸è¿å¥ V2 æä¸¾ï¼ä¸æ¯âé»è®¤å³é­âï¼èæ¯ V2 æ ¹æ¬æ²¡æ Mainnet æ§è¡å®ç°ã
15. ä»»ä½é¶æ®µæ²¡ææ»¡è¶³éªæ¶é¨æ§ï¼ç¦æ­¢è¿å¥ä¸ä¸é¶æ®µã

---

# 2. ç®æ ç®å½ä¸èè´£è¾¹ç

## 2.1 æ°å¢ç®å½

```text
services/automated_trading/
âââ __init__.py
âââ domain/
â   âââ enums.py
â   âââ commands.py
â   âââ events.py
â   âââ receipts.py
â   âââ state.py
â   âââ candidates.py
â   âââ invariants.py
âââ application/
â   âââ cycle_service.py
â   âââ decision_service.py
â   âââ entry_service.py
â   âââ exit_service.py
â   âââ protection_service.py
â   âââ reconciliation_service.py
â   âââ recovery_service.py
â   âââ sampling_service.py
â   âââ ai_review_service.py
âââ infrastructure/
â   âââ models.py
â   âââ repository.py
â   âââ binance_adapter.py
â   âââ local_paper_adapter.py
â   âââ runtime_lock.py
â   âââ market_snapshot_provider.py
âââ observability/
    âââ decision_funnel.py
    âââ runtime_snapshot.py
    âââ evidence_bundle.py
    âââ metrics.py
```

## 2.2 ç°ææä»¶å¤çç­ç¥

### å»ç»ï¼ä¸åå¢å åè½

```text
services/execution/paper_cycle_orchestrator.py
services/execution/paper_exchange_execution.py
services/execution/paper_order_lifecycle.py
services/execution/paper_signal.py
```

åè®¸çä¿®æ¹ä»éï¼

- ä¿çå·²éªè¯çå¹½çµåå®å¨å®å«ï¼
- æ·»å  Legacy Deprecated æ è®°ï¼
- å¨åæ¢é¶æ®µåæ­¢æ§ Testnet åå¥ï¼
- å é¤æ§å¥å£ã

ç¦æ­¢ç»§ç»­å¨è¿äºæä»¶ä¸­å¢å ï¼

- æ°ç­ç¥æ¡ä»¶ï¼
- æ° AI åæ¯ï¼
- æ°ä¿æ¤åç®æ³ï¼
- æ°å¯¹è´¦ç¶æï¼
- æ° Testnet Samplingï¼
- æ°åç«¯å­æ®µã

### å¤ç¨ä½éè¿ Adapter éç¦»

```text
services/execution/gateway.py
services/execution/scheduler_coordination.py
services/execution/order_normalizer.py
services/strategy_library/repository.py
```

V2 Application ä¸å¾ç´æ¥è°ç¨æ§ Orchestrator ææ§ Paper Lifecycleã

### éè¦ä¿®æ¹

```text
shared/models/__init__.py
apps/api/main.py
apps/api/routers/runs.py
services/execution/bootstrap.py
services/execution/scheduler.py
services/execution/tasks.py
frontend/admin/src/pages/PaperConsole.jsx
frontend/admin/src/hooks/useConsoleData.js
frontend/admin/src/components/RuntimePanels.jsx
frontend/admin/src/components/TradingConsolePanels.jsx
```

ä¿®æ¹æ¹å¼åºæ¯âåæ¢å° V2 å¥çº¦ææ è®° Legacyâï¼ä¸æ¯ç»§ç»­æ V2 é»è¾å¡åæ§æä»¶ã

---

# 3. æ ¸å¿é¢åæ¨¡å

## 3.1 è¿è¡æ¨¡å¼

```python
class AutomatedTradingMode(StrEnum):
    LOCAL_PAPER = "LOCAL_PAPER"
    BINANCE_TESTNET = "BINANCE_TESTNET"
```

ç¦æ­¢åºç°ï¼

```text
binance_simulation_first
mirror_to_gateway
testnet-but-local-fill
```

## 3.2 è¿è¡ç¶æ

```python
class EngineActivation(StrEnum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
```

è¯­ä¹ï¼

- `DISABLED`ï¼ä¸è¯ä¼°ãä¸æäº¤ï¼
- `SHADOW`ï¼è¯»åçå®æ°æ®ãçæåéãå®æ Gate åè®¢åè§èåï¼ä½ä¸æäº¤ï¼
- `ACTIVE`ï¼åè®¸ V2 æäº¤ Testnet è®¢åï¼
- åä¸æ¶å»åªè½æä¸ä¸ª `ACTIVE` Testnet Engineã

## 3.3 è®¢åç¶ææº

```text
INTENT_CREATED
  â PRETRADE_APPROVED
  â SUBMITTING
  â ACKNOWLEDGED
  â PARTIALLY_FILLED
  â FILLED
  â POSITION_PROJECTED
  â PROTECTION_PENDING
  â PROTECTED
  â EXIT_PENDING
  â EXIT_SUBMITTING
  â EXIT_ACKNOWLEDGED
  â EXIT_PARTIALLY_FILLED
  â CLOSED
```

å¤±è´¥ä¸æ¢å¤ç¶æï¼

```text
PRETRADE_REJECTED
EXCHANGE_REJECTED
EXCHANGE_UNKNOWN
PROTECTION_FAILED
RECOVERY_REQUIRED
EMERGENCY_CLOSE_PENDING
CANCELED
```

ç¦æ­¢è½¬æ¢ç¤ºä¾ï¼

```text
INTENT_CREATED â FILLED
ACKNOWLEDGED â POSITION_PROJECTED
EXCHANGE_REJECTED â FILLED
EXCHANGE_UNKNOWN â æ°å»ºç¬¬äºä¸ªåé»è¾è®¢å
PROTECTION_PENDING â PROTECTEDï¼æ²¡æäº¤ææä¿æ¤å IDï¼
```

## 3.4 Strategy Candidate

ç­ç¥å±åªè¾åºç¸å¯¹é£é©è®¡åï¼ä¸è¾åºéæ§ç»å¯¹ä»·æ ¼ï¼

```python
class TradeCandidate(FrozenModel):
    candidate_id: str
    cycle_id: str
    strategy_id: str
    strategy_version: str
    lane: Literal["PRODUCTION", "TESTNET_SAMPLING"]
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    side: Literal["LONG", "SHORT"]
    signal_candle_close_time: datetime
    signal_reference_price: Decimal
    confidence: Decimal
    stop_distance: Decimal
    take_profit_distance: Decimal
    max_entry_drift_bps: Decimal
    expires_at: datetime
    non_promotable: bool
```

æ§è¡å±å¨æäº¤åè®¡ç®ï¼

```text
LONG:
stop = average_fill_price - stop_distance
take = average_fill_price + take_profit_distance

SHORT:
stop = average_fill_price + stop_distance
take = average_fill_price - take_profit_distance
```

## 3.5 Exchange åæ§

```python
class ExchangeOrderReceipt(FrozenModel):
    account_id: str
    symbol: str
    client_order_id: str
    exchange_order_id: str
    status: str
    requested_quantity: Decimal
    acknowledged_at: datetime
    raw_hash: str

class ExchangeFillReceipt(FrozenModel):
    account_id: str
    symbol: str
    client_order_id: str
    exchange_order_id: str
    trade_ids: tuple[str, ...]
    side: Literal["BUY", "SELL"]
    reduce_only: bool
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal
    commissions: tuple[CommissionRecord, ...]
    exchange_event_time: datetime
    received_at: datetime
    raw_hash: str
```

## 3.6 äº¤æææå¨å¿«ç§

```python
class AuthoritativeAccountSnapshot(FrozenModel):
    account_id: str
    exchange_server_time: datetime
    received_at: datetime
    positions: tuple[AuthoritativePosition, ...]
    open_orders: tuple[AuthoritativeOrder, ...]
    recent_orders: tuple[AuthoritativeOrder, ...]
    recent_trades: tuple[AuthoritativeTrade, ...]
    source: Literal["BINANCE_TESTNET_REST", "BINANCE_TESTNET_STREAM"]
    complete: bool
```

`complete=False` æ¶ä¸å¾ç¨äºè§£é¤ Entry Blockã

---

# 4. æ°æ®åº V2 è®¾è®¡

## 4.1 æ°è¿ç§»

åå»ºï¼

```text
migrations/versions/0013_automated_trading_v2.py
```

åä¸ä»»å¡å¿é¡»åæ­¥å½å schema revision å¸¸éåå¯¹åºæµè¯ï¼é¿ååæ¬¡åºç°âè¿ç§»å·²å° 0013ãä»£ç ä»å 0012âã

## 4.2 æ°è¡¨

### `automated_trading_cycles`

å³é®å­æ®µï¼

```text
cycle_id UUID PK
engine_id
mode
activation
scheduled_for
started_at
completed_at
status
scheduler_instance_id
fencing_token
deployment_sha
reconciliation_status
entry_enabled
failure_code
```

å¯ä¸çº¦æï¼

```text
UNIQUE(engine_id, scheduled_for)
```

### `automated_trade_decisions`

```text
decision_id UUID PK
cycle_id FK
candidate_id
strategy_id
strategy_version
lane
symbol
signal_candle_close_time
terminal_stage
terminal_status
reason_code
trace JSON
created_at
```

å¯ä¸çº¦æï¼

```text
UNIQUE(strategy_id, symbol, signal_candle_close_time, lane)
```

### `automated_trade_intents`

```text
intent_id UUID PK
cycle_id FK
decision_id FK
position_group_id
client_order_id
symbol
side
action
reduce_only
state
requested_quantity NUMERIC(38,18)
signal_reference_price NUMERIC(38,18)
max_entry_drift_bps NUMERIC(18,8)
stop_distance NUMERIC(38,18)
take_profit_distance NUMERIC(38,18)
fencing_token
config_snapshot_id
config_hash
created_at
updated_at
```

å¯ä¸çº¦æï¼

```text
UNIQUE(client_order_id)
UNIQUE(decision_id, action)
```

### `exchange_order_receipts`

```text
receipt_id UUID PK
intent_id FK
account_id
exchange_order_id
client_order_id
status
requested_quantity NUMERIC(38,18)
acknowledged_at
raw_hash
raw_payload JSON
```

å¯ä¸çº¦æï¼

```text
UNIQUE(account_id, exchange_order_id)
UNIQUE(account_id, client_order_id)
```

### `exchange_fill_receipts`

```text
fill_receipt_id UUID PK
intent_id FK
account_id
exchange_order_id
trade_id
filled_quantity NUMERIC(38,18)
fill_price NUMERIC(38,18)
commission NUMERIC(38,18)
commission_asset
exchange_event_time
received_at
raw_hash
```

å¯ä¸çº¦æï¼

```text
UNIQUE(account_id, trade_id)
```

å¹³åæäº¤ä»·ç± repository æææ Fill Receipt èåï¼ä¸æ¥åè°ç¨æ¹æåã

### `managed_positions_v2`

```text
position_group_id UUID PK
account_id
symbol
position_side
strategy_id
strategy_version
lane
entry_intent_id FK
entry_fill_receipt_id FK
exchange_entry_order_id
quantity NUMERIC(38,18)
average_entry_price NUMERIC(38,18)
status
ownership_status
opened_at
closed_at
last_reconciled_at
```

çº¦æï¼

```text
BINANCE_TESTNET + MANAGED:
entry_fill_receipt_id NOT NULL
exchange_entry_order_id NOT NULL
quantity > 0
average_entry_price > 0
```

ä»åè®¸ä¸ä¸ªæå¼çï¼

```text
(account_id, symbol, position_side, ownership_status=MANAGED)
```

### `protection_orders_v2`

```text
protection_id UUID PK
position_group_id FK
protection_type STOP_LOSS | TAKE_PROFIT
client_order_id
exchange_order_id
trigger_price NUMERIC(38,18)
quantity NUMERIC(38,18) NULL
close_position BOOLEAN
state
last_exchange_update_at
failure_code
```

`ACTIVE` çæ°æ®åºåç½®æ¡ä»¶ï¼

```text
exchange_order_id IS NOT NULL
trigger_price > 0
```

### `reconciliation_runs_v2`

```text
reconciliation_id UUID PK
cycle_id FK
account_id
status HEALTHY | DEGRADED | UNAVAILABLE
snapshot_hash
exchange_position_count
local_position_count
mismatch_count
entry_blocked
error_code
started_at
completed_at
details JSON
```

### `recovery_incidents_v2`

```text
incident_id UUID PK
position_group_id NULL
intent_id NULL
severity
incident_type
state
attempt_count
last_error
entry_block_all
created_at
resolved_at
```

### `llm_invocations_v2`

```text
invocation_id UUID PK
cycle_id
decision_id NULL
stage MARKET_REVIEW | TRADE_REVIEW
provider
model
called
skip_reason
status
latency_ms
prompt_tokens
completion_tokens
total_tokens
request_hash
response_hash
error_code
created_at
```

## 4.3 ä¸è¿ç§»æ§å¹½çµå

æ§æ°æ®åªè¿å¥ Legacy Read Modelã

åæ¢æ¶ï¼

- æ¥è¯¢ Binance Testnet çå®ä»ä½ï¼
- æ²¡æäº¤ææä»ä½çæ§æ¬å° Position ä¸è¿ç§»ï¼
- æäº¤ææä»ä½ä½ä¸è½è¯æç­ç¥å½å±çï¼åå¥ `EXTERNAL_QUARANTINED`ï¼
- åªæè½éè¿ Client Order IDãOrder IDãæäº¤åç­ç¥èº«ä»½å®æ´å¹éçä»ä½ï¼æåè®¸å»ºç« V2 Managed Positionï¼
- ä¸åè®¸æ symbolãä»·æ ¼æ¥è¿ææ°éæ¥è¿çæµå½å±ã

---

# 5. å¯ä¸ Cycle é¡ºåº

V2 æ¯ä¸ªå¨æå¿é¡»ä¸¥æ ¼æä»¥ä¸é¡ºåºæ§è¡ï¼

```text
1. è·å Scheduler Lease å Fencing Token
2. åå»º automated_trading_cycle
3. æ ¡éª Engine Activation åé¨ç½²çæ¬
4. åæ­¥ Binance Server Time å Market Rules
5. æåå®æ´ Authoritative Account Snapshot
6. æ§è¡ Reconciliation
7. æ¢å¤ UNKNOWN / RECOVERY_REQUIRED / EMERGENCY_CLOSE_PENDING
8. ç®¡çå·²æä»ä½ï¼
   8.1 æ£æ¥äº¤ææä¿æ¤å
   8.2 æ£æ¥ç¡¬æ­¢æ/æ­¢ç/æ¶é´éåº/ç­ç¥å¤±æ
   8.3 æäº¤ ReduceOnly Exit
   8.4 å¯¹è´¦éåºç»æ
9. è¥ Reconciliation é HEALTHYï¼ç»æ Entry é¨å
10. è¯»åé­å K çº¿åå®æ¶ Market Snapshot
11. å¯¹æ¯ä¸ªæ çè¿è¡ Decision Funnel
12. çæ Production æ Testnet Sampling Candidate
13. å¯éæ§è¡ AI Trade Review
14. Entry Gate
15. Pre-submit ä»·æ ¼æ¼ç§»åçå£æ£æ¥
16. åå»º Intent åç¡®å®æ§ Client Order ID
17. æäº¤ Binance Testnet Market Order
18. æ Client Order ID / Order ID ç¡®è®¤æäº¤
19. åå¥ Fill Receipts
20. æå½± Managed Position
21. ä»¥çå®å¹³åæäº¤ä»·è®¡ç®ä¿æ¤ä»·
22. æäº¤å¹¶ç¡®è®¤ Binance Protection Orders
23. æ§è¡å¨ææ« Reconciliation
24. çæ Runtime Snapshot å Evidence Event
25. å®æ Cycle
```

ä¸å¯è°æ´çé¡ºåºï¼

- Recovery å Exit æ°¸è¿åäºæ° Entryï¼
- æ²¡æå¥åº·å¯¹è´¦ä¸å¾è¿å¥æ­¥éª¤ 10â22ï¼
- ä¸è½ååæ¬å° Position åç­å¾ Binanceï¼
- ä¸è½åæ è®° Protection ACTIVE åç­å¾äº¤ææè®¢åï¼
- å¨ææ«å¿é¡»åæ¬¡å¯¹è´¦ï¼ä¸è½åªå¨å¨æå¼å§å¯¹è´¦ã

---

# 6. Entry è®¾è®¡

## 6.1 åçåªæ¯æ Market Entry

åå ï¼

- å½åé¦è¦ç®æ æ¯è¯æèªç¶é­ç¯ï¼ä¸æ¯ä¼åæåæäº¤ï¼
- éä»·åä¼å¼å¥ Pendingãè¿æãè¿½ä»·ãé¨åæäº¤åæ¤åç«æï¼
- åè®© Exchange-Firstãä¿æ¤åéåºç¨³å®ï¼ååç¬è®¾è®¡ Limit Entry V3ã

## 6.2 Client Order ID

è¦æ±ï¼

- ç¡®å®æ§ï¼
- åä¸ Intent éè¯ä½¿ç¨åä¸ä¸ª Client Order IDï¼
- é¿åº¦ç¬¦å Binance éå¶ï¼
- å¯ä»æ¬å°ååè§£æ Engine/Intent ç±»åï¼
- ä¸åå«ç­ç¥å¯é¥æææä¿¡æ¯ã

å»ºè®®æ ¼å¼ï¼

```text
A2E-{intent_hash_20}-{leg}
A2X-{intent_hash_20}-{leg}
A2S-{position_hash_18}
A2T-{position_hash_18}
```

æµè¯å¿é¡»éªè¯ï¼

- é¿åº¦ï¼
- å­ç¬¦éï¼
- åä¸ Intent ç¨³å®ï¼
- ä¸å Intent ä¸å²çªï¼
- Entry/Exit/Stop/Target ä¸å²çªã

## 6.3 æäº¤è¶æ¶è¯­ä¹

### æäº¤åå¤±è´¥

æ²¡æååºç½ç»è¯·æ±ï¼

```text
state = EXCHANGE_REJECTED
reason = PRE_SUBMIT_FAILURE
```

### ååºè¯·æ±åè¶æ¶

ä¸è½å¤æ­äº¤æææ¯å¦æ¥æ¶ï¼

```text
state = EXCHANGE_UNKNOWN
```

å¤çï¼

1. ç¦æ­¢ç¨æ° Client Order ID éè¯ï¼
2. æå Client Order ID æ¥è¯¢è®¢åï¼
3. æ¥è¯¢è¿æè®¢ååç¨æ·æäº¤ï¼
4. è¥æ¾å°è®¢åï¼æçå®ç¶ææ¢å¤ï¼
5. è¥è¿ç»­å®æ´å¿«ç§ç¡®è®¤ä¸å­å¨ï¼åæ è®° `NOT_FOUND_CONFIRMED`ï¼
6. åªææ­¤æ¶æè½ç± Recovery Service å³å®éæ°æäº¤ã

## 6.4 é¨åæäº¤

- æ¯æ¡äº¤ææ Trade åå¥ç¬ç« Fill Receiptï¼
- èåå½å filled quantity å weighted average priceï¼
- åªæå½±å·²ç¡®è®¤çæäº¤æ°éï¼
- åç Market Entry ç­å¾ç»ææç­è¶æ¶åå¤çï¼
- å·²ææäº¤ä½è®¢åæªç»ææ¶ï¼å¿é¡»å¯¹å·²æäº¤æ°éæä¾ä¿æ¤ï¼
- åç»­æ°å¢æäº¤åï¼Protection Service éæ°å¯¹é½ä¿æ¤è¦çï¼
- ä¸åè®¸æ¬å°æ requested quantity å»ºç«æ»¡é¢ä»ä½ã

---

# 7. Protection è®¾è®¡

## 7.1 ä¿æ¤ä»·æ ¼æ¥æº

ç­ç¥è¾åºï¼

```text
stop_distance
take_profit_distance
```

æ§è¡å±ä½¿ç¨ï¼

```text
average_fill_price
tick_size
position_side
```

çæç»å¯¹ä»·æ ¼ã

## 7.2 å ä½æ ¡éª

LONGï¼

```text
stop < average_fill_price < take_profit
```

SHORTï¼

```text
take_profit < average_fill_price < stop
```

ä»·æ ¼å¿é¡»æ tick size åé£é©æ´å®å¨æ¹ååæ´ã

## 7.3 ç¶æ

```text
PLANNED
SUBMITTING
ACKNOWLEDGED
ACTIVE
TRIGGERED
CANCELED
FAILED
UNKNOWN
```

`ACTIVE` å¿é¡»æ Binance Exchange Order IDã

## 7.4 ä¿æ¤å¤±è´¥åçº§

```text
ç¬¬ä¸æ¬¡æäº¤å¤±è´¥
â æ¥è¯¢çå®ä»ä½
â ç¨åä¸é»è¾èº«ä»½ãæ°çä¿æ¤å°è¯ç¼å·éè¯ä¸æ¬¡
â ä»å¤±è´¥åç«å³ Market ReduceOnly ç´§æ¥å¹³ä»
â åæ¬¡æ¥è¯¢çå®ä»ä½
â ä»æªå¹³å EMERGENCY_CLOSE_PENDING
â å¨è´¦æ· Entry Block
â é«ä¼åçº§åè­¦
```

ä»»ä½å¼å¸¸ä¸å¾ä½¿ç¨ `suppress(Exception)` éé»åæã

## 7.5 Stop/TP ç«æ

å½ä¸ä¸ªä¿æ¤åè§¦åæ¶ï¼

1. æ¥æ¶äº¤æææ´æ°ï¼
2. æ¥è¯¢çå®ä»ä½ï¼
3. è¥å·²å¹³ï¼åæ¶åå¼ä¿æ¤åï¼
4. è¥é¨åå¹³ï¼åªä¿çå©ä½æ°éå¯¹åºä¿æ¤ï¼
5. è¥åæ¶åå¼è®¢åæ¶åç°å®ä¹å·²è§¦åï¼åæ¬¡æ¥è¯¢çå®ä»ä½ï¼
6. æ¬å°ä»¥äº¤æææç»ä»ä½ä¸ºåï¼
7. ä¸å ä¸ºæ¬å°ååé¡ºåºäº§çååä»ä½ã

---

# 8. Exit è®¾è®¡

## 8.1 Entry Gate ä¸ Exit Gate å®å¨åç¦»

```python
validate_entry(...)
validate_reduce_risk_exit(...)
```

### Entry Gate æ£æ¥

- Engine Activeï¼
- Reconciliation Healthyï¼
- Manifest/OOSï¼
- æ°æ®é­ååæ°é²åº¦ï¼
- Candidate æææï¼
- Price Driftï¼
- Spreadï¼
- é£é©é¢ç®ï¼
- ä»ä½ä¸éï¼
- ç¸å³æ§ï¼
- Net Edgeï¼
- å¯é AI é£é©æ è®°ï¼
- Entry Kill Switchã

### Exit Gate åªæ£æ¥

- æå¨äº¤ææä»ä½å­å¨ï¼
- side ç¡®å®åå°ä»ä½ï¼
- `reduce_only=True`ï¼
- quantity å¤§äº 0ï¼
- quantity ä¸è¶è¿æå¨ä»ä½ï¼
- Client Order ID å¹ç­ï¼
- Fencing Token ææï¼
- Gateway å¯è°ç¨ã

ä»¥ä¸æ¡ä»¶ä¸å¾é»æ­¢ç¡¬éåºï¼

- Manifest å¤±æï¼
- AI ä¸å¯ç¨æå¦å³ï¼
- MetaLabel ä¸éè¿ï¼
- ä¿¡å· K çº¿è¿æï¼
- Entry Kill Switchï¼
- å Edge ä¸è¶³ï¼
- æ°é»é£é©äºä»¶ã

## 8.2 å¹³ä»æ°é

```python
close_qty = min(requested_qty, authoritative_position_qty)
close_qty = floor_to_step_size(close_qty)
```

ä¸å¾åä¸æ©å¤§ã

## 8.3 Already Flat

äº¤ææè¿å ReduceOnly already flat æ¶ï¼

1. æ¥è¯¢æå¨ä»ä½ï¼
2. è¥ç¡®å®ä¸º 0ï¼è§ä¸ºå¹ç­æåï¼
3. å³é­æ¬å° Positionï¼
4. åæ¶æ®ä½ä¿æ¤ï¼
5. è®°å½ `ALREADY_FLAT_RECONCILED`ï¼
6. ä¸å°å¶è®°ä¸ºæ®éå¤±è´¥ã

## 8.4 éåºç±»å

é¦çæ¯æï¼

- HARD_STOPï¼
- TAKE_PROFITï¼
- TIME_EXITï¼
- STRATEGY_INVALIDATIONï¼
- OPPOSITE_SIGNAL_CLOSEï¼
- PROTECTION_FAILURE_EMERGENCYï¼
- MANUAL_REDUCE_ONLYã

ç¦æ­¢åå¨æç´æ¥åæï¼

```text
åå®æ´å¹³æ§ä»
â å¨ææ«å¯¹è´¦
â ä¸ä¸é­åå³ç­ K çº¿æåè®¸æ°æ¹å Entry
```

---

# 9. Reconciliation ä¸ Recovery

## 9.1 å¯¹è´¦ç¶æ

```text
HEALTHY
DEGRADED
UNAVAILABLE
RECOVERY_REQUIRED
```

### HEALTHY

- å®æ´å¿«ç§ï¼
- æ¬å°/äº¤ææå¯è§£éä¸è´ï¼
- åè®¸ Entryã

### DEGRADED

- å¿«ç§å¯ç¨ï¼ä½å­å¨éå³é®ä¸ä¸è´ï¼
- é»è®¤é»æ­¢ç¸å³ symbol Entryï¼
- åè®¸ Exit åæ¢å¤ã

### UNAVAILABLE

- Gateway ç¼ºå¤±ï¼
- REST è¶æ¶ï¼
- å¿«ç§ä¸å®æ´ï¼
- è§£æå¼å¸¸ï¼
- è´¦æ·èº«ä»½ä¸ç¡®å®ã

å¨ä½ï¼

```text
é»æ­¢å¨é¨ Entry
ä¿ç Exit
è¿ç»­å¤±è´¥è§¦åè´¦æ·çº§ Entry Kill
```

### RECOVERY_REQUIRED

- å­å¨ UNKNOWN è®¢åï¼
- æ¬å° Managed Position æ²¡æä¿æ¤ï¼
- ä¿æ¤è®¢åä¸ä»ä½ä¸ä¸è´ï¼
- äº¤ææåºç°çä¼¼ V2 Client ID ä½æ¬å°æ²¡æè®°å½ã

## 9.2 ä»ä½å½å±

ä¼åçº§ï¼

1. Position Group IDï¼
2. Client Order IDï¼
3. Exchange Order IDï¼
4. Fill Trade IDï¼
5. æä¹åç­ç¥èº«ä»½ã

ç¦æ­¢åªç¨ï¼

- symbolï¼
- æ°éæ¥è¿ï¼
- ä»·æ ¼æ¥è¿ï¼
- æ¶é´æ¥è¿ã

æ æ³è¯ææ¶ï¼

```text
ownership_status = EXTERNAL_QUARANTINED
entry_blocked_symbols += symbol
```

ä¸å¾èªå¨å¹³ä»ï¼ä¹ä¸å¾èªå¨ç»§æ¿æ§ä¿æ¤ã

## 9.3 éå¯æ¢å¤

è¿ç¨å¯å¨åçç¬¬ä¸ä¸ª Cycleï¼

1. ç¦æ­¢ Entryï¼
2. æåå®æ´è´¦æ·å¿«ç§ï¼
3. æ¢å¤ææ V2 Client Order IDï¼
4. æ¢å¤ UNKNOWN Intentï¼
5. æ£æ¥ææ Managed Position çä¿æ¤ï¼
6. å¤ç Emergency Close Pendingï¼
7. å®æå¥åº·å¯¹è´¦åæè½è§£é¤ Entry Blockã

---

# 10. âä¸ç´ä¸å¼åâçè§£å³æ¹å¼

## 10.1 Decision Funnel

æ¯ä¸ª symbolãæ¯æ ¹é­åå³ç­ K çº¿å¿é¡»è®°å½ä»¥ä¸é¶æ®µï¼

```text
CYCLE_STARTED
DATA_AVAILABLE
CANDLE_CLOSED
DATA_FRESH
TIMEFRAMES_ALIGNED
REGIME_EVALUATED
ENTRY_SIGNAL_EVALUATED
CANDIDATE_CREATED
META_LABEL_EVALUATED
MANIFEST_EVALUATED
RECONCILIATION_HEALTHY
RISK_APPROVED
AI_REVIEWED
PRICE_DRIFT_APPROVED
INTENT_CREATED
EXCHANGE_SUBMITTED
EXCHANGE_FILLED
POSITION_PROJECTED
PROTECTION_CONFIRMED
```

æ¯ä¸ªé¶æ®µï¼

```text
PASSED
SKIPPED
REJECTED
ERROR
```

ç¨³å® Reason Code ç¤ºä¾ï¼

```text
NO_ENTRY_SIGNAL
FOUR_HOUR_DIRECTION_CONFLICT
ONE_HOUR_REGIME_RANGE
RSI_OUTSIDE_RANGE
MACD_DIRECTION_MISMATCH
CANDIDATE_EXPIRED
MANIFEST_NOT_ELIGIBLE
RECONCILIATION_UNAVAILABLE
UNMANAGED_EXTERNAL_POSITION
RISK_LIMIT_EXCEEDED
PRICE_DRIFT_EXCEEDED
AI_PROVIDER_UNAVAILABLE
EXCHANGE_REJECTED
PROTECTION_FAILED
```

## 10.2 Production ä¸ Sampling åç¦»

### Production Lane

- åªåè®¸å·²éè¿ç ç©¶æåçåéï¼
- é¢çä½å¯ä»¥æ¥åï¼
- ç»æè¿å¥ç­ç¥è¯æ®ã

### Testnet Sampling Lane

ç®çï¼

- èªç¶ãé«é¢å°æµè¯æ´ä¸ªæ§è¡é¾ï¼
- ä¸è¯æçå©ï¼
- ä¸åä¸ç­ç¥æåï¼
- ä½¿ç¨ç¸å Exchange-Firstãä¿æ¤ãå¯¹è´¦ãéåºé¾è·¯ã

åçè§åï¼

```text
åªç¨é­å 15m K çº¿

LONG:
close > EMA50
MACD histogram > 0
RSI â [50, 72]
ATR14 > 0

SHORT:
close < EMA50
MACD histogram < 0
RSI â [28, 50]
ATR14 > 0

stop_distance = max(1.2 Ã ATR14, fill_price Ã 0.0035)
take_profit_distance = 1.5 Ã stop_distance
```

é¢å¤éå¶ï¼

- BTC/ETHï¼
- æ¯ symbol æå¤ä¸ä»ï¼
- åºå®æå° Testnet åä¹éé¢ï¼
- æ¯ symbol å·å´ï¼
- æ¯æ¥æå¤§äº¤ææ°ï¼
- æ è®° `NON_PROMOTABLE_PIPELINE_SAMPLE`ï¼
- AI Provider æéä¸å¾é»æ­¢ Samplingï¼ä½å¿é¡»è®°å½ã

è¿æ¡ Lane è§£å³âé¿æ¶é´å®å¨ä¸å¼åæ æ³éªè¯é¾è·¯âï¼ä½ä¸ä¼æ±¡ææ­£å¼ç­ç¥ç»è®ºã

---

# 11. å®æ¶ä»·æ ¼ä¸ K çº¿ä¸è´æ§

## 11.1 ä¿¡å·æ°æ®

- ææ åªåºäºé­å K çº¿ï¼
- ä¿å­ candle close proofï¼
- ä¿å­ exchange event time å received timeï¼
- å¤å¨æå¿é¡»æåä¸å³ç­æ¶ç¹å¯¹é½ï¼
- ä¸åè®¸ä½¿ç¨æªæ¥ K çº¿ã

## 11.2 Pre-submit Snapshot

æäº¤åå¿é¡»è¯»åï¼

```text
Binance server time
best bid
best ask
mark price
last price
spread
market rules
decision candle close time
decision age
ATR
```

## 11.3 ä»·æ ¼æ¼ç§»

```text
drift_bps = abs(mark_price - signal_reference_price)
            / signal_reference_price
            Ã 10000
```

Sampling é»è®¤éå¼ï¼

```text
max(20 bps, 0.25 Ã ATR / signal_reference_price Ã 10000)
```

è¶éï¼

- ä¸è¿½ä»·ï¼
- è®°å½ `PRICE_DRIFT_EXCEEDED`ï¼
- ç­ä¸ä¸æ ¹é­å K çº¿éæ°å¤æ­ã

å°å¹æ¼ç§»ï¼

- åè®¸æäº¤ï¼
- SL/TP ä»æå®éæäº¤ä»·éç®ã

---

# 12. AI éæè¾¹ç

## 12.1 ä¸¤ç±»è°ç¨

### MARKET_REVIEW

å®æ¶è¿è¡ï¼å³ä½¿æ²¡æ Candidate ä¹æ§è¡ï¼ç¨äºï¼

- éªè¯ API çå®æ¥éï¼
- æ±æ» 4h/1h/15m ç¹å¾ï¼
- è¾åºå¸åºç¶æä¸é£é©æ ç­¾ï¼
- ä¿å­ Token ç¨éã

### TRADE_REVIEW

ä»å¨ç¡®å®æ§ Candidate å·²çæåè¿è¡ï¼

è¾å¥ï¼

- ç»æåå¸åºç¹å¾ï¼
- Candidateï¼
- å½åä»ä½ï¼
- Fundingãæ³¢å¨çãå¸åºé£é©ï¼
- ä¸åå« API Keyã

è¾åºåºå® Schemaï¼

```json
{
  "bias": "support|neutral|oppose",
  "confidence": 0.0,
  "risk_flags": [],
  "summary": ""
}
```

## 12.2 æé

åç AI ä» Advisoryï¼

- ä¸åå»º Candidateï¼
- ä¸ä¿®æ¹ quantityï¼
- ä¸ä¿®æ¹ leverageï¼
- ä¸å¡«åç»å¯¹ SL/TPï¼
- ä¸é»æ­¢ç¡¬éåºï¼
- Sampling ä¸­ Provider å¤±è´¥æ¶ç»§ç»­ç¡®å®æ§æ§è¡ï¼
- Production ä¸­æ¯å¦åè®¸ AI å½±å Entryï¼å¿é¡»ç±ç¬ç«éç½®åæµè¯æ§å¶ã

## 12.3 å¯è§å¯æ§

æ¯æ¬¡å¨æé½å¿é¡»æ LLM è®°å½ï¼

- å·²è°ç¨ï¼
- ææªè°ç¨ååå ï¼
- Providerï¼
- Modelï¼
- Tokensï¼
- Latencyï¼
- Errorï¼
- Request/Response Hashã

API ç¨éä¸º 0 æ¶ï¼åç«¯å¿é¡»è½æç¡®æ¾ç¤ºï¼

```text
API_KEY_MISSING
NO_CANDIDATE
MARKET_REVIEW_DISABLED
PROVIDER_ERROR
RATE_LIMITED
```

---

# 13. Runtime Truth API

## 13.1 æ° Router

åå»ºï¼

```text
apps/api/routers/automated_trading.py
```

åç¼ï¼

```text
/api/v2/automated-trading
```

## 13.2 ç«¯ç¹

```text
GET  /runtime
GET  /cycles
GET  /decisions
GET  /orders
GET  /positions
GET  /protections
GET  /reconciliation
GET  /incidents
GET  /llm-invocations
GET  /evidence/latest
POST /controls/entry-disable
POST /controls/entry-enable
```

ä¸æä¾ UI ä¸­éæåæ¢ Local Paper/Testnet Mirror çå¼å³ã

Engine Activation çä¿®æ¹å¿é¡»ï¼

- éè¦ç®¡çåè®¤è¯ï¼
- åå¥å®¡è®¡ï¼
- æ ¡éªå¯ä¸åå¥èï¼
- ACTIVE åéªè¯ Testnetãå®å¨è¾¹çãSchedulerãå¯¹è´¦ã

## 13.3 `/runtime` è¿å

```json
{
  "engine": {
    "engine_id": "automated-trading-v2",
    "mode": "BINANCE_TESTNET",
    "activation": "ACTIVE",
    "entry_enabled": true,
    "mainnet_supported": false
  },
  "scheduler": {},
  "market_data": {},
  "exchange": {
    "source": "BINANCE_TESTNET",
    "timestamp": "...",
    "freshness": "FRESH",
    "positions": [],
    "open_orders": []
  },
  "local_projection": {
    "source": "V2_LOCAL_PROJECTION",
    "timestamp": "...",
    "positions": []
  },
  "reconciliation": {
    "status": "HEALTHY",
    "mismatches": []
  },
  "latest_decisions": [],
  "latest_incidents": [],
  "latest_llm_invocation": null
}
```

ææå­æ®µå¿é¡»æºå¸¦ï¼

- sourceï¼
- observed_atï¼
- freshnessï¼
- availabilityã

---

# 14. åç«¯æ¹é 

## 14.1 åæ­¢å¤æ¥å£çæµ Runtime

å½å `useConsoleData.js` ä¼ï¼

- æåå¤ä¸ª APIï¼
- ä» PaperRun åè¡¨æåå­ãåéæ°éææåä¸ä¸ªè¿è¡çé autoRunï¼
- æ··å Binance AccountãPaper Decision TraceãLocal Overviewï¼
- æ¥å£å¤±è´¥æ¶ä¿çæ§å¼ã

V2 æ¹ä¸ºï¼

```text
ä¸ä¸ª Runtime Snapshot
+ æç¡®çåé¡µæç»ç«¯ç¹
+ SSE/WebSocket å¢éäºä»¶
```

åå»ºï¼

```text
frontend/admin/src/hooks/useAutomatedTradingRuntime.js
frontend/admin/src/api/automatedTrading.js
frontend/admin/src/components/AutomatedTrading/
```

## 14.2 é¡µé¢

### Runtime Overview

æ¾ç¤ºï¼

- Engine Mode/Activationï¼
- Schedulerï¼
- å¯¹è´¦ç¶æï¼
- Binance Testnet è¿æ¥ï¼
- ææ° Cycleï¼
- Entry æ¯å¦è¢«é»æ­¢ï¼
- Mainnet ä¸æ¯æã

### Why No Trade

æ¾ç¤ºæ¯ä¸ªæ çæè¿ä¸æ¬¡ï¼

- å³ç­ K çº¿æ¶é´ï¼
- ç»æ­¢é¶æ®µï¼
- Reason Codeï¼
- ææ å¼ï¼
- Gate ç»æï¼
- æ¯å¦è°ç¨ AIï¼
- æ¯å¦è¿å¥äº¤ææã

### Exchange vs Local

å·¦å³åæ ï¼

- Binance çå®ä»ä½ï¼
- V2 æ¬å°æå½±ï¼
- å·®å¼ï¼
- å½å±ï¼
- æåå¯¹è´¦æ¶é´ã

### Orders

æ¸æ¥åºåï¼

```text
Intent
Exchange Order
Fill
Protection
Exit
```

ä¸åææ¬å° accepted æ¾ç¤ºæäº¤ææè®¢åã

### AI Calls

æ¾ç¤ºï¼

- Providerï¼
- Modelï¼
- Called/Skippedï¼
- Tokensï¼
- Errorï¼
- æè¿è°ç¨æ¶é´ã

## 14.3 ç¦æ­¢è¡ä¸º

- ä¸ä½¿ç¨ `?? 0` è¡¨ç¤ºæªç¥ä½é¢ãä»·æ ¼ãPnLï¼
- ä¸æ¾ç¤ºå Onlineï¼
- ä¸æ Local Paper ä»ä½æ··å¥ Testnet Positionsï¼
- ä¸æ Acceptance å¾è¿åæ¾ç¤ºä¸ºç­ç¥äº¤æï¼
- ä¸ä¿çâTestnet éåå¼å³âï¼
- ä¸ä½¿ç¨ `paper_run_id` çæµå½åçå®è¿è¡å¼æï¼
- API ä¸å¯ç¨æ¾ç¤ºâæªæ¥é/æ°æ®ä¸å¯ç¨/æåæåæ¶é´âã

---

# 15. è¿ç§»ä¸åæ¢ç­ç¥

## 15.1 Engine Selector

æ°å¢ç¯å¢éç½®ï¼

```text
AUTOMATED_TRADING_ENGINE=legacy|v2_shadow|v2_active
```

è§åï¼

- `legacy`ï¼æ§ç³»ç»ç»´æç°ç¶ï¼
- `v2_shadow`ï¼V2 ä¸åéè®¢åï¼
- `v2_active`ï¼V2 å¯ä¸åè®¸ Testnet åå¥ï¼
- ä»»ä½æ¨¡å¼ä¸ä¸å¾åºç°ä¸¤ä¸ªè®¢ååå¥èã

## 15.2 Legacy Freeze Test

æ°å¢æ¶ææµè¯ï¼

```text
test_legacy_execution_files_receive_no_new_business_dependencies
test_v2_does_not_import_paper_cycle_orchestrator
test_v2_does_not_import_paper_order_lifecycle
test_only_one_testnet_order_writer_is_active
```

## 15.3 åæ¢åå¤ç

1. åæ­¢æ§ Scheduler Entryï¼
2. ä¿çæ§ ReduceOnly å®å¨éåºç´å°ä»ä½å½é¶ï¼
3. æ¥è¯¢ Binance çå®ä»ä½ãè®¢ååæäº¤ï¼
4. åæ¶æ æ³å½å±çæ§ç­ç¥ä¿æ¤è®¢åï¼
5. ææå¤é¨ä»ä½è¿å¥ Quarantineï¼
6. ç¡®è®¤äº¤æææ²¡ææ§ Managed Positionï¼
7. çæ Cutover Evidence Bundleï¼
8. è®¾ç½® `v2_active`ï¼
9. V2 å¯å¨åå Recovery/Reconciliationï¼
10. å¥åº·åæå¼å¯ Entryã

## 15.4 åæ»

åæ»ä¸æ¯éæ°æå¼ Legacy Writerã

åè®¸çåæ»å¨ä½ï¼

```text
v2 Entry Disabled
V2 Exit/Recovery ç»§ç»­è¿è¡
æ°è®¢ååæ­¢
å·²æä»ä½ç± V2 ç®¡çå°å³é­
```

åªæææ V2 ä»ä½å½é¶ãææä¿æ¤è®¢ååæ¶ãå®æ´å¯¹è´¦å¥åº·åï¼æåè®¸ç³»ç»å®å¨åæºã

---

# 16. æµè¯åå±

## 16.1 Unit

è¦çï¼

- ç¶ææºï¼
- Client Order IDï¼
- ä»·æ ¼æ¼ç§»ï¼
- æ°éåæ´ï¼
- æ­¢ææ­¢çå ä½ï¼
- Entry/Exit Gateï¼
- å½å±ï¼
- Reason Codeï¼
- AI Schemaã

## 16.2 Repository/DB

è¦çï¼

- å¯ä¸çº¦æï¼
- äºå¡ï¼
- å¹ç­ï¼
- Decimal ç²¾åº¦ï¼
- Managed Position æäº¤å­è¯çº¦æï¼
- Protection Active çº¦æï¼
- éå¯æ¢å¤ã

## 16.3 Strict Fake Exchange

Fake å¿é¡»æ¨¡æçå®è¯­ä¹ï¼

- ACKï¼
- Partial Fillï¼
- Filledï¼
- Rejectï¼
- Timeout before requestï¼
- Timeout after requestï¼
- Duplicate Eventï¼
- Out-of-order Eventï¼
- Protection failureï¼
- Already Flatï¼
- REST unavailableï¼
- User Stream disconnectã

è¯æ®å¿é¡»æç¡®ï¼

```text
scope = STRICT_FAKE
network_calls = 0
real_exchange_orders = 0
```

ä¸å¾åå½åæâçå®é¾è·¯å·²éè¿âã

## 16.4 Binance Testnet Contract

çå®ç½ç»æå¨æåæ§è¿è¡ï¼

- è´¦æ·æéï¼
- Server Timeï¼
- Market Rulesï¼
- Market Entryï¼
- Fill æ¥è¯¢ï¼
- Stop/TPï¼
- ReduceOnly Exitï¼
- è®¢ååæ¶ï¼
- REST/User Stream æ¢å¤ã

è¯æ®å¿é¡»åå«çå®ï¼

```text
exchange_order_id
trade_id
server_time
account_id_hash
```

## 16.5 Natural Scheduler E2E

ç¦æ­¢è°ç¨ Acceptance å¿«æ·èæ¬ã

å¿é¡»ç±æ®é Scheduler èªç¶å®æï¼

```text
Closed Candle
â Candidate
â Gate
â Real Testnet Entry
â Real Fill
â Local Projection
â Real Protection
â Natural Exit Trigger
â Real ReduceOnly Exit
â Final Reconciliation
```

## 16.6 Soak

è³å°éªè¯ï¼

- å¤ä¸ª Scheduler å¨æï¼
- éå¯ï¼
- æ å¹½çµä»ï¼
- æ éå¤ Entryï¼
- æ æªä¿æ¤ Managed Positionï¼
- æ æ°¸ä¹ UNKNOWNï¼
- æ  Exchange/Local æªè§£éå·®å¼ã

è®¡åä¸­ä¸ä»¥åºå®æ¶é¿ååè´¨éï¼å®ææ åä»¥äºä»¶æ°éãå¼å¸¸è¦çåæç»ä¸è´æ§ä¸ºåã

---

# 17. åé¶æ®µä»»å¡

## Task 0ï¼å»ç»åºçº¿åå¯ä¸è®¾è®¡æº

**Files**

- Create: `docs/architecture/automated-trading-v2.md`
- Create: `docs/adr/ADR-001-automated-trading-v2-single-writer.md`
- Create: `docs/adr/ADR-002-exchange-first-receipts.md`
- Create: `docs/adr/ADR-003-entry-exit-gate-separation.md`
- Modify: `AGENTS.md`
- Test: `tests/contracts/test_automated_trading_architecture.py`

**Interfaces**

- Produces: æ¬è®¡åä¸­çç®å½ãç¶ææºãæ¨¡å¼ãè¡¨ååæ¢ç­ç¥æä¸ºå¯ä¸æå¨è®¾è®¡ã
- Consumes: å·²éªè¯çäºä¸ªå¹½çµå/éåºåå½æµè¯ã

- [ ] å°å½åæäº¤ SHAãéç½®å¿«ç§ãæ°æ®åº schema revisionãå·²æå¤±è´¥è¯æ®å½æ¡£ã
- [ ] å¨ `AGENTS.md` åå¥ï¼æ§ `paper_*` æä»¶åè½å»ç»ï¼ç¦æ­¢æ°å¢æ§è¡é»è¾ã
- [ ] æ°å¢æ¶ææµè¯ï¼é»æ­¢ V2 å¯¼å¥æ§ Orchestrator/Lifecycleã
- [ ] è¿è¡ï¼

```bash
pytest tests/contracts/test_automated_trading_architecture.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "docs: freeze legacy trading pipeline and define v2 boundaries"
```

**Gate 0**

- è®¾è®¡æä»¶ä¸å­å¨ä»»ä½å¾å®å ä½åå®¹ï¼
- ç¶æåç§°åæ°æ®åºå­æ®µä¸è´ï¼
- ææåç»­ PR åªå¼ç¨è¿ä¸ä»½æ¹æ¡ï¼
- æ§å®å¨è¡¥ä¸æµè¯ä¿æç»¿è²ã

---

## Task 1ï¼å»ºç« V2 Immutable Contracts ä¸ç¶ææº

**Files**

- Create: `services/automated_trading/domain/enums.py`
- Create: `services/automated_trading/domain/commands.py`
- Create: `services/automated_trading/domain/events.py`
- Create: `services/automated_trading/domain/receipts.py`
- Create: `services/automated_trading/domain/state.py`
- Create: `services/automated_trading/domain/invariants.py`
- Test: `tests/services/test_automated_trading_state_machine.py`
- Test: `tests/contracts/test_automated_trading_contracts.py`

**Interfaces**

```python
reduce_execution_event(
    current: ExecutionAggregate,
    event: AutomatedTradingEvent,
) -> ExecutionAggregate

assert_managed_position_invariants(position, receipts) -> None
```

- [ ] ååç¶æè½¬æ¢è¡¨çåæ°åå¤±è´¥æµè¯ã
- [ ] éªè¯ `INTENT_CREATED â FILLED` å¿é¡»å¤±è´¥ã
- [ ] éªè¯æ²¡æ Fill Receipt æ¶ `POSITION_PROJECTED` å¿é¡»å¤±è´¥ã
- [ ] éªè¯æ  Exchange Order ID ç Protection ä¸è½ ACTIVEã
- [ ] å®ç°æå° reducerã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_state_machine.py \
       tests/contracts/test_automated_trading_contracts.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: add immutable automated trading v2 contracts"
```

**Gate 1**

- ç¶ææºæ²¡æè°ç¨æ°æ®åºæ Gatewayï¼
- ææéæ³è½¬æ¢è¢«æµè¯è¦çï¼
- ä¸å¤ç¨æ§å­ç¬¦ä¸² `accepted/filled` çå«æ··è¯­ä¹ã

---

## Task 2ï¼å»ºç« V2 æ°æ®åºå Repository

**Files**

- Create: `services/automated_trading/infrastructure/models.py`
- Create: `services/automated_trading/infrastructure/repository.py`
- Create: `migrations/versions/0013_automated_trading_v2.py`
- Modify: æ°æ®åºæ¨¡åæ³¨åå¥å£
- Modify: å½å schema revision å¸¸é
- Test: `tests/services/test_automated_trading_repository.py`
- Test: `tests/services/test_database_schema.py`

**Interfaces**

```python
class AutomatedTradingRepository:
    create_cycle(...)
    append_event(...)
    create_intent(...)
    save_order_receipt(...)
    save_fill_receipt(...)
    project_position(...)
    save_protection(...)
    record_reconciliation(...)
    record_incident(...)
```

- [ ] ååâæ  Fill Receipt ä¸è½æå½± Managed Positionâæ°æ®åºå¤±è´¥æµè¯ã
- [ ] å Client Order IDãExchange Order IDãTrade ID å¹ç­æµè¯ã
- [ ] ä½¿ç¨ `Numeric`ï¼ä¸å¾ç¨ Float å­å¨ V2 æ°éåä»·æ ¼ã
- [ ] å®ç°è¿ç§»ã
- [ ] éªè¯ SQLite æ°å»ºãåçº§åéå¤åçº§ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_repository.py \
       tests/services/test_database_schema.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: persist automated trading v2 execution facts"
```

**Gate 2**

- Migration revision åä»£ç  current revision ä¸è´ï¼
- ææå¯ä¸çº¦æææï¼
- æ§è¡¨æ²¡æè¢«ç ´åï¼
- æ§å¹½çµåä¸ä¼èªå¨å¯¼å¥ V2ã

---

## Task 3ï¼å»ºç«äºæ¥è¿è¡æ¨¡å¼åå¯ä¸åå¥è

**Files**

- Create: `services/automated_trading/infrastructure/runtime_lock.py`
- Modify: `services/execution/bootstrap.py`
- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Modify: éç½®æ¨¡å
- Test: `tests/services/test_automated_trading_engine_activation.py`

**Interfaces**

```python
resolve_engine_activation(settings) -> EngineActivationConfig
acquire_testnet_writer(engine_id, fencing_token) -> WriterLease
```

- [ ] åä¸¤ä¸ª Engine åæ¶ Active å¿é¡»å¤±è´¥çæµè¯ã
- [ ] å Local Paper ä¸åå§å Binance Adapter çæµè¯ã
- [ ] å Binance Testnet ä¸æ³¨å¥ Local Fill Adapter çæµè¯ã
- [ ] å é¤ V2 ä¸­ `mirror_to_gateway` è¯­ä¹ã
- [ ] ä¿ç Legacy éç½®è¯»åå¼å®¹ï¼ä½è½¬æ¢ä¸ºæç¡®åè­¦ï¼ä¸ä¼ å¥ V2ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_engine_activation.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: enforce a single automated trading order writer"
```

**Gate 3**

- Shadow æ°¸ä¸è°ç¨ submitï¼
- Active åªæä¸ä¸ª writerï¼
- Mainnet æ æ³éç½®ï¼
- Testnet æªæ­¦è£æ¶æ¾å¼ Blockï¼ä¸åé Local Fillã

---

## Task 4ï¼å»ºç« Binance Testnet Adapter

**Files**

- Create: `services/automated_trading/infrastructure/binance_adapter.py`
- Create: `services/automated_trading/infrastructure/market_snapshot_provider.py`
- Reuse through adapter: `services/execution/gateway.py`
- Test: `tests/services/test_automated_trading_binance_adapter.py`

**Interfaces**

```python
class BinanceTestnetAdapter:
    fetch_authoritative_snapshot() -> AuthoritativeAccountSnapshot
    fetch_market_snapshot(symbol) -> PreSubmitMarketSnapshot
    submit_market_order(command) -> ExchangeOrderReceipt
    query_order_by_client_id(client_order_id) -> ExchangeOrderReceipt | None
    fetch_fills(exchange_order_id) -> tuple[ExchangeFillReceipt, ...]
    submit_protection(command) -> ExchangeOrderReceipt
    cancel_order(exchange_order_id) -> ExchangeOrderReceipt
```

- [ ] åå `_UnavailableBinanceClient` å¿é¡»è¿åæç¡®ä¸å¯ç¨ç¶æçæµè¯ã
- [ ] ç¦æ­¢ Adapter è¿ååæ¬å° OrderExecutionã
- [ ] åå§ååºåªä½ä¸º hash/å®¡è®¡ä¿å­ï¼Application åªæ¶è´¹æ åååæ§ã
- [ ] æµè¯ Binance Symbolãprecisionãstepãtick è½¬æ¢ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_binance_adapter.py \
       tests/services/test_binance_gateway.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: add authoritative binance testnet adapter"
```

**Gate 4**

- Adapter ä¸åå»ºæ¬å° Positionï¼
- Adapter ä¸æ§è¡ç­ç¥ï¼
- Gateway ç¼ºå¤±æ¯æ¾å¼å¼å¸¸ï¼
- ææäº¤ææèº«ä»½å­æ®µå¯è¿½æº¯ã

---

## Task 5ï¼å»ºç« Reconciliation å Recovery

**Files**

- Create: `services/automated_trading/application/reconciliation_service.py`
- Create: `services/automated_trading/application/recovery_service.py`
- Test: `tests/services/test_automated_trading_reconciliation.py`
- Test: `tests/services/test_automated_trading_recovery.py`

**Interfaces**

```python
reconcile(snapshot, local_state) -> ReconciliationResult
recover_pending_state(snapshot, incidents) -> RecoveryResult
```

- [ ] å Gateway Timeout é»æ­¢å¨é¨ Entry ççº¢ç¯æµè¯ã
- [ ] å UNAVAILABLE ä»åè®¸ ReduceOnly çæµè¯ã
- [ ] å UNKNOWN æ Client Order ID æ¢å¤çæµè¯ã
- [ ] åå¤é¨ä»ä½è¿å¥ Quarantine çæµè¯ã
- [ ] åè¿ç¨éå¯æ¢å¤ä¿æ¤å Emergency Close çæµè¯ã
- [ ] å®ç° Cycle å¼å§åç»æä¸¤æ¬¡å¯¹è´¦ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_reconciliation.py \
       tests/services/test_automated_trading_recovery.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: add fail-closed reconciliation and recovery"
```

**Gate 5**

- ææä¸å¯ç¨è·¯å¾ Entry Blockï¼
- æ æ³å½å±çä»ä½ä¸è¢«èªå¨æ¥ç®¡ï¼
- UNKNOWN ä¸ä¼ç²ç®éå¤æäº¤ï¼
- éå¯åæ¢å¤åå¼ä»ã

---

## Task 6ï¼å»ºç« Decision Funnel å Candidate Contract

**Files**

- Create: `services/automated_trading/domain/candidates.py`
- Create: `services/automated_trading/application/decision_service.py`
- Create: `services/automated_trading/observability/decision_funnel.py`
- Adapt existing strategy functions; do not import old Orchestrator
- Test: `tests/services/test_automated_trading_decision_funnel.py`

**Interfaces**

```python
evaluate_symbol(context) -> DecisionOutcome
DecisionOutcome.candidate: TradeCandidate | None
DecisionOutcome.terminal_stage
DecisionOutcome.reason_code
```

- [ ] æ¯æ ¹é­å K çº¿é½å¿é¡»æç»æè®°å½ã
- [ ] éå¤ K çº¿è®°å½ `DUPLICATE_DECISION`ï¼ä¸è½éé»è¿åã
- [ ] æ ä¿¡å·ãRegime ä¸å¹éãMetaLabelãManifestãRisk åå«ä½¿ç¨ä¸å Reason Codeã
- [ ] Candidate åªè¾åºè·ç¦»ï¼ä¸è¾åºæç»ç»å¯¹ä¿æ¤ä»·æ ¼ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_decision_funnel.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: make every automated trading decision observable"
```

**Gate 6**

- ç¨æ·è½åç¡®åç­âä¸ºä»ä¹æ²¡å¼åâï¼
- Decision Service æ¯çº¯å³ç­ï¼ä¸å Exchange ç¶æï¼
- Candidate ä¸ Execution Intent åç¦»ã

---

## Task 7ï¼Entry Gate å Exchange-First Entry

**Files**

- Create: `services/automated_trading/application/entry_service.py`
- Create: `services/automated_trading/domain/commands.py`
- Reuse: `services/execution/order_normalizer.py` via explicit adapter
- Test: `tests/services/test_automated_trading_entry.py`

**Interfaces**

```python
evaluate_entry(candidate, runtime_context) -> EntryGateResult
execute_entry(candidate, gate_result, snapshot) -> EntryExecutionResult
```

- [ ] åæªå¥åº·å¯¹è´¦ä¸å¾ create intent çæµè¯ã
- [ ] åæäº¤å¤±è´¥ä¸åå»º Position çæµè¯ã
- [ ] åæäº¤åè¶æ¶è¿å¥ UNKNOWN çæµè¯ã
- [ ] åéå¤ Cycle ä¸éå¤ä¸åçæµè¯ã
- [ ] åé¨åæäº¤ä»æå½±æäº¤éçæµè¯ã
- [ ] åçå® Fill Receipt æè½æå½± Position çæµè¯ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_entry.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: implement exchange-first automated entries"
```

**Gate 7**

- æ äº¤æææäº¤ä¸å¯è½åºç° Managed Positionï¼
- æ¬å° Intent ä¸å±ç¤ºæ Exchange Orderï¼
- Timeout åä¸ä¼éå¤å¼ä»ï¼
- æäº¤ä»·æ¥èª Fillï¼ä¸æ¥èªæ§ K çº¿ closeã

---

## Task 8ï¼Protection Coordinator

**Files**

- Create: `services/automated_trading/application/protection_service.py`
- Test: `tests/services/test_automated_trading_protection.py`

**Interfaces**

```python
build_protection_plan(position, candidate, market_rules) -> ProtectionPlan
ensure_protection(position_group_id) -> ProtectionResult
```

- [ ] åå®é Fill Price éç®ä¿æ¤ä»·æ ¼çæµè¯ã
- [ ] å tick size å®å¨åæ´æµè¯ã
- [ ] åæ  Exchange Order ID ä¸å¾ ACTIVE çæµè¯ã
- [ ] åä¿æ¤æäº¤å¤±è´¥è§¦åç´§æ¥å¹³ä»çæµè¯ã
- [ ] åä¿æ¤åç´§æ¥å¹³ä»åå¤±è´¥æ¶å¨å± Entry Block çæµè¯ã
- [ ] å Stop/TP åæ¶ç«æçæµè¯ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_protection.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: protect every exchange-confirmed position"
```

**Gate 8**

- ä¸å­å¨âManaged ä¸æªä¿æ¤ä½ç³»ç»å¥åº·âçç¶æï¼
- ä¿æ¤å¼å¸¸å¨é¨æä¹åï¼
- å¤±è´¥åçº§å¯å¨éå¯åç»§ç»­ã

---

## Task 9ï¼ReduceOnly Exit Coordinator

**Files**

- Create: `services/automated_trading/application/exit_service.py`
- Test: `tests/services/test_automated_trading_exit.py`

**Interfaces**

```python
evaluate_exit(position, context) -> ExitDecision
execute_reduce_only_exit(decision, authoritative_position) -> ExitExecutionResult
```

- [ ] Entry Kill Switch ä¸é»æ­¢éåºã
- [ ] AI ä¸è¢«è°ç¨äºç¡¬éåºã
- [ ] Manifestãæ°æ®è¿æãNet Edge ä¸é»æ­¢éåºã
- [ ] quantity ä¸è¶è¿æå¨ä»ä½ã
- [ ] Already Flat è¢«å¯¹è´¦ä¸ºå¹ç­æåã
- [ ] Partial Exit åªæå½±ç¡®è®¤æ°éã
- [ ] å¹³ä»ååæ¶æ®ä½ä¿æ¤ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_exit.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: add fail-safe reduce-only automated exits"
```

**Gate 9**

- ææéä½é£é©è·¯å¾ç¬ç«äº Entry Gateï¼
- èªå¨å¹³ä»çå®ä½¿ç¨ ReduceOnlyï¼
- æ¬å° CLOSED åªåçå¨äº¤ææç¡®è®¤ä»ä½å½é¶åã

---

## Task 10ï¼V2 Cycle Service å Scheduler æ¥ç®¡

**Files**

- Create: `services/automated_trading/application/cycle_service.py`
- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Test: `tests/services/test_automated_trading_cycle.py`
- Test: `tests/services/test_automated_trading_scheduler.py`

**Interfaces**

```python
run_automated_trading_cycle(request) -> AutomatedTradingCycleResult
```

- [ ] ææ¬è®¡åç¬¬ 5 èåºå®é¡ºåºåéææµè¯ã
- [ ] éªè¯ Recovery/Exit åäº Entryã
- [ ] éªè¯å¼å§åç»æä¸¤æ¬¡å¯¹è´¦ã
- [ ] éªè¯ fencing token è¿ææ¶ä¸æäº¤ã
- [ ] éªè¯ä¸¤ä¸ª Scheduler å®ä¾åªæä¸ä¸ªè½åè®¢åã
- [ ] éªè¯å¼å¸¸å Cycle æç»æï¼ä¸ç `running`ã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_cycle.py \
       tests/services/test_automated_trading_scheduler.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: orchestrate automated trading v2 cycles"
```

**Gate 10**

- V2 å¯ä»¥å¨ Strict Fake ä¸å®æèªç¶ EntryâProtectionâExitï¼
- ææå¨ä½å¯å³èå°åä¸ Cycle/Decision/Intent/Position Groupï¼
- æ§ Orchestrator ä¸åä¸ã

---

## Task 11ï¼Testnet Sampling Lane

**Files**

- Create: `services/automated_trading/application/sampling_service.py`
- Create: `services/strategy_library/candidates/testnet_sampling_v2.py`
- Modify: Candidate Registry
- Test: `tests/services/test_testnet_sampling_v2.py`

**Interfaces**

```python
generate_sampling_candidate(closed_bars, cooldown_state) -> TradeCandidate | None
```

- [ ] ä½¿ç¨æ¬è®¡åç¬¬ 10.2 èçç¡®å®æ§è§åã
- [ ] åªä½¿ç¨é­å K çº¿ã
- [ ] Candidate å¼ºå¶ `non_promotable=True`ã
- [ ] éå¶æ¯æ¥æ¬¡æ°ãå·å´åå symbol ä¸ä»ã
- [ ] Sampling ä»å¿é¡»ç»è¿ Exchange-FirstãProtectionãReconciliationã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_testnet_sampling_v2.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: add non-promotable testnet sampling lane"
```

**Gate 11**

- ç³»ç»è½äº§çè¶³å¤æµè¯æºä¼ï¼
- Sampling ä¸æ±¡æç­ç¥æåï¼
- ä¸éè¿æ¾å®½å®å¨é¨æ¢åå¼åé¢çã

---

## Task 12ï¼AI Review å Token å¯è§å¯æ§

**Files**

- Create: `services/automated_trading/application/ai_review_service.py`
- Modify: `services/agents/llm_runtime.py`
- Modify: `services/agents/llm_factory.py`
- Modify: `services/agents/service.py`
- Test: `tests/services/test_automated_trading_ai_review.py`
- Create: `scripts/smoke_automated_trading_llm.py`

**Interfaces**

```python
run_market_review(context) -> AIReviewResult
run_trade_review(candidate, context) -> AIReviewResult
```

- [ ] åæ²¡æ Candidate ä»è½è¿è¡ Market Review çæµè¯ã
- [ ] åæ¯æ¬¡è°ç¨æè·³è¿é½æ Invocation Record çæµè¯ã
- [ ] å Sampling Provider å¤±è´¥ä»ç»§ç»­ç¡®å®æ§æµç¨çæµè¯ã
- [ ] å Forced Exit ä»ä¸è°ç¨ AI çæµè¯ã
- [ ] å Token ç¨éæä¹åæµè¯ã
- [ ] Smoke èæ¬åªè°ç¨ LLMï¼ä¸åéäº¤æè®¢åã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_ai_review.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: make automated trading ai reviews observable"
```

**Gate 12**

- API ç¨éä¸åé çï¼
- AI æéæ²¡ææ©å¼ å°è®¢åæ°å¼ï¼
- AI æéä¸ä¼å¶é å¹½çµåæé»æ­¢ç¡¬éåºã

---

## Task 13ï¼Runtime Truth API

**Files**

- Create: `apps/api/routers/automated_trading.py`
- Create: `shared/models/automated_trading.py`
- Modify: `shared/models/__init__.py`
- Modify: `apps/api/main.py`
- Test: `tests/api/test_automated_trading_runtime_api.py`

- [ ] å `/runtime` ä¸è¿åå ä½å¼çæµè¯ã
- [ ] å Exchange å Local Projection åå¼è¿åçæµè¯ã
- [ ] å unavailable ä½¿ç¨ null åç¶æï¼èä¸æ¯ 0 çæµè¯ã
- [ ] å Decision ReasonãIncidentãLLM Token è¿åæµè¯ã
- [ ] åæ§å¶ç«¯ç¹è®¤è¯åå®¡è®¡æµè¯ã
- [ ] è¿è¡ï¼

```bash
pytest tests/api/test_automated_trading_runtime_api.py -v
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: expose a single automated trading runtime truth api"
```

**Gate 13**

- åç«¯ä¸åéè¦ç PaperRunï¼
- ä¸ä¸ª API è½è§£éçå®è´¦æ·ãæå½±åå·®å¼ï¼
- Legacy API æç¡®æ è®° deprecatedã

---

## Task 14ï¼åç«¯æ¿æ¢å ä½ä¸æ··åç¶æ

**Files**

- Create: `frontend/admin/src/api/automatedTrading.js`
- Create: `frontend/admin/src/hooks/useAutomatedTradingRuntime.js`
- Create: `frontend/admin/src/components/AutomatedTrading/*`
- Modify: `frontend/admin/src/pages/PaperConsole.jsx`
- Modify: `frontend/admin/src/hooks/useConsoleData.js`
- Modify: `frontend/admin/src/components/RuntimePanels.jsx`
- Modify: `frontend/admin/src/components/TradingConsolePanels.jsx`
- Test: å¯¹åº Vitest æä»¶

- [ ] å é¤éè¿ PaperRun åç§°çå½å Auto Run çé»è¾ã
- [ ] å é¤ Testnet Mirror Toggleã
- [ ] Positions é»è®¤æ¾ç¤º Binance Truthï¼Local Projection åç¬æ¾ç¤ºã
- [ ] æªæ¥éæ¾ç¤ºâæªæ¥éâï¼ä¸æ¾ç¤º 0ã
- [ ] Why No Trade æ¾ç¤ºé¶æ®µå Reason Codeã
- [ ] AI é¡µé¢æ¾ç¤º Tokens åè·³è¿åå ã
- [ ] Acceptance åæ è®°ä¸ºåºç¡è®¾æ½è®¢åï¼ä¸æ··å¥ç­ç¥äº¤æã
- [ ] è¿è¡ï¼

```bash
cd frontend/admin
npm test -- --run
npm run build
```

- [ ] æäº¤ï¼

```bash
git commit -m "feat: render automated trading runtime truth in console"
```

**Gate 14**

- é¡µé¢ææè¿è¡å¼é½æ source/time/freshnessï¼
- æ  Fake OnlineãMock BalanceãGhost Positionï¼
- API æ­çº¿ä¸ä¼ä¿çæ§ç¶æååå®æ¶ç¶æã

---

## Task 15ï¼Shadow è¿è¡

**Files**

- Create: `scripts/run_automated_trading_shadow.py`
- Create: `scripts/audit_automated_trading_shadow.py`
- Test: `tests/services/test_automated_trading_shadow.py`

Shadow å¿é¡»ï¼

- ä½¿ç¨çå®å¸åºæ°æ®ï¼
- ä½¿ç¨çå® Binance è´¦æ·åªè¯»å¿«ç§ï¼
- ä¸æäº¤è®¢åï¼
- çæ CandidateãGateãNormalized Orderãä¿æ¤è®¡åï¼
- ä¸æ§ç³»ç»å³ç­å¹¶è¡æ¯è¾ï¼ä½ä¸è¦æ±ç»æç¸åã

- [ ] è¯æ `network_order_submit_calls == 0`ã
- [ ] ç»è®¡æ¯å±æ¼æéè¿çã
- [ ] éªè¯ä»·æ ¼æ¼ç§»åä¿æ¤å ä½ã
- [ ] éªè¯æ§ç³»ç»å V2 çå·®å¼å¯è§£éã
- [ ] è¿è¡ï¼

```bash
pytest tests/services/test_automated_trading_shadow.py -v
python -m scripts.audit_automated_trading_shadow
```

- [ ] æäº¤ï¼

```bash
git commit -m "test: validate automated trading v2 in shadow mode"
```

**Gate 15**

- Shadow æ è®¢åï¼
- æ¯æ ¹ K çº¿å¯è§£éï¼
- æ²¡ææªå¤çå¼å¸¸ï¼
- Runtime API ä¸åç«¯å±ç¤ºä¸è´ã

---

## Task 16ï¼çå® Binance Testnet Contract éªæ¶

**Files**

- Create: `scripts/verify_automated_trading_testnet_contract.py`
- Create: `tests/integration/test_automated_trading_testnet_contract.py`
- Create: Evidence Schema

æµè¯åå®¹ï¼

1. Preflightï¼
2. Server Timeï¼
3. Market Rulesï¼
4. æå° Market Entryï¼
5. Order IDï¼
6. Trade IDï¼
7. Fill Receiptï¼
8. Stop/TPï¼
9. ReduceOnly Exitï¼
10. æç»ä»ä½åè®¢åå½é¶ã

æç¡®æ è®°ï¼

```text
proof_type = TESTNET_CONTRACT
natural_strategy = false
```

- [ ] é»è®¤ CI è·³è¿çå®ç½ç»æµè¯ã
- [ ] éè¦æ¾å¼ææå Testnet Credentialsã
- [ ] Evidence ä¸ä¿å­å¯é¥ã
- [ ] å¤±è´¥æ¶æ§è¡è¡¥å¿æ¸çã
- [ ] è¿è¡ï¼

```bash
pytest -m testnet_contract tests/integration/test_automated_trading_testnet_contract.py -v
```

**Gate 16**

- çå® Order IDãTrade IDï¼
- æ¬å°ä» Fill Receipt æå½±ï¼
- çå®ä¿æ¤å ReduceOnlyï¼
- æç»å½é¶ï¼
- ä»ä¸è½å£°ç§°èªç¶ç­ç¥å·²æéã

---

## Task 17ï¼èªç¶ Scheduler E2E

**Files**

- Create: `scripts/verify_natural_automated_trading_cycle.py`
- Create: `tests/integration/test_natural_automated_trading_cycle_contract.py`

å¿é¡»ä½¿ç¨ï¼

- æ®é Schedulerï¼
- Testnet Sampling æ Production Candidateï¼
- æ­£å¸¸ Cycleï¼
- æ­£å¸¸ä¿æ¤æéåºæ¡ä»¶ã

ç¦æ­¢ï¼

- Acceptance Serviceï¼
- æå·¥å¼ä»ï¼
- Synthetic Local Fillï¼
- ç´æ¥è°ç¨ Entry/Exit Service ç»è¿ Schedulerï¼
- å¼ºå¶ä¿®æ¹æ°æ®åºç¶æè§¦åå¹³ä»ã

Evidence å¿é¡»è¯æï¼

```text
cycle_id
decision_id
candidate_id
intent_id
entry exchange_order_id
entry trade_ids
position_group_id
stop/tp exchange_order_ids
exit trigger
exit exchange_order_id
exit trade_ids
final exchange position = 0
final local position = CLOSED
reconciliation = HEALTHY
```

**Gate 17**

åªæè¿ä¸ Gate éè¿ï¼æåè®¸å£°ç§°ï¼

> Binance Testnet èªç¶èªå¨å¼å¹³åé¾è·¯å·²æéã

---

## Task 18ï¼Cutover å Legacy Writer å é¤

**Files**

- Modify: `services/execution/scheduler.py`
- Modify: `services/execution/tasks.py`
- Modify: `apps/api/routers/runs.py`
- Modify: Legacy frontend controls
- Delete or disable old Testnet write call sites
- Test: `tests/contracts/test_single_testnet_writer_after_cutover.py`

- [ ] åæ­¢æ§ Entryã
- [ ] ç¡®è®¤æ§ä»ä½å½é¶æ Quarantineã
- [ ] ä¿å­ Cutover Evidenceã
- [ ] è®¾ç½® `v2_active`ã
- [ ] éªè¯æ§ Testnet submit call site ä¸å¯è¾¾ã
- [ ] ä¿çæ§æ°æ®è¯»åä¸æ®µè¿ç§»æã
- [ ] è¿è¡å®æ´æµè¯ãCIãåç«¯æå»ºå Hooksã
- [ ] æäº¤ï¼

```bash
git commit -m "refactor: cut over to automated trading v2 single writer"
```

**Gate 18**

- åªæ V2 è½æäº¤èªå¨ Testnet è®¢åï¼
- Legacy ä¸è½éè¿ APIãScheduler æéç½®éæ°æ­¦è£ï¼
- åæ»ä»å³é­ V2 Entryï¼
- æ²¡æååã

---

# 18. æéæ³¨å¥ç©éµ

å¿é¡»è¦çï¼

| åºæ¯ | Entry | Exit | æ¬å°ç¶æ |
|---|---|---|---|
| Gateway ç¼ºå¤± | å¨é»æ­¢ | è®°å½ä¸å¯ç¨å¹¶åè­¦ | æ å¹½çµå |
| æäº¤åè¶æ¶ | REJECTED | å¯éè¯ | æ è®¢ååæ§ |
| æäº¤åè¶æ¶ | UNKNOWN | Recovery æ¥è¯¢ | ä¸éå¤æäº¤ |
| é¨å Entry Fill | ä»æå½±æäº¤é | ä¿æ¤æäº¤é | ä¸æè¯·æ±éå»ºä» |
| User Stream æ­çº¿ | REST å¯¹è´¦ | REST å¯¹è´¦ | Entry Block ç´å°å¥åº· |
| REST å¯¹è´¦å¤±è´¥ | å¨é»æ­¢ | ä¿çéé£é© | UNAVAILABLE |
| ä¿æ¤æäº¤å¤±è´¥ | ä¸åå¼æ°ä» | ç´§æ¥ ReduceOnly | Incident æä¹å |
| ç´§æ¥å¹³ä»å¤±è´¥ | å¨è´¦æ· Entry Block | éè¯/äººå·¥åè­¦ | EMERGENCY_CLOSE_PENDING |
| Stop å·²è§¦åãåæ¶å¤±è´¥ | æ¥è¯¢ä»ä½ | åªå¤çå©ä½é | ä¸ååå¼ä» |
| ä¸¤ Scheduler | ä¸ä¸ªåå¾ Fencing | ä¸ä¸ªæç» | æ éå¤è®¢å |
| è¿ç¨å¨ ACK åå´©æº | Recovery æ Client ID | æ­£å¸¸æ¢å¤ | ä¸éå¤ Entry |
| è¿ç¨å¨ Fill åæå½±åå´©æº | Recovery ä» Trade æ¢å¤ | å»ºä»å¹¶ä¿æ¤ | æ ä¸¢å¤±ä»ä½ |
| æ¬å°æ°æ®åºæå | ç¦æ­¢ Entry | äº¤ææå¿«ç§æ¢å¤/äººå·¥éç¦» | ä¸çå½å± |
| å¤é¨äººå·¥ä»ä½ | é»æ­¢å symbol Entry | ä¸èªå¨å¹³ä» | EXTERNAL_QUARANTINED |
| AI Provider å¤±è´¥ | Sampling å¯ç»§ç»­ | ä¸å½±å Exit | Invocation ERROR |
| ä»·æ ¼æ¼ç§»è¶é | SKIPPED | ä¸éç¨ | æç¡® Reason |
| Protection æ¬å°æãäº¤æææ  | Entry Block | éå»ºæç´§æ¥å¹³ä» | ä¸æ å¥åº· |

---

# 19. å®æ´éªæ¶å½ä»¤

æ¯ä¸é¶æ®µé½å¿é¡»ä¿å­åå§è¾åºã

```bash
pytest tests/contracts/test_automated_trading_architecture.py -v
pytest tests/contracts/test_automated_trading_contracts.py -v
pytest tests/services/test_automated_trading_state_machine.py -v
pytest tests/services/test_automated_trading_repository.py -v
pytest tests/services/test_automated_trading_engine_activation.py -v
pytest tests/services/test_automated_trading_binance_adapter.py -v
pytest tests/services/test_automated_trading_reconciliation.py -v
pytest tests/services/test_automated_trading_recovery.py -v
pytest tests/services/test_automated_trading_decision_funnel.py -v
pytest tests/services/test_automated_trading_entry.py -v
pytest tests/services/test_automated_trading_protection.py -v
pytest tests/services/test_automated_trading_exit.py -v
pytest tests/services/test_automated_trading_cycle.py -v
pytest tests/services/test_automated_trading_scheduler.py -v
pytest tests/services/test_testnet_sampling_v2.py -v
pytest tests/services/test_automated_trading_ai_review.py -v
pytest tests/api/test_automated_trading_runtime_api.py -v
pytest -m "not integration" -v
ruff check .
ruff format --check .
mypy apps services shared scripts tests
pip-audit
cd frontend/admin && npm test -- --run && npm run build
python .claude/hooks/selftest.py
python scripts/sync_skill_copies.py --check
python scripts/refresh_current_state.py --run --check
```

çå®ç½ç»éªæ¶åç¬æ§è¡ï¼

```bash
pytest -m testnet_contract \
  tests/integration/test_automated_trading_testnet_contract.py -v

python -m scripts.verify_natural_automated_trading_cycle
```

---

# 20. æç» Definition of Done

ä»¥ä¸å¨é¨æ»¡è¶³åï¼ä¸å¾å£°ç§°âæ¹å¥½äºâæâé¾è·¯å·²æéâã

## 20.1 ä»£ç 

- [ ] V2 ä¸å¯¼å¥æ§ Orchestrator/Lifecycleã
- [ ] Local Paper ä¸ Binance Testnet å®å¨äºæ¥ã
- [ ] æ²¡æéç¨å½æ°è½å¨ Testnet æ åæ§æ¶ç´æ¥ fillã
- [ ] Entry å Exit Gate åç¦»ã
- [ ] å¯¹è´¦å¤±è´¥ Entry fail-closedã
- [ ] æ éé»ä¿æ¤å¼å¸¸ã
- [ ] å¯ä¸ Testnet Writerã
- [ ] Mainnet ä¸å¯éç½®ã

## 20.2 æ°æ®

- [ ] æ¯ä¸ª Managed Position æ Fill Receiptã
- [ ] æ¯ä¸ª ACTIVE Protection æ Exchange Order IDã
- [ ] æ¯ä¸ª Cycle æç»æã
- [ ] æ¯æ ¹è¯ä¼° K çº¿æ Decision Funnel ç»æã
- [ ] æ¯æ¬¡ LLM è°ç¨/è·³è¿æè®°å½ã
- [ ] Exchange å Local å·®å¼å¯è§£éã

## 20.3 çå® Testnet

- [ ] æ®é Scheduler èªç¶äº§ç Entryã
- [ ] Binance è¿åçå® Order IDã
- [ ] Binance è¿åçå® Trade IDã
- [ ] æ¬å°ä»çå® Fill æå½±ã
- [ ] Binance ä¿æ¤åçå®å­å¨ã
- [ ] æ­£å¸¸éåºæ¡ä»¶è§¦åã
- [ ] ReduceOnly çå®æäº¤ã
- [ ] æç» Binance Position ä¸º 0ã
- [ ] æç»æ¬å° Position ä¸º CLOSEDã
- [ ] Reconciliation HEALTHYã
- [ ] æ äººå·¥ Acceptance æ·å¾ã
- [ ] æ  Synthetic Local Fillã

## 20.4 åç«¯

- [ ] æ¾ç¤º Binance Truth å Local Projectionã
- [ ] å¹½çµå·®å¼æ¾ç¤ºçº¢è²åè­¦ã
- [ ] ä¸ºä»ä¹ä¸å¼åå¯è§ã
- [ ] AI Token ç¨éå¯è§ã
- [ ] æªæ¥éä¸æ¾ç¤º 0ã
- [ ] Acceptance ä¸ç­ç¥äº¤æåå¼ã
- [ ] ä¸å­å¨ Mirror Toggleã

---

# 21. é²è¿å·¥æ§è¡çºªå¾

1. æ¬è®¡åæ¯èªå¨å¼å¹³å V2 å¯ä¸ Source of Truthï¼æ§æ¢å¤è®¡ååªä½ä¸ºé®é¢è¯æ®ï¼ä¸å¾ä¸æ¬è®¡ååæ¶æ§è¡ã
2. æ¯ä¸ª Task ç¬ç« PR/Commitï¼ååå¤±è´¥æµè¯ï¼åå®ç°ã
3. æ¯ä¸ª Task å®æåå¿é¡»ç±å¦ä¸ä¸ä¸æè¿è¡ Spec Review å Code Quality Reviewã
4. ç¦æ­¢ä¸æ¬¡æäº¤åæ¶ä¿®æ¹ StrategyãExecutionãAI å Frontendã
5. ç¦æ­¢ä¸ºäºéè¿æµè¯ä¿®æ¹æµè¯ä¸­çæ­£ç¡®æ­è¨ã
6. ç¦æ­¢å¨æ§ `paper_*` æä»¶ä¸­âé¡ºæä¿®ä¸ä¸âæ°åè½ã
7. æ¯æ¬¡åç°æ°é®é¢åå½ç±»ï¼
   - å±äºå½å Taskï¼å¢å å¤±è´¥æµè¯åä¿®ï¼
   - å±äºåç»­ Taskï¼è®°å½å°å¯¹åº Taskï¼ä¸æåå®ç°ï¼
   - ä¸å¨æ¬è½®èå´ï¼è®°å½ä½ä¸æ©å±ã
8. åä¸åè®¾è¿ç»­ä¸¤æ¬¡å¤±è´¥ååæ­¢æè¡¥ä¸ï¼åå°ç¶ææºåæ¥å£è¾¹çåæã
9. ä¸ç¨æµè¯æ°éä½ä¸ºå®æè¯æï¼å¿é¡»æäº¤çå® Evidenceã
10. ä»»ä½å£°ç§°âçå®é¾è·¯å·²éâçæ¥åå¿é¡»èªå¨æ£æ¥ï¼

```text
network_calls > 0
real_exchange_orders > 0
entry_trade_ids not empty
exit_trade_ids not empty
final_exchange_position == 0
final_reconciliation == HEALTHY
proof_type == NATURAL_SCHEDULER_TESTNET
```

11. åæ»åªå³é­ Entryï¼ä¸åè®¸æ¢å¤æ§ååé¾è·¯ã
12. Cutover åä¸å é¤æ§æ°æ®ï¼Cutover åä¸åè®¸æ§ä»£ç éæ°è·å¾åæéã

---

# 22. å»ºè®®æ§è¡é¡ºåº

```text
Task 0â3ï¼éå®è¾¹çãç¶ææºãæ°æ®åºãå¯ä¸åå¥è
        â
Task 4â5ï¼äº¤ææ Adapterãå¯¹è´¦åæ¢å¤
        â
Task 6â10ï¼å³ç­ãEntryãProtectionãExitãCycle
        â
Task 11â12ï¼Sampling å AI
        â
Task 13â14ï¼API ååç«¯
        â
Task 15ï¼Shadow
        â
Task 16ï¼çå® Testnet Contract
        â
Task 17ï¼èªç¶ Scheduler E2E
        â
Task 18ï¼Cutover å Legacy Writer å é¤
```

ä¸¥æ ¼ç¦æ­¢è·³è¿ Task 0â10ï¼ç´æ¥å»âæé«å¼åé¢çâæâæ¥ AIâã

---

# 23. æç»æ¶æå¤æ­

æ¬æ¬¡å¤§æ¹çæ ¸å¿ä¸æ¯ææ§ç³»ç»æ¹å¾æ´å¤æï¼èæ¯å é¤åç§æ­§ä¹ï¼

1. **æ¨¡å¼æ­§ä¹**ï¼Local Paper å Binance Testnet ä¸åæ··åã
2. **ç¶ææ­§ä¹**ï¼IntentãACKãFillãPositionãProtection åèªæ¥ææç¡®ç¶æã
3. **çç¸æ­§ä¹**ï¼äº¤æææ¯å¯ä¸æ§è¡çç¸ï¼æ¬å°åªæ¯æå½±ã
4. **éªæ¶æ­§ä¹**ï¼FakeãAcceptanceãShadowãNatural Testnet ä½¿ç¨ä¸å Proof Typeï¼ä¸è½äºç¸ååã

æ§è¡å®æåï¼ç³»ç»åºå½åªæä¸æ¡èªå¨ Testnet é¾è·¯ï¼

```text
çå®é­åæ°æ®
â å¯è§£éåé
â å¯è§å¯ AI
â Entry Gate
â Binance Fill Receipt
â V2 Managed Position
â Binance Protection
â ReduceOnly Exit
â Binance/Local å¥åº·å¯¹è´¦
```

ä»»ä½æ æ³è¿å¥è¿æ¡é¾è·¯çè®¢åé½å¿é¡»åçå¨æç¡®çå¤±è´¥ç¶æï¼èä¸æ¯æä¸ºæ¬å°å¹½çµåã
