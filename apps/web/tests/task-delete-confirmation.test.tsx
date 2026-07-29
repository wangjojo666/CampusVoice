import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingAction } from "@campusvoice/shared-types";

import TasksPage from "@/app/tasks/page";

const mocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  prepareRemove: vi.fn(),
  confirm: vi.fn(),
  getAction: vi.fn(),
  cancelAction: vi.fn(),
  execute: vi.fn(),
  listLogs: vi.fn(),
  undo: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function taskRecord(id: string, title: string) {
  return {
    id,
    title,
    description: null,
    course: "机器学习",
    due_at: "2026-07-20T01:00:00Z",
    reminder_at: null,
    priority: "high" as const,
    status: "pending" as const,
    source_type: "manual" as const,
    source_document_id: null,
    created_at: "2026-07-12T12:00:00Z",
    updated_at: "2026-07-12T12:00:00Z",
    version: 1,
  };
}

function pendingTaskAction(status: PendingAction["status"], confirmationCount = 2): PendingAction {
  return {
    id: "action-delete-task",
    action: "delete_task",
    risk_level: "high",
    risk_reasons: ["deletes_data"],
    payload: {},
    status,
    confirmation_count: confirmationCount,
    confirmations_required: 2,
  };
}

async function submitTaskDeleteConfirmations(title = "提交机器学习作业") {
  await screen.findByText(title);
  fireEvent.click(screen.getByRole("button", { name: `删除${title}` }));
  fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
    target: { value: title },
  });
  fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
  await screen.findByLabelText("重新输入完整标题进行第二次确认");
  fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
    target: { value: title },
  });
  fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));
}

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    tasks: {
      list: mocks.listTasks,
      create: vi.fn(),
      update: vi.fn(),
      remove: mocks.prepareRemove,
    },
    actionLogs: { list: mocks.listLogs },
    actions: {
      confirm: mocks.confirm,
      get: mocks.getAction,
      cancel: mocks.cancelAction,
      execute: mocks.execute,
      undo: mocks.undo,
    },
  },
}));

