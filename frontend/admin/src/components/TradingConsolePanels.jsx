import { useMemo, useState } from "react";

import { asArray, formatNumber, formatPercent, formatTime } from "../utils/format";

export function ModeBanner({ status }) {
  const modeLabel = status?.mode === "testnet" ? "Binance Futures Testnet" : "Paper 模拟盘";
  const testnetLabel = status?.binance_use_testnet ? "Testnet 已锁定" : "Testnet 未启用";
  const liveLabel = status?.live_trading_enabled ? "实盘开关开启" : "实盘关闭";
  const gatewayLabel = status?.gateway_available ? "网关可用" : "网关待配置";
  return (
    <section className="mode-banner">
      <div>
        <span>当前模式</span>
        <strong>{modeLabel}</strong>
      </div>
      <div>
        <span>环境</span>
        <strong>{status?.app_env ?? "development"}</strong>
      </div>
      <div>
        <span>安全边界</span>
        <strong>{testnetLabel}</strong>
      </div>
      <div>
        <span>真实交易</span>
        <strong>{liveLabel}</strong>
      </div>
      <div>
        <span>交易网关</span>
        <strong>{gatewayLabel}</strong>
      </div>
    </section>
  );
}

export function MarketList({ universe, selectedSymbol, onSelect }) {
  const rows = asArray(universe);
  return (
    <section className="exchange-panel market-list-panel">
      <PanelTitle title="市场" meta="USDT 永续 Top20" />
      <div className="market-list-header">
        <span>交易对</span>
        <span>最新价</span>
        <span>24h 涨跌</span>
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
  const bids = asArray(orderBook?.bids).slice(0, 12);
  const asks = asArray(orderBook?.asks).slice(0, 12).reverse();
  const mid = Number(snapshot?.perp_last_price ?? snapshot?.spot_last_price ?? 0);
  const source = orderBook?.source === "binance_public_rest" ? "Binance 实时深度" : "等待实时深度";
  return (
    <section className="exchange-panel orderbook-panel">
      <PanelTitle title="订单簿" meta={source} />
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
  const rows = asArray(trades?.trades).slice(0, 18);
  const source = trades?.source === "binance_public_rest" ? "Binance 实时成交" : symbol;
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
            <span>{formatTime(trade.trade_time)}</span>
          </div>
        ))
      ) : (
        <div className="empty-list">暂无实时成交</div>
      )}
    </section>
  );
}

