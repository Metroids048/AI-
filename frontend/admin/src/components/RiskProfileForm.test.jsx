import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RiskProfileForm } from "./RiskProfileForm";

describe("RiskProfileForm", () => {
  it("submits edited profile values", () => {
    const onSubmit = vi.fn();
    render(
      <RiskProfileForm
        initialProfile={{ risk_profile_id: "rp-1", max_leverage: 2.5 }}
        onSubmit={onSubmit}
        submitLabel="更新"
      />,
    );
    fireEvent.change(screen.getByDisplayValue("2.5"), { target: { value: "3" } });
    fireEvent.click(screen.getByText("更新"));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ max_leverage: 3 }));
  });
});
