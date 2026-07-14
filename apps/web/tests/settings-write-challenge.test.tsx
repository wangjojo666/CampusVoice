import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";
import { getCurrentUserSettings } from "@/lib/user-settings";

const mocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  listHotwords: vi.fn(),
  beginRemove: vi.fn(),
  finishRemove: vi.fn(),
  updateSettings: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    settings: {
      get: mocks.getSettings,
      update: mocks.updateSettings,
    },
    hotwords: {
      list: mocks.listHotwords,
      create: vi.fn(),
      beginRemove: mocks.beginRemove,
      finishRemove: mocks.finishRemove,
    },
  },
}));

afterEach(cleanup);

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
    mocks.updateSettings.mockReset().mockImplementation(async (settings) => ({
      ...settings,
      asr_provider: "funasr",
      asr_model: "paraformer-zh-streaming",
      asr_device: "cpu",
    }));
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

  it("keeps ASR deployment state collapsed and excludes it from save payloads", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(mocks.getSettings).toHaveBeenCalledTimes(1));

    const technicalState = screen.getByText("技术状态").closest("details");
    expect(technicalState).not.toHaveAttribute("open");
    expect(screen.queryByRole("combobox", { name: "识别提供方" })).not.toBeInTheDocument();
    expect(screen.getByText("这些设置会影响什么")).toBeInTheDocument();
    expect(screen.getAllByText("funasr").length).toBeGreaterThan(0);
    expect(screen.getByText("paraformer-zh-streaming")).toBeInTheDocument();
    expect(screen.getByText("cpu")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledTimes(1));
    expect(mocks.updateSettings).toHaveBeenCalledWith({
      major: "人工智能",
      grade: "2024",
      current_courses: [],
      teacher_names: [],
      default_reminder_minutes: 30,
      timezone: "Asia/Shanghai",
    });
    expect(getCurrentUserSettings().timezone).toBe("Asia/Shanghai");
  });
});
