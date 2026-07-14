"use client";

import type { ActionLog, CalendarEvent, Task } from "@campusvoice/shared-types";
import { CalendarClock, CheckCircle2, Circle, Clock3, Mic2, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { CampusRadar } from "@/components/radar/campus-radar";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AsrRecorder } from "@/components/voice/asr-recorder";
import { WorkflowSnapshot } from "@/components/voice/workflow-snapshot";
import { ApiError, api } from "@/lib/api-client";
import { formatDateTime, relativeTime } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

const VOICE_EXAMPLES = [
  "创建日历事件：明天下午三点到四点在图书馆复习，提前半小时提醒我。",
  "新建待办：后天下午三点提交人工智能作业，提前一天提醒我。",
  "查询人工智能考试地点有没有变化。",
  "安排明天上午九点的机器学习考试复习日程。",
];

export default function HomePage() {
  const userSettings = useUserSettings();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [logs, setLogs] = useState<ActionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const setTranscript = useAssistantStore((state) => state.setTranscript);
  const setInputMode = useAssistantStore((state) => state.setInputMode);
  const clearResult = useAssistantStore((state) => state.clearResult);
  const resetAssistant = useAssistantStore((state) => state.reset);
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

  const upcomingTasks = useMemo(
    () =>
      tasks
        .filter((task) => task.status !== "completed")
        .sort((a, b) => (a.due_at ?? "9999").localeCompare(b.due_at ?? "9999"))
        .slice(0, 5),
    [tasks],
  );
  const upcomingEvents = useMemo(
    () => events.sort((a, b) => a.start_at.localeCompare(b.start_at)).slice(0, 5),
    [events],
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
        title="说一句，校园安排自动落地"
        description="实时识别校园术语，自动纠错并理解为待办或日程；你确认后才写入，写入后可验证、可撤销。"
      />

      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={() => void load()} compact />
        </div>
      ) : null}

      <section
        className="surface relative mb-6 overflow-hidden p-5 sm:p-7 lg:p-8"
        aria-labelledby="campus-voice-title"
      >
        <div className="pointer-events-none absolute -top-24 -right-20 size-80 rounded-full bg-teal-100/60 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 -left-16 size-72 rounded-full bg-gold-100/40 blur-3xl" />
        <div className="relative grid items-center gap-7 lg:grid-cols-[minmax(0,.9fr)_minmax(380px,1.1fr)] lg:gap-10">
          <div>
            <div className="mb-4 flex items-center gap-3">
              <span className="flex size-13 items-center justify-center rounded-2xl bg-teal-600 text-white shadow-[0_12px_30px_rgba(14,127,109,.24)]">
                <Mic2 size={26} aria-hidden="true" />
              </span>
              <div>
                <p className="text-xs font-bold tracking-[0.14em] text-teal-600 uppercase">
                  校园语音识别
                </p>
                <p className="mt-0.5 text-xs font-semibold text-ink-400">
                  语音是第一入口，可验证执行是核心价值
                </p>
              </div>
            </div>
            <h2
              id="campus-voice-title"
              className="max-w-xl text-2xl leading-tight font-extrabold tracking-[-0.035em] text-ink-950 sm:text-3xl"
            >
              直接说出任务、日程或校园问题
            </h2>
            <p className="mt-3 max-w-xl text-sm leading-6 text-ink-600 sm:text-[0.95rem]">
              实时转写并识别课程、地点等校园热词；完成后继续理解与风险检查，所有写入都由你确认。
            </p>

            <div className="mt-5">
              <p className="mb-1 text-xs font-bold text-ink-400">文本指令演示</p>
              <p className="mb-2 text-[0.68rem] leading-5 text-ink-400">
                点击只会填入文字，不会冒充语音识别结果。
              </p>
              <div className="flex flex-wrap gap-2">
                {VOICE_EXAMPLES.map((example) => {
                  const selected = transcript === example;
                  return (
                    <button
                      key={example}
                      type="button"
                      onClick={() => {
                        clearResult();
                        setTranscript(example);
                        setInputMode("text_demo");
                      }}
                      aria-pressed={selected}
                      className={`rounded-xl border px-3 py-2 text-left text-xs leading-5 font-semibold transition-colors ${selected ? "border-teal-200 bg-teal-50 text-teal-800" : "border-mist-200 bg-white/80 text-ink-600 hover:border-teal-100 hover:bg-teal-50/60"}`}
                    >
                      {example}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="rounded-[1.75rem] border border-white/80 bg-white/85 p-5 shadow-[0_18px_45px_rgba(34,56,68,.09)] backdrop-blur sm:p-6">
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

      <section className="mb-6 grid gap-4 sm:grid-cols-3">
        <div className="surface flex items-center gap-4 p-4">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-coral-50 text-coral-600">
            <Circle size={20} />
          </span>
          <div>
            <p className="text-2xl font-extrabold text-ink-950">
              {loading ? "—" : tasks.filter((task) => task.status !== "completed").length}
            </p>
            <p className="text-xs font-semibold text-ink-400">待处理事项</p>
          </div>
        </div>
        <div className="surface flex items-center gap-4 p-4">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
            <CalendarClock size={20} />
          </span>
          <div>
            <p className="text-2xl font-extrabold text-ink-950">{loading ? "—" : events.length}</p>
            <p className="text-xs font-semibold text-ink-400">日程记录</p>
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

      <section className="surface p-5 sm:p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">Today</p>
            <h2 className="mt-1 text-lg font-extrabold text-ink-950">近期安排</h2>
          </div>
          <Sparkles className="text-gold-500" size={19} />
        </div>
        {loading ? (
          <LoadingState rows={3} />
        ) : upcomingTasks.length === 0 && upcomingEvents.length === 0 ? (
          <EmptyState
            title="还没有近期安排"
            description="用语音添加一项待办或日历事件，确认并验证后会显示在这里。"
          />
        ) : (
          <div className="space-y-2.5">
            {upcomingEvents.map((event) => (
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
            {upcomingTasks.map((task) => (
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
