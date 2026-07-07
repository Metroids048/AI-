import { describe, expect, it } from "vitest";

import { buildRiskPriceLines } from "./MarketPanels";

describe("Market panels", () => {
  it("builds stoploss and takeprofit price lines from audited orders", () => {
    const lines = buildRiskPriceLines([
      {
        symbol: "BTC/USDT",
        stoploss_plan: { price: 59000 },
        takeprofit_plan: { price: 63000 },
      },
    ]);

    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({ price: 59000, title: "SL BTC/USDT" });
    expect(lines[1]).toMatchObject({ price: 63000, title: "TP BTC/USDT" });
  });
});
