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
        <h2>模拟盘验收</h2>
        <span>Testnet only</span>
      </div>
      <div className="acceptance-actions">
        <div>
          <strong>Top20 开平闭环</strong>
          <p>20 个币逐币开仓和平仓，目标 40 笔成交，最终零持仓零挂单。</p>
          <button type="button" onClick={onRunAcceptance}>运行 20 币验收</button>
        </div>
        <div>
          <strong>BTC 双腿 Carry</strong>
          <p>Spot 买入 + Futures 做空；任一腿失败立即补偿。</p>
          <button type="button" onClick={onRunCarry} disabled={!fundingSignal?.should_enter_paper}>
            运行双腿 Carry
          </button>
        </div>
      </div>
    </section>
  );
}
