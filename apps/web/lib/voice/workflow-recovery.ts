import type { PendingAction, VerificationResult } from "@campusvoice/shared-types";

import { ApiError } from "@/lib/api-client";

type AssistantMutationState = {
  workflowStatus: string;
  pendingAction: PendingAction | null;
  execution: VerificationResult | null;
  undoRecoveryActionId: string | null;
};

type AssistantUndoInvocationState = {
  workflowStatus: string;
  lastExecutedActionId: string | null;
  undoRecoveryActionId: string | null;
  error: string | null;
};

const retryableStatuses = new Set([0, 408, 425, 429]);
const retryableExecuteCodes = new Set(["action_execution_in_progress"]);
const retryableUndoCodes = new Set(["undo_in_progress", "undo_verification_in_progress"]);

function isRetryableFailure(reason: unknown, codes: ReadonlySet<string>) {
  if (!(reason instanceof ApiError)) return true;
  return (
    retryableStatuses.has(reason.status) ||
    reason.status >= 500 ||
    (reason.code ? codes.has(reason.code) : false)
  );
}

export function isRetryableExecuteFailure(reason: unknown) {
  return isRetryableFailure(reason, retryableExecuteCodes);
}

export function isRetryableUndoFailure(reason: unknown) {
  return isRetryableFailure(reason, retryableUndoCodes);
}

export function canInvokeAssistantUndo(
  state: AssistantUndoInvocationState,
  expectedActionId: string,
  mode: "normal" | "recovery",
) {
  if (state.lastExecutedActionId !== expectedActionId) return false;
  if (mode === "recovery") {
    return state.workflowStatus === "error" && state.undoRecoveryActionId === expectedActionId;
  }
  return (
    state.workflowStatus === "succeeded" &&
    state.undoRecoveryActionId === null &&
    state.error === null
  );
}

export function hasUnsettledAssistantMutation(state: AssistantMutationState) {
  if (state.undoRecoveryActionId) return true;
  if (state.pendingAction?.status !== "ready") return false;
  return (
    state.workflowStatus === "executing" ||
    (state.workflowStatus === "error" && (!state.execution || state.execution.retryable === true))
  );
}
