import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";

const mocks = vi.hoisted(() => ({ pathname: vi.fn(), logout: vi.fn() }));

vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname() }));
vi.mock("@/components/system/health-status", () => ({
  HealthStatus: () => <span>API 状态正常</span>,
}));
vi.mock("@/lib/api-client", () => ({
  API_BASE_URL: "https://api.campus.test",
  api: { auth: { logout: mocks.logout } },
}));

describe("application shell navigation", () => {
  beforeEach(() => {
    mocks.pathname.mockReturnValue("/tasks");
    mocks.logout.mockReset();
  });

  it("marks the current section in desktop and mobile navigation", () => {
    const { unmount } = render(
      <AppShell>
        <h1>待办工作区</h1>
      </AppShell>,
    );

    expect(screen.getByRole("heading", { name: "待办工作区" })).toBeInTheDocument();
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

  it("treats only the exact root path as the active home section", () => {
    mocks.pathname.mockReturnValue("/");

    const { unmount } = render(<AppShell>首页摘要</AppShell>);

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
});
