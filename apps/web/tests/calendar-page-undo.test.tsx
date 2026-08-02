import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingAction } from "@campusvoice/shared-types";

import CalendarPage from "@/app/calendar/page";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";

const mocks = vi.hoisted(() => ({
  listEvents: vi.fn(),
  listLogs: vi.fn(),
  undo: vi.fn(),
  prepareRemove: vi.fn(),
  confirm: vi.fn(),
  getAction: vi.fn(),
  cancelAction: vi.fn(),
  execute: vi.fn(),
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

function eventRecord(id: string, title: string) {
  return {
    id,
    title,
    description: null,
    course: "机器学习",
    start_at: "2026-08-18T01:00:00Z",
    end_at: "2026-08-18T03:00:00Z",
    location: "教学楼 A302",
    reminder_minutes: 30,
    source_type: "manual" as const,
    source_document_id: null,
    created_at: "2026-07-12T12:00:00Z",
    updated_at: "2026-07-12T12:00:00Z",
    version: 1,
  };
}

function pendingEventAction(
  id: string,
  status: PendingAction["status"],
  confirmationCount = 2,
): PendingAction {
  return {
    id,
    action: "delete_event",
    risk_level: "high",
    risk_reasons: ["deletes_data"],
    payload: {},
    status,
    confirmation_count: confirmationCount,
    confirmations_required: 2,
  };
}

function arrangeEventDelete(event: ReturnType<typeof eventRecord>) {
  const actionId = `action-${event.id}`;
  mocks.listEvents.mockResolvedValue({ items: [event], total: 1 });
  mocks.prepareRemove.mockResolvedValue(pendingEventAction(actionId, "awaiting_confirmation", 0));
  mocks.confirm
    .mockReset()
    .mockResolvedValueOnce(pendingEventAction(actionId, "awaiting_second_confirmation", 1))
    .mockResolvedValueOnce(pendingEventAction(actionId, "ready", 2));
  mocks.execute.mockResolvedValue({
    success: true,
    action: "delete_event",
    record_id: event.id,
    verified_fields: { absent: true },
    side_effects: [],
    message: "日程已删除并通过数据库验证",
  });
  return actionId;
}

async function submitEventDeleteConfirmations(event: ReturnType<typeof eventRecord>) {
  await screen.findAllByText(event.title);
  fireEvent.click(screen.getByRole("button", { name: `删除${event.title}` }));
  fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
    target: { value: event.title },
  });
  fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
  await screen.findByLabelText("重新输入完整标题进行第二次确认");
  fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
    target: { value: event.title },
  });
  fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));
}

vi.mock("next/dynamic", () => ({
  default:
    () =>
    ({
      events,
      onRangeChange,
    }: {
      events: Array<{ id: string; title: string }>;
      onRangeChange: (range: { start: string; end: string }) => void;
    }) => (
      <div data-testid="calendar-view">
        <button
          type="button"
          onClick={() =>
            onRangeChange({
              start: "2026-08-01T00:00:00.000Z",
              end: "2026-09-01T00:00:00.000Z",
            })
          }
        >
          切换日历范围
        </button>
        {events.map((event) => (
          <span key={event.id}>{event.title}</span>
        ))}
      </div>
    ),
}));
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
    actions: {
      undo: mocks.undo,
      confirm: mocks.confirm,
      get: mocks.getAction,
      cancel: mocks.cancelAction,
      execute: mocks.execute,
    },
  },
}));

