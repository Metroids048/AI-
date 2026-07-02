# 前期准备交付清单

> 状态更新（2026-07-02）：
> 当前仓库已经完成 Phase 0 文档冻结后的第一批 P1 落地。
> 已完成：统一领域模型代码、`/api/v1` 主接口、策略生命周期持久化、carry 回测应用服务、
> risk/review/agent/execution 首版持久化与 gatekeeper。
> 仍未完成：walk-forward/DSR 引擎、完整前端联调、Prometheus/dashboard、`docker-compose.test|paper|live` overlays。

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
- [x] 数据接入抽象（Binance-first persisted seam）
- [x] Celery 任务图（首版队列入口已接通，仍待扩展更多任务）
- [x] 风险规则实现（首版 gatekeeper）
- [x] Review 回写实现

## 进入下一轮开发前仍需补齐

- [ ] walk-forward / OOS / Deflated Sharpe / stress test 真正执行链
- [ ] Frontend admin 与真实 API 联调
- [ ] Prometheus + Grafana dashboard + 告警
- [ ] `docker-compose.test.yml` / `docker-compose.paper.yml` / `docker-compose.live.yml`
