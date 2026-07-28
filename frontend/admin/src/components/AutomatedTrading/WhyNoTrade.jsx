/**
 * WhyNoTrade — decision funnel inspector (plan section 14.2).
 *
 * Shows per-symbol: last decision bar time, terminal stage, reason code,
 * indicator values, gate result, AI invocation status.
 */
import React from "react";

function ReasonTag({ code }) {
  const BLOCKED = new Set([
    "NO_ENTRY_SIGNAL", "RECONCILIATION_UNAVAILABLE", "RECOVERY_REQUIRED",
    "ENTRY_GATE_BLOCKED", "EXCHANGE_UNAVAILABLE", "PRICE_DRIFT_EXCEEDED",
    "CANDIDATE_EXPIRED", "DAILY_TRADE_LIMIT_REACHED", "SYMBOL_COOLDOWN_ACTIVE",
  ]);
  const blocked = BLOCKED.has(code);
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs font-mono ${
        blocked ? "text-orange-300 bg-orange-900/30" : "text-gray-400 bg-gray-800"
      }`}
    >
      {code ?? "—"}
    </span>
  );
}

function DecisionRow({ decision }) {
  const evaluatedAt = decision.evaluated_at
    ? new Date(decision.evaluated_at).toLocaleTimeString()
    : "—";
  return (
    <div className="rounded bg-gray-900/60 border border-white/5 p-3 space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">{decision.symbol}</span>
        <span className="text-xs text-gray-500">{evaluatedAt}</span>
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-gray-400">阶段:</span>
        <span className="text-xs font-mono text-blue-300">{decision.terminal_stage ?? "—"}</span>
        <span className="text-xs text-gray-400">原因:</span>
        <ReasonTag code={decision.reason_code} />
      </div>
      {decision.exchange_submitted && (
        <div className="text-xs text-green-400">✓ 已提交至交易所</div>
      )}
    </div>
  );
}

export function WhyNoTrade({ decisions }) {
  if (!decisions || decisions.length === 0) {
    return (
      <div className="rounded-lg bg-gray-800/50 border border-white/10 p-4">
        <h3 className="text-sm font-semibold text-white mb-2">为什么不开单?</h3>
        <p className="text-xs text-gray-500">暂无决策记录</p>
      </div>
    );
  }

  // Group by symbol, keep only the latest per symbol.
  const bySymbol = {};
  for (const d of decisions) {
    if (!bySymbol[d.symbol] || d.evaluated_at > bySymbol[d.symbol].evaluated_at) {
      bySymbol[d.symbol] = d;
    }
  }

  return (
    <div className="rounded-lg bg-gray-800/60 border border-white/10 p-4">
      <h3 className="text-sm font-semibold text-white mb-3">为什么不开单?</h3>
      <div className="space-y-2">
        {Object.values(bySymbol).map((d) => (
          <DecisionRow key={d.symbol} decision={d} />
        ))}
      </div>
    </div>
  );
}