describe("CalendarPage undo entry", () => {
  afterEach(cleanup);
  beforeEach(() => {
    setCurrentUserSettings(DEFAULT_USER_SETTINGS);
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
    mocks.getAction.mockReset();
    mocks.cancelAction.mockReset().mockResolvedValue({
      id: "action-delete-event",
      action: "delete_event",
      risk_level: "high",
      risk_reasons: ["deletes_data"],
      payload: {},
      status: "cancelled",
      confirmation_count: 1,
      confirmations_required: 2,
    });
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

  it("retries the same event action when the first undo response is lost", async () => {
    mocks.listLogs.mockResolvedValue({
      items: [
        {
          id: "latest-event-log",
          action_id: "latest-event-action",
          action: "update_event",
          undoable: true,
          undone: false,
        },
        {
          id: "older-event-log",
          action_id: "older-event-action",
          action: "create_event",
          undoable: true,
          undone: false,
        },
      ],
      total: 2,
    });
    mocks.undo
      .mockReset()
      .mockRejectedValueOnce(new Error("undo response lost"))
      .mockResolvedValueOnce({
        success: true,
        action: "undo_update_event",
        record_id: "event-1",
        verified_fields: {},
        side_effects: [],
        message: "撤销已完成并通过数据库验证",
      });

    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));
    expect(await screen.findByText("撤销失败，请重试。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledTimes(2));
    expect(mocks.undo.mock.calls.map(([actionId]) => actionId)).toEqual([
      "latest-event-action",
      "latest-event-action",
    ]);
    expect(mocks.undo).not.toHaveBeenCalledWith("older-event-action");
    expect(mocks.listLogs).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("撤销已完成并通过数据库验证")).toBeInTheDocument();
  });

  it("keeps an empty month navigable and loads every page for the selected range", async () => {
    const rangeStart = "2026-08-01T00:00:00.000Z";
    const rangeEnd = "2026-09-01T00:00:00.000Z";
    const firstPage = Array.from({ length: 500 }, (_, index) => ({
      id: `event-page-one-${index}`,
      title: `首批日程 ${index}`,
      start_at: "2026-08-02T01:00:00.000Z",
    }));
    const laterEvent = {
      id: "event-later-page",
      title: "第二页的重要日程",
      start_at: "2026-08-20T01:00:00.000Z",
    };
    mocks.listEvents.mockImplementation(
      (request: { start: string; end: string; limit: number; offset: number }) => {
        if (request.start !== rangeStart) return Promise.resolve({ items: [], total: 0 });
        return request.offset === 0
          ? Promise.resolve({ items: firstPage, total: 501 })
          : Promise.resolve({ items: [laterEvent], total: 501 });
      },
    );

    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));

    expect(mocks.listEvents).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        start: expect.any(String),
        end: expect.any(String),
        limit: 500,
        offset: 0,
      }),
    );
    expect(screen.getByTestId("calendar-view")).toBeInTheDocument();
    expect(screen.getByText("当前可见范围暂无日程")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "切换日历范围" }));

    expect(await screen.findByText("第二页的重要日程")).toBeInTheDocument();
    expect(mocks.listEvents).toHaveBeenNthCalledWith(2, {
      start: rangeStart,
      end: rangeEnd,
      limit: 500,
      offset: 0,
    });
    expect(mocks.listEvents).toHaveBeenNthCalledWith(3, {
      start: rangeStart,
      end: rangeEnd,
      limit: 500,
      offset: 500,
    });

    fireEvent.click(screen.getByRole("button", { name: "切换日历范围" }));
    expect(mocks.listEvents).toHaveBeenCalledTimes(3);
  });

  it("ignores a stale range response after the timezone changes", async () => {
    type EventPage = {
      items: Array<{ id: string; title: string; start_at: string }>;
      total: number;
    };
    const staleEvent = {
      id: "stale-event",
      title: "stale range event",
      start_at: "2026-07-02T01:00:00.000Z",
    };
    const latestEvent = {
      id: "latest-event",
      title: "latest range event",
      start_at: "2026-07-03T01:00:00.000Z",
    };
    const firstRequest = deferred<EventPage>();
    const latestRequest = deferred<EventPage>();

    mocks.listEvents
      .mockReset()
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(() => latestRequest.promise);

    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));

    act(() => {
      setCurrentUserSettings({ ...DEFAULT_USER_SETTINGS, timezone: "UTC" });
    });
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(2));

    await act(async () => {
      latestRequest.resolve({ items: [latestEvent], total: 1 });
      await latestRequest.promise;
    });
    expect(await screen.findAllByText("latest range event")).toHaveLength(2);

    await act(async () => {
      firstRequest.resolve({ items: [staleEvent], total: 1 });
      await firstRequest.promise;
    });
    await waitFor(() => expect(screen.getAllByText("latest range event")).toHaveLength(2));
    expect(screen.queryByText("stale range event")).not.toBeInTheDocument();
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
  it("keeps the mounted calendar view while a new range is loading", async () => {
    const nextRange = deferred<{
      items: ReturnType<typeof eventRecord>[];
      total: number;
    }>();
    mocks.listEvents
      .mockReset()
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockImplementationOnce(() => nextRange.promise);

    render(<CalendarPage />);
    const originalView = await screen.findByTestId("calendar-view");
    fireEvent.click(screen.getByRole("button", { name: "切换日历范围" }));

    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("calendar-view")).toBe(originalView);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载当前可见范围");

    await act(async () => {
      nextRange.resolve({ items: [eventRecord("range-event", "新范围日程")], total: 1 });
      await nextRange.promise;
    });
    expect(await screen.findAllByText("新范围日程")).toHaveLength(2);
    expect(screen.getByTestId("calendar-view")).toBe(originalView);
  });

  it("clears old-range events when a new range fails without unmounting the calendar", async () => {
    const oldEvent = eventRecord("old-range-event", "旧范围日程");
    mocks.listEvents
      .mockReset()
      .mockResolvedValueOnce({ items: [oldEvent], total: 1 })
      .mockRejectedValueOnce(new Error("range request failed"));

    render(<CalendarPage />);
    await screen.findAllByText("旧范围日程");
    const originalView = screen.getByTestId("calendar-view");
    fireEvent.click(screen.getByRole("button", { name: "切换日历范围" }));

    expect(await screen.findByText("无法加载日历。")).toBeInTheDocument();
    expect(screen.queryByText("当前可见范围暂无日程")).not.toBeInTheDocument();
    expect(screen.queryByText("旧范围日程")).not.toBeInTheDocument();
    expect(screen.getByTestId("calendar-view")).toBe(originalView);
    expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
  });

  it("retries an already-ready event delete without confirming it a third time", async () => {
    const event = eventRecord("event-retry", "需要重试删除的日程");
    mocks.listEvents.mockResolvedValue({ items: [event], total: 1 });
    mocks.prepareRemove.mockResolvedValue({
      id: "action-delete-event-retry",
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
        id: "action-delete-event-retry",
        action: "delete_event",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "awaiting_second_confirmation",
        confirmation_count: 1,
        confirmations_required: 2,
      })
      .mockResolvedValueOnce({
        id: "action-delete-event-retry",
        action: "delete_event",
        risk_level: "high",
        risk_reasons: ["deletes_data"],
        payload: {},
        status: "ready",
        confirmation_count: 2,
        confirmations_required: 2,
      });
    mocks.execute
      .mockRejectedValueOnce(new Error("connection dropped after execute"))
      .mockResolvedValueOnce({
        success: true,
        action: "delete_event",
        record_id: event.id,
        verified_fields: { absent: true },
        side_effects: [],
        message: "日程已删除并通过数据库验证",
      });

    render(<CalendarPage />);
    await screen.findAllByText(event.title);
    fireEvent.click(screen.getByRole("button", { name: `删除${event.title}` }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: event.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));
    await screen.findByLabelText("重新输入完整标题进行第二次确认");
    fireEvent.change(screen.getByLabelText("重新输入完整标题进行第二次确认"), {
      target: { value: event.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    expect(await screen.findByText("删除失败。")).toBeInTheDocument();
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "重试删除并验证" }));

    expect(await screen.findByText("日程已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(2);
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
  });

  it("scans later action-log pages before choosing the newest undoable event action", async () => {
    const unrelatedLogs = Array.from({ length: 500 }, (_, index) => ({
      id: `task-log-${index}`,
      action: "update_task",
      action_id: `task-action-${index}`,
      undoable: true,
      undone: false,
    }));
    mocks.listLogs.mockImplementation((_limit: number, offset: number) =>
      offset === 0
        ? Promise.resolve({ items: unrelatedLogs, total: 1_001 })
        : Promise.resolve({
            items: [
              {
                id: "later-event-log",
                action: "update_event",
                action_id: "later-event-action",
                undoable: true,
                undone: false,
              },
            ],
            total: 1_001,
          }),
    );

    render(<CalendarPage />);
    await screen.findByTestId("calendar-view");
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("later-event-action"));
    expect(mocks.listLogs).toHaveBeenNthCalledWith(1, 500, 0);
    expect(mocks.listLogs).toHaveBeenNthCalledWith(2, 500, 500);
    expect(mocks.listLogs).toHaveBeenCalledTimes(2);
  });
  it("reloads the latest visible range when undo finishes after month navigation", async () => {
    const rangeStart = "2026-08-01T00:00:00.000Z";
    const rangeEnd = "2026-09-01T00:00:00.000Z";
    const currentRangeEvent = eventRecord("current-range-event", "当前月份日程");
    const undoRequest = deferred<{
      success: boolean;
      action: string;
      record_id: string;
      verified_fields: Record<string, unknown>;
      side_effects: unknown[];
      message: string;
    }>();
    mocks.listEvents.mockImplementation(
      (request: { start: string; end: string; limit: number; offset: number }) =>
        request.start === rangeStart
          ? Promise.resolve({ items: [currentRangeEvent], total: 1 })
          : Promise.resolve({ items: [], total: 0 }),
    );
    mocks.undo.mockReturnValue(undoRequest.promise);

    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));
    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("action-1"));

    fireEvent.click(screen.getByRole("button", { name: "切换日历范围" }));
    expect(await screen.findAllByText("当前月份日程")).toHaveLength(2);

    await act(async () => {
      undoRequest.resolve({
        success: true,
        action: "undo_create_event",
        record_id: "event-1",
        verified_fields: {},
        side_effects: [],
        message: "撤销已完成并通过数据库验证",
      });
      await undoRequest.promise;
    });

    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(3));
    expect(mocks.listEvents).toHaveBeenLastCalledWith({
      start: rangeStart,
      end: rangeEnd,
      limit: 500,
      offset: 0,
    });
    expect(screen.getAllByText("当前月份日程")).toHaveLength(2);
  });
  it("reconciles a lost first event confirmation and requires fresh second input", async () => {
    const event = eventRecord("event-lost-first", "第一次确认响应丢失日程");
    const actionId = arrangeEventDelete(event);
    mocks.confirm
      .mockReset()
      .mockRejectedValueOnce(new Error("first confirmation response lost"))
      .mockResolvedValueOnce(pendingEventAction(actionId, "ready", 2));
    mocks.getAction.mockResolvedValue(
      pendingEventAction(actionId, "awaiting_second_confirmation", 1),
    );

    render(<CalendarPage />);
    await screen.findAllByText(event.title);
    fireEvent.click(screen.getByRole("button", { name: `删除${event.title}` }));
    fireEvent.change(screen.getByLabelText("输入完整标题进行第一次确认"), {
      target: { value: event.title },
    });
    fireEvent.click(screen.getByRole("button", { name: "第一次确认删除" }));

    const secondInput = await screen.findByLabelText("重新输入完整标题进行第二次确认");
    expect(secondInput).toHaveValue("");
    expect(mocks.getAction).toHaveBeenCalledWith(actionId);
    expect(mocks.execute).not.toHaveBeenCalled();
    fireEvent.change(secondInput, { target: { value: event.title } });
    fireEvent.click(screen.getByRole("button", { name: "第二次确认并删除" }));

    expect(await screen.findByText("日程已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenCalledWith(actionId);
  });

  it("executes an event after reconciling a lost second confirmation to READY", async () => {
    const event = eventRecord("event-lost-second", "第二次确认响应丢失日程");
    const actionId = arrangeEventDelete(event);
    mocks.confirm
      .mockReset()
      .mockResolvedValueOnce(pendingEventAction(actionId, "awaiting_second_confirmation", 1))
      .mockRejectedValueOnce(new Error("second confirmation response lost"));
    mocks.getAction.mockResolvedValue(pendingEventAction(actionId, "ready", 2));

    render(<CalendarPage />);
    await submitEventDeleteConfirmations(event);

    expect(await screen.findByText("日程已删除并通过数据库验证")).toBeInTheDocument();
    expect(mocks.getAction).toHaveBeenCalledWith(actionId);
    expect(mocks.confirm).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenCalledWith(actionId);
  });

  it("allows event review after EXECUTING recovery becomes non-retryable", async () => {
    const event = eventRecord("event-executing", "执行中待核对日程");
    const actionId = arrangeEventDelete(event);
    mocks.execute
      .mockReset()
      .mockRejectedValueOnce(new Error("execute response lost"))
      .mockResolvedValueOnce({
        success: false,
        action: "delete_event",
        action_id: actionId,
        record_id: event.id,
        verified_fields: { absent: false },
        side_effects: [],
        message: "日程删除结果无法验证",
        retryable: false,
      });
    mocks.cancelAction.mockRejectedValueOnce(new Error("executing actions cannot be cancelled"));
    mocks.getAction.mockResolvedValue(pendingEventAction(actionId, "executing", 2));

    render(<CalendarPage />);
    await submitEventDeleteConfirmations(event);
    await screen.findByText("删除失败。");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(mocks.getAction).toHaveBeenCalledWith(actionId));
    const recover = await screen.findByRole("button", { name: "恢复删除并验证" });
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.click(recover);

    expect(await screen.findByText(/日程删除结果无法验证.*自动重试已停止/)).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(2);
    expect(mocks.execute).toHaveBeenLastCalledWith(actionId);
    expect(screen.getByRole("button", { name: "恢复删除并验证" })).toBeDisabled();
    const close = screen.getByRole("button", { name: "关闭并重新加载" });
    expect(close).toBeEnabled();
    fireEvent.click(close);

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "高风险：删除日程" })).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText(`删除操作 ${actionId} 已停止自动重试，请核对当前数据。`),
    ).toBeInTheDocument();
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(2));
    expect(mocks.cancelAction).toHaveBeenCalledTimes(1);
  });
});
