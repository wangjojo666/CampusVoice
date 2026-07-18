import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { WeeklyRhythmPanel } from "@/components/dashboard/weekly-rhythm-panel";
import type { DashboardResourceSnapshot } from "@/lib/dashboard/resource-status";
import { buildWeeklyRhythm } from "@/lib/dashboard/weekly-rhythm";

const completeReady: DashboardResourceSnapshot = { status: "ready", complete: true };

afterEach(cleanup);

describe("weekly rhythm panel truthfulness", () => {
  it("does not expose synthetic date semantics before the local clock is ready", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date(0),
      timezone: "Asia/Shanghai",
    });

    const { container } = render(
      <WeeklyRhythmPanel
        clockReady={false}
        eventsResource={{ status: "loading", complete: false }}
        rhythm={rhythm}
        tasksResource={{ status: "loading", complete: false }}
        timezone="Asia/Shanghai"
      />,
    );

    expect(container.querySelector("time")).not.toBeInTheDocument();
    expect(screen.getAllByText("日期待确认")).toHaveLength(7);
    expect(container).not.toHaveTextContent("1970");
  });

  it("shows a real empty state only when both resources are fresh and complete", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date("2026-07-17T00:30:00.000Z"),
      timezone: "Asia/Shanghai",
    });

    const { rerender } = render(
      <WeeklyRhythmPanel
        clockReady
        eventsResource={completeReady}
        rhythm={rhythm}
        tasksResource={completeReady}
        timezone="Asia/Shanghai"
      />,
    );
    expect(screen.getAllByText("没有排定任务或日程")).toHaveLength(7);

    rerender(
      <WeeklyRhythmPanel
        clockReady
        eventsResource={completeReady}
        rhythm={rhythm}
        tasksResource={{ status: "ready", complete: false }}
        timezone="Asia/Shanghai"
      />,
    );
    expect(screen.queryByText("没有排定任务或日程")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/待办仅加载部分/);
    expect(screen.getAllByText(/不能判断当天为空/)).toHaveLength(7);
  });

  it("keeps each resource independent across loading, error, and stale states", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date("2026-07-17T00:30:00.000Z"),
      timezone: "Asia/Shanghai",
    });

    render(
      <WeeklyRhythmPanel
        clockReady
        eventsResource={{ status: "error", complete: false }}
        rhythm={rhythm}
        tasksResource={{ status: "stale", complete: true }}
        timezone="Asia/Shanghai"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("待办为上次同步数据");
    expect(screen.getByRole("status")).toHaveTextContent("日程数据暂不可用");
    const firstDay = screen.getAllByRole("listitem")[0]!;
    expect(within(firstDay).getByText(/待办 0 · 上次同步/)).toBeInTheDocument();
    expect(within(firstDay).getByText("日程 — · 暂不可用")).toBeInTheDocument();
    expect(within(firstDay).queryByText("没有排定任务或日程")).not.toBeInTheDocument();
  });

  it("only offers real unfiltered destinations with 44px targets", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date("2026-07-17T00:30:00.000Z"),
      timezone: "Asia/Shanghai",
    });

    render(
      <WeeklyRhythmPanel
        clockReady
        eventsResource={completeReady}
        rhythm={rhythm}
        tasksResource={completeReady}
        timezone="Asia/Shanghai"
      />,
    );

    const taskLink = screen.getByRole("link", { name: "查看全部计划" });
    const eventLink = screen.getByRole("link", { name: "打开日程" });
    expect(taskLink).toHaveAttribute("href", "/tasks");
    expect(eventLink).toHaveAttribute("href", "/calendar");
    expect(taskLink).toHaveClass("min-h-11");
    expect(eventLink).toHaveClass("min-h-11");
    expect(taskLink.getAttribute("href")).not.toContain("?");
    expect(eventLink.getAttribute("href")).not.toContain("?");
  });
});
