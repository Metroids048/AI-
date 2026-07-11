import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TradingRecordsWorkspace } from "./TradingRecordsWorkspace";

describe("TradingRecordsWorkspace", () => {
  it("shows one scrollable record surface and switches tabs", () => {
    render(
      <TradingRecordsWorkspace
        tabs={[
          { id: "positions", label: "持仓", content: <div>持仓内容</div> },
          { id: "orders", label: "订单", content: <div>订单内容</div> },
        ]}
      />,
    );

    expect(screen.getByText("持仓内容")).toBeTruthy();
    expect(screen.queryByText("订单内容")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "订单" }));
    expect(screen.getByText("订单内容")).toBeTruthy();
    expect(screen.queryByText("持仓内容")).toBeNull();
    expect(screen.getByTestId("records-scroll").className).toContain("records-workspace-scroll");
  });
});
