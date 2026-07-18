import type { CalendarEvent, Task } from "@campusvoice/shared-types";

import { buildConsecutiveLocalDayWindows } from "@/lib/dashboard/local-days";

export type WeeklyRhythmItem = {
  kind: "task" | "event";
  id: string;
  title: string;
  at: string;
  href: "/tasks" | "/calendar";
  ongoing: boolean;
  carriesFromPreviousDay: boolean;
};

export type WeeklyRhythmDay = {
  dateKey: string;
  startMs: number;
  endMs: number;
  tasks: Task[];
  events: CalendarEvent[];
  items: WeeklyRhythmItem[];
  leadItem: WeeklyRhythmItem | null;
};

export type WeeklyRhythmSummary = {
  nowMs: number;
  weekEndMs: number;
  days: WeeklyRhythmDay[];
  overdueTasks: Task[];
};

type WeeklyRhythmOptions = {
  now: Date;
  timezone: string;
};

function timestamp(value: string | null | undefined) {
  if (!value) return null;
  const result = new Date(value).getTime();
  return Number.isNaN(result) ? null : result;
}

function isActiveTask(task: Task) {
  return task.status === "pending" || task.status === "in_progress";
}

export function buildWeeklyRhythm(
  tasks: readonly Task[],
  events: readonly CalendarEvent[],
  { now, timezone }: WeeklyRhythmOptions,
): WeeklyRhythmSummary {
  const nowMs = now.getTime();
  if (Number.isNaN(nowMs)) throw new RangeError("Weekly rhythm requires a valid current time");

  const windows = buildConsecutiveLocalDayWindows(now, timezone, 7);
  const weekEndMs = windows.at(-1)!.endMs;
  const scheduledTasks = tasks
    .filter(isActiveTask)
    .map((task) => ({ task, dueAt: timestamp(task.due_at) }))
    .filter((item): item is { task: Task; dueAt: number } => item.dueAt !== null);
  const overdueTasks = scheduledTasks
    .filter(({ dueAt }) => dueAt < nowMs)
    .sort((left, right) => left.dueAt - right.dueAt)
    .map(({ task }) => task);
  const upcomingTasks = scheduledTasks.filter(({ dueAt }) => dueAt >= nowMs && dueAt < weekEndMs);
  const activeEvents = events
    .map((event) => ({
      event,
      startAt: timestamp(event.start_at),
      endAt: timestamp(event.end_at),
    }))
    .filter(
      (item): item is { event: CalendarEvent; startAt: number; endAt: number } =>
        item.startAt !== null &&
        item.endAt !== null &&
        item.startAt < item.endAt &&
        item.endAt > nowMs,
    );

  const days = windows.map((window): WeeklyRhythmDay => {
    const dayTasks = upcomingTasks
      .filter(({ dueAt }) => dueAt >= window.startMs && dueAt < window.endMs)
      .sort((left, right) => left.dueAt - right.dueAt);
    const dayEvents = activeEvents
      .filter(({ startAt, endAt }) => startAt < window.endMs && endAt > window.startMs)
      .sort((left, right) => left.startAt - right.startAt);
    const taskItems: WeeklyRhythmItem[] = dayTasks.map(({ task }) => ({
      kind: "task",
      id: task.id,
      title: task.title,
      at: task.due_at!,
      href: "/tasks",
      ongoing: false,
      carriesFromPreviousDay: false,
    }));
    const eventItems: WeeklyRhythmItem[] = dayEvents.map(({ event, startAt, endAt }) => ({
      kind: "event",
      id: event.id,
      title: event.title,
      at: event.start_at,
      href: "/calendar",
      ongoing: window.startMs <= nowMs && nowMs < window.endMs && startAt <= nowMs && endAt > nowMs,
      carriesFromPreviousDay: startAt < window.startMs,
    }));
    const items = [...taskItems, ...eventItems].sort((left, right) => {
      if (left.ongoing !== right.ongoing) return left.ongoing ? -1 : 1;
      const leftAt = left.carriesFromPreviousDay
        ? window.startMs
        : (timestamp(left.at) ?? Number.POSITIVE_INFINITY);
      const rightAt = right.carriesFromPreviousDay
        ? window.startMs
        : (timestamp(right.at) ?? Number.POSITIVE_INFINITY);
      return leftAt - rightAt;
    });

    return {
      dateKey: window.dateKey,
      startMs: window.startMs,
      endMs: window.endMs,
      tasks: dayTasks.map(({ task }) => task),
      events: dayEvents.map(({ event }) => event),
      items,
      leadItem: items[0] ?? null,
    };
  });

  return { nowMs, weekEndMs, days, overdueTasks };
}
