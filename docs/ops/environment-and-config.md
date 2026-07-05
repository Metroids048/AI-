# 环境与配置规范

## 环境层级

- `dev`
- `test`
- `paper`
- `live`

## 配置分类

- 应用配置
- 数据源配置
- 交易所配置
- 风控参数配置
- Agent 配置
- 调度配置

## 配置原则

- 环境隔离
- 凭据不入库
- 风险参数可审计
- 研究 / 模拟盘 / 实盘逻辑隔离

## Binance 接入安全要求

- 不允许使用交易所登录密码作为系统集成凭据。
- 只能使用交易所官方创建的 API Key / Secret。
- 优先使用测试网或只读 Key，不要先连真实资金账户。
- `live` 环境默认禁止启用真实交易，除非运维明确打开 `LIVE_TRADING_ENABLED=true`。
- API Key 必须最小权限化：
  - 行情只读
  - 账户读取
  - 如需下单，仅在测试网或明确的实盘审批后开放交易权限
- 强烈建议开启：
  - 2FA
  - IP 白名单
  - 提现权限关闭

## 当前 Binance 相关环境变量

- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`
- `BINANCE_USE_TESTNET`
- `LIVE_TRADING_ENABLED`
- `DEFAULT_EXCHANGE`

默认建议：

```env
BINANCE_USE_TESTNET=true
LIVE_TRADING_ENABLED=false
DEFAULT_EXCHANGE=binance
```

## 当前项目中的 Binance 能力边界

- 已有：
  - Binance 公共行情 OHLCV / Funding 数据抓取与落库
  - Top20 成交额候选符号选择
  - 管理台 K 线展示
  - Paper 运行、持仓展示、订单展示
  - Binance USDT perpetual gateway 抽象与首版 CCXT 边界
- 仍需你自己提供安全凭据后才能真正在线使用：
  - 私有账户同步
  - 测试网下单 / 撤单
  - 真实账户连接

## 推荐接入顺序

1. 先改掉已泄露的 Binance 登录密码，并检查登录设备与 API 管理页。
2. 自己在 Binance 创建新的测试网 API Key / Secret。
3. 只把 `BINANCE_API_KEY` / `BINANCE_API_SECRET` 放进本地 `.env`，不要再发到聊天里。
4. 保持：

```env
BINANCE_USE_TESTNET=true
LIVE_TRADING_ENABLED=false
```

5. 先验证：
  - 行情抓取
  - 账户快照读取
  - 模拟盘 / 测试网下单闭环
6. 最后才考虑真实实盘权限。
