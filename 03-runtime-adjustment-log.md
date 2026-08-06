# 03 Runtime Adjustment Log

日期：2026-08-03

## L1 基线重锚

- 冻结参考提交：`df1bc89`。
- 当前施工基线：`493c7c625acb85cf0352cd5b34bd9c86d2ff849e`（`main`，与 `origin/main` 一致）。
- `df1bc89..493c7c6` 包含仓库整理、归档、hook 和知识库同步等后继提交。
- 对 4 个 Active 生产文件执行限定 diff，退出码为 0：两提交间无内容差异。
- 对 launcher、runtime activation、scheduler job resolution 执行限定 diff，退出码为 0：标准 Active 链无差异。

因此按冻结方案将当前文件夹重锚为 L1 基线；没有 reset、clean、checkout、分支切换、commit 或 push。

## PRE-000

| 检查 | 事实 | 结果 |
| --- | --- | --- |
| launcher flag | `v2_shadow` | 通过 |
| V2 activation | `SHADOW` | 通过 |
| legacy writer | `allow_legacy_writer=True` | 通过 |
| scheduler jobs | legacy `paper_runtime_cycle` + `automated_trading_v2_cycle` | 通过 |
| runtime mismatch | 显式失败，不 remap | 通过 |
| pre-commit hook | 已安装 | 通过 |

## Runtime 参数

- 代码默认：`PRETRADE_MIN_PRICE_DRIFT_BPS=20.0`。
- 代码默认：`PRETRADE_ATR_DRIFT_FRACTION=0.25`。
- 当前进程环境未发现上述两个变量覆盖，也未发现 `AUTOMATED_TRADING_ENGINE`、`BINANCE_USE_TESTNET` 或 `LIVE_TRADING_ENABLED` 的进程级覆盖。
- 未修改 `.env`、运行数据库或 engine flag。

## 发布边界

若部署环境存在不符合冻结值的漂移变量覆盖，应阻止发布并修正部署配置，不能绕过代码默认值。本轮没有启动/重启真实 API 或 RuntimeScheduler，也没有调用 Binance；A-504 仍待另行授权。

项目知识同步配置仍指向不存在的旧目录 `C:/Users/win/Desktop/AI--main`，当前全局工作区也未找到 `同步项目知识.py`，因此中央同步未执行；本次事实已写入项目 task history 和本日志，未直接改写中央主知识库。
