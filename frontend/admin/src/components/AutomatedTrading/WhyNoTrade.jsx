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

import { formatClock, formatDecisionReason, formatDecisionStage, formatSymbol } from "../../utils/format";

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
  return (
    <div className="why-no-trade-row">
      <div className="why-no-trade-row-head">
        <span className="why-no-trade-symbol">{formatSymbol(decision.symbol)}</span>
        <span className="why-no-trade-time">{formatClock(decision.evaluated_at)}</span>
      </div>
      <div className="why-no-trade-row-body">
        <span className="why-no-trade-label">停在</span>
        <span className="why-no-trade-stage" title={decision.terminal_stage ?? undefined}>
          {formatDecisionStage(decision.terminal_stage)}
        </span>
        <ReasonTag code={decision.reason_code} />
      </div>
      {decision.exchange_submitted ? (
        <div className="why-no-trade-submitted">已提交至交易所</div>
      ) : null}
    </div>
  );
}

export function WhyNoTrade({ decisions }) {
  if (!decisions || decisions.length === 0) {
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
    if (!current || decision.evaluated_at > current.evaluated_at) {
      bySymbol[decision.symbol] = decision;
    }
  }

  return (
    <div className="why-no-trade">
      <h3>为什么不开单？</h3>
      <div className="why-no-trade-list">
        {Object.values(bySymbol).map((decision) => (
          <DecisionRow key={decision.symbol} decision={decision} />
        ))}
      </div>
    </div>
  );
}
