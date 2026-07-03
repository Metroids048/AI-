# v2.0 集成方案对账文档

## 文档定位

本文件把 **v2.0 集成方案开发任务书 PDF**（`AI_Quant_v2_集成方案开发任务书.pdf`）
并入仓库设计体系，作为**工程落地细化层**。

真源优先级（冲突时上者为准）：

1. 研究报告 `AI_Quant_Research_Platform_完整报告.docx`（主真源）
2. `AGENTS.md`
3. `docs/architecture/platform-master-design.md`（实施母文档）
4. 各子设计文档（domain / data / agent / execution）
5. **本文件 + v2.0 PDF**（工程落地细化）

> PDF 提供的是 TimescaleDB / Jesse / VectorBT / infra 布局 / docker-compose v2 /
> .env 全量 key 等**实现级**细节；它服从 docx 与母文档，不改变六层架构与领域模型。

## Phase-0 任务号 → 骨架文件映射

| PDF 任务 | 落地文件 |
|---|---|
| P0-01 TimescaleDB + init.sql | `docker-compose.yml`、`infra/timescale/init.sql` |
| P0-02 celery_worker/beat/flower | `docker-compose.yml`、`apps/api/celery_app.py` |
| P0-05 Strategy 模型 + Alembic | `services/strategy_library/models.py`、`migrations/**` |
| P0-06 .env 全量 key | `.env.example` |
| P0-07 freqtrade 服务 | `docker-compose.yml`、`infra/freqtrade/**` |
| P0-12 strategies CRUD | `apps/api/routers/strategies.py` |
| 数据契约 §1.3 | `shared/models/**` |

## 命名裁决（PDF ↔ domain-and-interfaces-design.md）

domain 文档定义的领域对象更丰富，与 PDF 简版契约存在漂移，统一裁决如下：

| 概念 | PDF | domain 文档 | 平台采用 |
|---|---|---|---|
| 回测结果 | `BacktestReport`（指标） | `BacktestRun`（生命周期对象，含 `metrics_summary`） | **两者并存**：`BacktestReport` = 引擎产出的指标载荷 = `BacktestRun.metrics_summary`；`BacktestRun` 仍是领域层生命周期对象 |
| 风险事件 | `RiskEvent`（level/source/...） | `RiskEvent`（event_type/severity/affected_scope/resolution_status） | **采用 domain 超集**；PDF `level` → `severity` |
| 风险约束 | （无） | `RiskProfile` | 保留为独立对象（执行层 Phase 2） |
| WQ 本地引用 | `WORLDQUANT_BRAIN_SESSION` | — | 改为 `WORLDQUANT_ALPHA_LOCAL_PATH`（移植方法论，不上传密钥） |

`shared.models.RiskEvent` 存储为 `risk_events` 表的子集投影（见下）。

## 存储归属契约

| 拥有者 | 表 |
|---|---|
| `infra/timescale/init.sql` | `ohlcv_bars`、`market_extras`、`risk_events`、`macro_events`（时序/事件） |
| Alembic（`migrations/`） | `strategies`（及后续关系表：versions/runs/...） |

**任何表都不得被两边同时创建。** Order Book 数据只进 Redis，永不落库。

## 框架隔离（原则一）

- Freqtrade / Jesse / VectorBT / CCXT 只出现在 `services/` 与 `infra/`。
- `apps/api/` 只认 `shared.models`（如 `BacktestReport`），不知道任何框架存在。
- Freqtrade 以独立 Docker 服务运行，平台经 REST `:8080` 通信，不在 Python 进程内 `import freqtrade`。

## Alpha 方法论决策

`Desktop/alpha`（美股 WorldQuant 挖掘流水线）**移植方法论而非搬运表达式**：
算子词表 + 因子构造模式 → BTC/USDT 加密因子。详见
`research_source/worldquant_adapter/README.md`。

## 已选工程默认（可逆）

uv.lock（保留 setuptools 构建后端）· TimescaleDB 钉 `2.17.2-pg16` ·
pandas-ta（TA-Lib 注释可选）· `postgresql+psycopg://` 驱动 ·
strategies CRUD 暂用内存 seam（P0-12 接 repository）。
