"use client";

import type { ActionLog, CalendarEvent, Task } from "@campusvoice/shared-types";
import { ChevronDown, Clock3, ListTodo, Mic2, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { TodayPanel, type TodayResourceStatus } from "@/components/dashboard/today-panel";
import { PageHeader } from "@/components/layout/page-header";
import { CampusRadar } from "@/components/radar/campus-radar";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AsrRecorder } from "@/components/voice/asr-recorder";
import { WorkflowSnapshot } from "@/components/voice/workflow-snapshot";
import { ApiError, api } from "@/lib/api-client";
import { buildTodaySummary } from "@/lib/dashboard/today";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

const VOICE_STARTERS = [
  {
    label: "今晚排复习",
    prompt: "创建日历事件：今晚七点到九点在图书馆复习。",
  },
  {
    label: "盯住实验报告截止",
    prompt: "新建待办：后天晚上八点提交实验报告，提前一天提醒我。",
  },
  {
    label: "考试地点变了吗",
    prompt: "查询校园通知：人工智能考试地点有没有变化。",
  },
  {
    label: "看看已有日程",
    prompt: "查询我的日程。",
  },
] as const;

function failedResourceStatus(current: TodayResourceStatus): TodayResourceStatus {
  return current === "ready" || current === "stale" ? "stale" : "error";
}

