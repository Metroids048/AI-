import { asArray, formatNumber, formatTime } from "../utils/format";

export function PaperRunControls({ onAction, overview }) {
  const latestPaper = asArray(overview?.paper_runs).at(-1);
  return (
    <section className="panel controls-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Manual Gate</p>
          <h2>人工操作</h2>
        </div>
        <span>Paper only</span>
      </div>
      <ManualControls onAction={onAction} latestPaper={latestPaper} />
    </section>
  );
}

function ManualControls({ onAction, latestPaper }) {
  const strategyInput = "strategy-id-input";
  const gateInput = "gate-ref-input";
  const riskInput = "risk-text-input";

  const valueOf = (id) => document.getElementById(id)?.value ?? "";
  return (
    <>
      <div className="control-grid">
        <label>
          Strategy ID
          <input id={strategyInput} placeholder="strategy-id" />
        </label>
        <label>
          Gate / Backtest ID
          <input id={gateInput} placeholder="backtest-run-id" />
        </label>
        <button
          type="button"
          onClick={() => onAction("startPaper", { strategy_id: valueOf(strategyInput), gate_decision_ref: valueOf(gateInput) })}
        >
          启动 Paper
        </button>
        <button
          type="button"
          onClick={() => onAction("pausePaper", { paper_run_id: latestPaper?.paper_run_id })}
          disabled={!latestPaper?.paper_run_id}
        >
          暂停 Paper
        </button>
        <button
          type="button"
          onClick={() => onAction("autoCycle", { paper_run_id: latestPaper?.paper_run_id })}
          disabled={!latestPaper?.paper_run_id}
        >
          手动 Cycle
        </button>
        <button type="button" onClick={() => onAction("carryBacktest", { strategy_id: valueOf(strategyInput) })}>
          触发 Carry 回测
        </button>
      </div>
      <div className="risk-submit">
        <input id={riskInput} defaultValue="手动高严重度风险事件" />
        <button type="button" onClick={() => onAction("createRisk", { description: valueOf(riskInput) })}>
          提交风险事件
        </button>
      </div>
    </>
  );
}

export function OrdersTable({ orders }) {
  const rows = asArray(orders);
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>订单与 Gatekeeper</h2>
        <span>{rows.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>Symbol</th>
            <th>方向</th>
            <th>状态</th>
            <th>拒绝原因</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((order) => (
            <tr key={order.order_execution_id ?? `${order.symbol}-${order.created_at}`}>
              <td>{formatTime(order.created_at)}</td>
              <td>{order.symbol}</td>
              <td>{order.direction}</td>
              <td>{order.execution_status}</td>
              <td>{order.rejection_reason ?? "无"}</td>
            </tr>
          )) : (
            <tr><td colSpan="5">订单数据缺失</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

export function PositionsTable({ positions }) {
  const rows = asArray(positions);
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>持仓</h2>
        <span>{rows.length}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>Symbol</th>
            <th>方向</th>
            <th>数量</th>
            <th>入场</th>
            <th>标记</th>
            <th>未实现 PnL</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((position) => (
            <tr key={position.position_snapshot_id ?? `${position.symbol}-${position.snapshot_time}`}>
              <td>{formatTime(position.snapshot_time)}</td>
              <td>{position.symbol}</td>
              <td>{position.side}</td>
              <td>{formatNumber(position.quantity, 4)}</td>
              <td>{formatNumber(position.entry_price)}</td>
              <td>{formatNumber(position.mark_price)}</td>
              <td>{formatNumber(position.unrealized_pnl)}</td>
            </tr>
          )) : (
            <tr><td colSpan="7">持仓数据缺失</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

export function RiskEventFeed({ events, onResolve }) {
  const rows = asArray(events);
  return (
    <section className="panel risk-panel">
      <div className="panel-heading">
        <h2>风险事件</h2>
        <span>{rows.length}</span>
      </div>
      <div className="risk-list">
        {rows.length ? rows.map((event) => (
          <article key={event.risk_event_id ?? event.description} className={`risk-item severity-${event.severity}`}>
            <div>
              <strong>{event.severity}</strong>
              <span>{event.event_type}</span>
            </div>
            <p>{event.description}</p>
            <footer>
              <span>{event.resolution_status}</span>
              <button type="button" onClick={() => onResolve(event.risk_event_id, "acknowledged")} disabled={!event.risk_event_id}>
                确认
              </button>
              <button type="button" onClick={() => onResolve(event.risk_event_id, "resolved")} disabled={!event.risk_event_id}>
                恢复
              </button>
            </footer>
          </article>
        )) : <div className="empty-list">暂无活跃风险事件</div>}
      </div>
    </section>
  );
}

export function DecisionDebugPanel({ decisionTrace }) {
  const decisions = asArray(decisionTrace?.last_cycle_decisions);
  return (
    <section className="panel decision-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Decision Pipeline</p>
          <h2>决策链路调试</h2>
        </div>
        <span>{decisions.length}</span>
      </div>
      <div className="decision-list">
        {decisions.length ? decisions.map((item) => (
          <article key={item.idempotency_key ?? `${item.symbol}-${item.action}`} className="decision-item">
            <header>
              <strong>{item.symbol}</strong>
              <span>{item.action}</span>
            </header>
            <DecisionSummary trace={item.decision_trace ?? {}} />
          </article>
        )) : <div className="empty-list">暂无自动 cycle 决策记录</div>}
      </div>
    </section>
  );
}

function DecisionSummary({ trace }) {
  const signals = asArray(trace.signals);
  const ensemble = trace.ensemble ?? {};
  const metaLabel = trace.meta_label ?? {};
  const veto = trace.veto_result ?? {};
  return (
    <div className="decision-summary">
      <span>状态：{trace.pipeline_status ?? "unknown"}</span>
      <span>融合：{ensemble.fused_direction ?? "无"} / {formatNumber(ensemble.fused_confidence, 3)}</span>
      <span>Meta：{metaLabel.bet_decision ?? "无"} / {formatNumber(metaLabel.position_size_fraction, 3)}</span>
      <span>LLM：{veto.veto === true ? "否决" : veto.veto === false ? "不否决" : "未调用"}</span>
      <p>{veto.veto_reason ?? "暂无 LLM 理由"}</p>
      <div className="signal-chips">
        {signals.map((signal) => (
          <span key={`${signal.source}-${signal.reason}`}>{signal.source}:{signal.reason}:{signal.side}</span>
        ))}
      </div>
    </div>
  );
}
