import type { IntentResult, UserSettings } from "@campusvoice/shared-types";

import { fromLocalInputValue } from "@/lib/format";
import { getCurrentUserSettings } from "@/lib/user-settings";

function slotText(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function zonedDateTime(dateValue: unknown, timeValue: unknown, timeZone: string) {
  const date = slotText(dateValue);
  const time = slotText(timeValue);
  if (!date || !time) return null;
  return fromLocalInputValue(`${date}T${time.slice(0, 5)}`, timeZone);
}

function reminderAtFrom(dueAt: unknown, reminderMinutes: unknown) {
  if (typeof dueAt !== "string" || typeof reminderMinutes !== "number") return null;
  if (!Number.isFinite(reminderMinutes) || reminderMinutes < 0) return null;
  const dueTimestamp = Date.parse(dueAt);
  if (Number.isNaN(dueTimestamp)) return null;
  return new Date(dueTimestamp - reminderMinutes * 60_000).toISOString();
}

export function actionRequestFrom(
  intent: IntentResult,
  sourceDocumentId?: string | null,
  preferences: Pick<UserSettings, "timezone"> = getCurrentUserSettings(),
  inputSource: "voice" | "manual" = "voice",
) {
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
      payload.due_at = zonedDateTime(slots.due_date, slots.due_time, preferences.timezone);
    if (taskAction && !payload.reminder_at)
      payload.reminder_at = reminderAtFrom(payload.due_at, slots.reminder_minutes);
    if (!taskAction && !payload.start_at)
      payload.start_at = zonedDateTime(slots.date, slots.start_time, preferences.timezone);
    if (!taskAction && !payload.end_at)
      payload.end_at = zonedDateTime(slots.date, slots.end_time, preferences.timezone);
    Object.keys(payload).forEach((key) => payload[key] === null && delete payload[key]);
    if (sourceDocumentId) payload.source_document_id = sourceDocumentId;
    if (creating) payload.source_type = sourceDocumentId ? "document" : inputSource;
  }
  return {
    targetId: slotText(taskAction ? slots.task_id : slots.event_id) ?? undefined,
    targetTitle: !creating ? (slotText(slots.title) ?? undefined) : undefined,
    payload,
  };
}
