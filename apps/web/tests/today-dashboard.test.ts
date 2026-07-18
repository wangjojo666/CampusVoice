import type { CalendarEvent, Task } from "@campusvoice/shared-types";
import { describe, expect, it } from "vitest";

import { buildTodaySummary } from "@/lib/dashboard/today";

function task(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    title: "完成实验报告",
    description: null,
    course: "数据库",
    course_id: null,
    due_at: null,
    reminder_at: null,
    priority: "medium",
    status: "pending",
    source_type: "manual",
    source_document_id: null,
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    version: 1,
    ...overrides,
  };
}

function event(overrides: Partial<CalendarEvent>): CalendarEvent {
  return {
    id: "event-1",
    title: "机器学习研讨课",
    description: null,
    course: "机器学习",
    course_id: null,
    start_at: "2026-07-17T02:00:00.000Z",
    end_at: "2026-07-17T03:00:00.000Z",
    location: "B205",
    reminder_minutes: 30,
    source_type: "manual",
    source_document_id: null,
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    version: 1,
    ...overrides,
  };
}

describe("student Today summary", () => {
  it("uses the selected timezone's three-calendar-day boundary and exclusive buckets", () => {
    const tasks = [
      task({ id: "overdue", due_at: "2026-07-16T23:00:00.000Z" }),
      task({ id: "today", due_at: "2026-07-17T10:00:00.000Z" }),
      task({ id: "third-day-end", due_at: "2026-07-19T15:59:59.000Z" }),
      task({ id: "fourth-day-start", due_at: "2026-07-19T16:00:00.000Z" }),
      task({ id: "completed", due_at: "2026-07-17T10:00:00.000Z", status: "completed" }),
      task({ id: "cancelled", due_at: "2026-07-17T10:00:00.000Z", status: "cancelled" }),
      task({ id: "unscheduled", due_at: null }),
    ];

    const summary = buildTodaySummary(tasks, [], {
      now: new Date("2026-07-17T00:30:00.000Z"),
      timezone: "Asia/Shanghai",
    });

    expect(summary.nowMs).toBe(new Date("2026-07-17T00:30:00.000Z").getTime());
    expect(summary.overdueTasks.map((item) => item.id)).toEqual(["overdue"]);
    expect(summary.dueSoonTasks.map((item) => item.id)).toEqual(["today", "third-day-end"]);
    expect(summary.unscheduledTasks.map((item) => item.id)).toEqual(["unscheduled"]);
    expect(summary.dueSoonTasks).not.toContainEqual(expect.objectContaining({ id: "overdue" }));
    expect(summary.dueSoonTasks).not.toContainEqual(
      expect.objectContaining({ id: "fourth-day-start" }),
    );
  });

  it("shows an ongoing event before a future event and ignores ended events", () => {
    const summary = buildTodaySummary(
      [],
      [
        event({ id: "ended", end_at: "2026-07-17T00:29:59.000Z" }),
        event({
          id: "ongoing",
          title: "正在进行的答疑",
          start_at: "2026-07-17T00:00:00.000Z",
          end_at: "2026-07-17T01:00:00.000Z",
        }),
        event({
          id: "future",
          title: "下一场课程",
          start_at: "2026-07-17T02:00:00.000Z",
          end_at: "2026-07-17T03:00:00.000Z",
        }),
      ],
      { now: new Date("2026-07-17T00:30:00.000Z"), timezone: "Asia/Shanghai" },
    );

    expect(summary.nextEvent?.id).toBe("ongoing");
    expect(summary.upcomingEvents.map((item) => item.id)).toEqual(["ongoing", "future"]);
  });

  it("counts the seven-day load, sorts the timeline, and never mutates inputs", () => {
    const tasks = [
      task({ id: "later-task", due_at: "2026-07-21T08:00:00.000Z" }),
      task({ id: "early-task", due_at: "2026-07-17T08:00:00.000Z" }),
      task({ id: "before-week-end", due_at: "2026-07-23T15:59:59.000Z" }),
      task({ id: "at-week-end", due_at: "2026-07-23T16:00:00.000Z" }),
    ];
    const events = [
      event({
        id: "later-event",
        start_at: "2026-07-20T08:00:00.000Z",
        end_at: "2026-07-20T09:00:00.000Z",
      }),
      event({
        id: "early-event",
        start_at: "2026-07-17T02:00:00.000Z",
        end_at: "2026-07-17T03:00:00.000Z",
      }),
    ];
    const taskOrder = tasks.map((item) => item.id);
    const eventOrder = events.map((item) => item.id);

    const summary = buildTodaySummary(tasks, events, {
      now: new Date("2026-07-17T00:30:00.000Z"),
      timezone: "Asia/Shanghai",
    });

    expect(summary.sevenDayLoad).toBe(5);
    expect(summary.timeline.map((item) => item.id)).toEqual([
      "early-event",
      "early-task",
      "later-event",
      "later-task",
      "before-week-end",
    ]);
    expect(tasks.map((item) => item.id)).toEqual(taskOrder);
    expect(events.map((item) => item.id)).toEqual(eventOrder);
  });

  it("keeps local-day boundaries correct across daylight-saving changes", () => {
    const summary = buildTodaySummary(
      [
        task({ id: "before-boundary", due_at: "2026-03-11T03:59:59.000Z" }),
        task({ id: "at-boundary", due_at: "2026-03-11T04:00:00.000Z" }),
      ],
      [],
      {
        now: new Date("2026-03-08T15:00:00.000Z"),
        timezone: "America/New_York",
      },
    );

    expect(summary.dueSoonTasks.map((item) => item.id)).toEqual(["before-boundary"]);
  });

  it("uses the first valid local instant when a DST transition skips midnight", () => {
    const summary = buildTodaySummary(
      [
        task({ id: "before-skipped-midnight-boundary", due_at: "2026-09-06T03:59:59.000Z" }),
        task({ id: "at-first-valid-instant", due_at: "2026-09-06T04:00:00.000Z" }),
      ],
      [],
      {
        now: new Date("2026-09-03T15:00:00.000Z"),
        timezone: "America/Santiago",
      },
    );

    expect(summary.dueSoonTasks.map((item) => item.id)).toEqual([
      "before-skipped-midnight-boundary",
    ]);
  });
});
