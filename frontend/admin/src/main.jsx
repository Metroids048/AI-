import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";

const overviewCards = [
  { label: "Research Lane", value: "Carry / BTC Perp", tone: "bg-ink text-mist" },
  { label: "Validation Gate", value: "Sharpe > 1.0", tone: "bg-ember text-white" },
  { label: "Risk Posture", value: "Stoploss Required", tone: "bg-sage text-ink" },
  { label: "Ops Focus", value: "Paper Admission First", tone: "bg-white text-ink" },
];

const boardSections = [
  {
    title: "Strategy Board",
    eyebrow: "Strategy Layer",
    rows: [
      ["Ideas imported from research", "WorldQuant local alpha scan + manual notes"],
      ["Lifecycle spine", "Idea -> Draft -> Strategy -> Version"],
      ["Current state", "Persisted and covered by API/tests"],
    ],
  },
  {
    title: "Backtest Board",
    eyebrow: "Validation Layer",
    rows: [
      ["Primary lane", "Funding-rate / basis carry"],
      ["Execution model", "Persisted market data -> carry application service"],
      ["Quality checks", "Gap check, freshness check, gate decision"],
    ],
  },
  {
    title: "Risk Event Panel",
    eyebrow: "Execution Gate",
    rows: [
      ["Hard rejects", "No stoploss, stale data, veto=true, validation fail"],
      ["Live status", "High-severity risk event blocks order execution"],
      ["Persistence", "Risk profiles + risk events + order audit trail"],
    ],
  },
  {
    title: "Review Panel",
    eyebrow: "Review Layer",
    rows: [
      ["Daily report", "Generated from persisted failures"],
      ["Writeback", "Failure reasons and iteration history appended to strategy"],
      ["Next step", "Expand analytics and notification hooks"],
    ],
  },
];

function StatCard({ label, value, tone }) {
  return (
    <article className={`rounded-[28px] p-5 shadow-card ${tone}`}>
      <p className="text-xs uppercase tracking-[0.28em] opacity-70">{label}</p>
      <h2 className="mt-3 font-display text-2xl">{value}</h2>
    </article>
  );
}

function BoardSection({ eyebrow, title, rows }) {
  return (
    <section className="rounded-[32px] border border-white/60 bg-white/80 p-6 shadow-card backdrop-blur">
      <p className="text-xs uppercase tracking-[0.3em] text-steel">{eyebrow}</p>
      <h3 className="mt-3 font-display text-3xl text-ink">{title}</h3>
      <div className="mt-6 space-y-4">
        {rows.map(([label, value]) => (
          <div
            key={label}
            className="flex flex-col gap-2 rounded-2xl border border-stone-200/80 bg-mist/60 p-4 md:flex-row md:items-center md:justify-between"
          >
            <span className="text-sm font-semibold uppercase tracking-[0.14em] text-steel">
              {label}
            </span>
            <span className="text-sm text-ink">{value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function App() {
  return (
    <main className="min-h-screen px-5 py-8 text-ink md:px-10 lg:px-14">
      <div className="mx-auto max-w-7xl">
        <header className="grid gap-8 lg:grid-cols-[1.3fr_0.7fr] lg:items-end">
          <div>
            <p className="text-xs uppercase tracking-[0.36em] text-steel">
              AI Quant Research Platform
            </p>
            <h1 className="mt-4 max-w-4xl font-display text-5xl leading-tight md:text-6xl">
              Research, validation, risk gating, and review now share one control deck.
            </h1>
            <p className="mt-5 max-w-3xl text-base leading-7 text-steel">
              This admin shell mirrors the platform rule: strategy research is only allowed
              to progress when validation evidence, execution gates, and review writeback stay
              attached to the same lifecycle.
            </p>
          </div>

          <aside className="rounded-[36px] border border-white/60 bg-white/75 p-6 shadow-card backdrop-blur">
            <p className="text-xs uppercase tracking-[0.3em] text-steel">Phase Focus</p>
            <div className="mt-4 space-y-4">
              <div>
                <p className="text-sm uppercase tracking-[0.18em] text-steel">Current milestone</p>
                <p className="mt-1 text-xl font-semibold text-ink">
                  Research loop auditability before live autonomy
                </p>
              </div>
              <div>
                <p className="text-sm uppercase tracking-[0.18em] text-steel">North star</p>
                <p className="mt-1 text-sm leading-6 text-ink">
                  Every strategy should be explainable from research intake to paper admission
                  to failure writeback.
                </p>
              </div>
            </div>
          </aside>
        </header>

        <section className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {overviewCards.map((card) => (
            <StatCard key={card.label} {...card} />
          ))}
        </section>

        <section className="mt-8 grid gap-6 xl:grid-cols-2">
          {boardSections.map((section) => (
            <BoardSection key={section.title} {...section} />
          ))}
        </section>
      </div>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
