"use client";

import type {
  CalendarEvent,
  CalendarEventCreate,
  CalendarEventUpdate,
  EventConflict,
} from "@campusvoice/shared-types";
import { AlertTriangle, ArrowLeft, Check, MapPin, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { formatDateTime, fromLocalInputValue, toLocalInputValue } from "@/lib/format";

interface EventDraft {
  title: string;
  description: string;
  course: string;
  start_at: string;
  end_at: string;
  location: string;
  reminder_minutes: string;
}

export function EventForm({
  event,
  defaultStart,
  conflicts,
  busy,
  onSubmit,
  onCancel,
}: Readonly<{
  event: CalendarEvent | null;
  defaultStart?: Date | null;
  conflicts: EventConflict[];
  busy: boolean;
  onSubmit: (data: CalendarEventCreate | CalendarEventUpdate) => void | Promise<void>;
  onCancel: () => void;
}>) {
  const initialStart = event?.start_at ?? defaultStart?.toISOString();
  const initialEnd =
    event?.end_at ??
    (initialStart
      ? new Date(new Date(initialStart).getTime() + 60 * 60_000).toISOString()
      : undefined);
  const [draft, setDraft] = useState<EventDraft>({
    title: event?.title ?? "",
    description: event?.description ?? "",
    course: event?.course ?? "",
    start_at: toLocalInputValue(initialStart),
    end_at: toLocalInputValue(initialEnd),
    location: event?.location ?? "",
    reminder_minutes: String(event?.reminder_minutes ?? 30),
  });
  const [reviewing, setReviewing] = useState(false);

  const payload = {
    title: draft.title.trim(),
    description: draft.description.trim() || null,
    course: draft.course.trim() || null,
    start_at: fromLocalInputValue(draft.start_at) ?? "",
    end_at: fromLocalInputValue(draft.end_at),
    location: draft.location.trim() || null,
    reminder_minutes: draft.reminder_minutes ? Number(draft.reminder_minutes) : null,
    ...(event ? {} : { source_type: "manual" as const }),
  };

  if (reviewing) {
    return (
      <div>
        {conflicts.length > 0 ? (
          <div role="alert" className="rounded-2xl border border-coral-100 bg-coral-50 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 shrink-0 text-coral-600" size={20} />
              <div>
                <p className="font-extrabold text-coral-600">检测到时间冲突，已阻止保存</p>
                <p className="mt-1 text-sm text-ink-600">
                  请返回修改时间。声程不会静默覆盖现有日程。
                </p>
              </div>
            </div>
            <ul className="mt-3 space-y-2">
              {conflicts.map((conflict) => (
                <li key={conflict.event_id} className="rounded-xl bg-white/75 p-3 text-sm">
                  <strong>{conflict.title}</strong>
                  <span className="ml-2 text-ink-500">
                    {formatDateTime(conflict.start_at)} – {formatDateTime(conflict.end_at)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="rounded-2xl border border-teal-100 bg-teal-50/60 p-4">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 shrink-0 text-teal-700" size={20} />
              <div>
                <p className="font-extrabold text-teal-700">未检测到时间冲突</p>
                <p className="mt-1 text-sm text-ink-600">
                  请确认以下内容。保存后仍会重新查询数据库验证。
                </p>
              </div>
            </div>
          </div>
        )}
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
            <dt className="text-xs font-bold text-ink-400">开始</dt>
            <dd className="mt-1 font-semibold text-ink-800">{formatDateTime(payload.start_at)}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">结束</dt>
            <dd className="mt-1 font-semibold text-ink-800">{formatDateTime(payload.end_at)}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">地点</dt>
            <dd className="mt-1 flex items-center gap-1 font-semibold text-ink-800">
              <MapPin size={14} /> {payload.location ?? "未设置"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-400">提醒</dt>
            <dd className="mt-1 font-semibold text-ink-800">
              提前 {payload.reminder_minutes ?? 0} 分钟
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
            disabled={busy || conflicts.length > 0}
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
      onSubmit={(formEvent) => {
        formEvent.preventDefault();
        if (draft.title.trim() && draft.start_at) setReviewing(true);
      }}
      className="space-y-4"
    >
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">标题 *</span>
        <input
          autoFocus
          required
          value={draft.title}
          onChange={(input) => setDraft({ ...draft, title: input.target.value })}
          className="field"
          placeholder="例如：机器学习期中考试"
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">开始时间 *</span>
          <input
            required
            type="datetime-local"
            value={draft.start_at}
            onChange={(input) => setDraft({ ...draft, start_at: input.target.value })}
            className="field"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">结束时间 *</span>
          <input
            required
            type="datetime-local"
            min={draft.start_at}
            value={draft.end_at}
            onChange={(input) => setDraft({ ...draft, end_at: input.target.value })}
            className="field"
          />
        </label>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">课程</span>
          <input
            value={draft.course}
            onChange={(input) => setDraft({ ...draft, course: input.target.value })}
            className="field"
            placeholder="可选"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">地点</span>
          <input
            value={draft.location}
            onChange={(input) => setDraft({ ...draft, location: input.target.value })}
            className="field"
            placeholder="例如：教学楼 A302"
          />
        </label>
      </div>
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">说明</span>
        <textarea
          rows={3}
          value={draft.description}
          onChange={(input) => setDraft({ ...draft, description: input.target.value })}
          className="field resize-y"
        />
      </label>
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">提前提醒</span>
        <select
          value={draft.reminder_minutes}
          onChange={(input) => setDraft({ ...draft, reminder_minutes: input.target.value })}
          className="field"
        >
          <option value="0">不提醒</option>
          <option value="10">10 分钟</option>
          <option value="30">30 分钟</option>
          <option value="60">1 小时</option>
          <option value="1440">1 天</option>
        </select>
      </label>
      <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancel} className="btn-secondary">
          取消
        </button>
        <button
          type="submit"
          disabled={!draft.title.trim() || !draft.start_at}
          className="btn-primary"
        >
          检查冲突并核对
        </button>
      </div>
    </form>
  );
}
