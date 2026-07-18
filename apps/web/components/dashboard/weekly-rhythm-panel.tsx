import {
  AlertTriangle,
  CalendarDays,
  CalendarRange,
  Clock3,
  ListTodo,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

import {
  hasUsableDashboardData,
  type DashboardResourceSnapshot,
} from "@/lib/dashboard/resource-status";
import type { WeeklyRhythmDay, WeeklyRhythmSummary } from "@/lib/dashboard/weekly-rhythm";
import { formatTime } from "@/lib/format";

type WeeklyRhythmPanelProps = {
  clockReady: boolean;
  eventsResource: DashboardResourceSnapshot;
  rhythm: WeeklyRhythmSummary;
  tasksResource: DashboardResourceSnapshot;
  timezone: string;
};

function resourceMessage(label: string, resource: DashboardResourceSnapshot) {
  if (resource.status === "loading") return label + "正在同步";
  if (resource.status === "error") return label + "数据暂不可用";
  if (resource.status === "stale") {
    return label + "为上次同步数据" + (resource.complete ? "" : "，且仅加载部分");
  }
  return resource.complete ? label + "已完整同步" : label + "仅加载部分";
}

function countText(
  label: string,
  count: number,
  resource: DashboardResourceSnapshot,
  clockReady: boolean,
) {
  if (!clockReady) return label + " — · 校准中";
  if (resource.status === "loading") return label + " — · 同步中";
  if (resource.status === "error") return label + " — · 暂不可用";
  if (resource.status === "stale") {
    return (
      label +
      " " +
      (resource.complete ? count : "至少 " + count) +
      " · 上次同步" +
      (resource.complete ? "" : " / 仅加载部分")
    );
  }
  return resource.complete ? label + " " + count : label + " 至少 " + count + " · 已加载";
}

function dayLabel(day: WeeklyRhythmDay, timezone: string) {
  const date = new Date(day.startMs);
  return {
    weekday: new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, weekday: "short" }).format(
      date,
    ),
    date: new Intl.DateTimeFormat("zh-CN", {
      timeZone: timezone,
      month: "numeric",
      day: "numeric",
    }).format(date),
  };
}

