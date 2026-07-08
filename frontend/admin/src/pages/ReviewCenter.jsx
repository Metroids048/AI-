import { useQuery } from "@tanstack/react-query";

import { request } from "../api/client";
import { FeedPanel } from "../components/OpsPanels";
import { asArray } from "../utils/format";

export function ReviewCenter() {
  const reviews = useQuery({
    queryKey: ["review-reports"],
    queryFn: () => request("/api/v1/reviews"),
    refetchInterval: 30000,
  });

  return (
    <main className="app-shell page-shell">
      <header className="page-header">
        <p className="eyebrow">Review Layer</p>
        <h1>复盘中心</h1>
      </header>
      <section className="ops-grid">
        <FeedPanel
          title="复盘报告"
          items={reviews.data?.items}
          renderItem={(item) => (
            <>
              <strong>{item.report_date}</strong>
              <span>{item.report_status} / {asArray(item.failure_patterns).length} failure patterns</span>
            </>
          )}
        />
      </section>
    </main>
  );
}
