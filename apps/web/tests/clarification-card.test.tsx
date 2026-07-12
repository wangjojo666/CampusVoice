import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ClarificationCard } from "@/components/voice/clarification-card";

describe("ClarificationCard target selection", () => {
  it("lets the user choose a descriptive candidate without typing its internal id", async () => {
    const user = userEvent.setup();
    const onSelectCandidate = vi.fn();
    render(
      <ClarificationCard
        question="请选择具体记录"
        candidates={[
          { id: "task-internal-1", label: "实验报告 · 2026-07-18 09:00" },
          { id: "task-internal-2", label: "实验报告 · 2026-07-19 09:00" },
        ]}
        onSubmit={vi.fn()}
        onSelectCandidate={onSelectCandidate}
      />,
    );

    await user.click(screen.getByRole("button", { name: "实验报告 · 2026-07-19 09:00" }));
    expect(onSelectCandidate).toHaveBeenCalledWith("task-internal-2");
    expect(screen.queryByText("task-internal-2")).not.toBeInTheDocument();
  });
});
