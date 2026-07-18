import type { CalendarEvent, Task } from "@campusvoice/shared-types";
import { describe, expect, it } from "vitest";

import { buildWeeklyRhythm } from "@/lib/dashboard/weekly-rhythm";

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

describe("student weekly rhythm", () => {
  it("builds seven half-open local days, separates overdue work, and never mutates inputs", () => {
    const now = new Date("2026-07-17T00:30:00.000Z");
    const tasks = [
      task({ id: "later", due_at: "2026-07-20T08:00:00.000Z" }),
      task({ id: "overdue", due_at: "2026-07-17T00:29:59.000Z" }),
      task({ id: "at-now", due_at: now.toISOString(), status: "in_progress" }),
      task({ id: "week-end", due_at: "2026-07-23T16:00:00.000Z" }),
      task({ id: "completed", due_at: "2026-07-18T08:00:00.000Z", status: "completed" }),
      task({ id: "cancelled", due_at: "2026-07-18T08:00:00.000Z", status: "cancelled" }),
      task({ id: "unscheduled", due_at: null }),
    ];
    const events = [
      event({
        id: "cross-day",
        title: "跨午夜复习",
        start_at: "2026-07-18T15:30:00.000Z",
        end_at: "2026-07-18T17:30:00.000Z",
      }),
      event({
        id: "ended-at-now",
        start_at: "2026-07-16T23:30:00.000Z",
        end_at: now.toISOString(),
      }),
      event({
        id: "ongoing",
        title: "正在答疑",
        start_at: "2026-07-17T00:00:00.000Z",
        end_at: "2026-07-17T01:00:00.000Z",
      }),
      event({
        id: "invalid",
        start_at: "2026-07-19T03:00:00.000Z",
        end_at: "2026-07-19T03:00:00.000Z",
      }),
    ];
    const taskSnapshot = tasks.map((item) => ({ ...item }));
    const eventSnapshot = events.map((item) => ({ ...item }));

    const rhythm = buildWeeklyRhythm(tasks, events, { now, timezone: "Asia/Shanghai" });

    expect(rhythm.days).toHaveLength(7);
    expect(rhythm.days.map((day) => day.dateKey)).toEqual([
      "2026-07-17",
      "2026-07-18",
      "2026-07-19",
      "2026-07-20",
      "2026-07-21",
      "2026-07-22",
      "2026-07-23",
    ]);
    expect(rhythm.overdueTasks.map((item) => item.id)).toEqual(["overdue"]);
    expect(rhythm.days[0]?.tasks.map((item) => item.id)).toEqual(["at-now"]);
    expect(rhythm.days[0]?.events.map((item) => item.id)).toEqual(["ongoing"]);
    expect(rhythm.days[0]?.leadItem).toMatchObject({ id: "ongoing", ongoing: true });
    expect(rhythm.days[1]?.events.map((item) => item.id)).toContain("cross-day");
    expect(rhythm.days[2]?.events.map((item) => item.id)).toContain("cross-day");
    expect(rhythm.days.flatMap((day) => day.tasks).map((item) => item.id)).not.toContain(
      "week-end",
    );
    expect(rhythm.days.flatMap((day) => day.tasks).map((item) => item.id)).not.toContain(
      "completed",
    );
    expect(rhythm.days.flatMap((day) => day.events).map((item) => item.id)).not.toContain(
      "ended-at-now",
    );
    expect(tasks).toEqual(taskSnapshot);
    expect(events).toEqual(eventSnapshot);
  });

  it("uses 23-hour and 25-hour local days across New York DST changes", () => {
    const spring = buildWeeklyRhythm([], [], {
      now: new Date("2026-03-07T17:00:00.000Z"),
      timezone: "America/New_York",
    });
    const fall = buildWeeklyRhythm([], [], {
      now: new Date("2026-10-31T16:00:00.000Z"),
      timezone: "America/New_York",
    });

    expect((spring.days[2]!.startMs - spring.days[1]!.startMs) / 3_600_000).toBe(23);
    expect((fall.days[2]!.startMs - fall.days[1]!.startMs) / 3_600_000).toBe(25);
  });

  it("uses the first valid instant for a Santiago day whose midnight is skipped", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date("2026-09-03T15:00:00.000Z"),
      timezone: "America/Santiago",
    });
    const transitionDay = rhythm.days.find((day) => day.dateKey === "2026-09-06");

    expect(transitionDay?.startMs).toBe(new Date("2026-09-06T04:00:00.000Z").getTime());
  });

  it("skips a civil date that never existed and keeps every boundary increasing", () => {
    const rhythm = buildWeeklyRhythm([], [], {
      now: new Date("2011-12-29T22:00:00.000Z"),
      timezone: "Pacific/Apia",
    });

    expect(rhythm.days.map((day) => day.dateKey)).not.toContain("2011-12-30");
    expect(rhythm.days).toHaveLength(7);
    for (const day of rhythm.days) {
      expect(day.endMs).toBeGreaterThan(day.startMs);
    }
    for (let index = 1; index < rhythm.days.length; index += 1) {
      expect(rhythm.days[index]!.startMs).toBe(rhythm.days[index - 1]!.endMs);
    }
  });

  it("rejects an invalid current time", () => {
    expect(() =>
      buildWeeklyRhythm([], [], { now: new Date(Number.NaN), timezone: "Asia/Shanghai" }),
    ).toThrow(/valid current time/i);
  });
});
