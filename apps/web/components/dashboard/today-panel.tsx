import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  CalendarRange,
  Clock3,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

import type { TodaySummary } from "@/lib/dashboard/today";
import { formatDateTime } from "@/lib/format";

export type TodayResourceStatus = "loading" | "ready" | "stale" | "error";

type TodayPanelProps = {
  clockReady: boolean;
  dateLabel: string;
  eventsStatus: TodayResourceStatus;
  summary: TodaySummary;
  tasksStatus: TodayResourceStatus;
  timezone: string;
};

function hasUsableData(status: TodayResourceStatus) {
  return status === "ready" || status === "stale";
}

function statusMessages(
  clockReady: boolean,
  tasksStatus: TodayResourceStatus,
  eventsStatus: TodayResourceStatus,
) {
  if (!clockReady) return ["正在校准时间"];

  const messages: string[] = [];
  if (tasksStatus === "loading") messages.push("正在同步待办数据");
  if (tasksStatus === "error") messages.push("待办数据暂不可用");
  if (tasksStatus === "stale") messages.push("待办为上次同步数据");
  if (eventsStatus === "loading") messages.push("正在同步日程数据");
  if (eventsStatus === "error") messages.push("日程数据暂不可用");
  if (eventsStatus === "stale") messages.push("日程为上次同步数据");
  return messages.length > 0 ? messages : ["只展示真实任务与日程"];
}

