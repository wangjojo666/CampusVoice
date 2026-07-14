"use client";

import type {
  Task,
  TaskCreate,
  TaskPriority,
  TaskStatus,
  TaskUpdate,
} from "@campusvoice/shared-types";
import { ArrowLeft, Check, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { formatDateTime, fromLocalInputValue, toLocalInputValue } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";

interface TaskDraft {
  title: string;
  description: string;
  course: string;
  due_at: string;
  reminder_at: string;
  priority: TaskPriority;
  status: TaskStatus;
}

export function TaskForm({
  task,
  timezone,
  busy,
  onSubmit,
  onCancel,
}: Readonly<{
  task: Task | null;
  timezone?: string;
  busy: boolean;
  onSubmit: (data: TaskCreate | Omit<TaskUpdate, "expected_version">) => void | Promise<void>;
  onCancel: () => void;
}>) {
  const currentSettings = useUserSettings();
  const effectiveTimezone = timezone ?? currentSettings.timezone;
  const [draft, setDraft] = useState<TaskDraft>({
    title: task?.title ?? "",
    description: task?.description ?? "",
    course: task?.course ?? "",
    due_at: toLocalInputValue(task?.due_at, effectiveTimezone),
    reminder_at: toLocalInputValue(task?.reminder_at, effectiveTimezone),
    priority: task?.priority ?? "medium",
    status: task?.status ?? "pending",
  });
  const [reviewing, setReviewing] = useState(false);

  const payload = {
    title: draft.title.trim(),
    description: draft.description.trim() || null,
    course: draft.course.trim() || null,
    due_at: fromLocalInputValue(draft.due_at, effectiveTimezone),
    reminder_at: fromLocalInputValue(draft.reminder_at, effectiveTimezone),
    priority: draft.priority,
    ...(task ? { status: draft.status } : { source_type: "manual" as const }),
  };

  if (reviewing) {
    return (
      <div>
        <div className="rounded-2xl border border-teal-100 bg-teal-50/60 p-4">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 shrink-0 text-teal-700" size={20} />
            <div>
              <p className="font-extrabold text-teal-700">确认{task ? "修改" : "创建"}这项待办</p>
              <p className="mt-1 text-sm text-ink-600">保存后会以数据库重新查询的结果为准。</p>
            </div>
          </div>
        </div>
        <dl className="mt-4 grid gap-3 rounded-2xl border border-mist-200 p-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-bold text-ink-400">标题</dt>
            <dd className="mt-1 font-semibold text-ink-800">{payload.title}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">课程</dt>
            <dd className="mt-1 font-semibold text-ink-800">{payload.course ?? "未分类"}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">截止时间</dt>
            <dd className="mt-1 font-semibold text-ink-800">
              {draft.due_at
                ? formatDateTime(payload.due_at, { timeZone: effectiveTimezone })
                : "未设置"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">优先级</dt>
            <dd className="mt-1 font-semibold text-ink-800">
              {{ low: "低", medium: "中", high: "高" }[payload.priority]}
            </dd>
          </div>
        </dl>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            disabled={busy}
            onClick={() => setReviewing(false)}
            className="btn-secondary"
          >
            <ArrowLeft size={16} /> 返回修改
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onSubmit(payload)}
            className="btn-primary"
          >
            {busy ? (
              <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <Check size={16} />
            )}
            {busy ? "正在保存并验证" : "确认并保存"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (draft.title.trim()) setReviewing(true);
      }}
      className="space-y-4"
    >
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">标题 *</span>
        <input
          autoFocus
          required
          value={draft.title}
          onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          className="field"
          placeholder="例如：完成机器学习作业"
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">课程</span>
          <input
            value={draft.course}
            onChange={(event) => setDraft({ ...draft, course: event.target.value })}
            className="field"
            placeholder="可选"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">优先级</span>
          <select
            value={draft.priority}
            onChange={(event) =>
              setDraft({ ...draft, priority: event.target.value as TaskPriority })
            }
            className="field"
          >
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
        </label>
      </div>
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">说明</span>
        <textarea
          rows={3}
          value={draft.description}
          onChange={(event) => setDraft({ ...draft, description: event.target.value })}
          className="field resize-y"
          placeholder="补充要求或资料位置"
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">截止时间</span>
          <input
            type="datetime-local"
            value={draft.due_at}
            onChange={(event) => setDraft({ ...draft, due_at: event.target.value })}
            className="field"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">提醒时间</span>
          <input
            type="datetime-local"
            value={draft.reminder_at}
            onChange={(event) => setDraft({ ...draft, reminder_at: event.target.value })}
            className="field"
          />
        </label>
      </div>
      {task ? (
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">状态</span>
          <select
            value={draft.status}
            onChange={(event) => setDraft({ ...draft, status: event.target.value as TaskStatus })}
            className="field"
          >
            <option value="pending">待处理</option>
            <option value="in_progress">进行中</option>
            <option value="completed">已完成</option>
            <option value="cancelled">已取消</option>
          </select>
        </label>
      ) : null}
      <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancel} className="btn-secondary">
          取消
        </button>
        <button type="submit" disabled={!draft.title.trim()} className="btn-primary">
          核对并保存
        </button>
      </div>
    </form>
  );
}
