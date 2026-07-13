import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: { radar: { list: mocks.list } },
}));

import { CampusRadar } from "@/components/radar/campus-radar";
import { ApiError } from "@/lib/api-client";

const baseCard = {
  series_id: "nss_1",
  title: "2026 人工智能专业考试安排",
  from_revision: 1,
  to_revision: 2,
  change_count: 2,
  affected_tasks: 2,
  affected_events: 1,
  needs_review: false,
  message: "通知内容已更新。",
  created_at: "2026-07-13T00:00:00Z",
};

afterEach(cleanup);

describe("CampusRadar", () => {
  beforeEach(() => {
    mocks.list.mockReset();
  });

  it("keeps the legacy card contract as a traceable version-change link", async () => {
    mocks.list.mockResolvedValue({
      total: 1,
      items: [{ ...baseCard, change_set_id: "ncs_1", needs_review: true }],
    });
    render(<CampusRadar />);

    expect(await screen.findByText("2026 人工智能专业考试安排")).toBeInTheDocument();
    expect(screen.getByText("需审核")).toBeInTheDocument();
    expect(screen.getByText(/v1 → v2/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /通知版本变化：2026 人工智能专业考试安排/ }),
    ).toHaveAttribute("href", "/radar/ncs_1");
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith(4));
  });

  it("explains every card type and only opens diff detail for a real version change", async () => {
    mocks.list.mockResolvedValue({
      total: 4,
      items: [
        {
          ...baseCard,
          card_type: "new_notice",
          change_set_id: null,
          document_id: "doc-new",
          title: "新生报到通知",
          affected_tasks: 0,
          affected_events: 0,
        },
        {
          ...baseCard,
          card_type: "version_change",
          change_set_id: "ncs-change",
          title: "考试安排更新",
        },
        {
          ...baseCard,
          card_type: "upcoming_deadline",
          change_set_id: null,
          document_id: "doc-deadline",
          title: "奖学金申请",
          deadline_at: "2026-07-15T16:00:00+08:00",
          affected_tasks: 0,
          affected_events: 0,
        },
        {
          ...baseCard,
          card_type: "needs_review",
          change_set_id: null,
          document_id: "doc-review",
          title: "暂定补考安排",
          needs_review: true,
          applicability: "needs_review",
          applicability_reason: "年级信息不完整",
          affected_tasks: 0,
          affected_events: 0,
        },
      ],
    });
    render(<CampusRadar />);

    expect(await screen.findByText("与我有关的新通知")).toBeInTheDocument();
    expect(screen.getAllByText("通知版本变化")).toHaveLength(1);
    expect(screen.getByText("即将截止")).toBeInTheDocument();
    expect(screen.getByText("需人工确认")).toBeInTheDocument();
    expect(screen.getByText(/截止：/)).toBeInTheDocument();
    expect(screen.getByText("适用说明：年级信息不完整")).toBeInTheDocument();
    expect(screen.getByText("影响安排：1 个日程、2 个待办")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /通知版本变化：考试安排更新/ })).toHaveAttribute(
      "href",
      "/radar/ncs-change",
    );
    for (const name of [
      /与我有关的新通知：新生报到通知/,
      /即将截止：奖学金申请/,
      /需人工确认：暂定补考安排/,
    ]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute(
        "href",
        "/notices#notice-version-library",
      );
    }
  });

  it("shows a useful empty state", async () => {
    mocks.list.mockResolvedValue({ total: 0, items: [] });
    render(<CampusRadar />);

    expect(await screen.findByText(/暂无需要处理的版本变化/)).toBeInTheDocument();
  });

  it("shows an explicit error and retries the request", async () => {
    const user = userEvent.setup();
    mocks.list
      .mockRejectedValueOnce(new ApiError("雷达服务暂时不可用", { status: 503 }))
      .mockResolvedValueOnce({ total: 0, items: [] });
    render(<CampusRadar />);

    expect(await screen.findByText("雷达服务暂时不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText(/暂无需要处理的版本变化/)).toBeInTheDocument();
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });
});
