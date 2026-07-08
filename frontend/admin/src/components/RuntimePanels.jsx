import { asArray, formatNumber } from "../utils/format";

export function RiskEventFeed({ events, onResolve }) {
  const rows = asArray(events);
  return (
    <section className="exchange-panel risk-panel">
      <div className="panel-title">
        <h2>风控事件</h2>
        <span>{rows.length}</span>
      </div>
      <div className="risk-list">
        {rows.length ? (
          rows.map((event) => (
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
          ))
        ) : (
          <div className="empty-list">暂无活跃风控事件</div>
        )}
      </div>
    </section>
  );
}

export function DecisionDebugPanel({ decisionTrace }) {
  const decisions = asArray(decisionTrace?.last_cycle_decisions);
  return (
    <section className="exchange-panel decision-panel">
      <div className="panel-title">
        <h2>决策链路</h2>
        <span>{decisions.length}</span>
      </div>
      <div className="decision-list">
        {decisions.length ? (
          decisions.map((item) => (
            <article key={item.idempotency_key ?? `${item.symbol}-${item.action}`} className="decision-item">
              <header>
                <strong>{item.symbol}</strong>
                <span>{item.action}</span>
              </header>
              <DecisionSummary trace={item.decision_trace ?? {}} />
            </article>
          ))
        ) : (
          <div className="empty-list">暂无自动 cycle 决策记录</div>
        )}
      </div>
    </section>
  );
}

function DecisionSummary({ trace }) {
  const signals = asArray(trace.signals);
  const ensemble = trace.ensemble ?? {};
  const metaLabel = trace.meta_label ?? {};
  const veto = trace.veto_result ?? {};
  const carryRejections = asArray(trace.rejection_reasons);
  const rejectionReasons = [
    trace.rejection_reason,
    ...carryRejections,
    ...(asArray(trace.rejection_codes)),
    ...(asArray(trace.gatekeeper_rejection_codes)),
    ...(trace.pipeline_status === "discarded_low_confidence" ? ["signal_strength_below_threshold"] : []),
    ...(veto.veto === true ? [veto.veto_reason ?? "llm_veto"] : []),
  ].filter(Boolean);
  const isCarry = trace.strategy_lane === "carry" || trace.pipeline_status?.startsWith("funding_arbitrage");
  return (
    <div className="decision-summary">
      <span>车道：{trace.strategy_lane ?? (isCarry ? "carry" : "directional")}</span>
      <span>状态：{trace.pipeline_status ?? "unknown"}</span>
      {isCarry ? (
        <>
          <span>净边际(bps)：{trace.estimated_net_edge_bps ?? "—"}</span>
          <span>资金费率(bps)：{trace.funding_bps ?? "—"}</span>
        </>
      ) : (
        <>
          <span>融合：{ensemble.fused_direction ?? "无"} / {formatNumber(ensemble.fused_confidence, 3)}</span>
          <span>Meta：{metaLabel.bet_decision ?? "无"} / {formatNumber(metaLabel.position_size_fraction, 3)}</span>
          <span>LLM：{veto.veto === true ? "否决" : veto.veto === false ? "未否决" : "未调用"}</span>
        </>
      )}
      {!isCarry ? <p>{veto.veto_reason ?? "暂无 LLM 理由"}</p> : null}
      <div className="rejection-list">
        {rejectionReasons.length ? rejectionReasons.map((reason) => <span key={reason}>{reason}</span>) : <span>未被 Gatekeeper 拒绝</span>}
      </div>
      <div className="signal-chips">
        {signals.map((signal) => (
          <span key={`${signal.source}-${signal.reason}`}>{signal.source}:{signal.reason}:{signal.side}</span>
        ))}
      </div>
    </div>
  );
}
