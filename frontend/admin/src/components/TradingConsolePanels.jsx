import { useEffect, useMemo, useState } from "react";

import { asArray, formatClock, formatNumber, formatPercent, formatTime } from "../utils/format";

export function ModeBanner({ status }) {
  const modeLabel = status?.mode === "testnet" ? "Binance Futures Testnet" : "Paper 模拟盘";
  const testnetLabel = status?.binance_use_testnet ? "Testnet 已锁定" : "Testnet 未启用";
  const liveLabel = status?.live_trading_enabled ? "实盘开关开启" : "实盘关闭";
  const gatewayLabel = status?.gateway_available ? "网关可用" : "网关待配置";
  return (
    <section className="mode-banner">
      <div><span>当前模式</span><strong>{modeLabel}</strong></div>
      <div><span>环境</span><strong>{status?.app_env ?? "development"}</strong></div>
      <div><span>安全边界</span><strong>{testnetLabel}</strong></div>
      <div><span>真实交易</span><strong>{liveLabel}</strong></div>
      <div><span>交易网关</span><strong>{gatewayLabel}</strong></div>
    </section>
  );
}

export function MarketList({ universe, selectedSymbol, onSelect }) {
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
                <span>{item.symbol}</span>
                <span>{formatNumber(item.last_price)}</span>
                <span className={change >= 0 ? "positive" : "negative"}>{formatNumber(change, 2)}%</span>
              </button>
            );
          })
        ) : (
          <div className="empty-list">暂无市场列表</div>
        )}
      </div>
    </section>
  );
}

export function OrderBookPanel({ orderBook, snapshot }) {
  const bids = asArray(orderBook?.bids).slice(0, 13);
  const asks = asArray(orderBook?.asks).slice(0, 13).reverse();
  const mid = Number(snapshot?.perp_last_price ?? snapshot?.spot_last_price ?? 0);
  const source = sourceLabel(orderBook?.source);
  return (
    <section className="exchange-panel orderbook-panel">
      <PanelTitle title="盘口" meta={source} />
      <BookHeader />
      <div className="book-side asks">
        {asks.length ? asks.map((row) => <BookRow key={`ask-${row.price}`} row={row} side="ask" />) : <div className="empty-list">暂无卖盘</div>}
      </div>
      <div className="mid-price">
        <strong>{formatNumber(mid)}</strong>
        <span>标记价</span>
      </div>
      <div className="book-side bids">
        {bids.length ? bids.map((row) => <BookRow key={`bid-${row.price}`} row={row} side="bid" />) : <div className="empty-list">暂无买盘</div>}
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
        <MetricLine label="Funding" value={formatPercent(signal?.funding_rate)} />
        <MetricLine label="Funding bps" value={`${formatNumber(signal?.funding_bps, 2)} bps`} />
        <MetricLine label="Basis" value={`${formatNumber(signal?.basis_bps, 2)} bps`} />
        <MetricLine label="净边际" value={`${formatNumber(signal?.estimated_net_edge_bps, 2)} bps`} tone={signal?.should_enter_paper ? "positive" : "negative"} />
      </div>
      <div className="rejection-list">
        {asArray(signal?.rejection_reasons).length ? signal.rejection_reasons.map((item) => <span key={item}>{item}</span>) : <span>规则通过</span>}
      </div>
      <button type="button" onClick={onBacktest}>触发 Carry 回测</button>
      <p>{template.core_thesis ?? "使用现货/永续对冲，扣除手续费、滑点和基差风险后再进入 Paper。"}</p>
    </section>
  );
}

export function RuntimeControlPanel({ streamStatus, tradingStatus, onRunCycle }) {
  const streamLabel = streamStatus === "live" ? "实时行情已连接" : streamStatus === "connecting" ? "行情连接中" : "REST 轮询";
  return (
    <section className="exchange-panel runtime-control-panel">
      <PanelTitle title="自动交易" meta="7x24 Paper Cycle" />
      <AutoEngineStatusBadge status={tradingStatus} />
      <div className="metric-line positive">
        <span>行情流</span>
        <strong>{streamLabel}</strong>
      </div>
      <button type="button" onClick={onRunCycle}>运行一次自动开平仓 cycle</button>
      <p className="ticket-note">只扫描 running PaperRun；订单仍经过 Validation、Gatekeeper、Risk、Review 审计。</p>
    </section>
  );
}

export function AutoEngineStatusBadge({ status }) {
  const running = Boolean(status?.scheduler_running);
  const eta = status?.next_cycle_eta_seconds;
  const etaLabel = Number.isFinite(Number(eta)) ? ` / 下次 ${formatEta(Number(eta))}` : "";
  const error = status?.scheduler_error ? ` / ${status.scheduler_error}` : "";
  return (
    <div className={`auto-engine-badge ${running ? "positive" : "neutral"}`}>
      <span>{running ? "自动运行中" : "自动引擎停止"}</span>
      <strong>{status?.scheduler_mode ?? "disabled"}{running ? etaLabel : ""}</strong>
      {error ? <em>{error}</em> : null}
    </div>
  );
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
            <th>交易对</th>
            <th>方向</th>
            <th>类型</th>
            <th>限价</th>
            <th>止损</th>
            <th>止盈</th>
            <th>状态</th>
            <th>网关</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((order) => (
              <tr key={order.order_execution_id ?? `${order.symbol}-${order.created_at}`}>
                <td>{formatTime(order.created_at)}</td>
                <td>{order.symbol}</td>
                <td>{order.direction}</td>
                <td>{order.entry_context?.order_type ?? "-"}</td>
                <td>{formatNumber(order.entry_context?.limit_price)}</td>
                <td>{formatNumber(order.stoploss_plan?.price)}</td>
                <td>{formatNumber(order.takeprofit_plan?.price)}</td>
                <td>{order.execution_status}</td>
                <td>{order.gateway_name ?? "-"}</td>
                <td>
                  {canCancel(order) ? <button type="button" className="table-action" onClick={() => onCancel(order)}>撤单</button> : (order.rejection_reason ?? "-")}
                </td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="10">暂无订单</td></tr>
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
                <td>{position.side}</td>
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

function BookHeader() {
  return (
    <div className="compact-table-header three">
      <span>价格</span>
      <span>数量</span>
      <span>累计</span>
    </div>
  );
}

function BookRow({ row, side }) {
  const depth = useMemo(() => Math.min(1, Number(row.total ?? 0) / Math.max(Number(row.quantity ?? 1) * 14, 1)), [row]);
  return (
    <div className="book-row">
      <span className={side === "ask" ? "negative" : "positive"}>{formatNumber(row.price)}</span>
      <span>{formatNumber(row.quantity, 5)}</span>
      <span>{formatNumber(row.total, 4)}</span>
      <i style={{ transform: `scaleX(${depth})` }} />
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
  if (source === "binance_public_ws") return "Binance WS";
  if (source === "binance_public_rest") return "Binance REST";
  if (source === "binance_public_rest_error") return "REST 异常";
  return "等待行情";
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
