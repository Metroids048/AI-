import { useState } from "react";

const DEFAULT_PROFILE = {
  single_trade_risk_limit: 0.01,
  max_symbol_exposure: 0.1,
  max_total_exposure: 0.5,
  max_open_positions: 3,
  max_leverage: 3,
  daily_loss_limit: 0.03,
  weekly_loss_limit: 0.08,
  drawdown_limit: 0.1,
  hard_stop_drawdown_limit: 0.2,
  consecutive_loss_limit: 4,
  api_failure_limit: 3,
  api_failure_window_minutes: 10,
  market_scope: "BTC/USDT perpetual",
};

const FIELDS = [
  ["single_trade_risk_limit", "单交易风险"],
  ["max_symbol_exposure", "单品种敞口"],
  ["max_total_exposure", "总敞口"],
  ["max_open_positions", "最大持仓数"],
  ["max_leverage", "最大杠杆"],
  ["daily_loss_limit", "日损限制"],
  ["weekly_loss_limit", "周损限制"],
  ["drawdown_limit", "回撤限制"],
  ["hard_stop_drawdown_limit", "硬停止回撤"],
  ["consecutive_loss_limit", "连续亏损次数"],
  ["api_failure_limit", "API 失败次数"],
  ["api_failure_window_minutes", "API 失败窗口(分)"],
  ["market_scope", "市场范围"],
];

export function profileToForm(profile) {
  return { ...DEFAULT_PROFILE, ...(profile ?? {}) };
}

export function RiskProfileForm({ initialProfile, onSubmit, onCancel, submitLabel = "保存" }) {
  const [form, setForm] = useState(() => profileToForm(initialProfile));

  const updateField = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    onSubmit(form);
  };

  return (
    <form className="exchange-panel form-panel" onSubmit={handleSubmit}>
      <div className="panel-title">
        <h2>{initialProfile?.risk_profile_id ? "编辑风控配置" : "新建风控配置"}</h2>
      </div>
      <div className="form-grid">
        {FIELDS.map(([key, label]) => (
          <label key={key}>
            <span>{label}</span>
            <input
              type={key === "market_scope" ? "text" : "number"}
              step={key.endsWith("_limit") || key.includes("exposure") || key.includes("leverage") ? "0.001" : "1"}
              value={form[key] ?? ""}
              onChange={(event) => updateField(
                key,
                key === "market_scope" ? event.target.value : Number(event.target.value),
              )}
            />
          </label>
        ))}
      </div>
      <div className="form-row">
        <button type="submit">{submitLabel}</button>
        {onCancel ? <button type="button" onClick={onCancel}>取消</button> : null}
      </div>
    </form>
  );
}
