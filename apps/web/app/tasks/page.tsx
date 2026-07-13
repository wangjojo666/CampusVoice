"use client";

import type { PendingAction, Task, TaskCreate, TaskUpdate } from "@campusvoice/shared-types";
import { Check, ChevronDown, Clock3, Edit3, Plus, RotateCcw, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { TaskForm } from "@/components/tasks/task-form";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Modal } from "@/components/ui/modal";
import { ApiError, api } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format";

const statusLabel = {
  pending: "待处理",
  in_progress: "进行中",
  completed: "已完成",
  cancelled: "已取消",
} as const;
const priorityLabel = { low: "低", medium: "中", high: "高" } as const;

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [status, setStatus] = useState("active");
  const [course, setCourse] = useState("");
  const [query, setQuery] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState<Task | null>(null);
  const [deleteText, setDeleteText] = useState("");
  const [pendingDelete, setPendingDelete] = useState<PendingAction | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.tasks.list();
      setTasks(response.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法加载待办。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const courses = useMemo(
    () =>
      [
        ...new Set(
          tasks.map((task) => task.course).filter((item): item is string => Boolean(item)),
        ),
      ].sort(),
    [tasks],
  );
  const filtered = useMemo(
    () =>
      tasks.filter((task) => {
        if (status === "active" && ["completed", "cancelled"].includes(task.status)) return false;
        if (status !== "all" && status !== "active" && task.status !== status) return false;
        if (course && task.course !== course) return false;
        if (
          query &&
          !`${task.title} ${task.description ?? ""} ${task.course ?? ""}`
            .toLowerCase()
            .includes(query.toLowerCase())
        )
          return false;
        return true;
      }),
    [course, query, status, tasks],
  );

  const openCreate = () => {
    setEditing(null);
    setEditorOpen(true);
    setNotice(null);
  };
  const openEdit = (task: Task) => {
    setEditing(task);
    setEditorOpen(true);
    setNotice(null);
  };

  const save = async (data: TaskCreate | TaskUpdate) => {
    setBusy(true);
    setError(null);
    try {
      const result = editing
        ? await api.tasks.update(editing.id, {
            ...(data as TaskUpdate),
            expected_version: editing.version,
          })
        : await api.tasks.create(data as TaskCreate);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setEditorOpen(false);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "保存失败，未返回成功结果。");
    } finally {
      setBusy(false);
    }
  };

  const complete = async (task: Task) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.tasks.update(task.id, {
        status: task.status === "completed" ? "pending" : "completed",
        expected_version: task.version,
      });
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "状态更新失败。");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!deleting || deleteText !== deleting.title) return;
    setBusy(true);
    setError(null);
    try {
      const action = pendingDelete ?? (await api.tasks.remove(deleting.id));
      const isFirstConfirmation = action.status === "awaiting_confirmation";
      if (!isFirstConfirmation && action.status !== "awaiting_second_confirmation") {
        throw new ApiError("删除操作不在可确认状态，请重新发起。", {
          status: 409,
          details: action,
        });
      }

      if (pendingDelete === null) setPendingDelete(action);
      const updated = await api.actions.confirm(action.id, true);
      setPendingDelete(updated);

      if (isFirstConfirmation) {
        if (updated.status !== "awaiting_second_confirmation") {
          throw new ApiError("第一次确认后的状态不安全，未执行删除。", {
            status: 409,
            details: updated,
          });
        }
        setDeleteText("");
        setNotice("第一次确认已记录。请重新输入标题并完成第二次确认。");
        return;
      }

      if (updated.status !== "ready") {
        throw new ApiError("删除操作尚未获得全部确认，未执行。", {
          status: 409,
          details: updated,
        });
      }
      const result = await api.actions.execute(updated.id);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      setDeleting(null);
      setDeleteText("");
      setPendingDelete(null);
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
      const logs = await api.actionLogs.list(30);
      const target = logs.items.find((log) => log.undoable && !log.undone);
      if (!target) {
        setNotice("没有可撤销的最近操作。");
        return;
      }
      const result = await api.actions.undo(target.action_id ?? target.id);
      if (!result.success) throw new ApiError(result.message, { status: 409, details: result });
      setNotice(result.message);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "撤销失败。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Task manager"
        title="待办"
        description="管理学习任务。每次修改都会以后端事务和提交后的数据库验证结果为准。"
        actions={
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => void undoLatest()}
              className="btn-secondary"
            >
              <RotateCcw size={16} /> 撤销最近操作
            </button>
            <button type="button" onClick={openCreate} className="btn-primary">
              <Plus size={17} /> 新增待办
            </button>
          </>
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
          className="mb-5 flex items-center gap-2 rounded-2xl border border-teal-100 bg-teal-50 p-4 text-sm font-semibold text-teal-700"
        >
          <Check size={17} /> {notice}
        </div>
      ) : null}

      <section className="surface mb-5 grid gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_180px_180px]">
        <label className="relative">
          <span className="sr-only">搜索待办</span>
          <Search
            className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-300"
            size={17}
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="field !pl-9"
            placeholder="搜索标题、说明或课程"
          />
        </label>
        <label className="relative">
          <span className="sr-only">按课程筛选</span>
          <select
            value={course}
            onChange={(event) => setCourse(event.target.value)}
            className="field appearance-none !pr-8"
          >
            <option value="">全部课程</option>
            {courses.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
          <ChevronDown
            className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-ink-300"
            size={16}
          />
        </label>
        <label className="relative">
          <span className="sr-only">按状态筛选</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="field appearance-none !pr-8"
          >
            <option value="active">未完成</option>
            <option value="all">全部状态</option>
            <option value="pending">待处理</option>
            <option value="in_progress">进行中</option>
            <option value="completed">已完成</option>
            <option value="cancelled">已取消</option>
          </select>
          <ChevronDown
            className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-ink-300"
            size={16}
          />
        </label>
      </section>

      {loading ? (
        <LoadingState rows={5} />
      ) : filtered.length === 0 ? (
        <EmptyState
          title={tasks.length === 0 ? "还没有待办" : "没有符合条件的待办"}
          description={
            tasks.length === 0 ? "新增一项待办，或从语音助手创建并验证。" : "尝试清除筛选条件。"
          }
          action={
            tasks.length === 0 ? (
              <button type="button" onClick={openCreate} className="btn-primary">
                <Plus size={16} /> 新增待办
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((task) => (
            <article
              key={task.id}
              className={`surface flex flex-col gap-4 p-4 sm:flex-row sm:items-center ${task.status === "completed" ? "opacity-65" : ""}`}
            >
              <button
                type="button"
                disabled={busy}
                onClick={() => void complete(task)}
                aria-label={
                  task.status === "completed" ? `将${task.title}标为未完成` : `完成${task.title}`
                }
                className={`flex size-9 shrink-0 items-center justify-center rounded-xl border ${task.status === "completed" ? "border-teal-500 bg-teal-500 text-white" : "border-mist-200 bg-white text-transparent hover:border-teal-500 hover:text-teal-500"}`}
              >
                <Check size={17} />
              </button>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h2
                    className={`font-extrabold text-ink-900 ${task.status === "completed" ? "line-through" : ""}`}
                  >
                    {task.title}
                  </h2>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[0.65rem] font-bold ${task.priority === "high" ? "bg-coral-50 text-coral-600" : task.priority === "medium" ? "bg-gold-100/60 text-amber-700" : "bg-teal-50 text-teal-700"}`}
                  >
                    {priorityLabel[task.priority]}优先级
                  </span>
                  <span className="rounded-full bg-mist-100 px-2 py-0.5 text-[0.65rem] font-bold text-ink-500">
                    {statusLabel[task.status]}
                  </span>
                </div>
                {task.description ? (
                  <p className="mt-1 line-clamp-2 text-sm leading-5 text-ink-500">
                    {task.description}
                  </p>
                ) : null}
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink-400">
                  {task.course ? <span>{task.course}</span> : null}
                  <span className="inline-flex items-center gap-1">
                    <Clock3 size={13} /> {task.due_at ? formatDateTime(task.due_at) : "无截止时间"}
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 gap-2 self-end sm:self-auto">
                <button
                  type="button"
                  onClick={() => openEdit(task)}
                  className="btn-ghost !size-9 !min-h-0 !p-0"
                  aria-label={`编辑${task.title}`}
                >
                  <Edit3 size={17} />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDeleting(task);
                    setDeleteText("");
                    setPendingDelete(null);
                  }}
                  className="btn-ghost !size-9 !min-h-0 !p-0 text-coral-600"
                  aria-label={`删除${task.title}`}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        open={editorOpen}
        title={editing ? "编辑待办" : "新增待办"}
        description="请核对关键字段后再确认保存。"
        onClose={() => !busy && setEditorOpen(false)}
      >
        <TaskForm
          key={editing?.id ?? "new"}
          task={editing}
          busy={busy}
          onSubmit={save}
          onCancel={() => setEditorOpen(false)}
        />
      </Modal>
      <Modal
        open={Boolean(deleting)}
        title="高风险：删除待办"
        description={
          pendingDelete?.status === "awaiting_second_confirmation"
            ? "第一次确认已记录。请重新核对目标，并通过独立的第二次交互确认删除。"
            : "删除会改变数据。请输入完整标题并完成第一次确认。"
        }
        onClose={() => {
          if (!busy) {
            setDeleting(null);
            setDeleteText("");
            setPendingDelete(null);
          }
        }}
      >
        {deleting ? (
          <div>
            <div className="rounded-2xl border border-coral-100 bg-coral-50 p-4 text-sm text-coral-600">
              即将删除：<strong>{deleting.title}</strong>
            </div>
            <label className="mt-4 block">
              <span className="mb-1.5 block text-sm font-bold text-ink-700">
                {pendingDelete?.status === "awaiting_second_confirmation"
                  ? "重新输入完整标题进行第二次确认"
                  : "输入完整标题进行第一次确认"}
              </span>
              <input
                autoFocus
                value={deleteText}
                onChange={(event) => setDeleteText(event.target.value)}
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
                onClick={() => {
                  setDeleting(null);
                  setDeleteText("");
                  setPendingDelete(null);
                }}
                className="btn-secondary"
              >
                取消
              </button>
              <button
                type="button"
                disabled={busy || deleteText !== deleting.title}
                onClick={() => void remove()}
                className="btn-danger"
              >
                <Trash2 size={16} />
                {busy
                  ? pendingDelete?.status === "awaiting_second_confirmation"
                    ? "正在删除并验证"
                    : "正在记录第一次确认"
                  : pendingDelete?.status === "awaiting_second_confirmation"
                    ? "第二次确认并删除"
                    : "第一次确认删除"}
              </button>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
