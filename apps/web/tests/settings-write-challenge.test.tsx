import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("keeps saving disabled when the authoritative settings request fails", async () => {
    mocks.getSettings.mockRejectedValueOnce(new Error("settings unavailable"));

    render(<SettingsPage />);

    expect(await screen.findByText("设置加载失败")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "保存设置" });
    expect(save).toBeDisabled();
    expect(screen.queryByLabelText("专业")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
    fireEvent.click(save);
    expect(mocks.updateSettings).not.toHaveBeenCalled();
  });

  it("coalesces rapid retries and waits for authoritative settings before saving", async () => {
    mocks.getSettings.mockRejectedValueOnce(new Error("settings unavailable"));
    render(<SettingsPage />);
    expect(await screen.findByText("设置加载失败")).toBeInTheDocument();

    let resolveRetry!: (value: {
      major: string;
      grade: string;
      current_courses: [];
      teacher_names: [];
      default_reminder_minutes: number;
      timezone: string;
      asr_provider: string;
      asr_model: string;
      asr_device: string;
    }) => void;
    mocks.getSettings.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRetry = resolve;
      }),
    );
    const retry = screen.getByRole("button", { name: "重试" });
    act(() => {
      retry.click();
      retry.click();
    });

    expect(mocks.getSettings).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "保存设置" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();

    await act(async () => {
      resolveRetry({
        major: "计算机科学",
        grade: "2025",
        current_courses: [],
        teacher_names: [],
        default_reminder_minutes: 45,
        timezone: "Asia/Shanghai",
        asr_provider: "funasr",
        asr_model: "paraformer-zh-streaming",
        asr_device: "cpu",
      });
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "保存设置" })).toBeEnabled());
  });

  it("does not reload settings for an unrelated hotword load error", async () => {
    mocks.listHotwords.mockRejectedValueOnce(new Error("hotwords unavailable"));
    render(<SettingsPage />);

    await waitFor(() => expect(mocks.getSettings).toHaveBeenCalledTimes(1));
    const save = await screen.findByRole("button", { name: "保存设置" });
    await waitFor(() => expect(save).toBeEnabled());

    fireEvent.change(screen.getByLabelText("专业"), {
      target: { value: "保留本地草稿" },
    });

    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("专业")).toHaveValue("保留本地草稿");
    expect(mocks.getSettings).toHaveBeenCalledTimes(1);
  });
  it("preserves edits made while an older settings snapshot is being saved", async () => {
    render(<SettingsPage />);
    const save = await screen.findByRole("button", { name: "保存设置" });
    await waitFor(() => expect(save).toBeEnabled());

    fireEvent.change(screen.getByLabelText("专业"), {
      target: { value: "提交时的专业" },
    });
    let resolveSave!: (value: {
      major: string;
      grade: string;
      current_courses: [];
      teacher_names: [];
      default_reminder_minutes: number;
      timezone: string;
      asr_provider: string;
      asr_model: string;
      asr_device: string;
    }) => void;
    mocks.updateSettings.mockReset().mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    fireEvent.click(save);
    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("专业"), {
      target: { value: "保存期间的新草稿" },
    });
    await act(async () => {
      resolveSave({
        major: "提交时的专业",
        grade: "2024",
        current_courses: [],
        teacher_names: [],
        default_reminder_minutes: 30,
        timezone: "Asia/Shanghai",
        asr_provider: "funasr",
        asr_model: "paraformer-zh-streaming",
        asr_device: "cpu",
      });
    });

    expect(screen.getByLabelText("专业")).toHaveValue("保存期间的新草稿");
    expect(await screen.findByText(/新修改仍保留在当前草稿中/)).toBeInTheDocument();
    expect(getCurrentUserSettings().major).toBe("提交时的专业");
  });
});
