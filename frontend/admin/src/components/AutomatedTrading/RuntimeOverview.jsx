/**
 * RuntimeOverview — V2 engine status panel (plan section 14.2).
 *
 * Shows: Mode/Activation, Scheduler, Reconciliation,
 * Binance Testnet connectivity, latest Cycle, Entry blocked state.
 * Never shows fake "Online", mock balances, or ghost positions.
 */
import React from "react";

function StatusBadge({ value, good, bad }) {
  const isGood = good && good.includes(value);
  const isBad = bad && bad.includes(value);
  const color = isGood
    ? "text-green-400 bg-green-900/30"
    : isBad
    ? "text-red-400 bg-red-900/30"
    : "text-yellow-400 bg-yellow-900/30";
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono ${color}`}>
      {value ?? "—"}
    </span>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-white/5 last:border-0">
      <span className="text-xs text-gray-400 pr-4">{label}</span>
      <span className="text-xs text-right">{children}</span>
    </div>
  );
}

export function RuntimeOverview({ snapshot, error, lastSuccessAt }) {
  if (error && !snapshot) {
    return (
      <div className="rounded-lg bg-red-900/20 border border-red-500/30 p-4 text-sm text-red-300">
        未接通 — {error}
        {lastSuccessAt && (
          <span className="ml-2 text-gray-500 text-xs">
            (最后成功: {new Date(lastSuccessAt).toLocaleTimeString()})
          </span>
        )}
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="rounded-lg bg-gray-800/50 p-4 text-sm text-gray-400 animate-pulse">
        加载中…
      </div>
    );
  }

  const { engine, scheduler, reconciliation, exchange } = snapshot;

  return (
    <div className="rounded-lg bg-gray-800/60 border border-white/10 p-4 space-y-1">
      <h3 className="text-sm font-semibold text-white mb-3">V2 引擎状态</h3>

      <Row label="Mode">
        <StatusBadge
          value={engine?.mode ?? "—"}
          good={["BINANCE_TESTNET"]}
          bad={["LOCAL_PAPER"]}
        />
      </Row>

      <Row label="Activation">
        <StatusBadge
          value={engine?.activation ?? "—"}
          good={["ACTIVE"]}
          bad={["DISABLED"]}
        />
      </Row>

      <Row label="入场开关">
        {engine?.entry_enabled === false ? (
          <span className="text-red-400 text-xs font-mono">已禁止</span>
        ) : (
          <span className="text-green-400 text-xs font-mono">允许</span>
        )}
      </Row>

      <Row label="Mainnet">
        <span className="text-gray-500 text-xs">不支持</span>
      </Row>

      <Row label="Scheduler">
        <StatusBadge
          value={scheduler?.running ? "运行中" : "已停止"}
          good={["运行中"]}
          bad={["已停止"]}
        />
      </Row>

      <Row label="对账状态">
        <StatusBadge
          value={reconciliation?.status ?? "—"}
          good={["HEALTHY"]}
          bad={["UNAVAILABLE", "RECOVERY_REQUIRED"]}
        />
      </Row>

      <Row label="交易所连接">
        <StatusBadge
          value={exchange?.available ? "已连接" : "未接通"}
          good={["已连接"]}
          bad={["未接通"]}
        />
      </Row>

      {error && (
        <p className="text-xs text-yellow-400 pt-2">
          ⚠ 数据可能过期 (最后成功:{" "}
          {lastSuccessAt
            ? new Date(lastSuccessAt).toLocaleTimeString()
            : "从未"}
          )
        </p>
      )}
    </div>
  );
}
