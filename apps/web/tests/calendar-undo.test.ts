import type { ActionLog } from "@campusvoice/shared-types";
import { describe, expect, it } from "vitest";

import { latestUndoableEventAction, latestUndoableTaskAction } from "@/lib/calendar/undo";

function log(overrides: Partial<ActionLog>): ActionLog {
  return {
    id: "log-default",
    action: "create_task",
    risk_level: "medium",
    confirmed: true,
    success: true,
    message: "已验证",
    created_at: "2026-07-12T12:00:00Z",
    ...overrides,
  };
}

describe("calendar undo selection", () => {
  it("selects the latest still-available event action only", () => {
    const result = latestUndoableEventAction([
      log({ id: "task", action_id: "task-action", undoable: true }),
      log({ id: "undone", action: "update_event", action_id: "event-old", undone: true }),
      log({ id: "event", action: "create_event", action_id: "event-new", undoable: true }),
    ]);

    expect(result?.action_id).toBe("event-new");
  });

  it("selects an older task action when the latest global action is an event", () => {
    const result = latestUndoableTaskAction([
      log({ id: "event", action: "create_event", action_id: "event-new", undoable: true }),
      log({ id: "task", action: "update_task", action_id: "task-older", undoable: true }),
    ]);

    expect(result?.action_id).toBe("task-older");
  });
});
