import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CalendarPage from "@/app/calendar/page";

const mocks = vi.hoisted(() => ({
  listEvents: vi.fn(),
  listLogs: vi.fn(),
  undo: vi.fn(),
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
      remove: vi.fn(),
    },
    actionLogs: { list: mocks.listLogs },
    actions: { undo: mocks.undo },
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
  });

  it("undoes the latest event action and refreshes the calendar", async () => {
    render(<CalendarPage />);
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "撤销最近操作" }));

    await waitFor(() => expect(mocks.undo).toHaveBeenCalledWith("action-1"));
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("撤销已完成并通过数据库验证")).toBeInTheDocument();
  });
});