export function WeeklyRhythmPanel({
  clockReady,
  eventsResource,
  rhythm,
  tasksResource,
  timezone,
}: WeeklyRhythmPanelProps) {
  const taskUsable = hasUsableDashboardData(tasksResource);
  const eventUsable = hasUsableDashboardData(eventsResource);
  const statusText = [
    clockReady ? "从今天起 7 个真实本地日" : "正在校准本地日期",
    resourceMessage("待办", tasksResource),
    resourceMessage("日程", eventsResource),
  ].join(" · ");
  const overdueKnown = clockReady && taskUsable;
  const overdueText = overdueKnown
    ? (tasksResource.complete ? "" : "至少 ") + rhythm.overdueTasks.length
    : "—";

  return (
    <section
      className="surface relative mb-6 overflow-hidden p-5 sm:p-7"
      aria-labelledby="weekly-rhythm-title"
    >
      <div className="pointer-events-none absolute -top-24 left-1/3 size-64 rounded-full bg-gold-100/45 blur-3xl" />
      <div className="relative">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div>
            <p className="text-xs font-bold tracking-[0.14em] text-teal-700 uppercase">
              Weekly rhythm
            </p>
            <h2
              id="weekly-rhythm-title"
              className="mt-1.5 text-2xl font-extrabold tracking-[-0.03em] text-ink-950"
            >
              一周节奏
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600">
              从今天起 7 个本地日，只整理已经存在的截止与日程。跨日事项会出现在实际覆盖的每一天。
            </p>
          </div>
          <Link
            href="/tasks"
            className="inline-flex min-h-11 items-center gap-3 self-start rounded-2xl border border-coral-100 bg-coral-50 px-4 py-2.5 text-sm font-bold text-coral-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-600"
          >
            <AlertTriangle size={17} aria-hidden="true" />
            逾期待办 {overdueText}
            {tasksResource.status === "stale" ? " · 上次同步" : ""}
          </Link>
        </div>

        <p
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className="mt-4 flex items-start gap-2 rounded-xl border border-mist-100 bg-mist-50/75 px-3 py-2 text-xs font-bold text-ink-600"
        >
          <Sparkles className="mt-0.5 shrink-0 text-gold-500" size={14} aria-hidden="true" />
          {statusText}
        </p>

        <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          {rhythm.days.map((day, index) => {
            const labels = dayLabel(day, timezone);
            const availableItems = day.items.filter((item) =>
              item.kind === "task" ? taskUsable : eventUsable,
            );
            const leadItem = availableItems[0] ?? null;
            const exactEmpty =
              clockReady &&
              tasksResource.status === "ready" &&
              tasksResource.complete &&
              eventsResource.status === "ready" &&
              eventsResource.complete &&
              day.tasks.length === 0 &&
              day.events.length === 0;
            const uncertainEmpty = !leadItem && !exactEmpty;
            const uncertainReason =
              !clockReady ||
              tasksResource.status === "loading" ||
              eventsResource.status === "loading"
                ? "仍在同步，不能判断当天为空"
                : !tasksResource.complete || !eventsResource.complete
                  ? "仅展示已加载数据，不能判断当天为空"
                  : tasksResource.status === "error" || eventsResource.status === "error"
                    ? "部分数据不可用，不能判断当天为空"
                    : "使用上次同步数据，不能确认当天为空";

            return (
              <li
                key={day.dateKey}
                className={
                  index === 0
                    ? "min-w-0 rounded-2xl border border-teal-200 bg-teal-50/70 p-4"
                    : "min-w-0 rounded-2xl border border-mist-100 bg-white/80 p-4"
                }
              >
                <div className="flex items-start justify-between gap-2">
                  {clockReady ? (
                    <time dateTime={day.dateKey}>
                      <span className="block text-sm font-extrabold text-ink-950">
                        {labels.weekday}
                      </span>
                      <span className="mt-0.5 block text-xs font-semibold text-ink-600">
                        {labels.date}
                      </span>
                    </time>
                  ) : (
                    <div>
                      <span className="block text-sm font-extrabold text-ink-950">校准中</span>
                      <span className="mt-0.5 block text-xs font-semibold text-ink-600">
                        日期待确认
                      </span>
                    </div>
                  )}
                  {index === 0 && clockReady ? (
                    <span className="rounded-full bg-teal-600 px-2 py-1 text-[0.68rem] font-bold text-white">
                      今天
                    </span>
                  ) : null}
                </div>

                <div className="mt-4 space-y-1.5 text-xs font-bold text-ink-600">
                  <p className="flex items-center gap-1.5">
                    <ListTodo className="text-coral-600" size={14} aria-hidden="true" />
                    {countText("待办", day.tasks.length, tasksResource, clockReady)}
                  </p>
                  <p className="flex items-center gap-1.5">
                    <CalendarDays className="text-teal-700" size={14} aria-hidden="true" />
                    {countText("日程", day.events.length, eventsResource, clockReady)}
                  </p>
                </div>

                <div className="mt-4 border-t border-mist-100 pt-3">
                  {leadItem ? (
                    <>
                      <p className="line-clamp-2 text-sm leading-5 font-bold text-ink-900">
                        {leadItem.title}
                      </p>
                      <p className="mt-1.5 flex items-center gap-1.5 text-xs font-semibold text-ink-600">
                        <Clock3 size={13} aria-hidden="true" />
                        {leadItem.ongoing
                          ? "正在进行"
                          : leadItem.carriesFromPreviousDay
                            ? "跨日进行"
                            : formatTime(leadItem.at, timezone)}
                      </p>
                    </>
                  ) : exactEmpty ? (
                    <p className="text-xs leading-5 font-semibold text-ink-600">
                      没有排定任务或日程
                    </p>
                  ) : uncertainEmpty ? (
                    <p className="text-xs leading-5 font-semibold text-ink-600">
                      {uncertainReason}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>

        <div className="mt-5 flex flex-wrap gap-2">
          <Link
            href="/tasks"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-mist-200 bg-white px-4 py-2.5 text-sm font-bold text-ink-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
          >
            <ListTodo size={16} aria-hidden="true" /> 查看全部计划
          </Link>
          <Link
            href="/calendar"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-mist-200 bg-white px-4 py-2.5 text-sm font-bold text-ink-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
          >
            <CalendarRange size={16} aria-hidden="true" /> 打开日程
          </Link>
        </div>
      </div>
    </section>
  );
}
