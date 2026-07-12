import type { IntentResult } from "@campusvoice/shared-types";

function slotText(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function shanghaiDateTime(dateValue: unknown, timeValue: unknown) {
  const date = slotText(dateValue);
  const time = slotText(timeValue);
  if (!date || !time) return null;
  const timestamp = new Date(`${date}T${time.length === 5 ? `${time}:00` : time}+08:00`);
  return Number.isNaN(timestamp.getTime()) ? null : timestamp.toISOString();
}

export function actionRequestFrom(intent: IntentResult, sourceDocumentId?: string | null) {
  const slots = intent.slots;
  const taskAction = intent.intent.endsWith("_task");
  const deleting = intent.intent.startsWith("delete_");
  const creating = intent.intent.startsWith("create_");
  const allowed = taskAction
    ? [
        "title",
        "description",
        "course",
        "due_at",
        "reminder_at",
        "priority",
        "status",
        "source_document_id",
        "expected_version",
      ]
    : [
        "title",
        "description",
        "course",
        "start_at",
        "end_at",
        "location",
        "reminder_minutes",
        "source_document_id",
        "expected_version",
      ];
  const payload: Record<string, unknown> = {};
  if (!deleting) {
    for (const key of allowed) {
      const value = slots[key];
      if (value !== undefined && value !== null && value !== "") payload[key] = value;
    }
    if (!creating) {
      delete payload.title;
      const newTitle = slotText(slots.new_title);
      if (newTitle) payload.title = newTitle;
    }
    if (taskAction && !payload.due_at)
      payload.due_at = shanghaiDateTime(slots.due_date, slots.due_time);
    if (!taskAction && !payload.start_at)
      payload.start_at = shanghaiDateTime(slots.date, slots.start_time);
    if (!taskAction && !payload.end_at)
      payload.end_at = shanghaiDateTime(slots.date, slots.end_time);
    Object.keys(payload).forEach((key) => payload[key] === null && delete payload[key]);
    if (sourceDocumentId) payload.source_document_id = sourceDocumentId;
    if (creating) payload.source_type = sourceDocumentId ? "document" : "voice";
  }
  return {
    targetId: slotText(taskAction ? slots.task_id : slots.event_id) ?? undefined,
    targetTitle: !creating ? (slotText(slots.title) ?? undefined) : undefined,
    payload,
  };
}
