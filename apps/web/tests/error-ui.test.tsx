import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/ui/error-state";

describe("ErrorState", () => {
  it("shows a friendly message and offers an explicit retry", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    render(
      <ErrorState
        title="保存未验证"
        message="数据库重新查询失败，未将本次操作标记为成功。"
        onRetry={retry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("保存未验证");
    expect(screen.getByRole("alert")).toHaveTextContent("未将本次操作标记为成功");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
