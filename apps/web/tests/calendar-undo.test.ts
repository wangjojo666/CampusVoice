import type { ActionLog } from "@campusvoice/shared-types";
import { describe, expect, it } from "vitest";

import { latestUndoableEventAction } from "@/lib/calendar/undo";

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
});
