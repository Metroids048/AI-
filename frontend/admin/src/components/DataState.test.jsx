import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataState } from "./Common";

describe("DataState", () => {
  it("keeps loading distinct from an empty successful response", () => {
    const { rerender } = render(
      <DataState loading hasData={false} emptyLabel="暂无复盘记录">
        <div>数据</div>
      </DataState>,
    );

    expect(screen.getByText("正在读取数据…")).toBeTruthy();
    expect(screen.queryByText("暂无复盘记录")).toBeNull();

    rerender(
      <DataState loading={false} hasData={false} emptyLabel="暂无复盘记录">
        <div>数据</div>
      </DataState>,
    );

    expect(screen.getByText("暂无复盘记录")).toBeTruthy();
  });
});
