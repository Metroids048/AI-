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
  return (
    <div className="why-no-trade-summary">
      <p className="why-no-trade-status">{summaryCopy(summary)}</p>
      <p className="why-no-trade-age">
        {hasEntryAge ? `已 ${formatNumber(hours, 1)} 小时未产生新开仓` : "当前查询窗口内没有新开仓成交记录"}
      </p>
      <div className="why-no-trade-stats">
        <span>有效策略判断 {decisions.effective ?? 0} 次</span>
        {rows.map(([code, count]) => <span key={code}>{formatDecisionReason(code)} {count} 次</span>)}
        <span>重复轮询 {decisions.duplicate ?? 0} 次</span>
      </div>
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
