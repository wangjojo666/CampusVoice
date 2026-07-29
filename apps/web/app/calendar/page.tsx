"use client";

import type {
  CalendarEvent,
  CalendarEventCreate,
  CalendarEventUpdate,
  EventConflict,
  PendingAction,
} from "@campusvoice/shared-types";
import { CalendarPlus, Check, Clock3, Edit3, MapPin, Plus, RotateCcw, Trash2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { VerifiedFinish } from "@/components/actions/verified-finish";
import type { CalendarRange } from "@/components/calendar/calendar-view";
import { EventForm } from "@/components/calendar/event-form";
import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Modal } from "@/components/ui/modal";
import {
  confirmActionAndReconcile,
  isActionExecutionRecoveryStatus,
} from "@/lib/action-confirmation";
import { ApiError, api } from "@/lib/api-client";
import { latestUndoableEventAction } from "@/lib/calendar/undo";
import { firstValidInstantOfLocalDay, localDateKey } from "@/lib/dashboard/local-days";
import { formatDateTime } from "@/lib/format";
import { collectAllPages } from "@/lib/pagination";
import { useUserSettings } from "@/lib/user-settings";
import { createVerifiedFinishEvent, type VerifiedFinishEvent } from "@/lib/verified-finish";

const CalendarView = dynamic(
  () => import("@/components/calendar/calendar-view").then((module) => module.CalendarView),
  {
    ssr: false,
    loading: () => <LoadingState rows={5} label="正在加载日历" />,
  },
);

