# 数据与接入设计包

## 文档定位

本文件定义平台五级数据源的统一接入框架、实时性策略、存储分层、调度方式与事件链路。

引用关系：

- 上游：
  - `AI_Quant_Research_Platform_完整报告.docx`
  - `AGENTS.md`
  - `platform-master-design.md`
  - `domain-and-interfaces-design.md`

本文件不解决字段级 schema，而是定义：

- 数据源如何分层接入
- 各数据源为哪些层服务
- 实时 / 半实时 / 离线的运行策略
- 哪些数据进入风险链路，哪些进入研究链路

---

## 01 数据源总览

### 1.1 五级数据源

平台统一支持：

- `A级市场数据`
- `B级宏观数据`
- `C级新闻数据`
- `D级社媒数据`
- `E级研究数据`

### 1.2 数据源设计原则

- 先定义统一接入框架，再逐步启用真实接入
- 先定义“数据为谁服务”，再定义“怎么抓”
- 不把所有数据都当作交易信号
- 研究源、风险源、执行源必须区分

---

## 02 各级数据源职责

### 2.1 A级市场数据

用途：

- 直接进入 Strategy Layer 和 Validation Layer
- 为 Execution Layer 提供实时行情基准

典型内容：

- OHLCV
- Volume
- ATR
- VWAP
- EMA
- Bollinger
- RSI
- MACD
- ADX
- Funding Rate
- Open Interest
- Long/Short Ratio
- Liquidation
- Order Book

实时性：

- 行情与资金费率：实时或准实时
- 技术指标：由基础行情派生计算
- 订单簿与清算：按接入能力决定高频或准实时

### 2.2 B级宏观数据

用途：

- 主要服务 Risk Engine
- 次要服务 Review Layer 与 Research Layer

典型内容：

- FOMC
- CPI
- PPI
- 非农
- 利率决议
- GDP
- PMI
- ETF 审批相关事件

实时性：

- 事件日历可离线同步
- 风险生效点在事件前后窗口准时触发

### 2.3 C级新闻数据

用途：

- 作为风险标签输入
- 辅助 Review 与 Research

典型内容：

- 金十
- Reuters / Bloomberg RSS
- CoinDesk / The Block / Decrypt
- A股新闻
- SEC Filing

实时性：

- 准实时

### 2.4 D级社媒数据

用途：

- 作为事件识别与风险提示输入

典型内容：

- 重点账号动态
- 关键主题关键词命中

实时性：

- 准实时

### 2.5 E级研究数据

用途：

- 进入 Research Layer
- 生成 `StrategyIdea`
- 沉淀研究来源索引

典型内容：

- GitHub 策略仓库
- 学术论文
- Reddit
- Telegram
- YouTube
- WorldQuant
- 现有 A 股系统

实时性：

- 主要为离线/批处理

---

## 03 数据服务边界

### 3.1 直接服务交易与验证的数据

- 只有 `A级市场数据` 可直接进入策略验证与执行链路

### 3.2 主要服务风险控制的数据

- `B/C/D级` 主要作为风险标签与风险开关来源

### 3.3 主要服务研究发现的数据

- `E级` 只进入研究发现链路，不直接影响执行

---

## 04 接入框架

### 4.1 接入接口族

平台需要统一抽象以下接口：

- `MarketDataFeed`
- `MacroCalendarFeed`
- `NewsFeed`
- `SocialSignalFeed`
- `ResearchSourceFeed`

### 4.2 接入流水线

统一流程建议：

1. `fetch`
2. `normalize`
3. `validate`
4. `store_raw`
5. `store_curated`
6. `emit_event`
7. `publish_reference`

### 4.3 IngestionJob 的作用

所有外部接入都通过 `IngestionJob` 描述：

- 接入哪类源
- 调度频率
- 输入时间窗
- 输出引用
- 作业状态

---

## 05 存储分层

### 5.1 PostgreSQL

存储：

- 策略库主数据
- 回测/模拟盘/实盘结果
- 风险事件事实记录
- 复盘与失败沉淀
- 数据接入任务记录
- 标准化后的引用数据索引

### 5.2 Redis

存储：

- 短期缓存
- 实时行情快照
- 风险开关短状态
- 任务协调临时状态

### 5.3 原始数据与工件

建议预留：

- 原始抓取副本
- 研究附件元数据
- 事件处理工件引用

这部分可先以文件/对象存储引用设计，具体落地留到实现阶段。

---

## 06 实时性策略

### 6.1 P0

- `A级`：真实接入
- `B/C/D/E`：框架定义完成，按阶段启用

### 6.2 P1

- `B级` 宏观事件风险开关上线
- `C/D级` 准实时标签链路上线
- `E级` 研究源批量同步上线

### 6.3 P2

- 强化多源融合与更复杂的事件驱动分析

---

## 07 调度策略

### 7.1 调度角色

- FastAPI：配置与查询入口
- Celery：抓取、标准化、分类、回写等异步任务
- Redis：任务协调和短期队列辅助
- PostgreSQL：最终事实状态

### 7.2 调度模式

- `realtime`
- `near_realtime`
- `scheduled_batch`
- `manual_backfill`

### 7.3 失败处理

每个 `IngestionJob` 都必须可区分：

- 抓取失败
- 标准化失败
- 校验失败
- 事件发布失败
- 输出写入失败

---

## 08 数据与风险链路

### 8.1 风险链路

- `B/C/D级` 数据 -> 分类/识别 -> `RiskEvent` -> `Risk Engine`

### 8.2 研究链路

- `E级` 数据 -> `Research Agent` -> `StrategyIdea`

### 8.3 市场链路

- `A级` 数据 -> 技术指标/派生数据 -> Validation / Execution / Review

---

## 09 数据质量与治理

必须定义的数据质量维度：

- 来源可追溯
- 时间戳标准统一
- 时区统一
- 缺失与延迟可见
- 原始记录与标准化结果可关联

---

## 10 下一步承接

下一步承接目标：

- 将接入接口固化为 Python 抽象
- 将 `IngestionJob`、风险链路、研究链路接入到任务编排设计包
- 为后续 API schema 预留对象边界
