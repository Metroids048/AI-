/**
 * WhyNoTrade — decision funnel inspector (plan section 14.2).
 *
 * Shows per-symbol: last decision bar time, terminal stage, reason code.
 *
 * Presentation rules:
 * - Stage and reason codes are operator-facing, so they render through the shared
 *   Chinese mapping in utils/format. Raw enum codes are kept as a title attribute
 *   so the machine-readable value stays available for debugging.
 * - Colors come from the light workbench palette in styles.css. Do not reintroduce
 *   dark-theme utility classes here; text-white on this light card is invisible.
 */
import React from "react";

import { formatClock, formatDecisionReason, formatDecisionStage, formatNoTradeSummary, formatNumber, formatSymbol } from "../../utils/format";

/** Reason codes that mean "the runtime refused to act", as opposed to "no signal yet". */
const BLOCKED_REASONS = new Set([
  "NO_ENTRY_SIGNAL",
  "RECONCILIATION_UNAVAILABLE",
  "RECOVERY_REQUIRED",
  "ENTRY_GATE_BLOCKED",
  "ENTRY_KILL_SWITCH_ACTIVE",
  "EXCHANGE_UNAVAILABLE",
  "PRICE_DRIFT_EXCEEDED",
  "CANDIDATE_EXPIRED",
  "DAILY_TRADE_LIMIT_REACHED",
  "SYMBOL_COOLDOWN_ACTIVE",
  "RISK_LIMIT_EXCEEDED",
  "NET_EDGE_AFTER_COST_NEGATIVE",
  "NO_TRADE_COST_INEFFICIENT",
  "VETOED",
]);

function ReasonTag({ code }) {
  const raw = code ? String(code) : "";
  const blocked = BLOCKED_REASONS.has(raw.toUpperCase());
  return (
    <span className={`why-no-trade-reason ${blocked ? "is-blocked" : ""}`} title={raw || undefined}>
      {formatDecisionReason(code)}
    </span>
  );
}

function DecisionRow({ decision }) {
  const evaluatedAt = decision.last_decision_at ?? decision.evaluated_at;
  const terminalStage = decision.entry_gate_result ?? decision.terminal_stage;
  const terminalReason = decision.terminal_reason ?? decision.reason_code;
  const exchangeSubmitted = decision.entry_submitted ?? decision.exchange_submitted;
  return (
    <div className="why-no-trade-row">
      <div className="why-no-trade-row-head">
        <span className="why-no-trade-symbol">{formatSymbol(decision.symbol)}</span>
        <span className="why-no-trade-time">{formatClock(evaluatedAt)}</span>
      </div>
      <div className="why-no-trade-row-body">
        <span className="why-no-trade-label">停在</span>
        <span className="why-no-trade-stage" title={terminalStage ?? undefined}>
          {formatDecisionStage(terminalStage)}
        </span>
        <ReasonTag code={terminalReason} />
      </div>
      {exchangeSubmitted ? (
        <div className="why-no-trade-submitted">已提交至交易所</div>
      ) : null}
    </div>
  );
}

function summaryCopy(summary) {
  const dominant = summary?.entry_runtime?.reason ?? summary?.decisions?.dominant_reason;
  const count = dominant ? Number(summary?.decisions?.reason_counts?.[dominant] ?? 0) : 0;
  return formatNoTradeSummary(summary?.summary_code, dominant, count);
}

