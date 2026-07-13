import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TasksPage from "@/app/tasks/page";

const mocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  prepareRemove: vi.fn(),
  confirm: vi.fn(),
  execute: vi.fn(),
}));

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
    actionLogs: { list: vi.fn() },
    actions: {
      confirm: mocks.confirm,
      execute: mocks.execute,
      undo: vi.fn(),
    },
  },
}));

describe("TasksPage destructive confirmation", () => {
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
    mocks.execute.mockReset().mockResolvedValue({
      success: true,
      action: "delete_task",
      record_id: "task-delete",
      verified_fields: { absent: true },
      side_effects: [],
      message: "待办已删除并通过数据库验证",
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

    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: "提交机器学习作业" },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(2));
    expect(mocks.execute).toHaveBeenCalledWith("action-delete-task");
    expect(await screen.findByText("待办已删除并通过数据库验证")).toBeInTheDocument();
  });
});
