import type { ActionLog } from "@campusvoice/shared-types";

const EVENT_ACTIONS = new Set(["create_event", "update_event", "delete_event"]);
const TASK_ACTIONS = new Set(["create_task", "update_task", "delete_task"]);

function latestUndoableAction(logs: readonly ActionLog[], actions: ReadonlySet<string>) {
  return logs.find(
    (log) => actions.has(log.action) && log.undoable && !log.undone && Boolean(log.action_id),
  );
}

export function latestUndoableEventAction(logs: readonly ActionLog[]) {
  return latestUndoableAction(logs, EVENT_ACTIONS);
}

export function latestUndoableTaskAction(logs: readonly ActionLog[]) {
  return latestUndoableAction(logs, TASK_ACTIONS);
}
