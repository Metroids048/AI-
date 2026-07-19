import { useEffect, useState } from "react";

import { asArray, formatNumber, formatTime } from "../utils/format";

const DEFAULT_AUTO_SETTINGS = {
  execution_mode: "binance_simulation_first",
  max_leverage: 10,
  risk_per_trade: 0.01,
  order_notional_usdt: "",
  max_open_positions: 5,
  max_symbols: 20,
  max_symbol_exposure: 0.15,
  max_total_exposure: 0.5,
  daily_loss_limit: 0.04,
  weekly_loss_limit: 0.08,
  hard_stop_drawdown_limit: 0.15,
  asset_risk_tiers: {
    core: { tier: "core", symbols: ["BTC/USDT", "ETH/USDT", "SOL/USDT"], leverage: 20, max_position_fraction: 0.15 },
    standard: { tier: "standard", symbols: [], leverage: 10, max_position_fraction: 0.06 },
  },
  strategy_lanes: ["carry", "trend_breakout", "mean_reversion"],
  stoploss: { atr_multiple: 2, fixed_bps: 250 },
  takeprofit: { risk_reward: 2.5, trail_after_r: 1.5 },
  llm_veto_enabled: true,
  market_intelligence_enabled: true,
};

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

export function RejectionFunnelPanel({ summary }) {
  const counts = summary?.counts ?? {};
  const recent = asArray(summary?.recent);
  return (
    <section className="exchange-panel decision-panel">
      <div className="panel-title">
        <h2>拒单漏斗</h2>
        <span>{recent.length}</span>
      </div>
      <div className="rejection-list">
        {Object.entries(counts).map(([category, count]) => <span key={category}>{category}: {count}</span>)}
      </div>
      <div className="decision-list">
        {recent.length ? recent.map((item) => (
          <article key={item.order_execution_id ?? `${item.symbol}-${item.category}`} className="decision-item">
            <strong>{item.symbol} · {item.category} · {item.message ?? item.codes?.join(", ") ?? "无错误详情"}</strong>
            {item.gateway_order_id ? <span>币安订单 #{item.gateway_order_id}</span> : null}
          </article>
        )) : <div className="empty-list">暂无拒单记录</div>}
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
    decisionTrace?.auto_paper_runtime_key === "auto_paper_mature_templates"
      ? "成熟模板集合"
      : decisionTrace?.auto_paper_runtime_key === "auto_paper_btc_technical"
        ? "旧方向策略"
      : decisionTrace?.auto_paper_runtime_key === "auto_paper_btc_funding"
        ? "Carry 资金费率"
        : decisionTrace?.strategy_lane ?? "auto";
  const assets = asArray(decisionTrace?.execution_profile?.universe_assets);
  const evidenceRows = buildTop20EvidenceRows(decisionTrace);
  return (
    <section className="exchange-panel top20-panel">
      <div className="panel-title">
        <h2>Top20 自动监控</h2>
        <span>{scanned.length || candidates.length}/20</span>
      </div>
      <div className="decision-summary">
        <span>车道：{laneLabel}</span>
        <span>周期：{decisionTrace?.last_runtime_timeframe ?? "1h"}</span>
        <span>上次扫描：{decisionTrace?.last_cycle_at ? formatTime(decisionTrace.last_cycle_at) : "等待首轮 cycle"}</span>
        <span>Live WS：{liveCount} 路</span>
        <span>
          本轮：开 {counts.opened ?? 0} / 平 {counts.closed ?? 0} / 拒 {counts.rejected ?? 0} / 跳过 {counts.skipped ?? 0}
        </span>
      </div>
      <div className="signal-chips">
        {evidenceRows.map((item) => {
          const asset = assets.find((candidate) => candidate.platform_symbol === item.symbol);
          return (
            <span
              key={item.symbol}
              className={item.level >= 4 ? "positive" : asset?.tradable_status === "trading" ? "" : "negative"}
              title={[item.reason, asset?.reason].filter(Boolean).join(" / ")}
            >
              {asset?.display_symbol ?? item.symbol}:{item.label}
            </span>
          );
        })}
      </div>
      {!scanned.length && !candidates.length ? (
        <div className="empty-list">等待自动 PaperRun 写入 Top20 候选范围</div>
      ) : null}
    </section>
  );
}

export function buildTop20EvidenceRows(decisionTrace) {
  const candidates = asArray(decisionTrace?.candidate_symbols);
  const scanned = new Set(asArray(decisionTrace?.last_scanned_symbols));
  const decisions = new Map(
    asArray(decisionTrace?.last_cycle_decisions).map((item) => [item.symbol, item]),
  );
  return candidates.map((symbol) => {
    const decision = decisions.get(symbol);
    const trace = decision?.decision_trace ?? {};
    const signals = asArray(trace.signals);
    const rejections = [
      trace.rejection_reason,
      ...asArray(trace.rejection_codes),
      ...asArray(trace.gatekeeper_rejection_codes),
    ].filter(Boolean);
    let level = 0;
    let label = "候选";
    if (scanned.has(symbol)) {
      level = 1;
      label = "已采集";
    }
    if (signals.length) {
      level = 2;
      label = "有信号";
    }
    if (trace.pipeline_status === "bet_taken" && !rejections.length) {
      level = 3;
      label = "过 Gate";
    }
    if (decision?.order_execution_id) {
      level = 4;
      label = "已提交";
    }
    if (decision?.order_execution_id && /^(open_|close|closed_)/.test(decision.action ?? "")) {
      level = 5;
      label = "已成交";
    }
    return { symbol, level, label, reason: rejections.join(", ") || decision?.reason || "" };
  });
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

export function MessageSourcesPanel({ dataSources, intelligenceSignal, riskEvents }) {
  const news = asArray(dataSources?.news_items).slice(0, 6);
  const macro = asArray(dataSources?.macro_items).slice(0, 6);
  const risks = asArray(riskEvents).slice(0, 6);
  const intelligence = intelligenceSignal
    ? [{
        id: "market-intelligence",
        source: "market_intelligence",
        title: `${intelligenceSignal.direction ?? "neutral"} / weight ${formatNumber(intelligenceSignal.vote_weight, 3)}`,
        severity: intelligenceSignal.risk_level,
        published_at: intelligenceSignal.generated_at,
        action: intelligenceSignal.active_event_cooldown ? "cooldown" : intelligenceSignal.should_participate ? "vote" : "observe",
      }]
    : [];
  const rows = [
    ...news.map((item) => ({
      id: item.id ?? item.url ?? item.title,
      source: item.source ?? "news",
      title: item.title ?? "news item",
      severity: item.severity ?? "info",
      published_at: item.published_at,
      action: item.classification_summary ?? "risk-classified",
    })),
    ...macro.map((item) => ({
      id: item.id ?? item.event_name,
      source: item.source ?? "macro",
      title: item.event_name ?? "macro event",
      severity: item.impact ?? "scheduled",
      published_at: item.scheduled_at,
      action: item.is_active_pause_window ? "pause-window" : "calendar",
    })),
    ...risks.map((item) => ({
      id: item.risk_event_id ?? item.description,
      source: item.source,
      title: item.description,
      severity: item.severity,
      published_at: item.occurred_at,
      action: item.recommended_action ?? item.resolution_status,
    })),
    ...intelligence,
  ].slice(0, 14);
  return (
    <section className="exchange-panel data-sources-panel">
      <div className="panel-title">
        <h2>消息源</h2>
        <span>{rows.length}</span>
      </div>
      <div className="compact-table">
        <div className="compact-table-row four header">
          <span>来源</span>
          <span>级别</span>
          <span>动作</span>
          <span>时间</span>
        </div>
        {rows.length ? rows.map((item) => (
          <div className="compact-table-row four" key={item.id}>
            <span title={item.title}>{item.source}</span>
            <span>{item.severity}</span>
            <span>{item.action}</span>
            <span>{item.published_at ? formatTime(item.published_at) : "-"}</span>
          </div>
        )) : <div className="empty-list">暂无消息源明细</div>}
      </div>
    </section>
  );
}

export function AutoSettingsPanel({ paperRunId, autoSettings, onSave }) {
  const [form, setForm] = useState(() => normalizeSettings(autoSettings));

  useEffect(() => {
    setForm(normalizeSettings(autoSettings));
  }, [paperRunId, JSON.stringify(autoSettings ?? {})]);

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const updateNested = (group, key, value) => setForm((current) => ({
    ...current,
    [group]: { ...(current[group] ?? {}), [key]: value },
  }));
  const submit = () => {
    onSave?.({
      ...form,
      max_leverage: Number(form.max_leverage),
      risk_per_trade: Number(form.risk_per_trade),
      order_notional_usdt: Number(form.order_notional_usdt) > 0 ? Number(form.order_notional_usdt) : null,
      max_open_positions: Number(form.max_open_positions),
      max_symbols: Number(form.max_symbols),
      max_symbol_exposure: Number(form.max_symbol_exposure),
      max_total_exposure: Number(form.max_total_exposure),
      daily_loss_limit: Number(form.daily_loss_limit),
      weekly_loss_limit: Number(form.weekly_loss_limit),
      hard_stop_drawdown_limit: Number(form.hard_stop_drawdown_limit),
      strategy_lanes: String(form.strategy_lanes_text ?? "").split(",").map((item) => item.trim()).filter(Boolean),
      stoploss: {
        atr_multiple: Number(form.stoploss?.atr_multiple),
        fixed_bps: Number(form.stoploss?.fixed_bps),
      },
      takeprofit: {
        risk_reward: Number(form.takeprofit?.risk_reward),
        trail_after_r: Number(form.takeprofit?.trail_after_r),
      },
    });
  };

  return (
    <section className="exchange-panel runtime-control-panel">
      <div className="panel-title">
        <h2>自动开单设置</h2>
        <span>{paperRunId ? "可保存" : "等待 PaperRun"}</span>
      </div>
      <div className="ticket-grid">
        <label>执行模式<select value={form.execution_mode} onChange={(event) => update("execution_mode", event.target.value)}>
          <option value="binance_simulation_first">币安模拟盘优先</option>
          <option value="paper_only">仅本地 Paper</option>
        </select></label>
        <label>杠杆<input type="number" value={form.max_leverage} onChange={(event) => update("max_leverage", event.target.value)} /></label>
        <label>单笔风险<input type="number" step="0.001" value={form.risk_per_trade} onChange={(event) => update("risk_per_trade", event.target.value)} /></label>
        <label>固定金额<input type="number" value={form.order_notional_usdt} onChange={(event) => update("order_notional_usdt", event.target.value)} /></label>
        <label>最多持仓<input type="number" value={form.max_open_positions} onChange={(event) => update("max_open_positions", event.target.value)} /></label>
        <label>扫描币种<input type="number" value={form.max_symbols} onChange={(event) => update("max_symbols", event.target.value)} /></label>
        <label>单币敞口<input type="number" step="0.01" value={form.max_symbol_exposure} onChange={(event) => update("max_symbol_exposure", event.target.value)} /></label>
        <label>总敞口<input type="number" step="0.01" value={form.max_total_exposure} onChange={(event) => update("max_total_exposure", event.target.value)} /></label>
        <label>日亏损<input type="number" step="0.01" value={form.daily_loss_limit} onChange={(event) => update("daily_loss_limit", event.target.value)} /></label>
        <label>周亏损<input type="number" step="0.01" value={form.weekly_loss_limit} onChange={(event) => update("weekly_loss_limit", event.target.value)} /></label>
        <label>硬停止<input type="number" step="0.01" value={form.hard_stop_drawdown_limit} onChange={(event) => update("hard_stop_drawdown_limit", event.target.value)} /></label>
        <label>策略车道<input value={form.strategy_lanes_text} onChange={(event) => update("strategy_lanes_text", event.target.value)} /></label>
        <label>ATR止损<input type="number" step="0.1" value={form.stoploss?.atr_multiple} onChange={(event) => updateNested("stoploss", "atr_multiple", event.target.value)} /></label>
        <label>固定bps<input type="number" value={form.stoploss?.fixed_bps} onChange={(event) => updateNested("stoploss", "fixed_bps", event.target.value)} /></label>
        <label>盈亏比<input type="number" step="0.1" value={form.takeprofit?.risk_reward} onChange={(event) => updateNested("takeprofit", "risk_reward", event.target.value)} /></label>
        <label>移动止损R<input type="number" step="0.1" value={form.takeprofit?.trail_after_r} onChange={(event) => updateNested("takeprofit", "trail_after_r", event.target.value)} /></label>
      </div>
      <div className="risk-tier-preview" aria-label="有效资产风险档位">
        {renderTierPreviewRows(form)}
        <div><span>全局强风控</span><strong>最多 {form.max_open_positions} 仓 · 总敞口 {formatPercent(form.max_total_exposure)} · 硬回撤 {formatPercent(form.hard_stop_drawdown_limit)}</strong></div>
        <p className="ticket-note">档位随上方杠杆/单币敞口滑块保存后重新计算，保存前展示的是当前生效值。</p>
      </div>
      <div className="ticket-type-tabs">
        <button type="button" className={form.llm_veto_enabled ? "active" : ""} onClick={() => update("llm_veto_enabled", !form.llm_veto_enabled)}>LLM Veto</button>
        <button type="button" className={form.market_intelligence_enabled ? "active" : ""} onClick={() => update("market_intelligence_enabled", !form.market_intelligence_enabled)}>Market Intelligence</button>
      </div>
      <button type="button" onClick={submit} disabled={!paperRunId}>保存自动开单设置</button>
    </section>
  );
}

export function OrderSyncPanel({ orderSync }) {
  const local = asArray(orderSync?.local_orders);
  const gateway = asArray(orderSync?.gateway_recent_orders);
  const refs = asArray(orderSync?.protection_order_refs);
  const unmatchedLocal = asArray(orderSync?.unmatched_local_orders);
  const unmatchedGateway = asArray(orderSync?.unmatched_gateway_orders);
  return (
    <section className="exchange-panel table-panel">
      <div className="panel-title">
        <h2>订单同步</h2>
        <span>本地 {local.length} / Binance {gateway.length}</span>
      </div>
      <div className="decision-summary">
        <span>模式：{orderSync?.execution_mode ?? "-"}</span>
        <span>持仓：{asArray(orderSync?.positions).length}</span>
        <span>保护单：{refs.length}</span>
        <span>已匹配：{orderSync?.matched_local_order_count ?? 0}</span>
        <span>本地未匹配：{unmatchedLocal.length}</span>
        <span>Binance 未匹配：{unmatchedGateway.length}</span>
      </div>
      <div className="compact-table">
        <div className="compact-table-row four header">
          <span>本地单</span>
          <span>交易对</span>
          <span>状态</span>
          <span>Binance</span>
        </div>
        {local.slice(0, 8).map((order) => (
          <div className="compact-table-row four" key={order.order_execution_id}>
            <span>{String(order.order_execution_id ?? "").slice(0, 8)}</span>
            <span>{order.symbol}</span>
            <span>{order.execution_status}</span>
            <span>{order.gateway_order_id ?? order.gateway_status ?? "-"}</span>
          </div>
        ))}
        {!local.length ? <div className="empty-list">暂无本地自动订单</div> : null}
      </div>
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
          <span>扣费后净边际（bps）：{trace.estimated_net_edge_bps ?? "—"}</span>
          <span>四腿往返成本（bps）：{trace.round_trip_cost_bps ?? "—"}</span>
          <span>资金费（bps）：{trace.funding_bps ?? "—"}</span>
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

// Mirrors services/execution/risk_tiers.py TIER_SCALE_RATIOS so the preview matches
// what the backend will actually persist once "保存自动开单设置" is clicked.
const TIER_SCALE_RATIOS = {
  core: { leverage: 1, fraction: 1 },
  vol_low: { leverage: 0.75, fraction: 0.8 },
  standard: { leverage: 0.5, fraction: 0.4 },
  vol_mid: { leverage: 0.4, fraction: 0.4 },
  vol_high: { leverage: 0.2, fraction: 0.2 },
};

const TIER_LABELS = {
  core: "核心币 BTC/ETH/SOL",
  standard: "其余固定 Top20",
  vol_low: "低波动分档",
  vol_mid: "中波动分档",
  vol_high: "高波动分档",
};

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "-";
}

function renderTierPreviewRows(form) {
  const tiers = form.asset_risk_tiers ?? {};
  const maxLeverage = Number(form.max_leverage) || 0;
  const maxExposure = Number(form.max_symbol_exposure) || 0;
  const order = ["core", "vol_low", "standard", "vol_mid", "vol_high"];
  const names = order.filter((name) => tiers[name]).concat(Object.keys(tiers).filter((name) => !order.includes(name)));
  return names.map((name) => {
    const ratio = TIER_SCALE_RATIOS[name] ?? { leverage: 1, fraction: 1 };
    const leverage = Math.max(1, Math.min(125, Math.round(maxLeverage * ratio.leverage * 100) / 100));
    const fraction = Math.max(0.01, Math.min(1, Math.round(maxExposure * ratio.fraction * 10000) / 10000));
    return (
      <div key={name}>
        <span>{TIER_LABELS[name] ?? name}</span>
        <strong>{leverage}x · 单币 {formatPercent(fraction)}</strong>
      </div>
    );
  });
}

function normalizeSettings(value) {
  const merged = {
    ...DEFAULT_AUTO_SETTINGS,
    ...(value ?? {}),
    stoploss: { ...DEFAULT_AUTO_SETTINGS.stoploss, ...(value?.stoploss ?? {}) },
    takeprofit: { ...DEFAULT_AUTO_SETTINGS.takeprofit, ...(value?.takeprofit ?? {}) },
  };
  return {
    ...merged,
    execution_mode: merged.execution_mode ?? DEFAULT_AUTO_SETTINGS.execution_mode,
    max_leverage: merged.max_leverage ?? DEFAULT_AUTO_SETTINGS.max_leverage,
    risk_per_trade: merged.risk_per_trade ?? DEFAULT_AUTO_SETTINGS.risk_per_trade,
    order_notional_usdt: merged.order_notional_usdt ?? "",
    max_open_positions: merged.max_open_positions ?? DEFAULT_AUTO_SETTINGS.max_open_positions,
    max_symbols: merged.max_symbols ?? DEFAULT_AUTO_SETTINGS.max_symbols,
    max_symbol_exposure: merged.max_symbol_exposure ?? DEFAULT_AUTO_SETTINGS.max_symbol_exposure,
    max_total_exposure: merged.max_total_exposure ?? DEFAULT_AUTO_SETTINGS.max_total_exposure,
    daily_loss_limit: merged.daily_loss_limit ?? DEFAULT_AUTO_SETTINGS.daily_loss_limit,
    weekly_loss_limit: merged.weekly_loss_limit ?? DEFAULT_AUTO_SETTINGS.weekly_loss_limit,
    hard_stop_drawdown_limit: merged.hard_stop_drawdown_limit ?? DEFAULT_AUTO_SETTINGS.hard_stop_drawdown_limit,
    strategy_lanes_text: asArray(merged.strategy_lanes ?? DEFAULT_AUTO_SETTINGS.strategy_lanes).join(","),
    stoploss: {
      ...merged.stoploss,
      atr_multiple: merged.stoploss?.atr_multiple ?? DEFAULT_AUTO_SETTINGS.stoploss.atr_multiple,
      fixed_bps: merged.stoploss?.fixed_bps ?? DEFAULT_AUTO_SETTINGS.stoploss.fixed_bps,
    },
    takeprofit: {
      ...merged.takeprofit,
      risk_reward: merged.takeprofit?.risk_reward ?? DEFAULT_AUTO_SETTINGS.takeprofit.risk_reward,
      trail_after_r: merged.takeprofit?.trail_after_r ?? DEFAULT_AUTO_SETTINGS.takeprofit.trail_after_r,
    },
    llm_veto_enabled: merged.llm_veto_enabled ?? DEFAULT_AUTO_SETTINGS.llm_veto_enabled,
    market_intelligence_enabled: merged.market_intelligence_enabled ?? DEFAULT_AUTO_SETTINGS.market_intelligence_enabled,
  };
}
