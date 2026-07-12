import type { ActionLog } from "@campusvoice/shared-types";

const EVENT_ACTIONS = new Set(["create_event", "update_event", "delete_event"]);

export function latestUndoableEventAction(logs: readonly ActionLog[]) {
  return logs.find(
    (log) => EVENT_ACTIONS.has(log.action) && log.undoable && !log.undone && Boolean(log.action_id),
  );
}