function SummaryCard({ summary }) {
  if (!summary) return null;
  const hours = summary.hours_since_last_entry;
  const hasEntryAge = hours !== null && hours !== undefined && Number.isFinite(Number(hours));
  const decisions = summary.decisions ?? {};
  const reasonCounts = decisions.reason_counts ?? {};
  const rows = Object.entries(reasonCounts)
    .filter(([code]) => code !== "DUPLICATE_DECISION")
    .sort(([, left], [, right]) => Number(right) - Number(left))
    .slice(0, 5);
  const protection = summary.protection ?? {};
  const reconciliation = summary.reconciliation ?? {};
  const throughput = summary.throughput ?? {};
  const historical = summary.historical_window ?? {};
  const operationalCounts = historical.operational_block_counts ?? throughput.operational_block_counts ?? throughput.blocker_counts ?? {};
  const strategyCounts = historical.strategy_filter_counts ?? throughput.strategy_filter_counts ?? {};
  const systemCounts = historical.system_failure_counts ?? throughput.system_failure_counts ?? {};
  const operationalTotal = Object.values(operationalCounts).reduce((sum, value) => sum + Number(value), 0);
  const blockerRows = Object.entries(operationalCounts)
    .sort(([, left], [, right]) => Number(right) - Number(left))
    .slice(0, 5);
  const strategyRows = Object.entries(strategyCounts)
    .sort(([, left], [, right]) => Number(right) - Number(left))
    .slice(0, 5);
  const systemRows = Object.entries(systemCounts)
    .sort(([, left], [, right]) => Number(right) - Number(left))
    .slice(0, 5);
  const current = summary.current_status ?? {};
  const activeBlocker = current.active_blocker;
  return (
    <div className="why-no-trade-summary">
      <p className="why-no-trade-status">{summaryCopy(summary)}</p>
      <div className="why-no-trade-current-status">
        <strong>当前状态</strong>
        <span>{current.runtime_health === "healthy" ? "正常等待交易机会" : "运行状态需要关注"}</span>
        <span>当前 Active Blocker：{activeBlocker ? formatDecisionReason(activeBlocker) : "无"}</span>
      </div>
      {protection.p0_unprotected ? (
        <p className="why-no-trade-p0" role="alert">
          P0：当前权威持仓存在未确认保护，自动开仓已阻断。受管保护 {protection.protected_positions ?? 0}，未保护 {protection.unprotected_positions ?? 0}。
        </p>
      ) : null}
      {reconciliation.status && reconciliation.status !== "healthy" ? (
        <p className="why-no-trade-reconciliation" title={reconciliation.discrepancy_codes?.join(", ") || undefined}>
          对账：{reconciliation.status} · 影响 {reconciliation.affected_symbols?.join("、") || "未指定"}
          {reconciliation.recovery_action ? ` · ${reconciliation.recovery_action}` : ""}
        </p>
      ) : null}
      <p className="why-no-trade-age">
        {hasEntryAge ? `已 ${formatNumber(hours, 1)} 小时未产生新开仓` : "当前查询窗口内没有新开仓成交记录"}
      </p>
      {throughput.current_open_positions !== null && throughput.current_open_positions !== undefined && throughput.effective_max_open_positions ? (
        <p className="why-no-trade-throughput" role={throughput.at_capacity ? "status" : undefined}>
          {throughput.at_capacity
            ? `当前已达到 Testnet Canary 持仓上限：${throughput.current_open_positions} / ${throughput.effective_max_open_positions}，因此暂停新增仓位，等待现有持仓退出`
            : `Testnet Canary 当前持仓：${throughput.current_open_positions} / ${throughput.effective_max_open_positions}，仍可新增 ${throughput.remaining_slots ?? 0} 个仓位`}
        </p>
      ) : null}
      {strategyRows.length ? (
        <div className="why-no-trade-blockers">
          <span>近 {historical.window_hours ?? summary.window_hours ?? 3} 小时策略过滤</span>
          {strategyRows.map(([code, count]) => <span key={code}>{formatDecisionReason(code)} {count} 次</span>)}
        </div>
      ) : null}
      {blockerRows.length ? (
        <div className="why-no-trade-blockers">
          <span>近 {historical.window_hours ?? summary.window_hours ?? 3} 小时执行阻断</span>
          {blockerRows.map(([code, count]) => (
            <span key={code}>
              {formatDecisionReason(code)} {count} 次
              {operationalTotal ? `（${formatNumber(Number(count) / operationalTotal * 100, 1)}%）` : ""}
            </span>
          ))}
        </div>
      ) : null}
      {systemRows.length ? (
        <div className="why-no-trade-blockers">
          <span>近 {historical.window_hours ?? summary.window_hours ?? 3} 小时系统故障</span>
          {systemRows.map(([code, count]) => <span key={code}>{formatDecisionReason(code)} {count} 次</span>)}
        </div>
      ) : null}
      <div className="why-no-trade-stats">
        <span>有效策略判断 {decisions.effective ?? 0} 次</span>
        {rows.map(([code, count]) => <span key={code}>{formatDecisionReason(code)} {count} 次</span>)}
        <span>重复轮询 {decisions.duplicate ?? 0} 次</span>
      </div>
      {summary.funnel ? (
        <div className="why-no-trade-stats">
          <span>基础信号 {summary.funnel.base_signal_detected ?? summary.funnel.signal_generated ?? 0}</span>
          <span>多周期确认 {summary.funnel.mtf_confirmation_passed ?? 0}</span>
          <span>Candidate {summary.funnel.candidate_created ?? 0}</span>
          <span>R2 拒绝 {summary.funnel.r2_cost_rejected ?? 0}</span>
          <span>Intent {summary.funnel.intent_created ?? 0}</span>
          <span>提交订单 {summary.funnel.exchange_submitted ?? 0}</span>
          <span>成交订单 {summary.funnel.filled_orders ?? summary.funnel.exchange_filled ?? 0}</span>
          <span>Fill Event {summary.funnel.fill_events ?? 0}</span>
          <span>保护事件 {summary.funnel.protection_events ?? summary.funnel.protection_confirmed ?? 0}</span>
          <span>当前保护仓位 {protection.protected_positions ?? 0}</span>
        </div>
      ) : null}
    </div>
  );
}

export function WhyNoTrade({ decisions, noTradeSummary }) {
  if (!noTradeSummary && (!decisions || decisions.length === 0)) {
    return (
      <div className="why-no-trade">
        <h3>为什么不开单？</h3>
        <p className="why-no-trade-empty">暂无决策记录</p>
      </div>
    );
  }

  // Group by symbol, keep only the latest per symbol.
  const bySymbol = {};
  for (const decision of decisions) {
    const current = bySymbol[decision.symbol];
    const decisionTime = decision.last_decision_at ?? decision.evaluated_at ?? "";
    const currentTime = current?.last_decision_at ?? current?.evaluated_at ?? "";
    if (!current || decisionTime > currentTime) {
      bySymbol[decision.symbol] = decision;
    }
  }

  return (
    <div className="why-no-trade">
      <h3>为什么不开单？</h3>
      <SummaryCard summary={noTradeSummary} />
      {!decisions || decisions.length === 0 ? null : (
      <div className="why-no-trade-list">
        {Object.values(bySymbol).map((decision) => (
          <DecisionRow key={decision.symbol} decision={decision} />
        ))}
      </div>
      )}
    </div>
  );
}
