# Repository Map

审计截止：2026-07-23 15:47:58 Asia/Shanghai（账本截止对应 UTC 2026-07-23T07:47:58.116805）。HEAD=`ff542f8086deccea81d77e23991930d29364d7d3`，分支 `main`。`git ls-files` 记录 660 个已跟踪文件；用户计划中的 631 是此前基线分类口径，本次不把生成物或运行时文件伪装成源码文件。

## 目录与职责

| 区域 | 证据 | 关注面 |
|---|---|---|
| `apps/api` | FastAPI、Celery、桌面 local server、`routers/runs.py` | API、手动交易、自动周期触发 |
| `services/execution` | scheduler、paper runtime、gatekeeper、gateway、reconciliation、exit ladder | 真实交易链路与状态写入 |
| `services/strategy_library` | models、repositories、ensemble、strategy runner | 策略、Ensemble、decision/event 持久化 |
| `services/data`、`research_source` | candle/universe/外部信息源 | 行情输入与研究来源 |
| `shared` | settings、trading/workflow models | 配置和领域契约 |
| `tests` | 120 个已跟踪测试文件 | scheduler、decision、execution、paper、manual、exit、testnet mock |
| `scripts`、`infra`、`docker-compose*` | 启动、部署、验证、历史 archive | 多入口与运行模式 |
| `frontend/admin` | React/Vite/Vitest | 运维控制台；不是交易真源 |

## 重复实现与边界

当前存在 `services/strategy_library/ensemble/service.py.backup`、`scripts/archive/`、`docs/archive/` 等历史实现。它们被纳入审计搜索，但不作为当前事实。运行时同时存在桌面外部 scheduler、API in-process scheduler、Celery task 配置；compose 校验脚本要求 paper/live 使用 Celery 模式，桌面脚本则显式使用独立 local scheduler。

## 运行证据

`.local_paper_console.db` 通过只读 URI 与 `PRAGMA query_only=ON` 读取；`.local_runtime_ledger.db` 同样只读。未输出 `.env`、密钥、Cookie、完整账户响应或数据库副本。
