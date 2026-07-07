export function ValidationCenter() {
  return <PlatformPlaceholder eyebrow="Validation Layer" title="验证中心" items={["回测运行", "优化任务", "样本外验证", "压力测试报告"]} />;
}

export function PlatformPlaceholder({ eyebrow, title, items }) {
  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
      </header>
      <section className="exchange-panel">
        <div className="panel-title"><h2>入口</h2><span>{items.length}</span></div>
        <div className="signal-chips">
          {items.map((item) => <span key={item}>{item}</span>)}
        </div>
      </section>
    </main>
  );
}
