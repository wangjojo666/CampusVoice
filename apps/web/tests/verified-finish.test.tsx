import type { VerificationResult } from "@campusvoice/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ExecutionResult } from "@/components/actions/execution-result";
import { createVerifiedFinishEvent } from "@/lib/verified-finish";

const success: VerificationResult = {
  success: true,
  action: "create_task",
  record_id: "task-1",
  verified_fields: { title: true },
  side_effects: [],
  message: "待办写入后已重新查询验证",
};

afterEach(cleanup);

describe("verified finish feedback", () => {
  it("does not replay a historical success unless a fresh verification event is supplied", () => {
    render(<ExecutionResult result={success} />);

    expect(screen.queryByText("这一步稳稳落地了")).not.toBeInTheDocument();
  });

  it("creates feedback only for a fresh successful verification result", () => {
    const failedEvent = createVerifiedFinishEvent(
      {
        ...success,
        success: false,
        record_id: "task-partial",
        verified_fields: { title: true },
        message: "数据库写入完成但最终复验失败",
      },
      "execute",
    );
    expect(failedEvent).toBeNull();

    const event = createVerifiedFinishEvent(success, "execute");
    const { container } = render(<ExecutionResult result={success} verifiedFinish={event} />);

    expect(screen.getByText("这一步稳稳落地了")).toBeInTheDocument();
    expect(screen.getByText("已通过数据库重新查询核对。")).toBeInTheDocument();
    expect(container.querySelector(".verified-finish")).toBeInTheDocument();
    expect(container.querySelector(".verified-finish svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("uses accurate wording for a verified undo", () => {
    const undoEvent = createVerifiedFinishEvent(
      { ...success, action: "undo_create_task", message: "撤销后已重新查询验证" },
      "undo",
    );
    render(<ExecutionResult result={success} verifiedFinish={undoEvent} />);

    expect(screen.getByText("已撤回并通过数据库验证。")).toBeInTheDocument();
    expect(screen.queryByText("这一步稳稳落地了")).not.toBeInTheDocument();
  });
});
