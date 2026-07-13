import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/app-shell";

const mocks = vi.hoisted(() => ({ pathname: vi.fn() }));

vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname() }));
vi.mock("@/components/system/health-status", () => ({
  HealthStatus: () => <span>API 状态正常</span>,
}));

describe("application shell navigation", () => {
  beforeEach(() => mocks.pathname.mockReturnValue("/tasks"));

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
});