function currentMonthRange(timezone: string): CalendarRange {
  const monthStart = `${localDateKey(new Date(), timezone).slice(0, 7)}-01`;
  const nextMonth = new Date(`${monthStart}T00:00:00.000Z`);
  nextMonth.setUTCMonth(nextMonth.getUTCMonth() + 1);
  const nextMonthStart = nextMonth.toISOString().slice(0, 10);
  return {
    start: new Date(firstValidInstantOfLocalDay(monthStart, timezone)).toISOString(),
    end: new Date(firstValidInstantOfLocalDay(nextMonthStart, timezone)).toISOString(),
  };
}
export default function CalendarPage() {
  const userSettings = useUserSettings();
  const timezone = userSettings.timezone;
  const [rangeState, setRangeState] = useState<{
    timezone: string;
    range: CalendarRange;
  }>(() => ({
    timezone,
    range: currentMonthRange(timezone),
  }));
  const visibleRange =
    rangeState.timezone === timezone ? rangeState.range : currentMonthRange(timezone);
  const visibleRangeRef = useRef(visibleRange);
  const loadGenerationRef = useRef(0);
  const deleteGenerationRef = useRef(0);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [verifiedFinish, setVerifiedFinish] = useState<VerifiedFinishEvent | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<CalendarEvent | null>(null);
  const [defaultStart, setDefaultStart] = useState<Date | null>(null);
  const [conflicts, setConflicts] = useState<EventConflict[]>([]);
  const [deleting, setDeleting] = useState<CalendarEvent | null>(null);
  const [deleteText, setDeleteText] = useState("");
  const [pendingDelete, setPendingDelete] = useState<PendingAction | null>(null);
  const [deleteRetryBlocked, setDeleteRetryBlocked] = useState(false);
  const deleteCanResumeExecution = isActionExecutionRecoveryStatus(pendingDelete?.status);
  const deleteNeedsExecutionRecovery =
    pendingDelete?.status === "executing" || pendingDelete?.status === "executed";
  const deleteCancellationBlocked = !deleteRetryBlocked && deleteNeedsExecutionRecovery;

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    const range = visibleRangeRef.current;
    setLoading(true);
    setError(null);
    setEvents([]);
    try {
      const items = await collectAllPages(
        ({ limit, offset }) =>
          api.events.list({
            start: range.start,
            end: range.end,
            limit,
            offset,
          }),
        {
          getKey: (event) => event.id,
          shouldContinue: () => generation === loadGenerationRef.current,
        },
      );
      if (generation !== loadGenerationRef.current) return;
      setEvents(items);
    } catch (reason) {
      if (generation !== loadGenerationRef.current) return;
      setError(reason instanceof ApiError ? reason.userMessage : "无法加载日历。");
    } finally {
      if (generation === loadGenerationRef.current) {
        setLoading(false);
        setHasLoaded(true);
      }
    }
  }, []);
  useEffect(() => {
    visibleRangeRef.current = { start: visibleRange.start, end: visibleRange.end };
  }, [visibleRange.end, visibleRange.start]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      loadGenerationRef.current += 1;
    };
  }, [load, visibleRange.end, visibleRange.start]);

  const updateVisibleRange = useCallback(
    (range: CalendarRange) => {
      visibleRangeRef.current = range;
      setRangeState((current) =>
        current.timezone === timezone &&
        current.range.start === range.start &&
        current.range.end === range.end
          ? current
          : { timezone, range },
      );
    },
    [timezone],
  );

  const openCreate = (date?: Date) => {
    setEditing(null);
    setDefaultStart(date ?? new Date());
    setConflicts([]);
    setEditorOpen(true);
    setNotice(null);
    setVerifiedFinish(null);
  };
  const openEdit = (event: CalendarEvent) => {
    setEditing(event);
    setDefaultStart(null);
    setConflicts([]);
    setEditorOpen(true);
    setNotice(null);
    setVerifiedFinish(null);
  };

  const checkConflictsForReview = async (
    data: CalendarEventCreate | Omit<CalendarEventUpdate, "expected_version">,
  ) => {
    if (!data.start_at || !data.end_at) {
      setError("请同时填写开始与结束时间，以便检查冲突。");
      return false;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    setVerifiedFinish(null);
    setConflicts([]);
    try {
      const result = await api.events.checkConflict({
        start_at: data.start_at,
        end_at: data.end_at,
        exclude_event_id: editing?.id,
      });
      setConflicts(result.conflicts);
      return true;
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法完成时间冲突检查。");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async (
    data: CalendarEventCreate | Omit<CalendarEventUpdate, "expected_version">,
  ) => {
    if (!data.start_at || !data.end_at) {
      setError("请同时填写开始与结束时间，以便检查冲突。");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    setVerifiedFinish(null);
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
            ...(data as Omit<CalendarEventUpdate, "expected_version">),
            expected_version: editing.version,
          })
        : await api.events.create(data as CalendarEventCreate);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setVerifiedFinish(createVerifiedFinishEvent(result, "execute"));
      setEditorOpen(false);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "保存失败，未返回验证成功结果。");
    } finally {
      setBusy(false);
    }
  };

  const setDeleteTarget = useCallback((target: CalendarEvent | null) => {
    deleteGenerationRef.current += 1;
    setDeleting(target);
    setDeleteText("");
    setPendingDelete(null);
    setDeleteRetryBlocked(false);
  }, []);

  const dismissDeleteForReview = async () => {
    const actionId = pendingDelete?.id;
    setNotice(
      actionId
        ? `删除操作 ${actionId} 已停止自动重试，请核对当前数据。`
        : "删除操作已停止自动重试，请核对当前数据。",
    );
    setBusy(false);
    setDeleteTarget(null);
    await load();
  };

  const cancelDelete = async () => {
    const action = pendingDelete;
    if (!action) {
      setDeleteTarget(null);
      return;
    }
    const generation = deleteGenerationRef.current;
    const isCurrent = () => generation === deleteGenerationRef.current;
    setBusy(true);
    setError(null);
    try {
      await api.actions.cancel(action.id);
      if (!isCurrent()) return;
      setBusy(false);
      setDeleteTarget(null);
    } catch (reason) {
      if (!isCurrent()) return;
      try {
        const current = await api.actions.get(action.id);
        if (!isCurrent()) return;
        setPendingDelete(current);
        if (current.status === "executing" || current.status === "executed") {
          setError("删除已进入执行或验证恢复阶段，无法取消。请恢复同一操作并获取权威结果。");
          return;
        }
        if (["cancelled", "expired", "undone"].includes(current.status)) {
          setNotice("删除操作已结束，已重新加载当前数据。");
          setBusy(false);
          setDeleteTarget(null);
          await load();
          return;
        }
      } catch {
        // Keep the original cancellation error when authoritative state cannot be read.
      }
      setError(reason instanceof ApiError ? reason.userMessage : "取消删除失败，请重试。");
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const remove = async () => {
    const target = deleting;
    if (!target || (!deleteCanResumeExecution && deleteText !== target.title)) return;
    const generation = deleteGenerationRef.current;
    const isCurrent = () => generation === deleteGenerationRef.current;
    setBusy(true);
    setError(null);
    setNotice(null);
    setVerifiedFinish(null);
    try {
      let action = pendingDelete;
      if (!action) {
        action = await api.events.remove(target.id);
        if (!isCurrent()) return;
        setPendingDelete(action);
      }

      if (!isActionExecutionRecoveryStatus(action.status)) {
        const isFirstConfirmation = action.status === "awaiting_confirmation";
        if (!isFirstConfirmation && action.status !== "awaiting_second_confirmation") {
          throw new ApiError("删除操作不在可确认状态，请重新发起。", {
            status: 409,
            details: action,
          });
        }

        const outcome = await confirmActionAndReconcile(action.id);
        if (!isCurrent()) return;
        const updated = outcome.action;

        if (isFirstConfirmation) {
          if (updated.status === "awaiting_second_confirmation") {
            setPendingDelete(updated);
            setDeleteText("");
            setNotice("第一次确认已记录。请重新输入标题并完成第二次确认。");
            return;
          }
          if (outcome.confirmationError) throw outcome.confirmationError;
          throw new ApiError("第一次确认后的状态不安全，未执行删除。", {
            status: 409,
            details: updated,
          });
        }

        setPendingDelete(updated);
        if (updated.status !== "ready" && updated.status !== "failed") {
          if (outcome.confirmationError) throw outcome.confirmationError;
          throw new ApiError("删除操作尚未获得全部确认，未执行。", {
            status: 409,
            details: updated,
          });
        }
        action = updated;
      }

      const result = await api.actions.execute(action.id);
      if (!isCurrent()) return;
      if (!result.success) {
        if (result.retryable === false) {
          setDeleteRetryBlocked(true);
          setError(`${result.message} 自动重试已停止，请人工核对当前数据。`);
          return;
        }
        throw new ApiError(result.message, { status: 409, details: result });
      }
      setNotice(result.message);
      setVerifiedFinish(createVerifiedFinishEvent(result, "execute"));
      setDeleting(null);
      setDeleteText("");
      setPendingDelete(null);
      await load();
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof ApiError && reason.code === "retry_limit_reached") {
        setDeleteRetryBlocked(true);
        setError(`${reason.userMessage} 自动重试已停止，请人工核对当前数据。`);
        return;
      }
      setError(reason instanceof ApiError ? reason.userMessage : "删除失败。");
    } finally {
      if (isCurrent()) setBusy(false);
    }
  };

  const undoLatest = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setVerifiedFinish(null);
    try {
      const logs = await collectAllPages(
        ({ limit, offset }) => api.actionLogs.list(limit, offset),
        {
          getKey: (log) => log.id,
          shouldStop: (page) => Boolean(latestUndoableEventAction(page)),
        },
      );
      const latest = latestUndoableEventAction(logs);
      if (!latest?.action_id) {
        setError("没有可撤销的最近日历操作。");
        return;
      }
      const result = await api.actions.undo(latest.action_id);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setVerifiedFinish(createVerifiedFinishEvent(result, "undo"));
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
        eyebrow="我的日程"
        title="日程"
        description="按月或按周查看课程与校园安排。保存前检查时间冲突，写入后再次验证。"
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
          <ErrorState message={error} onRetry={loading ? undefined : () => void load()} compact />
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          className="mb-5 rounded-2xl border border-teal-100 bg-teal-50 p-4 text-sm font-semibold text-teal-700"
        >
          <div className="flex items-center gap-2">
            <Check size={17} aria-hidden="true" /> {notice}
          </div>
          {verifiedFinish ? (
            <div className="mt-3">
              <VerifiedFinish key={verifiedFinish.id} event={verifiedFinish} />
            </div>
          ) : null}
        </div>
      ) : null}
      {!hasLoaded ? (
        <LoadingState rows={6} />
      ) : (
        <>
          {loading ? (
            <p role="status" className="mb-3 text-sm font-semibold text-ink-500">
              正在加载当前可见范围…
            </p>
          ) : null}
          {!loading && !error && events.length === 0 ? (
            <div className="mb-5">
              <EmptyState
                title="当前可见范围暂无日程"
                description="可以切换月份或周继续查看，也可以直接新建日程。"
                action={
                  <button type="button" onClick={() => openCreate()} className="btn-primary">
                    <CalendarPlus size={16} /> 新建日程
                  </button>
                }
              />
            </div>
          ) : null}
          <section className="surface overflow-hidden p-3 sm:p-5" aria-busy={loading}>
            <CalendarView
              key={timezone}
              events={events}
              onEventClick={openEdit}
              onDateClick={openCreate}
              onRangeChange={updateVisibleRange}
            />
          </section>
        </>
      )}

      <Modal
        open={editorOpen}
        title={editing ? "编辑日程" : "新建日程"}
        description="声程会在保存前查询重叠日程。"
        onClose={() => !busy && setEditorOpen(false)}
        wide
      >
        <EventForm
          key={`${editing?.id ?? "new"}-${defaultStart?.toISOString() ?? ""}-${userSettings.timezone}-${userSettings.default_reminder_minutes}`}
          event={editing}
          defaultStart={defaultStart}
          timezone={userSettings.timezone}
          defaultReminderMinutes={userSettings.default_reminder_minutes}
          conflicts={conflicts}
          busy={busy}
          onCheckConflicts={checkConflictsForReview}
          onSubmit={save}
          onConflictReset={() => setConflicts([])}
          onCancel={() => setEditorOpen(false)}
        />
      </Modal>

      <Modal
        open={Boolean(deleting)}
        title="高风险：删除日程"
        description={
          deleteRetryBlocked
            ? `自动重试已停止。请人工核对当前数据；操作编号：${pendingDelete?.id ?? "未知"}。`
            : deleteNeedsExecutionRecovery
              ? "删除已进入执行或验证恢复阶段，无法取消。请恢复同一操作并获取权威结果。"
              : pendingDelete?.status === "ready" || pendingDelete?.status === "failed"
                ? "删除已完成两次确认，但执行或结果回传未成功。可安全重试同一操作。"
                : pendingDelete?.status === "awaiting_second_confirmation"
                  ? "第一次确认已记录。请重新核对目标，并通过独立的第二次交互确认删除。"
                  : "请输入完整标题并完成第一次确认。"
        }
        onClose={() => {
          if (!busy && !deleteCancellationBlocked) {
            if (deleteRetryBlocked) void dismissDeleteForReview();
            else void cancelDelete();
          }
        }}
      >
        {deleting ? (
          <div>
            <div className="rounded-2xl border border-coral-100 bg-coral-50 p-4">
              <p className="font-extrabold text-coral-600">{deleting.title}</p>
              <div className="mt-2 flex flex-wrap gap-3 text-sm text-ink-600">
                <span className="inline-flex items-center gap-1">
                  <Clock3 size={14} />
                  {formatDateTime(deleting.start_at, { timeZone: userSettings.timezone })}
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
              <span className="mb-1.5 block text-sm font-bold text-ink-700">
                {deleteCanResumeExecution
                  ? "该操作已完成确认，无需再次输入标题"
                  : pendingDelete?.status === "awaiting_second_confirmation"
                    ? "重新输入完整标题进行第二次确认"
                    : "输入完整标题进行第一次确认"}
              </span>
              <input
                autoFocus
                value={deleteText}
                disabled={busy || deleteRetryBlocked || deleteCanResumeExecution}
                onChange={(input) => setDeleteText(input.target.value)}
                className="field"
                placeholder={deleting.title}
              />
            </label>
            {pendingDelete?.status === "awaiting_second_confirmation" ? (
              <p className="mt-3 rounded-xl border border-coral-100 bg-white p-3 text-sm font-semibold text-coral-600">
                第一次确认已完成。只有再次点击下方按钮后，系统才会执行删除。
              </p>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={busy || deleteCancellationBlocked}
                onClick={() =>
                  deleteRetryBlocked ? void dismissDeleteForReview() : void cancelDelete()
                }
                className="btn-secondary"
              >
                {deleteRetryBlocked ? "关闭并重新加载" : "取消"}
              </button>
              <button
                type="button"
                disabled={
                  busy ||
                  deleteRetryBlocked ||
                  (!deleteCanResumeExecution && deleteText !== deleting.title)
                }
                onClick={() => void remove()}
                className="btn-danger"
              >
                <Trash2 size={16} />
                {busy
                  ? deleteNeedsExecutionRecovery
                    ? "正在恢复删除并验证"
                    : pendingDelete?.status === "ready" || pendingDelete?.status === "failed"
                      ? "正在重试删除并验证"
                      : pendingDelete?.status === "awaiting_second_confirmation"
                        ? "正在删除并验证"
                        : "正在记录第一次确认"
                  : deleteNeedsExecutionRecovery
                    ? "恢复删除并验证"
                    : pendingDelete?.status === "ready" || pendingDelete?.status === "failed"
                      ? "重试删除并验证"
                      : pendingDelete?.status === "awaiting_second_confirmation"
                        ? "第二次确认并删除"
                        : "第一次确认删除"}
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
                      <p className="mt-1 text-xs text-ink-400">
                        {formatDateTime(event.start_at, { timeZone: userSettings.timezone })}
                      </p>
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
                        disabled={busy}
                        onClick={() => setDeleteTarget(event)}
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