describe("TasksPage destructive confirmation", () => {
  afterEach(cleanup);

  beforeEach(() => {
    const task = {
      id: "task-delete",
      title: "提交机器学习作业",
      description: null,
      course: "机器学习",
      due_at: "2026-07-18T01:00:00Z",
      reminder_at: null,
      priority: "high" as const,
      status: "pending" as const,
      source_type: "manual" as const,
      source_document_id: null,
      created_at: "2026-07-12T12:00:00Z",
      updated_at: "2026-07-12T12:00:00Z",
      version: 1,
    };
    mocks.listTasks.mockReset().mockResolvedValue({ items: [task], total: 1 });
    mocks.prepareRemove.mockReset().mockResolvedValue({
      id: "action-delete-task",
      action: "delete_task",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "awaiting_confirmation",
      confirmation_count: 0,
      confirmations_required: 2,
    });
    mocks.confirm
      .mockReset()
      .mockResolvedValueOnce({
        id: "action-delete-task",
        action: "delete_task",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "awaiting_second_confirmation",
        confirmation_count: 1,
        confirmations_required: 2,
      })
      .mockResolvedValueOnce({
        id: "action-delete-task",
        action: "delete_task",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "ready",
        confirmation_count: 2,
        confirmations_required: 2,
      });
    mocks.getAction.mockReset();
    mocks.cancelAction.mockReset().mockResolvedValue({
      id: "action-delete-task",
      action: "delete_task",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "cancelled",
      confirmation_count: 1,
      confirmations_required: 2,
    });
    mocks.execute.mockReset().mockResolvedValue({
      success: true,
      action: "delete_task",
      record_id: "task-delete",
      verified_fields: { absent: true },
      side_effects: [],
      message: "待办已删除并通过数据库验证",
    });
    mocks.listLogs.mockReset().mockResolvedValue({ items: [], total: 0 });
    mocks.undo.mockReset().mockResolvedValue({
      success: true,
      action: "undo_update_task",
      record_id: "task-delete",
      verified_fields: {},
      side_effects: [],
      message: "待办撤销成功",
    });
  });

  it("does not execute after the first click and executes only after a second click", async () => {
    render(<TasksPage />);
    await waitFor(() => expect(mocks.listTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "删除提交机器学习作业" }));

    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(mocks.prepareRemove).toHaveBeenCalledWith("task-delete");
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(
      await screen.findByText("第一次确认已完成。只有再次点击下方按钮后，系统才会执行删除。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("这一步稳稳落地了")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(2));
    expect(mocks.execute).toHaveBeenCalledWith("action-delete-task");
    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
    expect(screen.getByText("这一步稳稳落地了")).toBeInTheDocument();
  });

  it("skips a newer event action and undoes the latest task action", async () => {
    mocks.listLogs.mockResolvedValue({
      items: [
        {
          id: "event-log",
          action: "create_event",
          action_id: "event-action",
          undoable: true,
          undone: false,
        },
        {
          id: "task-log",
          action: "update_task",
          action_id: "task-action",
          undoable: true,
          undone: false,
        },
      ],
      total: 2,
    });

    render(<TasksPage />);
    await waitFor(() => expect(mocks.listTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("task-action"));
    expect(mocks.undo).not.toHaveBeenCalledWith("event-action");
    expect(await screen.findByText("待办撤销成功")).toBeInTheDocument();
    expect(screen.getByText("已撤回并通过数据库验证。")).toBeInTheDocument();
  });

  it("loads later pages before applying the active-task filter", async () => {
    const completedTasks = Array.from({ length: 500 }, (_, index) => ({
      id: `completed-task-${index}`,
      title: `已完成待办 ${index}`,
      description: null,
      course: "归档课程",
      due_at: null,
      reminder_at: null,
      priority: "low" as const,
      status: "completed" as const,
      source_type: "manual" as const,
      source_document_id: null,
      created_at: "2026-07-12T12:00:00Z",
      updated_at: "2026-07-12T12:00:00Z",
      version: 1,
    }));
    const laterActiveTask = {
      id: "active-task-later-page",
      title: "第二页仍需处理的待办",
      description: null,
      course: "机器学习",
      due_at: "2026-07-20T01:00:00Z",
      reminder_at: null,
      priority: "high" as const,
      status: "pending" as const,
      source_type: "manual" as const,
      source_document_id: null,
      created_at: "2026-07-12T12:00:00Z",
      updated_at: "2026-07-12T12:00:00Z",
      version: 1,
    };
    mocks.listTasks.mockImplementation(({ offset }: { offset: number }) =>
      offset === 0
        ? Promise.resolve({ items: completedTasks, total: 501 })
        : Promise.resolve({ items: [laterActiveTask], total: 501 }),
    );

    render(<TasksPage />);

    expect(await screen.findByText("第二页仍需处理的待办")).toBeInTheDocument();
    expect(mocks.listTasks).toHaveBeenNthCalledWith(1, { limit: 500, offset: 0 });
    expect(mocks.listTasks).toHaveBeenNthCalledWith(2, { limit: 500, offset: 500 });
    expect(mocks.listTasks).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("已完成待办 0")).not.toBeInTheDocument();
  });
  it("keeps the newest task load when an older request resolves last", async () => {
    const staleRequest = deferred<{ items: ReturnType<typeof taskRecord>[]; total: number }>();
    const latestTask = taskRecord("latest-task", "最新加载的待办");
    mocks.listTasks
      .mockReset()
      .mockImplementationOnce(() => staleRequest.promise)
      .mockResolvedValueOnce({ items: [latestTask], total: 1 });
    mocks.listLogs.mockResolvedValue({
      items: [
        {
          id: "task-log-for-refresh",
          action: "update_task",
          action_id: "task-action-for-refresh",
          undoable: true,
          undone: false,
        },
      ],
      total: 1,
    });

    render(<TasksPage />);
    await waitFor(() => expect(mocks.listTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    expect(await screen.findByText("最新加载的待办")).toBeInTheDocument();
    expect(mocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      staleRequest.resolve({ items: [taskRecord("stale-task", "过期加载的待办")], total: 1 });
      await staleRequest.promise;
    });
    expect(screen.getByText("最新加载的待办")).toBeInTheDocument();
    expect(screen.queryByText("过期加载的待办")).not.toBeInTheDocument();
  });

  it("locks cancellation and other delete targets while confirmation is in flight", async () => {
    const firstTask = taskRecord("task-a", "目标 A");
    const secondTask = taskRecord("task-b", "目标 B");
    const prepared = deferred<{
      id: string;
      action: "delete_task";
      risk_level: "high";
      risk_reasons: string[];
      payload: Record<string, unknown>;
      status: "awaiting_confirmation";
      confirmation_count: number;
      confirmations_required: number;
    }>();
    mocks.listTasks.mockResolvedValue({ items: [firstTask, secondTask], total: 2 });
    mocks.prepareRemove.mockReturnValue(prepared.promise);

    render(<TasksPage />);
    await screen.findByText("目标 A");
    fireEvent.click(screen.getByRole("button", { name: "删除目标 A" }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "目标 A" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));

    await waitFor(() => expect(mocks.prepareRemove).toHaveBeenCalledWith("task-a"));
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByLabelText("输入完整标题进行第一次确认")).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除目标 B" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "删除目标 B" }));

    await act(async () => {
      prepared.resolve({
        id: "action-task-a",
        action: "delete_task",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "awaiting_confirmation",
        confirmation_count: 0,
        confirmations_required: 2,
      });
      await prepared.promise;
    });
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(mocks.prepareRemove).toHaveBeenCalledTimes(1);
    expect(screen.getByText("即将删除：")).toHaveTextContent("目标 A");
  });

  it("retries an already-ready delete without confirming it a third time", async () => {
    mocks.execute
      .mockReset()
      .mockRejectedValueOnce(new Error("connection dropped after execute"))
      .mockResolvedValueOnce({
        success: true,
        action: "delete_task",
        record_id: "task-delete",
        verified_fields: { absent: true },
        side_effects: [],
        message: "待办已删除并通过数据库验证",
      });

    render(<TasksPage />);
    await screen.findByText("提交机器学习作业");
    fireEvent.click(screen.getByRole("button", { name: "删除提交机器学习作业" }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
    await screen.findByLabelText("重新输入完整标题进行第二次确认");
    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    expect(await screen.findByText("删除失败。")).toBeInTheDocument();
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "重试删除并验证" }));

    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(2);
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
  });

  it("scans later action-log pages before choosing the newest undoable task action", async () => {
    const unrelatedLogs = Array.from({ length: 500 }, (_, index) => ({
      id: `event-log-${index}`,
      action: "create_event",
      action_id: `event-action-${index}`,
      undoable: true,
      undone: false,
    }));
    mocks.listLogs.mockImplementation((_limit: number, offset: number) =>
      offset === 0
        ? Promise.resolve({ items: unrelatedLogs, total: 1_001 })
        : Promise.resolve({
            items: [
              {
                id: "later-task-log",
                action: "update_task",
                action_id: "later-task-action",
                undoable: true,
                undone: false,
              },
            ],
            total: 1_001,
          }),
    );

    render(<TasksPage />);
    await screen.findByText("提交机器学习作业");
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("later-task-action"));
    expect(mocks.listLogs).toHaveBeenNthCalledWith(1, 500, 0);
    expect(mocks.listLogs).toHaveBeenNthCalledWith(2, 500, 500);
    expect(mocks.listLogs).toHaveBeenCalledTimes(2);
  });
  it("reconciles a lost first-confirm response and still requires fresh second input", async () => {
    mocks.confirm
      .mockReset()
      .mockRejectedValueOnce(new Error("first confirmation response lost"))
      .mockResolvedValueOnce({
        id: "action-delete-task",
        action: "delete_task",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "ready",
        confirmation_count: 2,
        confirmations_required: 2,
      });
    mocks.getAction.mockResolvedValue({
      id: "action-delete-task",
      action: "delete_task",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "awaiting_second_confirmation",
      confirmation_count: 1,
      confirmations_required: 2,
    });

    render(<TasksPage />);
    await screen.findByText("提交机器学习作业");
    fireEvent.click(screen.getByRole("button", { name: "删除提交机器学习作业" }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));

    const secondInput = await screen.findByLabelText("重新输入完整标题进行第二次确认");
    expect(secondInput).toHaveValue("");
    expect(mocks.getAction).toHaveBeenCalledWith("action-delete-task");
    expect(mocks.execute).not.toHaveBeenCalled();

    fireEvent.change(secondInput, { target: { value: "提交机器学习作业" } });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenCalledOnce();
  });

  it("executes after reconciling a lost second-confirm response to READY", async () => {
    mocks.confirm
      .mockReset()
      .mockResolvedValueOnce({
        id: "action-delete-task",
        action: "delete_task",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "awaiting_second_confirmation",
        confirmation_count: 1,
        confirmations_required: 2,
      })
      .mockRejectedValueOnce(new Error("second confirmation response lost"));
    mocks.getAction.mockResolvedValue({
      id: "action-delete-task",
      action: "delete_task",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "ready",
      confirmation_count: 2,
      confirmations_required: 2,
    });

    render(<TasksPage />);
    await screen.findByText("提交机器学习作业");
    fireEvent.click(screen.getByRole("button", { name: "删除提交机器学习作业" }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
    await screen.findByLabelText("重新输入完整标题进行第二次确认");
    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.getAction).toHaveBeenCalledWith("action-delete-task");
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenCalledWith("action-delete-task");
  });

  it("cancels the server-side pending action before closing the delete dialog", async () => {
    render(<TasksPage />);
    await screen.findByText("提交机器学习作业");
    fireEvent.click(screen.getByRole("button", { name: "删除提交机器学习作业" }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
    await screen.findByLabelText("重新输入完整标题进行第二次确认");

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(mocks.cancelAction).toHaveBeenCalledWith("action-delete-task"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "高风险：删除待办" })).not.toBeInTheDocument(),
    );
  });
  it("does not describe a failed initial load as an empty task list", async () => {
    mocks.listTasks.mockRejectedValueOnce(new Error("task load failed"));

    render(<TasksPage />);

    expect(await screen.findByText("无法加载待办。")).toBeInTheDocument();
    expect(screen.queryByText("还没有待办")).not.toBeInTheDocument();
  });

  it("keeps an EXECUTING delete recoverable after cancellation is rejected", async () => {
    mocks.execute
      .mockReset()
      .mockRejectedValueOnce(new Error("execute response lost"))
      .mockResolvedValueOnce({
        success: true,
        action: "delete_task",
        record_id: "task-delete",
        verified_fields: { absent: true },
        side_effects: [],
        message: "待办已删除并通过数据库验证",
      });
    mocks.cancelAction.mockRejectedValueOnce(new Error("executing actions cannot be cancelled"));
    mocks.getAction.mockResolvedValue(pendingTaskAction("executing"));

    render(<TasksPage />);
    await submitTaskDeleteConfirmations();
    expect(await screen.findByText("删除失败。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(mocks.getAction).toHaveBeenCalledWith("action-delete-task"));
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    const recover = await screen.findByRole("button", { name: "恢复删除并验证" });
    expect(recover).toBeEnabled();
    fireEvent.click(recover);

    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenLastCalledWith("action-delete-task");
  });

  it("allows manual review after EXECUTING recovery becomes non-retryable", async () => {
    mocks.execute
      .mockReset()
      .mockRejectedValueOnce(new Error("execute response lost"))
      .mockResolvedValueOnce({
        success: false,
        action: "delete_task",
        action_id: "action-delete-task",
        record_id: "task-delete",
        verified_fields: { absent: false },
        side_effects: [],
        message: "删除结果无法验证",
        retryable: false,
      });
    mocks.cancelAction.mockRejectedValueOnce(new Error("executing actions cannot be cancelled"));
    mocks.getAction.mockResolvedValue(pendingTaskAction("executing"));

    render(<TasksPage />);
    await submitTaskDeleteConfirmations();
    await screen.findByText("删除失败。");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    const recover = await screen.findByRole("button", { name: "恢复删除并验证" });
    fireEvent.click(recover);

    expect(await screen.findByText(/删除结果无法验证.*自动重试已停止/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复删除并验证" })).toBeDisabled();
    const close = screen.getByRole("button", { name: "关闭并重新加载" });
    expect(close).toBeEnabled();
    fireEvent.click(close);

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "高风险：删除待办" })).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText("删除操作 action-delete-task 已停止自动重试，请核对当前数据。"),
    ).toBeInTheDocument();
    await waitFor(() => expect(mocks.listTasks).toHaveBeenCalledTimes(2));
    expect(mocks.cancelAction).toHaveBeenCalledTimes(1);
  });
});
