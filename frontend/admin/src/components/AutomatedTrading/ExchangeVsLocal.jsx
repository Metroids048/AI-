/**
 * ExchangeVsLocal — side-by-side Binance truth vs local projection (plan 14.2).
 *
 * Left: Binance real positions.  Right: V2 local projection.
 * Discrepancies are highlighted in red.
 * Never mixes Local Paper into the Testnet column.
 */
import React from "react";

function PositionRow({ pos, label }) {
  return (
    <div className="rounded bg-gray-900/50 p-2 text-xs space-y-0.5">
      <div className="flex justify-between">
        <span className="font-mono text-white">{pos.symbol ?? label}</span>
        <span className="text-gray-400">{pos.qty ?? pos.quantity ?? "—"}</span>
      </div>
      {pos.entry_price && (
        <div className="text-gray-500">入场: {pos.entry_price}</div>
      )}
    </div>
  );
}

function Column({ title, source, available, positions, observedAt }) {
  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs font-semibold text-gray-300">{title}</h4>
        {available === false && (
          <span className="text-xs text-red-400 bg-red-900/30 px-1.5 py-0.5 rounded">
            未接通
          </span>
        )}
      </div>
      <p className="text-xs text-gray-600 mb-2 font-mono truncate">{source}</p>
      {observedAt && (
        <p className="text-xs text-gray-600 mb-2">
          {new Date(observedAt).toLocaleTimeString()}
        </p>
      )}
      {positions && positions.length > 0 ? (
        <div className="space-y-1">
          {positions.map((p, i) => (
            <PositionRow key={p.symbol ?? i} pos={p} label={`pos-${i}`} />
          ))}
        </div>
      ) : (
        <p className="text-xs text-gray-600 italic">
          {available === false ? "数据不可用" : "无持仓"}
        </p>
      )}
    </div>
  );
}

export function ExchangeVsLocal({ positionsData }) {
  if (!positionsData) {
    return (
      <div className="rounded-lg bg-gray-800/50 border border-white/10 p-4">
        <h3 className="text-sm font-semibold text-white mb-2">
          交易所 vs 本地投影
        </h3>
        <p className="text-xs text-gray-500 animate-pulse">加载中…</p>
      </div>
    );
  }

  const { exchange, local_projection } = positionsData;

  const exchangeQty = (exchange?.positions ?? []).length;
  const localQty = (local_projection?.positions ?? []).length;
  const hasMismatch = exchange?.available && exchangeQty !== localQty;

  return (
    <div
      className={`rounded-lg bg-gray-800/60 border p-4 ${
        hasMismatch ? "border-red-500/50" : "border-white/10"
      }`}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">
          交易所 vs 本地投影
        </h3>
        {hasMismatch && (
          <span className="text-xs text-red-400 bg-red-900/30 px-2 py-0.5 rounded">
            ⚠ 不一致 ({exchangeQty} vs {localQty})
          </span>
        )}
      </div>

      <div className="flex gap-4">
        <Column
          title="Binance Testnet (真相)"
          source={exchange?.source ?? "BINANCE_TESTNET"}
          available={exchange?.available}
          positions={exchange?.positions}
          observedAt={exchange?.observed_at}
        />

        <div className="w-px bg-white/10" />

        <Column
          title="本地投影 (V2)"
          source={local_projection?.source ?? "V2_LOCAL_PROJECTION"}
          available={true}
          positions={local_projection?.positions}
          observedAt={local_projection?.observed_at}
        />
      </div>
    </div>
  );
}
