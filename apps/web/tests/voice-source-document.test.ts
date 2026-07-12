import type { IntentResult } from "@campusvoice/shared-types";
import { beforeEach, describe, expect, it } from "vitest";

import { actionRequestFrom } from "@/lib/voice/action-request";
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
  beforeEach(() => useAssistantStore.getState().reset());

  it("places the evidence document id in the action payload", () => {
    const request = actionRequestFrom(intent, "doc-1");

    expect(request.payload).toMatchObject({
      title: "机器学习考试",
      source_type: "document",
      source_document_id: "doc-1",
    });
  });

  it("clears the document source when the workflow resets", () => {
    useAssistantStore.getState().setSourceDocumentId("doc-1");
    useAssistantStore.getState().setTranscript("创建待办");

    useAssistantStore.getState().reset();

    expect(useAssistantStore.getState().sourceDocumentId).toBeNull();
    expect(useAssistantStore.getState().transcript).toBe("");
  });
});
