import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";
import {
  DEFAULT_USER_SETTINGS,
  setCurrentUserSettings,
  useUserSettings,
} from "@/lib/user-settings";

const mocks = vi.hoisted(() => ({ pathname: vi.fn(), logout: vi.fn(), getSettings: vi.fn() }));

vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname() }));
vi.mock("@/components/system/health-status", () => ({
  HealthStatus: () => <span>API 状态正常</span>,
}));
vi.mock("@/lib/api-client", () => ({
  API_BASE_URL: "https://api.campus.test",
  api: { auth: { logout: mocks.logout }, settings: { get: mocks.getSettings } },
}));

afterEach(() => {
  cleanup();
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
});

function SettingsAwareWriteEntry() {
  const settings = useUserSettings();
  return (
    <button type="button">
      准备写入 {settings.timezone} / {settings.default_reminder_minutes}
    </button>
  );
}

describe("application shell navigation", () => {
  beforeEach(() => {
    mocks.pathname.mockReturnValue("/tasks");
    mocks.logout.mockReset();
    mocks.getSettings.mockReset().mockResolvedValue({
      major: null,
      grade: null,
      current_courses: [],
      teacher_names: [],
      default_reminder_minutes: 30,
      timezone: "Asia/Shanghai",
      asr_provider: "disabled",
      asr_model: "",
      asr_device: "",
    });
  });

  it("marks the current section in desktop and mobile navigation", async () => {
    const { unmount } = render(
      <AppShell>
        <h1>待办工作区</h1>
      </AppShell>,
    );

    expect(await screen.findByRole("heading", { name: "待办工作区" })).toBeInTheDocument();
    expect(screen.getAllByText("API 状态正常")).toHaveLength(2);
    const taskLinks = screen.getAllByRole("link", { name: "待办" });
    expect(taskLinks).toHaveLength(2);
    taskLinks.forEach((link) => {
      expect(link).toHaveAttribute("href", "/tasks");
      expect(link).toHaveAttribute("aria-current", "page");
    });
    screen.getAllByRole("link", { name: "首页" }).forEach((link) => {
      expect(link).not.toHaveAttribute("aria-current");
    });
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "移动端主导航" })).toBeInTheDocument();
    unmount();
  });

  it("treats only the exact root path as the active home section", async () => {
    mocks.pathname.mockReturnValue("/");

    const { unmount } = render(<AppShell>首页摘要</AppShell>);
    await screen.findByText("首页摘要");

    const desktopNavigation = screen.getByRole("navigation", { name: "主导航" });
    expect(within(desktopNavigation).getByRole("link", { name: "首页" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(desktopNavigation).getByRole("link", { name: "语音助手" })).not.toHaveAttribute(
      "aria-current",
    );
    unmount();
  });

  it("offers desktop and mobile OIDC logout and preserves the session on failure", async () => {
    mocks.logout.mockRejectedValue(new Error("network unavailable"));
    render(<AppShell oidcEnabled>校园首页</AppShell>);

    const logoutButtons = screen.getAllByRole("button", { name: "退出登录" });
    expect(logoutButtons).toHaveLength(2);
    fireEvent.click(logoutButtons[1]!);

    expect(
      await screen.findByText("退出登录未完成，当前会话仍然有效。请检查网络后重试。"),
    ).toBeInTheDocument();
    expect(mocks.logout).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "重试退出" })).toBeInTheDocument();
  });

  it("does not mount page write entrypoints until user settings finish loading", async () => {
    let resolveSettings: ((value: unknown) => void) | undefined;
    mocks.getSettings.mockReturnValue(
      new Promise((resolve) => {
        resolveSettings = resolve;
      }),
    );

    render(
      <AppShell>
        <SettingsAwareWriteEntry />
      </AppShell>,
    );

    expect(screen.queryByRole("button", { name: /准备写入/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("正在加载个人设置")).toBeInTheDocument();
    resolveSettings?.({
      major: null,
      grade: null,
      current_courses: [],
      teacher_names: [],
      default_reminder_minutes: 60,
      timezone: "UTC",
      asr_provider: "disabled",
      asr_model: "",
      asr_device: "",
    });

    expect(await screen.findByRole("button", { name: "准备写入 UTC / 60" })).toBeInTheDocument();
  });

  it("keeps page write entrypoints unmounted when settings loading fails", async () => {
    mocks.getSettings.mockRejectedValue(new Error("settings unavailable"));

    render(
      <AppShell>
        <button type="button">准备写入</button>
      </AppShell>,
    );

    expect(await screen.findByText("无法加载个人设置")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "准备写入" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    await waitFor(() => expect(mocks.getSettings).toHaveBeenCalledTimes(1));
  });
});