export function TodayPanel({
  clockReady,
  dateLabel,
  eventsStatus,
  summary,
  tasksStatus,
  timezone,
}: TodayPanelProps) {
  const taskDataAvailable = hasUsableData(tasksStatus);
  const eventDataAvailable = hasUsableData(eventsStatus);
  const nextEvent = eventDataAvailable ? summary.nextEvent : null;
  const nextEventIsOngoing =
    clockReady &&
    nextEvent !== null &&
    new Date(nextEvent.start_at).getTime() <= summary.nowMs &&
    new Date(nextEvent.end_at).getTime() > summary.nowMs;
  const firstDeadline = taskDataAvailable ? (summary.dueSoonTasks[0] ?? null) : null;
  const messages = statusMessages(clockReady, tasksStatus, eventsStatus);
  const loadingData = !clockReady || tasksStatus === "loading" || eventsStatus === "loading";
  const unavailableData = tasksStatus === "error" || eventsStatus === "error";
  const staleData = tasksStatus === "stale" || eventsStatus === "stale";
  const taskCountsAvailable = clockReady && taskDataAvailable;
  const sevenDayCountAvailable = clockReady && taskDataAvailable && eventDataAvailable;

  return (
    <section
      className="surface relative mb-6 overflow-hidden p-5 sm:p-7 lg:p-8"
      aria-labelledby="today-panel-title"
    >
      <div className="pointer-events-none absolute -top-24 -right-20 size-72 rounded-full bg-teal-100/70 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-28 left-1/3 size-64 rounded-full bg-gold-100/55 blur-3xl" />
      <div className="relative">
        <div className="mb-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <p className="text-xs font-bold tracking-[0.14em] text-teal-700 uppercase">
              {dateLabel} · 我的校园节奏
            </p>
            <h2
              id="today-panel-title"
              className="mt-1.5 text-2xl font-extrabold tracking-[-0.035em] text-ink-950 sm:text-3xl"
            >
              今天
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-ink-600">
              {loadingData
                ? "正在整理你的近期安排…"
                : unavailableData
                  ? "部分安排暂时无法确认，请重试后再判断今天是否清空。"
                  : staleData
                    ? "当前含上次成功同步的数据，恢复连接后会重新核对。"
                    : summary.dueSoonTasks.length === 0 && summary.overdueTasks.length === 0
                      ? "今天很清爽，可以从下一件最重要的事开始。"
                      : "先接住临近截止，再留意下一场安排。"}
            </p>
          </div>
          <div
            role="status"
            aria-live="polite"
            className="flex w-fit flex-wrap items-center gap-2 rounded-2xl bg-white/80 px-3 py-2 text-xs font-bold text-teal-800 shadow-sm"
          >
            <Sparkles size={14} aria-hidden="true" />
            {messages.map((message) => (
              <span key={message}>{message}</span>
            ))}
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,.75fr)]">
          {!clockReady || eventsStatus === "loading" ? (
            <div className="flex min-h-44 flex-col justify-between rounded-[1.6rem] border border-teal-100 bg-teal-50/70 p-5 sm:p-6">
              <span className="inline-flex items-center gap-2 text-xs font-bold tracking-[0.12em] text-teal-800 uppercase">
                <CalendarClock size={16} aria-hidden="true" /> 下一安排
              </span>
              <div className="mt-8">
                <h3 className="text-xl font-extrabold text-ink-900">正在加载下一安排</h3>
                <p className="mt-2 text-sm leading-6 text-ink-600">正在核对真实日程，请稍候。</p>
              </div>
            </div>
          ) : eventsStatus === "error" ? (
            <div className="flex min-h-44 flex-col justify-between rounded-[1.6rem] border border-gold-100 bg-gold-100/45 p-5 sm:p-6">
              <span className="inline-flex items-center gap-2 text-xs font-bold tracking-[0.12em] text-amber-900 uppercase">
                <CalendarClock size={16} aria-hidden="true" /> 下一安排
              </span>
              <div className="mt-8">
                <h3 className="text-xl font-extrabold text-ink-900">日程数据暂不可用</h3>
                <p className="mt-2 text-sm leading-6 text-ink-600">
                  当前不能判断是否有下一安排，请使用上方重试入口。
                </p>
              </div>
            </div>
          ) : nextEvent ? (
            <Link
              href="/calendar"
              aria-label={
                (nextEventIsOngoing ? "正在进行：" : "下一安排：") +
                nextEvent.title +
                "，" +
                formatDateTime(nextEvent.start_at, { timeZone: timezone }) +
                (eventsStatus === "stale" ? "，上次同步数据" : "")
              }
              className="group flex min-h-44 flex-col justify-between rounded-[1.6rem] bg-ink-950 p-5 text-white shadow-[0_18px_42px_rgba(18,27,34,.18)] transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-teal-500 sm:p-6"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2 text-xs font-bold tracking-[0.12em] text-teal-100 uppercase">
                  <CalendarClock size={16} aria-hidden="true" />
                  {nextEventIsOngoing ? "正在进行" : "下一安排"}
                  {eventsStatus === "stale" ? " · 上次同步" : ""}
                </span>
                <ArrowRight
                  className="shrink-0 text-white/70 transition-transform group-hover:translate-x-1 group-hover:text-white"
                  size={19}
                  aria-hidden="true"
                />
              </div>
              <div className="mt-8">
                <h3 className="text-xl leading-tight font-extrabold sm:text-2xl">
                  {nextEvent.title}
                </h3>
                <p className="mt-2 text-sm font-semibold text-white/80">
                  {formatDateTime(nextEvent.start_at, { timeZone: timezone })}
                  {nextEvent.location ? " · " + nextEvent.location : ""}
                </p>
              </div>
            </Link>
          ) : eventsStatus === "stale" ? (
            <div className="flex min-h-44 flex-col justify-between rounded-[1.6rem] border border-gold-100 bg-gold-100/45 p-5 sm:p-6">
              <span className="inline-flex items-center gap-2 text-xs font-bold tracking-[0.12em] text-amber-900 uppercase">
                <CalendarClock size={16} aria-hidden="true" /> 下一安排
              </span>
              <div className="mt-8">
                <h3 className="text-xl font-extrabold text-ink-900">日程暂未刷新</h3>
                <p className="mt-2 text-sm leading-6 text-ink-600">
                  上次同步时没有排定日程；恢复连接后会重新核对。
                </p>
              </div>
            </div>
          ) : (
            <div className="flex min-h-44 flex-col justify-between rounded-[1.6rem] border border-teal-100 bg-teal-50/70 p-5 sm:p-6">
              <span className="inline-flex items-center gap-2 text-xs font-bold tracking-[0.12em] text-teal-800 uppercase">
                <CalendarClock size={16} aria-hidden="true" /> 下一安排
              </span>
              <div className="mt-8">
                <h3 className="text-xl font-extrabold text-ink-900">暂时没有排定日程</h3>
                <p className="mt-2 text-sm leading-6 text-ink-600">
                  可以问声程创建复习、考试或社团安排，确认后才会写入。
                </p>
              </div>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            <Link
              href="/tasks"
              className="flex min-h-24 items-center gap-3 rounded-2xl border border-coral-100 bg-coral-50/75 p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-coral-600"
            >
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white text-coral-600">
                <Clock3 size={19} aria-hidden="true" />
              </span>
              <span>
                <span className="block text-2xl font-extrabold text-ink-950">
                  {taskCountsAvailable ? summary.dueSoonTasks.length : "—"}
                </span>
                <span className="block text-xs font-bold text-ink-600">3 天内截止</span>
              </span>
            </Link>
            <Link
              href="/tasks"
              className="flex min-h-24 items-center gap-3 rounded-2xl border border-gold-100 bg-gold-100/45 p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-700"
            >
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white text-amber-800">
                <AlertCircle size={19} aria-hidden="true" />
              </span>
              <span>
                <span className="block text-2xl font-extrabold text-ink-950">
                  {taskCountsAvailable ? summary.overdueTasks.length : "—"}
                </span>
                <span className="block text-xs font-bold text-ink-600">需要补救</span>
              </span>
            </Link>
            <div
              title="未来七个本地日历日内的任务截止与日程合计"
              className="flex min-h-24 items-center gap-3 rounded-2xl border border-teal-100 bg-teal-50/70 p-4"
            >
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white text-teal-800">
                <CalendarRange size={19} aria-hidden="true" />
              </span>
              <span>
                <span className="block text-2xl font-extrabold text-ink-950">
                  {sevenDayCountAvailable ? summary.sevenDayLoad : "—"}
                </span>
                <span className="block text-xs font-bold text-ink-600">7 天任务与日程</span>
              </span>
            </div>
          </div>
        </div>

        {clockReady &&
        taskDataAvailable &&
        (summary.unscheduledTasks.length > 0 || firstDeadline) ? (
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-2xl border border-white/80 bg-white/70 px-4 py-3 text-xs font-semibold text-ink-600 backdrop-blur">
            {firstDeadline ? (
              <span>
                最先截止：
                <strong className="ml-1 text-ink-900">{firstDeadline.title}</strong>
              </span>
            ) : null}
            {summary.unscheduledTasks.length > 0 ? (
              <span>{summary.unscheduledTasks.length} 项还没设置截止时间</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
