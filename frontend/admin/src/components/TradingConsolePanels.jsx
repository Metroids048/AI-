import { useEffect, useMemo, useState } from "react";

import { asArray, formatClock, formatNumber, formatPercent, formatTime } from "../utils/format";

export function ModeBanner({ status }) {
  const modeLabel = status?.mode === "testnet" ? "币安合约模拟盘" : "本地模拟盘";
  const testnetLabel = status?.binance_use_testnet ? "币安模拟盘已锁定" : "币安模拟盘未启用";
  const liveLabel = status?.live_trading_enabled ? "实盘开关开启" : "实盘关闭";
  const gatewayLabel = status?.gateway_available ? "网关可用" : "网关待配置";
  return (
    <section className="mode-banner">
      <div><span>当前模式</span><strong>{modeLabel}</strong></div>
      <div><span>环境</span><strong>{status?.app_env === "development" ? "开发环境" : status?.app_env ?? "加载中"}</strong></div>
      <div><span>安全边界</span><strong>{testnetLabel}</strong></div>
      <div><span>真实交易</span><strong>{liveLabel}</strong></div>
      <div><span>交易网关</span><strong>{gatewayLabel}</strong></div>
      <div className="mode-banner-note">
        币安模拟账户统一入口：
        <a href="https://demo.binance.com/en/futures/BTCUSDT" target="_blank" rel="noreferrer">demo.binance.com</a>
        （模拟盘链接会自动跳转到此）。须登录同一账号；订单以本平台「币安模拟账户 API 面板」为准。
      </div>
    </section>
  );
}

export function BinanceSyncHero({ account }) {
  if (!account) {
    return (
      <section className="binance-sync-hero loading">
        <strong>币安模拟账户探测已暂停</strong>
        <span>本地工作台不会自动重试受限交易所接口，验收操作会先执行一次显式预检。</span>
      </section>
    );
  }
  if (!account.connected) {
    const issue = connectionIssue(account.error);
    return (
      <section className="binance-sync-hero error">
        <strong>{issue.title}</strong>
        <span>{issue.detail}</span>
      </section>
    );
  }
  const latest = asArray(account.recent_orders)[0];
  const syncedLabel = account.synced_at ? formatTime(account.synced_at) : "刚刚";
  return (
    <section className="binance-sync-hero ok">
      <div className="binance-sync-hero-main">
        <strong>币安模拟盘 API 已连通 — 与自动下单同一账户</strong>
        <span>
          钱包 {formatNumber(account.wallet_balance, 2)} USDT · 可用 {formatNumber(account.available_balance, 2)} USDT ·
          持仓 {account.open_position_count ?? 0} · 后端 {account.api_backend ?? "模拟环境"} · 同步 {syncedLabel} ·
          {latest ? `最新 #${latest.order_id} ${sideLabel(latest.side)} ${orderStatusLabel(latest.status)}` : "暂无最近订单"}
        </span>
      </div>
      <p className="binance-sync-hero-note">
        自动交易开启镜像后会<strong>先向币安下单</strong>，本面板即你在币安的真实持仓和订单（API 真源）。
        网页受地区限制时需通过可用网络登录 demo.binance.com；网页无法登录不影响 API 对账。
      </p>
    </section>
  );
}

