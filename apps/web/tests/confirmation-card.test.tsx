import type { PendingAction } from "@campusvoice/shared-types";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationCard } from "@/components/actions/confirmation-card";

const highRiskAction: PendingAction = {
  id: "action-1",
  action: "delete_event",
  title: "删除机器学习考试",
  risk_level: "high",
  risk_reasons: ["删除数据难以撤销", "识别置信度较低"],
  payload: { title: "机器学习考试", target_id: "event-1" },
  status: "awaiting_second_confirmation",
};

describe("ConfirmationCard", () => {
  it("makes the second confirmation explicit for a high-risk action", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmationCard action={highRiskAction} onConfirm={onConfirm} onCancel={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("高风险操作");
    expect(screen.getByText("机器学习考试")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "再次确认并执行" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("does not enable confirmation while input is still missing", () => {
    render(
      <ConfirmationCard
        action={{ ...highRiskAction, status: "needs_input", missing_fields: ["start_at"] }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "确认操作" })).toBeDisabled();
  });
});