export function TradingTicket({ symbol, mode, latestPosition, onAction }) {
  const [strategyId, setStrategyId] = useState("");
  const [backtestId, setBacktestId] = useState("");
  const [quantity, setQuantity] = useState("0.01");
  const [price, setPrice] = useState("61000");
  const [stoploss, setStoploss] = useState("");
  const [takeprofit, setTakeprofit] = useState("");
  const [leverage, setLeverage] = useState("1");
  const missingEvidence = !strategyId.trim() || !backtestId.trim();
  const openDisabled = missingEvidence || !stoploss.trim();
  const closeDisabled = missingEvidence || !latestPosition;
  const validationHint = missingEvidence
    ? "请先填写已通过验证的 Strategy ID 和 Backtest ID"
    : !stoploss.trim()
      ? "开仓必须填写止损价"
      : "订单会进入统一风控和审计链路";

  const commonPayload = () => ({
    mode,
    strategy_id: strategyId.trim(),
    validation_backtest_run_id: backtestId.trim(),
    symbol,
    quantity: Number(quantity),
    reference_price: Number(price),
    leverage: Number(leverage),
    stoploss_price: stoploss ? Number(stoploss) : undefined,
    takeprofit_price: takeprofit ? Number(takeprofit) : undefined,
    account_equity: 10000,
  });
  return (
    <section className="exchange-panel trading-ticket">
      <PanelTitle title="下单" meta={mode === "testnet" ? "Testnet" : "Paper"} />
      <div className="ticket-grid">
        <label>
          Strategy ID
          <input value={strategyId} onChange={(event) => setStrategyId(event.target.value)} placeholder="validated strategy" />
        </label>
        <label>
          Backtest ID
          <input value={backtestId} onChange={(event) => setBacktestId(event.target.value)} placeholder="validation evidence" />
        </label>
        <label>
          数量
          <input value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="decimal" />
        </label>
        <label>
          参考价
          <input value={price} onChange={(event) => setPrice(event.target.value)} inputMode="decimal" />
        </label>
        <label>
          止损价
          <input value={stoploss} onChange={(event) => setStoploss(event.target.value)} inputMode="decimal" placeholder="必填" />
        </label>
        <label>
          止盈价
          <input value={takeprofit} onChange={(event) => setTakeprofit(event.target.value)} inputMode="decimal" />
        </label>
        <label>
          杠杆
          <input value={leverage} onChange={(event) => setLeverage(event.target.value)} inputMode="decimal" />
        </label>
      </div>
      <div className="ticket-actions">
        <button type="button" className="buy" disabled={openDisabled} onClick={() => onAction("manualOrder", { ...commonPayload(), direction: "long" })}>
          开多
        </button>
        <button type="button" className="sell" disabled={openDisabled} onClick={() => onAction("manualOrder", { ...commonPayload(), direction: "short" })}>
          开空
        </button>
        <button
          type="button"
          onClick={() =>
            onAction("closePosition", {
              mode,
              strategy_id: strategyId.trim(),
              validation_backtest_run_id: backtestId.trim(),
              symbol,
              reference_price: Number(price),
              account_equity: 10000,
            })
          }
          disabled={closeDisabled}
        >
          平仓
        </button>
        <button type="button" disabled={!strategyId.trim()} onClick={() => onAction("adjustLeverage", { mode, strategy_id: strategyId.trim(), symbol, leverage: Number(leverage) })}>
          调整杠杆
        </button>
      </div>
      <p className="ticket-note">{validationHint}</p>
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
        <MetricLine
          label="净边际"
          value={`${formatNumber(signal?.estimated_net_edge_bps, 2)} bps`}
          tone={signal?.should_enter_paper ? "positive" : "negative"}
        />
      </div>
      <div className="rejection-list">
        {asArray(signal?.rejection_reasons).length ? (
          signal.rejection_reasons.map((item) => <span key={item}>{item}</span>)
        ) : (
          <span>规则通过</span>
        )}
      </div>
      <button type="button" onClick={onBacktest}>触发 Carry 回测</button>
      <p>{template.core_thesis ?? "使用现货/永续对冲，扣除手续费、滑点和基差风险后再进入 Paper。"}</p>
    </section>
  );
}

export function RuntimeControlPanel({ streamStatus, onRunCycle }) {
  const streamLabel = streamStatus === "live" ? "实时 K线已连接" : streamStatus === "connecting" ? "K线连接中" : "K线轮询/REST";
  return (
    <section className="exchange-panel runtime-control-panel">
      <PanelTitle title="自动交易" meta="7x24 Paper Cycle" />
      <div className="metric-line positive">
        <span>行情流</span>
        <strong>{streamLabel}</strong>
      </div>
      <button type="button" onClick={onRunCycle}>运行一次自动开平仓 cycle</button>
      <p className="ticket-note">只扫描 running PaperRun；每个订单仍经过 Validation、Gatekeeper、Risk、Review 审计。</p>
    </section>
  );
}

export function OrdersTable({ orders, onCancel }) {
  const rows = asArray(orders);
  return (
    <section className="exchange-panel table-panel">
      <PanelTitle title="当前委托 / 历史订单" meta={`${rows.length}`} />
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>交易对</th>
            <th>方向</th>
            <th>状态</th>
            <th>网关</th>
            <th>拒绝原因</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((order) => (
              <tr key={order.order_execution_id ?? `${order.symbol}-${order.created_at}`}>
                <td>{formatTime(order.created_at)}</td>
                <td>{order.symbol}</td>
                <td>{order.direction}</td>
                <td>{order.execution_status}</td>
                <td>{order.gateway_name ?? "-"}</td>
                <td>
                  {order.rejection_reason ?? "-"}
                  {canCancel(order) ? (
                    <button type="button" className="table-action" onClick={() => onCancel(order)}>
                      撤单
                    </button>
                  ) : null}
                </td>
              </tr>
            ))
          ) : (
            <tr><td colSpan="6">暂无订单</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function canCancel(order) {
  return !["filled", "cancelled", "rejected"].includes(order.execution_status);
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
  const depth = useMemo(() => Math.min(1, Number(row.total ?? 0) / Math.max(Number(row.quantity ?? 1) * 12, 1)), [row]);
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
