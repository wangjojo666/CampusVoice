import type { CalendarEvent, Task } from "@campusvoice/shared-types";

import {
  addLocalDays,
  firstValidInstantOfLocalDay,
  localDateKey,
} from "@/lib/dashboard/local-days";

export type TodayTimelineItem =
  | {
      kind: "event";
      id: string;
      title: string;
      at: string;
      href: "/calendar";
      event: CalendarEvent;
    }
  | {
      kind: "task";
      id: string;
      title: string;
      at: string;
      href: "/tasks";
      task: Task;
    };

export type TodaySummary = {
  nowMs: number;
  activeTasks: Task[];
  dueSoonTasks: Task[];
  overdueTasks: Task[];
  unscheduledTasks: Task[];
  upcomingEvents: CalendarEvent[];
  nextEvent: CalendarEvent | null;
  sevenDayLoad: number;
  timeline: TodayTimelineItem[];
};

type TodaySummaryOptions = {
  now: Date;
  timezone: string;
};

function timestamp(value: string | null | undefined) {
  if (!value) return null;
  const result = new Date(value).getTime();
  return Number.isNaN(result) ? null : result;
}

function isActiveTask(task: Task) {
  return task.status !== "completed" && task.status !== "cancelled";
}

export function buildTodaySummary(
  tasks: readonly Task[],
  events: readonly CalendarEvent[],
  { now, timezone }: TodaySummaryOptions,
): TodaySummary {
  const nowMs = now.getTime();
  if (Number.isNaN(nowMs)) throw new RangeError("Today summary requires a valid current time");

  const todayKey = localDateKey(now, timezone);
  const threeDayEnd = firstValidInstantOfLocalDay(addLocalDays(todayKey, 3), timezone);
  const sevenDayEnd = firstValidInstantOfLocalDay(addLocalDays(todayKey, 7), timezone);

  const activeTasks = tasks.filter(isActiveTask);
  const scheduledTasks = activeTasks
    .map((task) => ({ task, dueAt: timestamp(task.due_at) }))
    .filter((item): item is { task: Task; dueAt: number } => item.dueAt !== null);
  const dueSoonTasks = scheduledTasks
    .filter(({ dueAt }) => dueAt >= nowMs && dueAt < threeDayEnd)
    .sort((left, right) => left.dueAt - right.dueAt)
    .map(({ task }) => task);
  const overdueTasks = scheduledTasks
    .filter(({ dueAt }) => dueAt < nowMs)
    .sort((left, right) => left.dueAt - right.dueAt)
    .map(({ task }) => task);
  const unscheduledTasks = activeTasks.filter((task) => timestamp(task.due_at) === null);

  const upcomingEvents = events
    .map((event) => ({
      event,
      startAt: timestamp(event.start_at),
      endAt: timestamp(event.end_at),
    }))
    .filter(
      (item): item is { event: CalendarEvent; startAt: number; endAt: number } =>
        item.startAt !== null && item.endAt !== null && item.endAt > nowMs,
    )
    .sort((left, right) => left.startAt - right.startAt)
    .map(({ event }) => event);
  const ongoingEvents = upcomingEvents
    .filter((event) => {
      const startAt = timestamp(event.start_at);
      return startAt !== null && startAt <= nowMs;
    })
    .sort((left, right) => {
      const leftEnd = timestamp(left.end_at) ?? Number.POSITIVE_INFINITY;
      const rightEnd = timestamp(right.end_at) ?? Number.POSITIVE_INFINITY;
      return leftEnd - rightEnd;
    });
  const nextEvent =
    ongoingEvents[0] ??
    upcomingEvents.find(
      (event) => (timestamp(event.start_at) ?? Number.POSITIVE_INFINITY) > nowMs,
    ) ??
    null;

  const taskTimeline: TodayTimelineItem[] = scheduledTasks
    .filter(({ dueAt }) => dueAt >= nowMs && dueAt < sevenDayEnd)
    .map(({ task }) => ({
      kind: "task",
      id: task.id,
      title: task.title,
      at: task.due_at!,
      href: "/tasks",
      task,
    }));
  const eventTimeline: TodayTimelineItem[] = upcomingEvents
    .filter((event) => (timestamp(event.start_at) ?? Number.POSITIVE_INFINITY) < sevenDayEnd)
    .map((event) => ({
      kind: "event",
      id: event.id,
      title: event.title,
      at: event.start_at,
      href: "/calendar",
      event,
    }));
  const timeline = [...taskTimeline, ...eventTimeline].sort(
    (left, right) => (timestamp(left.at) ?? 0) - (timestamp(right.at) ?? 0),
  );

  return {
    nowMs,
    activeTasks,
    dueSoonTasks,
    overdueTasks,
    unscheduledTasks,
    upcomingEvents,
    nextEvent,
    sevenDayLoad: timeline.length,
    timeline,
  };
}