export default function HomePage() {
  const userSettings = useUserSettings();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [logs, setLogs] = useState<ActionLog[]>([]);
  const [taskStatus, setTaskStatus] = useState<TodayResourceStatus>("loading");
  const [eventStatus, setEventStatus] = useState<TodayResourceStatus>("loading");
  const [logStatus, setLogStatus] = useState<TodayResourceStatus>("loading");
  const [clockMs, setClockMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logsExpanded, setLogsExpanded] = useState(false);
  const setTranscript = useAssistantStore((state) => state.setTranscript);
  const setInputMode = useAssistantStore((state) => state.setInputMode);
  const clearResult = useAssistantStore((state) => state.clearResult);
  const resetAssistant = useAssistantStore((state) => state.reset);
  const transcript = useAssistantStore((state) => state.transcript);

  const load = useCallback(async () => {
    setTaskStatus((current) => (current === "error" ? "loading" : current));
    setEventStatus((current) => (current === "error" ? "loading" : current));
    setLogStatus((current) => (current === "error" ? "loading" : current));
    const results = await Promise.allSettled([
      api.tasks.list(),
      api.events.list(),
      api.actionLogs.list(6),
    ]);
    const failed: string[] = [];
    const [taskResult, eventResult, logResult] = results;
    if (taskResult.status === "fulfilled") {
      setTasks(taskResult.value.items);
      setTaskStatus("ready");
    } else {
      setTaskStatus(failedResourceStatus);
      failed.push(
        taskResult.reason instanceof ApiError ? taskResult.reason.userMessage : "待办加载失败",
      );
    }
    if (eventResult.status === "fulfilled") {
      setEvents(eventResult.value.items);
      setEventStatus("ready");
    } else {
      setEventStatus(failedResourceStatus);
      failed.push(
        eventResult.reason instanceof ApiError ? eventResult.reason.userMessage : "日程加载失败",
      );
    }
    if (logResult.status === "fulfilled") {
      setLogs(logResult.value.items);
      setLogStatus("ready");
    } else {
      setLogStatus(failedResourceStatus);
      failed.push(
        logResult.reason instanceof ApiError ? logResult.reason.userMessage : "操作记录加载失败",
      );
    }
    setError(failed.length > 0 ? [...new Set(failed)].join(" ") : null);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const updateClock = () => setClockMs(Date.now());
    updateClock();
    const timer = window.setInterval(updateClock, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const summary = useMemo(
    () =>
      buildTodaySummary(tasks, events, {
        now: new Date(clockMs ?? 0),
        timezone: userSettings.timezone,
      }),
    [clockMs, events, tasks, userSettings.timezone],
  );
  const timelineItems = useMemo(
    () =>
      summary.timeline
        .filter((item) => item.kind !== "event" || item.id !== summary.nextEvent?.id)
        .slice(0, 6),
    [summary],
  );
  const dateLabel = useMemo(
    () =>
      clockMs === null
        ? "正在校准时间"
        : new Intl.DateTimeFormat("zh-CN", {
            timeZone: userSettings.timezone,
            month: "long",
            day: "numeric",
            weekday: "long",
          }).format(new Date(clockMs)),
    [clockMs, userSettings.timezone],
  );
  const scheduleLoading =
    clockMs === null || (taskStatus === "loading" && eventStatus === "loading");
  const scheduleIncomplete = taskStatus === "error" || eventStatus === "error";
  const schedulePartlyLoading =
    !scheduleLoading && (taskStatus === "loading" || eventStatus === "loading");
  const scheduleStale = taskStatus === "stale" || eventStatus === "stale";
  const scheduleStatusText = scheduleIncomplete
    ? "仅展示已成功加载的安排"
    : schedulePartlyLoading
      ? "部分安排仍在同步"
      : scheduleStale
        ? "含上次同步数据"
        : null;
  const verifiedCount = logs.filter((log) => log.success === true).length;
  const failedCount = logs.filter((log) => log.success === false).length;

  return (
    <div>
      <PageHeader
        eyebrow={"今天 · " + dateLabel}
        title="今天先把最重要的事接住"
        description="先看下一安排和临近截止，再用一句话把任务、复习或通知变化变成可确认、可撤销的个人行动。"
      />

      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={() => void load()} compact />
        </div>
      ) : null}

      <TodayPanel
        clockReady={clockMs !== null}
        dateLabel={dateLabel}
        eventsStatus={eventStatus}
        summary={summary}
        tasksStatus={taskStatus}
        timezone={userSettings.timezone}
      />

      <section
        className="surface relative mb-6 overflow-hidden p-5 sm:p-7"
        aria-labelledby="home-voice-title"
      >
        <div className="pointer-events-none absolute -top-24 -right-20 size-72 rounded-full bg-teal-100/55 blur-3xl" />
        <div className="relative grid items-center gap-6 lg:grid-cols-[minmax(0,.88fr)_minmax(380px,1.12fr)] lg:gap-8">
          <div>
            <div className="mb-4 flex items-center gap-3">
              <span className="flex size-12 items-center justify-center rounded-2xl bg-teal-600 text-white shadow-[0_12px_30px_rgba(14,127,109,.22)]">
                <Mic2 size={24} aria-hidden="true" />
              </span>
              <div>
                <p className="text-xs font-bold tracking-[0.14em] text-teal-600 uppercase">
                  一句话开始
                </p>
                <h2 id="home-voice-title" className="mt-0.5 text-xl font-extrabold text-ink-950">
                  问声程
                </h2>
              </div>
            </div>
            <p className="max-w-xl text-sm leading-6 text-ink-600">
              说出安排、截止或校园问题。声程会先让你核对内容，再执行任何写入。
            </p>
            <div className="mt-5">
              <p className="mb-1 text-xs font-bold text-ink-500">试试这些校园场景</p>
              <p className="mb-3 text-[0.7rem] leading-5 text-ink-500">
                点击只会填入文字，不会冒充语音识别结果。
              </p>
              <div className="flex flex-wrap gap-2">
                {VOICE_STARTERS.map((starter) => {
                  const selected = transcript === starter.prompt;
                  return (
                    <button
                      key={starter.label}
                      type="button"
                      title={starter.prompt}
                      onClick={() => {
                        clearResult();
                        setTranscript(starter.prompt);
                        setInputMode("text_demo");
                      }}
                      aria-pressed={selected}
                      className={
                        selected
                          ? "min-h-11 rounded-xl border border-teal-200 bg-teal-50 px-3.5 py-2 text-left text-xs leading-5 font-bold text-teal-800 transition-colors"
                          : "min-h-11 rounded-xl border border-mist-200 bg-white/82 px-3.5 py-2 text-left text-xs leading-5 font-bold text-ink-600 transition-colors hover:border-teal-100 hover:bg-teal-50/60"
                      }
                    >
                      {starter.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="rounded-[1.6rem] border border-white/80 bg-white/88 p-5 shadow-[0_18px_45px_rgba(34,56,68,.09)] backdrop-blur sm:p-6">
            <AsrRecorder
              compact
              onTranscriptChange={(text) => {
                clearResult();
                setTranscript(text);
                setInputMode("voice");
              }}
              onReset={() => {
                resetAssistant();
              }}
            />
            <WorkflowSnapshot />
          </div>
        </div>
      </section>

      <CampusRadar />

      <section className="surface mb-6 p-5 sm:p-6" aria-labelledby="up-next-title">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-wider text-ink-500 uppercase">Up next</p>
            <h2 id="up-next-title" className="mt-1 text-lg font-extrabold text-ink-950">
              接下来
            </h2>
          </div>
          <Sparkles className="text-gold-500" size={19} aria-hidden="true" />
        </div>
        {scheduleLoading ? (
          <LoadingState rows={3} />
        ) : timelineItems.length === 0 && summary.unscheduledTasks.length === 0 ? (
          scheduleStatusText ? (
            <EmptyState
              title="部分安排暂不可用"
              description={scheduleStatusText + "。恢复同步后，这里会重新核对近期任务与日程。"}
            />
          ) : (
            <EmptyState
              title="还没有近期安排"
              description="用语音添加一项计划或日程，确认并验证后会显示在这里。"
            />
          )
        ) : (
          <div className="space-y-2.5">
            {scheduleStatusText ? (
              <p
                role="status"
                className="rounded-xl border border-gold-100 bg-gold-100/45 px-3 py-2 text-xs font-bold text-amber-900"
              >
                {scheduleStatusText}
              </p>
            ) : null}
            {timelineItems.map((item) => (
              <Link
                key={item.kind + "-" + item.id}
                href={item.href}
                className="flex min-h-16 items-center gap-3 rounded-2xl border border-mist-100 p-3.5 transition-colors hover:bg-mist-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-500"
              >
                <span
                  className={
                    item.kind === "event"
                      ? "flex size-10 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700"
                      : "flex size-10 shrink-0 items-center justify-center rounded-xl bg-coral-50 text-coral-600"
                  }
                >
                  {item.kind === "event" ? (
                    <Clock3 size={18} aria-hidden="true" />
                  ) : (
                    <ListTodo size={18} aria-hidden="true" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-bold text-ink-800">{item.title}</span>
                  <span className="mt-0.5 block text-xs text-ink-600">
                    {formatDateTime(item.at, { timeZone: userSettings.timezone })}
                    {item.kind === "event" && item.event.location
                      ? " · " + item.event.location
                      : item.kind === "task" && item.task.course
                        ? " · " + item.task.course
                        : ""}
                  </span>
                </span>
              </Link>
            ))}
            {summary.unscheduledTasks.slice(0, 3).map((task) => (
              <Link
                key={"unscheduled-" + task.id}
                href="/tasks"
                className="flex min-h-16 items-center gap-3 rounded-2xl border border-dashed border-mist-200 p-3.5 transition-colors hover:bg-mist-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-500"
              >
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-mist-100 text-ink-500">
                  <ListTodo size={18} aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-bold text-ink-800">{task.title}</span>
                  <span className="mt-0.5 block text-xs text-ink-600">
                    {task.course ?? "未分类"} · 无截止时间
                  </span>
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="surface p-5 sm:p-6" aria-labelledby="execution-history-title">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
              <ShieldCheck size={20} aria-hidden="true" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                Safe landing
              </p>
              <h2
                id="execution-history-title"
                className="mt-0.5 text-lg font-extrabold text-ink-950"
              >
                最近落地
              </h2>
            </div>
          </div>
          {(logStatus === "ready" || logStatus === "stale") && logs.length > 0 ? (
            <p className="text-sm font-semibold text-ink-500">
              {verifiedCount} 项已验证
              {failedCount > 0 ? " · " + failedCount + " 项需要留意" : ""}
              {logStatus === "stale" ? " · 上次同步" : ""}
            </p>
          ) : null}
        </div>

        {logStatus === "loading" ? (
          <div className="mt-4">
            <LoadingState rows={2} />
          </div>
        ) : logStatus === "error" ? (
          <div className="mt-4">
            <EmptyState
              title="操作记录暂不可用"
              description="当前无法确认最近的验证与撤销记录，请使用上方重试入口重新加载。"
            />
          </div>
        ) : logStatus === "stale" && logs.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="操作记录暂未刷新"
              description="上次同步时没有记录；恢复连接后会重新核对。"
            />
          </div>
        ) : logs.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="还没有操作记录"
              description="确认后的数据修改会留下可复核、可撤销的执行记录。"
            />
          </div>
        ) : (
          <div className="mt-4">
            <button
              type="button"
              aria-expanded={logsExpanded}
              aria-controls="execution-history-details"
              onClick={() => setLogsExpanded((value) => !value)}
              className="flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border border-mist-200 bg-mist-50 px-4 py-2.5 text-left text-sm font-bold text-ink-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-500"
            >
              查看执行详情
              <ChevronDown
                className={
                  logsExpanded
                    ? "shrink-0 rotate-180 text-ink-400 transition-transform"
                    : "shrink-0 text-ink-400 transition-transform"
                }
                size={17}
                aria-hidden="true"
              />
            </button>
            {logsExpanded ? (
              <div
                id="execution-history-details"
                className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3"
              >
                {logs.map((log) => (
                  <div key={log.id} className="rounded-2xl border border-mist-100 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm leading-6 font-bold text-ink-800">
                        {log.message ?? log.action}
                      </p>
                      <span
                        className={
                          log.success === true
                            ? "shrink-0 rounded-full bg-teal-50 px-2 py-1 text-[0.65rem] font-bold text-teal-700"
                            : log.success === false
                              ? "shrink-0 rounded-full bg-coral-50 px-2 py-1 text-[0.65rem] font-bold text-coral-600"
                              : "shrink-0 rounded-full bg-mist-100 px-2 py-1 text-[0.65rem] font-bold text-ink-500"
                        }
                      >
                        {log.success === true
                          ? "已验证"
                          : log.success === false
                            ? "失败"
                            : "待处理"}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-ink-600">
                      {relativeTime(log.created_at)} ·{" "}
                      {log.risk_level === "high"
                        ? "高风险"
                        : log.risk_level === "medium"
                          ? "中风险"
                          : "低风险"}
                    </p>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="mt-3 text-right">
              <Link
                href="/voice"
                className="inline-flex min-h-11 items-center justify-center rounded-xl px-3 text-sm font-bold text-teal-700 hover:text-teal-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
              >
                查看确认与验证流程
              </Link>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
