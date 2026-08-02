import type {
  CalendarEvent,
  CorrectionResult,
  IntentResult,
  KnowledgeAnswer,
  PendingAction,
  VerificationResult,
} from "@campusvoice/shared-types";
import { create } from "zustand";

export type WorkflowStatus =
  "idle" | "analyzing" | "preparing" | "confirming" | "executing" | "succeeded" | "error";
export type AssistantInputMode = "voice" | "text_demo" | null;

interface AssistantState {
  transcript: string;
  inputMode: AssistantInputMode;
  workflowStatus: WorkflowStatus;
  intent: IntentResult | null;
  correction: CorrectionResult | null;
  pendingAction: PendingAction | null;
  execution: VerificationResult | null;
  lastExecutedActionId: string | null;
  activeOperationId: string | null;
  undoRecoveryActionId: string | null;
  scheduleResults: CalendarEvent[] | null;
  knowledgeAnswer: KnowledgeAnswer | null;
  sourceDocumentId: string | null;
  error: string | null;
  setTranscript: (transcript: string) => void;
  setInputMode: (mode: AssistantInputMode) => void;
  setWorkflowStatus: (status: WorkflowStatus) => void;
  setIntent: (intent: IntentResult | null) => void;
  setCorrection: (correction: CorrectionResult | null) => void;
  setPendingAction: (action: PendingAction | null) => void;
  setExecution: (execution: VerificationResult | null) => void;
  setLastExecutedActionId: (actionId: string | null) => void;
  setActiveOperationId: (operationId: string | null) => void;
  setUndoRecoveryActionId: (actionId: string | null) => void;
  setScheduleResults: (results: CalendarEvent[] | null) => void;
  setKnowledgeAnswer: (answer: KnowledgeAnswer | null) => void;
  setSourceDocumentId: (documentId: string | null) => void;
  setError: (error: string | null) => void;
  clearResult: () => void;
  reset: () => void;
}

const initialWorkflow = {
  workflowStatus: "idle" as WorkflowStatus,
  intent: null,
  correction: null,
  pendingAction: null,
  execution: null,
  lastExecutedActionId: null,
  activeOperationId: null,
  undoRecoveryActionId: null,
  scheduleResults: null,
  knowledgeAnswer: null,
  error: null,
};

export const useAssistantStore = create<AssistantState>((set) => ({
  transcript: "",
  inputMode: null,
  sourceDocumentId: null,
  ...initialWorkflow,
  setTranscript: (transcript) => set({ transcript }),
  setInputMode: (inputMode) => set({ inputMode }),
  setWorkflowStatus: (workflowStatus) => set({ workflowStatus }),
  setIntent: (intent) => set({ intent }),
  setCorrection: (correction) => set({ correction }),
  setPendingAction: (pendingAction) => set({ pendingAction }),
  setExecution: (execution) => set({ execution }),
  setLastExecutedActionId: (lastExecutedActionId) => set({ lastExecutedActionId }),
  setActiveOperationId: (activeOperationId) => set({ activeOperationId }),
  setUndoRecoveryActionId: (undoRecoveryActionId) => set({ undoRecoveryActionId }),
  setScheduleResults: (scheduleResults) => set({ scheduleResults }),
  setKnowledgeAnswer: (knowledgeAnswer) => set({ knowledgeAnswer }),
  setSourceDocumentId: (sourceDocumentId) => set({ sourceDocumentId }),
  setError: (error) => set({ error }),
  clearResult: () => set(initialWorkflow),
  reset: () => set({ transcript: "", inputMode: null, sourceDocumentId: null, ...initialWorkflow }),
}));
