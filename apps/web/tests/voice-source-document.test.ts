import type { IntentResult } from "@campusvoice/shared-types";
import { beforeEach, describe, expect, it } from "vitest";

import { actionRequestFrom } from "@/lib/voice/action-request";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

const intent: IntentResult = {
  intent: "create_event",
  confidence: 0.92,
  slots: {
    title: "机器学习考试",
    date: "2026-07-18",
    start_time: "09:00",
  },
  missing_fields: [],
  ambiguities: [],
  source_text: "根据通知创建日历",
  requires_confirmation: true,
};

describe("notice-to-voice source document lineage", () => {
  beforeEach(() => {
    useAssistantStore.getState().reset();
    setCurrentUserSettings(DEFAULT_USER_SETTINGS);
  });

  it("places the evidence document id in the action payload", () => {
    const request = actionRequestFrom(intent, "doc-1", DEFAULT_USER_SETTINGS, "manual");

    expect(request.payload).toMatchObject({
      title: "机器学习考试",
      source_type: "document",
      source_document_id: "doc-1",
    });
  });

  it("preserves explicit voice and manual sources when there is no document", () => {
    const voiceRequest = actionRequestFrom(intent, null, DEFAULT_USER_SETTINGS, "voice");
    const manualRequest = actionRequestFrom(intent, null, DEFAULT_USER_SETTINGS, "manual");

    expect(voiceRequest.payload).toMatchObject({ source_type: "voice" });
    expect(manualRequest.payload).toMatchObject({ source_type: "manual" });
  });

  it("uses the user timezone and leaves an implicit reminder to the server setting", () => {
    setCurrentUserSettings({
      ...DEFAULT_USER_SETTINGS,
      timezone: "America/New_York",
    });
    const request = actionRequestFrom(intent);

    expect(request.payload).toMatchObject({ start_at: "2026-07-18T13:00:00.000Z" });
    expect(request.payload).not.toHaveProperty("reminder_minutes");
  });

  it("turns an explicit task reminder offset into reminder_at", () => {
    const request = actionRequestFrom({
      ...intent,
      intent: "create_task",
      slots: {
        title: "提交人工智能作业",
        due_date: "2026-07-16",
        due_time: "15:00",
        reminder_minutes: 1440,
      },
    });

    expect(request.payload).toMatchObject({
      due_at: "2026-07-16T07:00:00.000Z",
      reminder_at: "2026-07-15T07:00:00.000Z",
      source_type: "voice",
    });
    expect(request.payload).not.toHaveProperty("reminder_minutes");
  });

  it("clears the document source when the workflow resets", () => {
    useAssistantStore.getState().setSourceDocumentId("doc-1");
    useAssistantStore.getState().setTranscript("创建待办");

    useAssistantStore.getState().reset();

    expect(useAssistantStore.getState().sourceDocumentId).toBeNull();
    expect(useAssistantStore.getState().transcript).toBe("");
  });
});
