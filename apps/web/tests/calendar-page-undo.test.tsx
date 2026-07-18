import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CalendarPage from "@/app/calendar/page";

const mocks = vi.hoisted(() => ({
  listEvents: vi.fn(),
  listLogs: vi.fn(),
  undo: vi.fn(),
  prepareRemove: vi.fn(),
  confirm: vi.fn(),
  execute: vi.fn(),
}));

vi.mock("next/dynamic", () => ({ default: () => () => <div data-testid="calendar-view" /> }));
vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    events: {
      list: mocks.listEvents,
      checkConflict: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: mocks.prepareRemove,
    },
    actionLogs: { list: mocks.listLogs },
    actions: { undo: mocks.undo, confirm: mocks.confirm, execute: mocks.execute },
  },
}));

describe("CalendarPage undo entry", () => {
  beforeEach(() => {
    mocks.listEvents.mockReset().mockResolvedValue({ items: [], total: 0 });
    mocks.listLogs.mockReset().mockResolvedValue({
      items: [
        {
          id: "log-1",
          action_id: "action-1",
          action: "create_event",
          risk_level: "medium",
          confirmed: true,
          success: true,
          message: "已验证",
          undoable: true,
          undone: false,
          created_at: "2026-07-12T12:00:00Z",
        },
      ],
      total: 1,
    });
    mocks.undo.mockReset().mockResolvedValue({
      success: true,
      action: "undo_create_event",
      record_id: "event-1",
      verified_fields: {},
      side_effects: [],
      message: "撤销已完成并通过数据库验证",
    });
    mocks.prepareRemove.mockReset();
    mocks.confirm.mockReset();
    mocks.execute.mockReset();
  });

  it("undoes the latest event action and refreshes the calendar", async () => {
    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("action-1"));
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("撤销已完成并通过数据库验证")).toBeInTheDocument();
    expect(screen.getByText("已撤回并通过数据库验证。")).toBeInTheDocument();
  });

  it("requires two separate confirmation clicks before deleting an event", async () => {
    const event = {
      id: "event-delete",
      title: "机器学习考试",
      description: null,
      course: "机器学习",
      start_at: "2026-07-18T01:00:00Z",
      end_at: "2026-07-18T03:00:00Z",
      location: "教学楼 A302",
      reminder_minutes: 30,
      source_type: "manual" as const,
      source_document_id: null,
      created_at: "2026-07-12T12:00:00Z",
      updated_at: "2026-07-12T12:00:00Z",
      version: 1,
    };
    mocks.listEvents.mockResolvedValue({ items: [event], total: 1 });
    mocks.prepareRemove.mockResolvedValue({
      id: "action-delete-event",
      action: "delete_event",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "awaiting_confirmation",
      confirmation_count: 0,
      confirmations_required: 2,
    });
    mocks.confirm
      .mockResolvedValueOnce({
        id: "action-delete-event",
        action: "delete_event",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "awaiting_second_confirmation",
        confirmation_count: 1,
        confirmations_required: 2,
      })
      .mockResolvedValueOnce({
        id: "action-delete-event",
        action: "delete_event",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "ready",
        confirmation_count: 2,
        confirmations_required: 2,
      });
    mocks.execute.mockResolvedValue({
      success: true,
      action: "delete_event",
      record_id: "event-delete",
      verified_fields: { absent: true },
      side_effects: [],
      message: "日程已删除并通过数据库验证",
    });

    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "删除机器学习考试" }));

    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: event.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(mocks.prepareRemove).toHaveBeenCalledWith(event.id);
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(
      await screen.findByText("第一次确认已完成。只有再次点击下方按钮后，系统才会执行删除。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("这一步稳稳落地了")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: event.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(2));
    expect(mocks.execute).toHaveBeenCalledWith("action-delete-event");
    expect(await screen.findByText("日程已删除并通过数据库验证")).toBeInTheDocument();
    expect(screen.getByText("这一步稳稳落地了")).toBeInTheDocument();
  });
});
