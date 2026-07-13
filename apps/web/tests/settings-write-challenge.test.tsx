import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listHotwords: vi.fn(),
  beginRemove: vi.fn(),
  finishRemove: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    settings: {
      get: mocks.getSettings,
      update: vi.fn(),
    },
    hotwords: {
      list: mocks.listHotwords,
      create: vi.fn(),
      beginRemove: mocks.beginRemove,
      finishRemove: mocks.finishRemove,
    },
  },
}));

describe("SettingsPage write challenges", () => {
  beforeEach(() => {
    mocks.getSettings.mockReset().mockResolvedValue({
      major: "人工智能",
      grade: "2024",
      current_courses: [],
      teacher_names: [],
      default_reminder_minutes: 30,
      timezone: "Asia/Shanghai",
      asr_provider: "funasr",
      asr_model: "paraformer-zh-streaming",
      asr_device: "cpu",
    });
    mocks.listHotwords.mockReset().mockResolvedValue({
      items: [
        {
          id: "hotword-1",
          value: "机器学习",
          category: "ai_term",
          source: "user",
          active: true,
          created_at: "2026-07-12T12:00:00Z",
        },
      ],
      total: 1,
    });
    mocks.beginRemove.mockReset().mockResolvedValue({
      challenge: "server-stage-two",
      stage: 2,
      required_stages: 2,
      expires_at: "2026-07-12T12:02:00Z",
    });
    mocks.finishRemove.mockReset().mockResolvedValue({
      success: true,
      action: "delete_hotword",
      record_id: "hotword-1",
      verified_fields: { absent: true },
      side_effects: [],
      message: "热词已删除",
    });
  });

  it("does not delete on the first click and requires a second independent click", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(mocks.listHotwords).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "删除热词机器学习" }));

    await waitFor(() => expect(mocks.beginRemove).toHaveBeenCalledWith("hotword-1"));
    expect(mocks.finishRemove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "删除热词机器学习" })).toBeInTheDocument();
    expect(await screen.findByRole("dialog")).toHaveTextContent("第二次确认");

    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    await waitFor(() =>
      expect(mocks.finishRemove).toHaveBeenCalledWith("hotword-1", "server-stage-two"),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除热词机器学习" })).not.toBeInTheDocument();
  });
});
