"use client";

import type {
  CalendarEvent,
  CalendarEventCreate,
  CalendarEventUpdate,
  EventConflict,
} from "@campusvoice/shared-types";
import { CalendarPlus, Check, Clock3, Edit3, MapPin, Plus, RotateCcw, Trash2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";

import { EventForm } from "@/components/calendar/event-form";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Modal } from "@/components/ui/modal";
import { ApiError, api } from "@/lib/api-client";
import { latestUndoableEventAction } from "@/lib/calendar/undo";
import { formatDateTime } from "@/lib/format";

const CalendarView = dynamic(
  () => import("@/components/calendar/calendar-view").then((module) => module.CalendarView),
  {
    ssr: false,
    loading: () => <LoadingState rows={5} label="正在加载日历" />,
  },
);

export default function CalendarPage() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<CalendarEvent | null>(null);
  const [defaultStart, setDefaultStart] = useState<Date | null>(null);
  const [conflicts, setConflicts] = useState<EventConflict[]>([]);
  const [deleting, setDeleting] = useState<CalendarEvent | null>(null);
  const [deleteText, setDeleteText] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.events.list();
      setEvents(response.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法加载日历。");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const openCreate = (date?: Date) => {
    setEditing(null);
    setDefaultStart(date ?? new Date());
    setConflicts([]);
    setEditorOpen(true);
    setNotice(null);
  };
  const openEdit = (event: CalendarEvent) => {
    setEditing(event);
    setDefaultStart(null);
    setConflicts([]);
    setEditorOpen(true);
    setNotice(null);
  };

  const save = async (data: CalendarEventCreate | CalendarEventUpdate) => {
    if (!data.start_at || !data.end_at) {
      setError("请同时填写开始与结束时间，以便检查冲突。");
      return;
    }
    setBusy(true);
    setError(null);
    setConflicts([]);
    try {
      const conflictResult = await api.events.checkConflict({
        start_at: data.start_at,
        end_at: data.end_at,
        exclude_event_id: editing?.id,
      });
      if (conflictResult.has_conflict) {
        setConflicts(conflictResult.conflicts);
        return;
      }
      const result = editing
        ? await api.events.update(editing.id, {
            ...(data as CalendarEventUpdate),
            expected_version: editing.version,
          })
        : await api.events.create(data as CalendarEventCreate);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setEditorOpen(false);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "保存失败，未返回验证成功结果。");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!deleting || deleteText !== deleting.title) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.events.remove(deleting.id);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setDeleting(null);
      setDeleteText("");
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "删除失败。");
    } finally {
      setBusy(false);
    }
  };

  const undoLatest = async () => {
    setBusy(true);
    setError(null);
    try {
      const logs = await api.actionLogs.list(50);
      const latest = latestUndoableEventAction(logs.items);
      if (!latest?.action_id) {
        setError("没有可撤销的最近日历操作。");
        return;
      }
      const result = await api.actions.undo(latest.action_id);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "撤销失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Calendar"
        title="日历"
        description="按月或按周查看课程与学习安排。保存前检查时间冲突，写入后再次验证。"
        actions={
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void undoLatest()}
              disabled={busy}
              className="btn-secondary"
            >
              <RotateCcw size={16} /> 撤销最近操作
            </button>
            <button type="button" onClick={() => openCreate()} className="btn-primary">
              <Plus size={17} /> 新建日程
            </button>
          </div>
        }
      />
      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={() => void load()} compact />
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          className="mb-5 flex items-center gap-2 rounded-2xl border border-teal-100 bg-teal-50 p-4 text-sm font-semibold text-teal-700"
        >
          <Check size={17} /> {notice}
        </div>
      ) : null}
      {loading ? (
        <LoadingState rows={6} />
      ) : events.length === 0 ? (
        <EmptyState
          title="日历还是空的"
          description="新建日程，或使用语音助手在确认后添加。"
          action={
            <button type="button" onClick={() => openCreate()} className="btn-primary">
              <CalendarPlus size={16} /> 新建日程
            </button>
          }
        />
      ) : (
        <section className="surface overflow-hidden p-3 sm:p-5">
          <CalendarView events={events} onEventClick={openEdit} onDateClick={openCreate} />
        </section>
      )}

      <Modal
        open={editorOpen}
        title={editing ? "编辑日程" : "新建日程"}
        description="声程会在保存前查询重叠日程。"
        onClose={() => !busy && setEditorOpen(false)}
        wide
      >
        <EventForm
          key={`${editing?.id ?? "new"}-${defaultStart?.toISOString() ?? ""}`}
          event={editing}
          defaultStart={defaultStart}
          conflicts={conflicts}
          busy={busy}
          onSubmit={save}
          onCancel={() => setEditorOpen(false)}
        />
      </Modal>

      <Modal
        open={Boolean(deleting)}
        title="高风险：删除日程"
        description="请再次输入日程标题完成第二次确认。"
        onClose={() => !busy && setDeleting(null)}
      >
        {deleting ? (
          <div>
            <div className="rounded-2xl border border-coral-100 bg-coral-50 p-4">
              <p className="font-extrabold text-coral-600">{deleting.title}</p>
              <div className="mt-2 flex flex-wrap gap-3 text-sm text-ink-600">
                <span className="inline-flex items-center gap-1">
                  <Clock3 size={14} />
                  {formatDateTime(deleting.start_at)}
                </span>
                {deleting.location ? (
                  <span className="inline-flex items-center gap-1">
                    <MapPin size={14} />
                    {deleting.location}
                  </span>
                ) : null}
              </div>
            </div>
            <label className="mt-4 block">
              <span className="mb-1.5 block text-sm font-bold text-ink-700">输入完整标题确认</span>
              <input
                autoFocus
                value={deleteText}
                onChange={(input) => setDeleteText(input.target.value)}
                className="field"
                placeholder={deleting.title}
              />
            </label>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setDeleting(null)} className="btn-secondary">
                取消
              </button>
              <button
                type="button"
                disabled={busy || deleteText !== deleting.title}
                onClick={() => void remove()}
                className="btn-danger"
              >
                <Trash2 size={16} />
                {busy ? "正在删除并验证" : "再次确认删除"}
              </button>
            </div>
          </div>
        ) : null}
      </Modal>

      {events.length > 0 ? (
        <section className="surface mt-6 p-5">
          <h2 className="mb-4 text-lg font-extrabold text-ink-950">近期日程</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {[...events]
              .sort((a, b) => a.start_at.localeCompare(b.start_at))
              .slice(0, 6)
              .map((event) => (
                <article key={event.id} className="rounded-2xl border border-mist-100 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h3 className="font-bold text-ink-800">{event.title}</h3>
                      <p className="mt-1 text-xs text-ink-400">{formatDateTime(event.start_at)}</p>
                    </div>
                    <div className="flex">
                      <button
                        type="button"
                        onClick={() => openEdit(event)}
                        className="btn-ghost !size-8 !min-h-0 !p-0"
                        aria-label={`编辑${event.title}`}
                      >
                        <Edit3 size={15} />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDeleting(event);
                          setDeleteText("");
                        }}
                        className="btn-ghost !size-8 !min-h-0 !p-0 text-coral-600"
                        aria-label={`删除${event.title}`}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                  {event.location ? (
                    <p className="mt-2 flex items-center gap-1 text-xs text-ink-500">
                      <MapPin size={13} />
                      {event.location}
                    </p>
                  ) : null}
                </article>
              ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
