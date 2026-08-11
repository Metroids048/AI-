import { describe, expect, it } from "vitest";

import { formatBoolean, formatEnum, formatFieldLabel } from "./format";

describe("presentation mapping", () => {
  it("maps protocol enums and field names without changing machine values", () => {
    expect(formatEnum("configured")).toBe("已配置");
    expect(formatEnum("grid_search")).toBe("网格搜索");
    expect(formatFieldLabel("execution_engine")).toBe("执行引擎");
    expect(formatBoolean(false)).toBe("否");
    expect(formatEnum("BTC")).toBe("BTC");
  });
});
