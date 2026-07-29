import type { PendingAction } from "@campusvoice/shared-types";

import { api } from "@/lib/api-client";

export type ConfirmationOutcome = {
  action: PendingAction;
  confirmationError: unknown | null;
};

export function isActionExecutionRecoveryStatus(
  status: PendingAction["status"] | undefined,
): boolean {
  return (
    status === "ready" || status === "failed" || status === "executing" || status === "executed"
  );
}

export async function confirmActionAndReconcile(actionId: string): Promise<ConfirmationOutcome> {
  try {
    return {
      action: await api.actions.confirm(actionId, true),
      confirmationError: null,
    };
  } catch (confirmationError) {
    try {
      return {
        action: await api.actions.get(actionId),
        confirmationError,
      };
    } catch {
      throw confirmationError;
    }
  }
}