export function TestnetAccountPanel({ account }) {
  if (!account) {
    return (
      <section className="exchange-panel testnet-account-panel">
        <PanelTitle title="币安模拟账户" meta="按需探测" />
        <div className="empty-list">本地工作台已暂停自动账户轮询，避免在交易所限流时阻塞操作台。</div>
      </section>
    );
  }
  const positions = asArray(account.positions);
  const orders = asArray(account.recent_orders);
  const modeLabel = "币安模拟交易";
  return (
    <section className="exchange-panel testnet-account-panel">
      <PanelTitle
        title={`模拟账户 · ${modeLabel}`}
        meta={account.connected ? `API 已连接 ${account.api_base}` : "API 未连接"}
      />
      {account.warning ? <p className="panel-warning">{account.warning}</p> : null}
      {account.web_ui_url ? (
        <p className="panel-hint">
          对应网页（须登录同一账号）：
          <a href={account.web_ui_url} target="_blank" rel="noreferrer">{account.web_ui_url}</a>
        </p>
      ) : null}
      {account.error ? <p className="panel-error">{account.error}</p> : null}
      {account.connected ? (
        <>
          <div className="metric-grid compact">
            <MetricLine label="钱包 USDT" value={formatNumber(account.wallet_balance, 2)} />
            <MetricLine label="可用 USDT" value={formatNumber(account.available_balance, 2)} />
            <MetricLine label="未实现盈亏（PnL）" value={formatNumber(account.unrealized_pnl, 2)} />
            <MetricLine label="持仓数" value={String(account.open_position_count ?? 0)} />
          </div>
          <div className="subpanel-title">持仓（币安 API 真源）</div>
          {positions.length ? (
            <table className="compact-table">
              <thead>
                <tr>
                  <th>交易对</th>
                  <th>方向</th>
                  <th>数量</th>
                  <th>开仓价</th>
                  <th>标记价</th>
                  <th>名义 USDT</th>
                  <th>保证金</th>
                  <th>杠杆</th>
                  <th>未实现 PnL</th>
                  <th>强平价</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={`${p.symbol}-${p.side}`}>
                    <td>{p.symbol}</td>
                    <td>{sideLabel(p.side)}</td>
                    <td>{formatNumber(p.quantity, 4)}</td>
                    <td>{formatNumber(p.entry_price)}</td>
                    <td>{formatNumber(p.mark_price)}</td>
                    <td>{formatNumber(p.notional_usdt, 2)}</td>
                    <td>{formatNumber(p.margin_usdt, 2)}</td>
                    <td>{p.leverage ? `${formatNumber(p.leverage, 0)}x` : "-"}</td>
                    <td className={Number(p.unrealized_pnl) >= 0 ? "positive" : "negative"}>{formatNumber(p.unrealized_pnl, 2)}</td>
                    <td>{p.liquidation_price ? formatNumber(p.liquidation_price) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-list">币安当前无持仓</div>
          )}
          <div className="subpanel-title">当前挂单（币安 Open Orders）</div>
          {asArray(account.open_orders).length ? (
            <table className="compact-table">
              <thead>
                <tr>
                  <th>订单 ID</th>
                  <th>交易对</th>
                  <th>方向</th>
                  <th>类型</th>
                  <th>数量</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {asArray(account.open_orders).map((order) => (
                  <tr key={`open-${order.order_id}`}>
                    <td>{order.order_id}</td>
                    <td>{order.symbol}</td>
                    <td>{sideLabel(order.side)}</td>
                    <td>{order.order_type}</td>
                    <td>{formatNumber(order.quantity, 4)}</td>
                    <td>{orderStatusLabel(order.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-list">币安当前无挂单</div>
          )}
          <div className="subpanel-title">最近订单（币安 orderId）</div>
          {orders.length ? (
            <table className="compact-table">
              <thead>
                <tr>
                  <th>订单编号</th>
                  <th>交易对</th>
                  <th>方向</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>数量</th>
                  <th>均价</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {orders.slice(0, 12).map((o) => (
                  <tr key={o.order_id}>
                    <td>{o.order_id}</td>
                    <td>{o.symbol}</td>
                    <td>{sideLabel(o.side)}</td>
                    <td>{orderTypeLabel(o.order_type)}</td>
                    <td>{orderStatusLabel(o.status)}</td>
                    <td>{formatNumber(o.quantity, 4)}</td>
                    <td>{o.avg_price ? formatNumber(o.avg_price) : "-"}</td>
                    <td>{o.update_time ? formatClock(new Date(o.update_time)) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty-list">暂无币安订单</div>
          )}
        </>
      ) : null}
    </section>
  );
}

export function MarketList({ universe, universeStatus = "loading", selectedSymbol, onSelect }) {
  const rows = asArray(universe);
  return (
    <section className="exchange-panel market-list-panel">
      <PanelTitle title="市场" meta="USDT 永续" />
      <div className="market-list-header">
        <span>交易对</span>
        <span>价格</span>
        <span>24h</span>
      </div>
      <div className="market-list">
        {rows.length ? (
          rows.map((item) => {
            const change = Number(item.price_change_percent ?? 0);
            return (
              <button
                key={item.symbol}
                type="button"
                className={`market-row ${item.symbol === selectedSymbol ? "active" : ""}`}
                onClick={() => onSelect(item.symbol, item.perp_symbol)}
              >
                <span title={item.reason ?? ""}>
                  {item.display_symbol ?? item.symbol}
                  <small>{marketStatusLabel(item.tradable_status)}</small>
                </span>
                <span>{formatNumber(item.last_price)}</span>
                <span className={change >= 0 ? "positive" : "negative"}>{formatNumber(change, 2)}%</span>
              </button>
            );
          })
        ) : (
          <div className="empty-list">
            {universeStatus === "error" ? "固定 Top20 市场列表加载失败" : "正在加载固定 Top20 市场列表"}
          </div>
        )}
      </div>
    </section>
  );
}

export function RecentTradesPanel({ trades, symbol }) {
  const rows = asArray(trades?.trades).slice(0, 24);
  const source = trades?.source ? sourceLabel(trades.source) : symbol;
  return (
    <section className="exchange-panel trades-panel">
      <PanelTitle title="最新成交" meta={source} />
      <div className="compact-table-header three">
        <span>价格</span>
        <span>数量</span>
        <span>时间</span>
      </div>
      {rows.length ? (
        rows.map((trade, index) => (
          <div className="compact-table-row three" key={trade.trade_id ?? `${trade.trade_time}-${index}`}>
            <span className={trade.side === "sell" ? "negative" : "positive"}>{formatNumber(trade.price)}</span>
            <span>{formatNumber(trade.quantity, 5)}</span>
            <span>{formatClock(trade.trade_time)}</span>
          </div>
        ))
      ) : (
        <div className="empty-list">暂无实时成交</div>
      )}
    </section>
  );
}

export function TradingTicket({ symbol, timeframe, mode, manualContext, latestPosition, latestPrice, onAction }) {
  const [quantity, setQuantity] = useState("0.01");
  const [leverage, setLeverage] = useState("1");
  const [orderType, setOrderType] = useState("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [stoploss, setStoploss] = useState("");
  const [takeprofit, setTakeprofit] = useState("");
  const [customStops, setCustomStops] = useState(false);
  const referencePrice = Number(latestPrice);
  const notional = Number(quantity) * (Number(limitPrice) || referencePrice || 0);
  const missingLimit = orderType === "limit" && !Number(limitPrice);
  const missingContext = !manualContext?.strategy_id || !manualContext?.validation_backtest_run_id;
  const missingPrice = !Number.isFinite(referencePrice) || referencePrice <= 0;
  const openDisabled = missingContext || missingPrice || !Number(quantity) || missingLimit || (!Number(stoploss) && customStops);
  const closeDisabled = missingContext || !latestPosition;

  useEffect(() => {
    if (!Number.isFinite(referencePrice) || referencePrice <= 0) return;
    setLimitPrice(String(referencePrice.toFixed(2)));
    if (!customStops) {
      setStoploss(String((referencePrice * 0.99).toFixed(2)));
      setTakeprofit(String((referencePrice * 1.02).toFixed(2)));
    }
  }, [referencePrice, symbol]);

  const buildPayload = (direction) => {
    const price = orderType === "limit" ? Number(limitPrice) : referencePrice;
    const defaultStop = direction === "long" ? price * 0.99 : price * 1.01;
    const defaultTake = direction === "long" ? price * 1.02 : price * 0.98;
    return {
      mode,
      strategy_id: manualContext.strategy_id,
      validation_backtest_run_id: manualContext.validation_backtest_run_id,
      paper_run_id: manualContext.paper_run_id,
      symbol,
      direction,
      quantity: Number(quantity),
      reference_price: price,
      leverage: Number(leverage),
      order_type: orderType,
      limit_price: orderType === "limit" ? Number(limitPrice) : undefined,
      time_in_force: "GTC",
      timeframe,
      stoploss_price: Number(stoploss) || Number(defaultStop.toFixed(2)),
      takeprofit_price: Number(takeprofit) || Number(defaultTake.toFixed(2)),
      account_equity: 10000,
    };
  };

  return (
    <section className="exchange-panel trading-ticket">
      <PanelTitle title="下单" meta={mode === "testnet" ? "Testnet" : "Paper"} />
      <div className="ticket-type-tabs">
        {["market", "limit"].map((item) => (
          <button key={item} type="button" className={orderType === item ? "active" : ""} onClick={() => setOrderType(item)}>
            {item === "market" ? "市价" : "限价"}
          </button>
        ))}
      </div>
      <div className="ticket-grid">
        <label>
          数量
          <input value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="decimal" />
        </label>
        <label>
          估算 USDT
          <input value={formatNumber(notional, 2)} readOnly />
        </label>
        <label>
          限价
          <input value={limitPrice} onChange={(event) => setLimitPrice(event.target.value)} inputMode="decimal" disabled={orderType !== "limit"} />
        </label>
        <label>
          杠杆
          <input value={leverage} onChange={(event) => setLeverage(event.target.value)} inputMode="decimal" />
        </label>
        <label>
          止损
          <input
            value={stoploss}
            onChange={(event) => {
              setCustomStops(true);
              setStoploss(event.target.value);
            }}
            inputMode="decimal"
            placeholder="开仓必填"
          />
        </label>
        <label>
          止盈
          <input
            value={takeprofit}
            onChange={(event) => {
              setCustomStops(true);
              setTakeprofit(event.target.value);
            }}
            inputMode="decimal"
          />
        </label>
        <label>
          当前价
          <input value={formatNumber(referencePrice)} readOnly />
        </label>
        <label>
          TIF
          <input value="GTC" readOnly />
        </label>
      </div>
      <div className="ticket-actions">
        <button type="button" className="buy" disabled={openDisabled} onClick={() => onAction("manualOrder", buildPayload("long"))}>
          开多
        </button>
        <button type="button" className="sell" disabled={openDisabled} onClick={() => onAction("manualOrder", buildPayload("short"))}>
          开空
        </button>
        <button
          type="button"
          disabled={closeDisabled}
          onClick={() =>
            onAction("closePosition", {
              mode,
              strategy_id: manualContext.strategy_id,
              validation_backtest_run_id: manualContext.validation_backtest_run_id,
              paper_run_id: manualContext.paper_run_id,
              symbol,
              reference_price: referencePrice,
              timeframe,
              account_equity: 10000,
            })
          }
        >
          平仓
        </button>
        <button type="button" disabled={missingContext} onClick={() => onAction("adjustLeverage", { mode, strategy_id: manualContext.strategy_id, symbol, leverage: Number(leverage) })}>
          调杠杆
        </button>
      </div>
      <p className="ticket-note">{ticketHint({ missingContext, missingLimit, customStops, stoploss })}</p>
      <details className="evidence-details">
        <summary>风控证据 / 高级</summary>
        <dl>
          <dt>Strategy</dt><dd>{manualContext?.strategy_id ?? "--"}</dd>
          <dt>Backtest</dt><dd>{manualContext?.validation_backtest_run_id ?? "--"}</dd>
          <dt>PaperRun</dt><dd>{manualContext?.paper_run_id ?? "--"}</dd>
          <dt>说明</dt><dd>{manualContext?.warning ?? "Paper-only sandbox evidence"}</dd>
        </dl>
      </details>
    </section>
  );
}

export function FundingPanel({ signal, onBacktest }) {
  const template = signal?.recommended_strategy_template ?? {};
  return (
    <section className="exchange-panel funding-panel">
      <PanelTitle title="资金费率套利" meta={signal?.should_enter_paper ? "可进入 Paper" : "等待"} />
      <div className="funding-metrics">
        <MetricLine label="资金费率" value={formatPercent(signal?.funding_rate)} />
        <MetricLine label="资金费（基点 bps）" value={`${formatNumber(signal?.funding_bps, 2)} bps`} />
        <MetricLine label="基差（风险参考）" value={`${formatNumber(signal?.basis_bps, 2)} bps`} />
        <MetricLine label="四腿往返成本" value={`${formatNumber(signal?.round_trip_cost_bps, 2)} bps`} />
        <MetricLine label="扣费后净边际" value={`${formatNumber(signal?.estimated_net_edge_bps, 2)} bps`} tone={signal?.should_enter_paper ? "positive" : "negative"} />
      </div>
      <div className="rejection-list">
        {asArray(signal?.rejection_reasons).length ? signal.rejection_reasons.map((item) => <span key={item}>{item}</span>) : <span>规则通过</span>}
      </div>
      <button type="button" onClick={onBacktest}>触发 Carry 回测</button>
      <p>{template.core_thesis ?? "只有预计资金费覆盖现货与永续开平四笔的手续费和滑点后，才进入模拟盘；基差不计入收益。"}</p>
    </section>
  );
}

export function RuntimeControlPanel({ streamStatus, tradingStatus, mirrorToGateway = false, onMirrorToggle, onRunCycle }) {
  const streamLabel = streamStatus === "live" ? "实时行情已连接" : streamStatus === "connecting" ? "行情连接中" : "REST 轮询";
  return (
    <section className="exchange-panel runtime-control-panel">
      <PanelTitle title="自动交易" meta="7x24 Paper Cycle" />
      <AutoEngineStatusBadge status={tradingStatus} />
      <div className="metric-line positive">
        <span>行情流</span>
        <strong>{streamLabel}</strong>
      </div>
      <button type="button" className={mirrorToGateway ? "active" : ""} onClick={() => onMirrorToggle?.(!mirrorToGateway)}>
        {mirrorToGateway ? "关闭 Testnet 镜像" : "开启 Testnet 镜像"}
      </button>
      <button type="button" onClick={onRunCycle}>运行一次自动开平仓 cycle</button>
      <p className="ticket-note">
        开启镜像后：策略信号<strong>先提交币安</strong>（BINANCE_AUTO_EXECUTE），成功才本地成交；下方「Mock 账户」即币安真持仓。
      </p>
    </section>
  );
}

export function AutoEngineStatusBadge({ status }) {
  const running = Boolean(status?.scheduler_running);
  const eta = status?.next_cycle_eta_seconds;
  const etaLabel = Number.isFinite(Number(eta)) ? ` / 下次 ${formatEta(Number(eta))}` : "";
  const error = status?.scheduler_error ? ` / ${status.scheduler_error}` : "";
  const executionLabel = {
    ready: `Mock 自动下单可执行 / Top${status?.fixed_top20_count ?? 20} 数据、风控与验收均已通过`,
    armed: `Mock 自动下单已武装 / Top${status?.fixed_top20_count ?? 20} 监控中`,
    monitoring_only: "仅监控，Mock 自动下单未开启",
    blocked_missing_credentials: "缺少 Mock API 凭据",
    blocked_gateway_unavailable: "Mock 下单网关不可用",
    blocked_safety_boundary: "安全边界阻止自动下单",
  }[status?.auto_execution_state] ?? (status?.execution_blockers?.length ? `自动下单阻断：${status.execution_blockers.join("、")}` : null);
  return (
    <div className={`auto-engine-badge ${running ? "positive" : "neutral"}`}>
      <span>{running ? "自动运行中" : "自动引擎停止"}</span>
      <strong>{status?.scheduler_mode ?? "disabled"}{running ? etaLabel : ""}</strong>
      {executionLabel ? <em>{executionLabel}</em> : null}
      {error ? <em>{error}</em> : null}
    </div>
  );
}

function marketStatusLabel(status) {
  if (status === "trading") return "可交易";
  if (status && status !== "unknown") return "不可交易";
  return "待校验";
}

export function OrdersTable({ orders, onCancel }) {
  const rows = asArray(orders);
  return (
    <section className="exchange-panel table-panel">
      <PanelTitle title="订单" meta={`${rows.length}`} />
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>来源</th>
            <th>交易对</th>
            <th>方向</th>
            <th>类型</th>
            <th>限价</th>
            <th>止损</th>
            <th>止盈</th>
            <th>状态</th>
            <th>币安 ID</th>
            <th>网关</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((order) => (
              <tr key={order.order_execution_id ?? `${order.symbol}-${order.created_at}`}>
                <td>{formatTime(order.created_at)}</td>
                <td>{orderSourceLabel(order)}</td>
                <td>{order.symbol}</td>
                <td>{sideLabel(order.direction)}</td>
                <td>{orderTypeLabel(order.entry_context?.order_type)}</td>
                <td>{formatNumber(order.entry_context?.limit_price)}</td>
                <td>{formatNumber(order.stoploss_plan?.price)}</td>
                <td>{formatNumber(order.takeprofit_plan?.price)}</td>
                <td>{orderStatusLabel(order.execution_status)}</td>
                <td>{order.gateway_order_id ?? "-"}</td>
                <td>{order.gateway_name ?? "-"}</td>
                <td>
                  {canCancel(order) ? <button type="button" className="table-action" onClick={() => onCancel(order)}>撤单</button> : (order.rejection_reason ?? "-")}
                </td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="12">暂无订单</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

export function PositionsTable({ positions }) {
  const rows = asArray(positions);
  return (
    <section className="exchange-panel table-panel">
      <PanelTitle title="持仓" meta={`${rows.length}`} />
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>交易对</th>
            <th>方向</th>
            <th>数量</th>
            <th>开仓价</th>
            <th>标记价</th>
            <th>未实现 PnL</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((position) => (
              <tr key={position.position_snapshot_id ?? `${position.symbol}-${position.snapshot_time}`}>
                <td>{formatTime(position.snapshot_time)}</td>
                <td>{position.symbol}</td>
                <td>{sideLabel(position.side)}</td>
                <td>{formatNumber(position.quantity, 4)}</td>
                <td>{formatNumber(position.entry_price)}</td>
                <td>{formatNumber(position.mark_price)}</td>
                <td>{formatNumber(position.unrealized_pnl)}</td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="7">暂无持仓</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

export function PanelTitle({ title, meta }) {
  return (
    <div className="panel-title">
      <h2>{title}</h2>
      <span>{meta}</span>
    </div>
  );
}

function MetricLine({ label, value, tone = "neutral" }) {
  return (
    <div className={`metric-line ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function sourceLabel(source) {
  if (source === "binance_public_ws") return "币安 WS";
  if (source === "binance_public_rest") return "币安 REST";
  if (source === "binance_public_rest_error") return "REST 异常";
  return "等待行情";
}

export function OrderBookPanel({ orderBook, symbol }) {
  const asks = asArray(orderBook?.asks).slice(0, 10).reverse();
  const bids = asArray(orderBook?.bids).slice(0, 10);
  const maxQuantity = Math.max(
    1,
    ...asks.map((row) => Number(row.quantity ?? row[1] ?? 0)),
    ...bids.map((row) => Number(row.quantity ?? row[1] ?? 0)),
  );
  const bestAsk = Number(asks.at(-1)?.price ?? asks.at(-1)?.[0]);
  const bestBid = Number(bids[0]?.price ?? bids[0]?.[0]);
  const midPrice = Number.isFinite(bestAsk) && Number.isFinite(bestBid) ? (bestAsk + bestBid) / 2 : null;

  const rows = (items, side) => items.map((row, index) => {
    const price = Number(row.price ?? row[0]);
    const quantity = Number(row.quantity ?? row[1]);
    return (
      <div className="book-row" key={`${side}-${price}-${index}`}>
        <i style={{ transform: `scaleX(${Math.min(1, quantity / maxQuantity)})` }} />
        <span className={side === "ask" ? "negative" : "positive"}>{formatNumber(price)}</span>
        <span>{formatNumber(quantity, 4)}</span>
        <span>{formatNumber(price * quantity, 2)}</span>
      </div>
    );
  });

  return (
    <section className="exchange-panel order-book-panel">
      <PanelTitle title="盘口" meta={orderBook?.source ? sourceLabel(orderBook.source) : symbol} />
      <div className="book-header"><span>价格 (USDT)</span><span>数量</span><span>合计</span></div>
      <div className="book-side asks">{asks.length ? rows(asks, "ask") : <div className="empty-list">暂无卖盘</div>}</div>
      <div className="mid-price">
        <strong>{midPrice ? formatNumber(midPrice) : "--"}</strong>
        <span>{symbol}</span>
      </div>
      <div className="book-side bids">{bids.length ? rows(bids, "bid") : <div className="empty-list">暂无买盘</div>}</div>
    </section>
  );
}

function sideLabel(side) {
  if (side === "long" || side === "buy") return "多";
  if (side === "short" || side === "sell") return "空";
  return side ?? "-";
}

function orderTypeLabel(orderType) {
  if (orderType === "market") return "市价";
  if (orderType === "limit") return "限价";
  return orderType ?? "-";
}

function orderStatusLabel(status) {
  if (status === "filled") return "已成交";
  if (status === "submitted") return "已提交";
  if (status === "accepted") return "已受理";
  if (status === "rejected") return "已拒绝";
  if (status === "cancelled") return "已撤销";
  return status ?? "-";
}

function orderSourceLabel(order) {
  const kind = order?.entry_context?.execution_kind;
  if (kind === "testnet_acceptance") return "连通性验收（不计策略收益）";
  if (kind === "binance_demo_reconciliation") return "币安模拟盘补录（不计策略收益）";
  if (order?.paper_run_id) return "自动策略";
  return "手动模拟单";
}

function connectionIssue(error) {
  const detail = String(error || "");
  if (/418|restricted countries|service unavailable from a restricted/i.test(detail)) {
    return { title: "Binance 拒绝当前网络出口", detail: "项目代理未生效或出口地区受限，请确认 127.0.0.1:7890 正在运行。" };
  }
  if (/proxy|connection refused|127\.0\.0\.1:7890/i.test(detail)) {
    return { title: "项目代理未连接", detail: "无法连接 BINANCE_HTTPS_PROXY，请先启动本地 HTTP 代理。" };
  }
  if (/timeout|timed out/i.test(detail)) {
    return { title: "Binance 连接超时", detail: "网络请求未在限定时间完成，请检查代理和 Binance Testnet 状态。" };
  }
  return { title: "币安 API 未连接", detail: detail || "请检查 Testnet 凭证与项目代理配置。" };
}

function ticketHint({ missingContext, missingLimit, customStops, stoploss }) {
  if (missingContext) return "正在绑定 Paper-only 风控证据。";
  if (!Number.isFinite(Number(stoploss)) && customStops) return "开仓必须填写止损。";
  if (missingLimit) return "限价单需要填写限价。";
  return "开仓会进入统一 Gatekeeper；没有止损会被拒绝。";
}

function canCancel(order) {
  return !["filled", "cancelled", "rejected"].includes(order.execution_status);
}

function formatEta(seconds) {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = String(Math.floor(safe / 60)).padStart(2, "0");
  const rest = String(safe % 60).padStart(2, "0");
  return `${minutes}:${rest}`;
}
