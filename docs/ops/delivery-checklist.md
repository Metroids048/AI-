# 前期准备交付清单

> 状态更新（2026-07-04）：
> 当前仓库状态统一为 `Phase 0 完成 + 第一批 P1 落地`。
> 已完成：统一领域模型代码、`/api/v1` 主接口、策略生命周期持久化、carry 回测应用服务、
> risk/review/agent/execution 首版持久化与 gatekeeper，以及单租户管理令牌鉴权、通知 dispatcher、前端 build 恢复、`compose-validate` 脚本化。
> 本轮 P0 补漏追加：运行数据库不得入仓、compose runtime 必须读 `.env`、用户可见 Markdown 不得使用本机绝对路径、非本地环境不得使用默认管理 token。
> 下一轮 P1 顺序：1. Celery Beat / 7x24 调度；2. 前端管理台补齐；3. B/C/D 级数据源接入。
> 仍未完成：完整 DSR 引擎、Prometheus/dashboard runtime、具备 Docker 主机上的 compose smoke、Email adapter 与 live/exchange 闭环。

## 治理与真源

- [x] 研究报告真源固化
- [x] AGENTS.md
- [x] 项目记忆体系

## 总设计

- [x] 平台总设计包
- [x] 附录 A/B/C

## 子设计

- [x] 领域与接口设计包
- [x] 数据与接入设计包
- [x] Agent 与任务编排设计包
- [x] 执行 / 风控 / 复盘设计包

## 产品与路线

- [x] 产品规格
- [x] 功能清单
- [x] 阶段路线图
- [x] 环境与配置规范

## 后续进入开发前必须具备

- [x] 领域模型代码
- [x] API schema（首版 `/api/v1` 已落地，后续继续扩展）
- [x] 最小 API 鉴权基线（单租户 `ADMIN_API_TOKEN`）
- [x] 数据接入抽象（Binance-first persisted seam）
- [x] Celery 任务图（首版队列入口已接通，仍待扩展更多任务）
- [x] 风险规则实现（首版 gatekeeper）
- [x] Review 回写实现
- [x] 通知 outbox + dispatcher（首批 `Telegram + Webhook`）
- [x] `frontend/admin` 可重复 build 校验
- [x] `compose-validate` 脚本与 CI 路径

## 进入下一轮开发前仍需补齐

- [ ] walk-forward / OOS / Deflated Sharpe / stress test 真正执行链
- [ ] Prometheus + Grafana dashboard + 告警
- [ ] 在具备 Docker 的主机或 CI 上完成 `docker-compose.test.yml` / `docker-compose.paper.yml` / `docker-compose.live.yml` runtime smoke
- [ ] Email adapter、真实 Telegram/Webhook 凭据演练与值班告警联调
