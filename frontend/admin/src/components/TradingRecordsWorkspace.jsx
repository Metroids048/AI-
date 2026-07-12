import { useMemo, useState } from "react";

export function TradingRecordsWorkspace({ tabs }) {
  const availableTabs = useMemo(() => (tabs ?? []).filter(Boolean), [tabs]);
  const [activeId, setActiveId] = useState(() => availableTabs[0]?.id ?? "");
  const active = availableTabs.find((tab) => tab.id === activeId) ?? availableTabs[0];
  return (
    <section className="exchange-panel records-workspace">
      <div className="records-workspace-tabs" role="tablist" aria-label="交易记录与自动化">
        {availableTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active?.id}
            className={tab.id === active?.id ? "active" : ""}
            onClick={() => setActiveId(tab.id)}
          >
            {tab.label}
            {tab.count !== undefined ? <span>{tab.count}</span> : null}
          </button>
        ))}
      </div>
      <div className="records-workspace-scroll" data-testid="records-scroll">
        {active?.content ?? <div className="empty-list">暂无内容</div>}
      </div>
    </section>
  );
}

export function ExecutionAcceptancePanel({ onRunAcceptance, onRunCarry, fundingSignal }) {
  return (
    <section className="exchange-panel acceptance-panel">
      <div className="panel-title">
        <h2>交易连通性验收</h2>
        <span>仅币安模拟盘</span>
      </div>
      <div className="acceptance-actions">
        <div>
          <strong>固定 20 币连通性验收</strong>
          <p>逐币开仓后立即平仓，目标是验证下单、平仓和对账链路；这些成交不计入策略收益。</p>
          <button type="button" onClick={onRunAcceptance}>执行连通性验收</button>
        </div>
        <div>
          <strong>BTC 双腿资金费率对冲</strong>
          <p>现货买入、永续做空；任一腿失败会立即补偿并回到零净敞口。</p>
          <button type="button" onClick={onRunCarry} disabled={!fundingSignal?.should_enter_paper}>
            执行双腿对冲
          </button>
        </div>
      </div>
    </section>
  );
}
