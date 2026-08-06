# 附录 A：项目结构与代码仓组织

## 目标

本附录定义平台在代码仓层面的组织方式，避免后续实现阶段出现随意堆积脚本或跨层污染。

## 顶层目录职责

- `apps/api`
  - FastAPI 入口、路由组织、编排入口、依赖注入、应用配置
- `frontend/admin`
  - 内部研究台管理后台
- `services/data`
  - 五级数据源接入、标准化、缓存、调度适配
- `services/strategy_library`
  - 策略定义、版本、来源、状态、失败记录
- `services/agents`
  - Strategy/Coding/Backtest/Optimization/Research/News/Twitter/Telegram/Risk/Review Agent
- `services/validation`
  - 回测、优化、样本外验证、模拟盘/实盘准入
- `services/execution`
  - Execution Engine、Risk Engine、订单与持仓执行协调
- `services/review`
  - 日报、诊断、知识沉淀回写
- `research_source/worldquant_adapter`
  - WorldQuant 相关接入，仅作研究来源
- `docs`
  - 总设计与子设计文档
- `tests`
  - 单元、接口、流程验证测试

## 仓内组织原则

- 禁止把主业务逻辑写在入口文件里
- 禁止跨层直接依赖不经过明确接口
- 禁止将临时研究脚本当作核心主流程
- 禁止把 WorldQuant 代码直接混入主研究链路

## 未来允许新增的非核心目录

- `scripts/`
  - 只放辅助脚本，不放平台核心逻辑
- `ops/`
  - 部署、监控、运行辅助配置
- `artifacts/`
  - 不纳入代码主干的输出工件
