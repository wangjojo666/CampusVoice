"use client";

import type { ActionLog, CalendarEvent, Task } from "@campusvoice/shared-types";
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  Circle,
  Clock3,
  Mic2,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { CampusRadar } from "@/components/radar/campus-radar";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AsrRecorder } from "@/components/voice/asr-recorder";
import { ApiError, api } from "@/lib/api-client";
import { formatDateTime, relativeTime, sameDayInTimeZone } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

export default function HomePage() {
  const userSettings = useUserSettings();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [logs, setLogs] = useState<ActionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const setTranscript = useAssistantStore((state) => state.setTranscript);
  const transcript = useAssistantStore((state) => state.transcript);

  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      api.tasks.list(),
      api.events.list(),
      api.actionLogs.list(6),
    ]);
    const failed: string[] = [];
    const [taskResult, eventResult, logResult] = results;
    if (taskResult.status === "fulfilled") setTasks(taskResult.value.items);
    else
      failed.push(
        taskResult.reason instanceof ApiError ? taskResult.reason.userMessage : "待办加载失败",
      );
    if (eventResult.status === "fulfilled") setEvents(eventResult.value.items);
    else
      failed.push(
        eventResult.reason instanceof ApiError ? eventResult.reason.userMessage : "日程加载失败",
      );
    if (logResult.status === "fulfilled") setLogs(logResult.value.items);
    else
      failed.push(
        logResult.reason instanceof ApiError ? logResult.reason.userMessage : "操作记录加载失败",
      );
    setError(failed.length > 0 ? [...new Set(failed)].join(" ") : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const todayTasks = useMemo(
    () =>
      tasks
        .filter(
          (task) =>
            task.status !== "completed" &&
            (sameDayInTimeZone(task.due_at, new Date(), userSettings.timezone) ||
              task.due_at === null),
        )
        .slice(0, 5),
    [tasks, userSettings.timezone],
  );
  const todayEvents = useMemo(
    () =>
      events
        .filter((event) => sameDayInTimeZone(event.start_at, new Date(), userSettings.timezone))
        .sort((a, b) => a.start_at.localeCompare(b.start_at))
        .slice(0, 5),
    [events, userSettings.timezone],
  );
  const dateLabel = new Intl.DateTimeFormat("zh-CN", {
    timeZone: userSettings.timezone,
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());

  return (
    <div>
      <PageHeader
        eyebrow={dateLabel}
        title="今天，准备从哪一步开始？"
        description="说出任务或日程，声程会在执行前补全信息、检查风险，并在写入后重新验证。"
      />

      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={() => void load()} compact />
        </div>
      ) : null}

      <CampusRadar />

      <section className="mb-6 grid gap-4 sm:grid-cols-3">
        <div className="surface flex items-center gap-4 p-4">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-coral-50 text-coral-600">
            <Circle size={20} />
          </span>
          <div>
            <p className="text-2xl font-extrabold text-ink-950">
              {loading ? "—" : todayTasks.length}
            </p>
            <p className="text-xs font-semibold text-ink-400">今日待处理</p>
          </div>
        </div>
        <div className="surface flex items-center gap-4 p-4">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
            <CalendarClock size={20} />
          </span>
          <div>
            <p className="text-2xl font-extrabold text-ink-950">
              {loading ? "—" : todayEvents.length}
            </p>
            <p className="text-xs font-semibold text-ink-400">今日日程</p>
          </div>
        </div>
        <div className="surface flex items-center gap-4 p-4">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-gold-100/70 text-amber-700">
            <CheckCircle2 size={20} />
          </span>
          <div>
            <p className="text-2xl font-extrabold text-ink-950">
              {loading ? "—" : logs.filter((log) => log.success).length}
            </p>
            <p className="text-xs font-semibold text-ink-400">最近验证成功</p>
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,.8fr)]">
        <section className="surface relative overflow-hidden p-5 sm:p-7">
          <div className="pointer-events-none absolute -top-20 -right-20 size-64 rounded-full bg-teal-100/50 blur-3xl" />
          <div className="relative mb-4 flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                Quick voice
              </p>
              <h2 className="mt-1 text-xl font-extrabold text-ink-950">直接说出你的安排</h2>
              <p className="mt-1 text-sm text-ink-500">
                例如：“周五上午九点有机器学习考试，提前一天提醒我。”
              </p>
            </div>
            <span className="hidden size-11 items-center justify-center rounded-2xl bg-white text-teal-600 shadow-sm sm:flex">
              <Mic2 size={21} />
            </span>
          </div>
          <div className="relative">
            <AsrRecorder
              compact
              onTranscriptChange={setTranscript}
              onReset={() => setTranscript("")}
            />
          </div>
          {transcript ? (
            <div className="relative mt-4 flex justify-end">
              <Link href="/voice" className="btn-primary">
                继续理解并检查 <ArrowRight size={16} />
              </Link>
            </div>
          ) : null}
        </section>

        <section className="surface p-5 sm:p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">Today</p>
              <h2 className="mt-1 text-lg font-extrabold text-ink-950">今天的节奏</h2>
            </div>
            <Sparkles className="text-gold-500" size={19} />
          </div>
          {loading ? (
            <LoadingState rows={3} />
          ) : todayTasks.length === 0 && todayEvents.length === 0 ? (
            <EmptyState
              title="今天还没有安排"
              description="用语音添加一项待办或日历事件，确认并验证后会显示在这里。"
            />
          ) : (
            <div className="space-y-2.5">
              {todayEvents.map((event) => (
                <Link
                  key={`event-${event.id}`}
                  href="/calendar"
                  className="flex items-center gap-3 rounded-2xl border border-mist-100 p-3.5 transition-colors hover:bg-mist-50"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
                    <Clock3 size={17} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-bold text-ink-800">{event.title}</p>
                    <p className="mt-0.5 truncate text-xs text-ink-400">
                      {formatDateTime(event.start_at, { timeZone: userSettings.timezone })}
                      {event.location ? ` · ${event.location}` : ""}
                    </p>
                  </div>
                </Link>
              ))}
              {todayTasks.map((task) => (
                <Link
                  key={`task-${task.id}`}
                  href="/tasks"
                  className="flex items-center gap-3 rounded-2xl border border-mist-100 p-3.5 transition-colors hover:bg-mist-50"
                >
                  <span
                    className={`size-3 shrink-0 rounded-full ${task.priority === "high" ? "bg-coral-500" : task.priority === "medium" ? "bg-gold-500" : "bg-teal-500"}`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-bold text-ink-800">{task.title}</p>
                    <p className="mt-0.5 truncate text-xs text-ink-400">
                      {task.course ?? "未分类"} ·{" "}
                      {task.due_at
                        ? formatDateTime(task.due_at, { timeZone: userSettings.timezone })
                        : "无截止时间"}
                    </p>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="surface mt-6 p-5 sm:p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">
              Verified history
            </p>
            <h2 className="mt-1 text-lg font-extrabold text-ink-950">最近操作</h2>
          </div>
          <Link href="/voice" className="text-sm font-bold text-teal-700 hover:text-teal-600">
            查看语音流程
          </Link>
        </div>
        {loading ? (
          <LoadingState rows={2} />
        ) : logs.length === 0 ? (
          <EmptyState
            title="还没有操作记录"
            description="所有数据修改都会留下风险、确认与验证记录。"
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {logs.map((log) => (
              <div key={log.id} className="rounded-2xl border border-mist-100 p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-bold text-ink-800">
                    {log.message ?? log.action}
                  </p>
                  <span
                    className={`shrink-0 rounded-full px-2 py-1 text-[0.65rem] font-bold ${log.success === true ? "bg-teal-50 text-teal-700" : log.success === false ? "bg-coral-50 text-coral-600" : "bg-mist-100 text-ink-500"}`}
                  >
                    {log.success === true ? "已验证" : log.success === false ? "失败" : "待处理"}
                  </span>
                </div>
                <p className="mt-2 text-xs text-ink-400">
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
        )}
      </section>
    </div>
  );
}
