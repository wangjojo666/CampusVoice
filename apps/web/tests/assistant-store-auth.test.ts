import type {
  CorrectionResult,
  IntentResult,
  KnowledgeAnswer,
  PendingAction,
  VerificationResult,
} from "@campusvoice/shared-types";
import { beforeEach, describe, expect, it } from "vitest";

import {
  getAccessToken,
  loginRedirectForUnauthorized,
  setAccessToken,
  websocketProtocols,
} from "@/lib/auth";
import { useAssistantStore } from "@/stores/assistant-store";

const intent: IntentResult = {
  intent: "create_task",
  confidence: 0.94,
  slots: { title: "复习机器学习" },
  missing_fields: [],
  ambiguities: [],
  source_text: "创建复习机器学习待办",
  requires_confirmation: true,
};

const correction: CorrectionResult = {
  record_id: "correction-1",
  original_text: "复习机器学西",
  corrected_text: "复习机器学习",
  changes: [],
};

const pendingAction: PendingAction = {
  id: "action-1",
  action: "create_task",
  risk_level: "medium",
  risk_reasons: ["writes_data"],
  payload: { title: "复习机器学习" },
  status: "awaiting_confirmation",
};

const execution: VerificationResult = {
  success: true,
  action: "create_task",
  record_id: "task-1",
  verified_fields: { title: true },
  side_effects: [],
  message: "待办已写入并复查",
};

const knowledgeAnswer: KnowledgeAnswer = {
  answer: "报名截止到 7 月 20 日。",
  sufficient: true,
  evidence: [],
};

describe("assistant workflow state", () => {
  beforeEach(() => useAssistantStore.getState().reset());

  it("keeps the correction, confirmation, execution, and evidence lineage together", () => {
    const store = useAssistantStore.getState();
    store.setTranscript("创建复习机器学西待办");
    store.setWorkflowStatus("confirming");
    store.setIntent(intent);
    store.setCorrection(correction);
    store.setPendingAction(pendingAction);
    store.setExecution(execution);
    store.setKnowledgeAnswer(knowledgeAnswer);
    store.setSourceDocumentId("document-1");
    store.setError("等待用户确认");

    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "创建复习机器学西待办",
      workflowStatus: "confirming",
      intent,
      correction,
      pendingAction,
      execution,
      knowledgeAnswer,
      sourceDocumentId: "document-1",
      error: "等待用户确认",
    });
  });

  it("clears workflow results without losing the user's transcript or source document", () => {
    useAssistantStore.getState().setTranscript("继续处理这条通知");
    useAssistantStore.getState().setSourceDocumentId("document-2");
    useAssistantStore.getState().setIntent(intent);
    useAssistantStore.getState().setPendingAction(pendingAction);
    useAssistantStore.getState().setWorkflowStatus("error");
    useAssistantStore.getState().setError("服务繁忙");

    useAssistantStore.getState().clearResult();

    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "继续处理这条通知",
      sourceDocumentId: "document-2",
      workflowStatus: "idle",
      intent: null,
      pendingAction: null,
      error: null,
    });
  });
});

describe("in-memory browser authentication", () => {
  beforeEach(() => setAccessToken(null));

  it("keeps bearer credentials out of persistent storage and clears them explicitly", () => {
    setAccessToken("short-lived-access-token");
    expect(getAccessToken()).toBe("short-lived-access-token");
    expect(window.localStorage.getItem("access_token")).toBeNull();

    setAccessToken(null);
    expect(getAccessToken()).toBeNull();
  });

  it("constructs the authenticated WebSocket subprotocol without putting tickets in URLs", () => {
    expect(websocketProtocols("one-time-ticket")).toEqual([
      "campusvoice",
      "campusvoice.ticket.one-time-ticket",
    ]);
  });

  it("redirects OIDC 401 responses without looping on a callback error", () => {
    expect(
      loginRedirectForUnauthorized(
        "https://api.campus.test",
        "https://app.campus.test/tasks",
        true,
      ),
    ).toBe("https://api.campus.test/api/auth/login");
    expect(
      loginRedirectForUnauthorized(
        "https://api.campus.test",
        "https://app.campus.test/?auth_error=nonce_mismatch",
        true,
      ),
    ).toBeNull();
    expect(
      loginRedirectForUnauthorized(
        "https://api.campus.test",
        "https://app.campus.test/tasks",
        false,
      ),
    ).toBeNull();
  });
});
