import { asArray, formatNumber, formatTime } from "../utils/format";

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

export function Top20MonitorPanel({ decisionTrace, tradingStatus }) {
  const scanned = asArray(decisionTrace?.last_scanned_symbols);
  const candidates = asArray(decisionTrace?.candidate_symbols);
  const counts = decisionTrace?.last_action_counts ?? {};
  const liveFeeds = Object.values(tradingStatus?.live_feed_status ?? {});
  const liveCount = liveFeeds.filter((feed) => feed?.status === "live").length;
  const laneLabel =
    decisionTrace?.auto_paper_runtime_key === "auto_paper_btc_technical"
      ? "方向策略 (4h+15m)"
      : decisionTrace?.auto_paper_runtime_key === "auto_paper_btc_funding"
        ? "Carry 资金费率"
        : decisionTrace?.strategy_lane ?? "auto";
  return (
    <section className="exchange-panel top20-panel">
      <div className="panel-title">
        <h2>Top20 自动监控</h2>
        <span>{scanned.length || candidates.length}/20</span>
      </div>
      <div className="decision-summary">
        <span>车道：{laneLabel}</span>
        <span>周期：{decisionTrace?.last_runtime_timeframe ?? "15m"}</span>
        <span>上次扫描：{decisionTrace?.last_cycle_at ? formatTime(decisionTrace.last_cycle_at) : "等待首轮 cycle"}</span>
        <span>Live WS：{liveCount} 路</span>
        <span>
          本轮：开 {counts.opened ?? 0} / 平 {counts.closed ?? 0} / 拒 {counts.rejected ?? 0} / 跳过 {counts.skipped ?? 0}
        </span>
      </div>
      <div className="signal-chips">
        {(scanned.length ? scanned : candidates).map((symbol) => (
          <span key={symbol}>{symbol}</span>
        ))}
      </div>
      {!scanned.length && !candidates.length ? (
        <div className="empty-list">等待自动 PaperRun 写入 Top20 候选范围</div>
      ) : null}
    </section>
  );
}

export function DataSourcesPanel({ dataSources, intelligenceSignal }) {
  const providers = Object.values(intelligenceSignal?.provider_status ?? {});
  const newsTotal = dataSources?.news_total ?? 0;
  const macroTotal = dataSources?.macro_total ?? 0;
  return (
    <section className="exchange-panel data-sources-panel">
      <div className="panel-title">
        <h2>信息源</h2>
        <span>C/B/D 级</span>
      </div>
      <div className="decision-summary">
        <span>新闻 RSS：{newsTotal} 条</span>
        <span>宏观事件：{macroTotal} 条</span>
        {dataSources?.news_refresh_error ? <span>新闻刷新：{dataSources.news_refresh_error}</span> : null}
        {dataSources?.macro_refresh_error ? <span>宏观刷新：{dataSources.macro_refresh_error}</span> : null}
      </div>
      <div className="rejection-list">
        {providers.length ? providers.map((provider) => (
          <span key={provider.provider}>{provider.provider}:{provider.status}</span>
        )) : <span>情报 Provider 等待首次刷新</span>}
      </div>
      <p className="ticket-note">
        完整新闻/宏观/Agent 任务见 <a href="/ops">运维 Ops</a>；情报因子见 Market Intelligence 与 <a href="/review">复盘 Review</a>。
      </p>
    </section>
  );
}

export function MarketIntelligencePanel({ signal }) {
  const components = Object.entries(signal?.component_scores ?? {});
  const providers = Object.values(signal?.provider_status ?? {});
  return (
    <section className="exchange-panel intelligence-panel">
      <div className="panel-title">
        <h2>Market Intelligence</h2>
        <span>{signal?.should_participate ? "投票中" : signal?.active_event_cooldown ? "冷却" : "观察"}</span>
      </div>
      {signal ? (
        <div className="decision-summary">
          <span>方向：{signal.direction ?? "neutral"}</span>
          <span>Long：{formatNumber(signal.long_probability, 3)}</span>
          <span>Short：{formatNumber(signal.short_probability, 3)}</span>
          <span>置信：{formatNumber(signal.confidence, 3)}</span>
          <span>权重：{formatNumber(signal.vote_weight, 3)} / 0.300</span>
          <span>风险：{signal.risk_level}</span>
          {signal.active_event_cooldown ? <p>{signal.rationale?.[0] ?? "重大事件冷却中，情报投票禁用"}</p> : null}
          <div className="rejection-list">
            {providers.map((provider) => (
              <span key={provider.provider}>{provider.provider}:{provider.status}</span>
            ))}
          </div>
          <div className="signal-chips">
            {components.length ? components.map(([name, value]) => (
              <span key={name}>{name}:{formatNumber(value, 3)}</span>
            )) : <span>等待更多情报因子</span>}
          </div>
        </div>
      ) : (
        <div className="empty-list">暂无情报信号</div>
      )}
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
