import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowSnapshot } from "@/components/voice/workflow-snapshot";
import { ApiError } from "@/lib/api-client";
import {
  canInvokeAssistantUndo,
  isRetryableExecuteFailure,
  isRetryableUndoFailure,
} from "@/lib/voice/workflow-recovery";
import { useAssistantStore } from "@/stores/assistant-store";

const mocks = vi.hoisted(() => ({
  undoAction: vi.fn(),
  executionOnUndo: null as (() => void) | null,
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    readonly status: number;
    readonly code?: string;

    constructor(message: string, options: { status: number; code?: string } = { status: 0 }) {
      super(message);
      this.status = options.status;
      this.code = options.code;
    }

    get userMessage() {
      return this.message;
    }
  },
  api: { actions: { undo: mocks.undoAction } },
}));

vi.mock("@/components/actions/execution-result", () => ({
  ExecutionResult: ({ result, onUndo }: { result: { message: string }; onUndo?: () => void }) => {
    mocks.executionOnUndo = onUndo ?? null;
    return <div>{result.message}</div>;
  },
}));

vi.mock("@/components/ui/error-state", () => ({
  ErrorState: ({ message }: { message: string }) => <div role="alert">{message}</div>,
}));

afterEach(() => cleanup());

beforeEach(() => {
  useAssistantStore.getState().reset();
  mocks.undoAction.mockReset();
  mocks.executionOnUndo = null;
});

function setSuccessfulAction(actionId: string, recordId: string) {
  const store = useAssistantStore.getState();
  store.setExecution({
    success: true,
    action: "create_task",
    record_id: recordId,
    verified_fields: { title: true },
    side_effects: [],
    message: `${actionId} 已写入并复验`,
  });
  store.setLastExecutedActionId(actionId);
  store.setUndoRecoveryActionId(null);
  store.setError(null);
  store.setWorkflowStatus("succeeded");
}

describe("workflow recovery classification", () => {
  it.each([
    [0, undefined],
    [408, undefined],
    [425, undefined],
    [429, undefined],
    [503, undefined],
    [409, "action_execution_in_progress"],
  ])("keeps retryable execute failures for status %s and code %s", (status, code) => {
    expect(isRetryableExecuteFailure(new ApiError("execute failed", { status, code }))).toBe(true);
  });

  it.each(["undo_in_progress", "undo_verification_in_progress"])(
    "keeps the exact undo recovery id for conflict code %s",
    (code) => {
      expect(isRetryableUndoFailure(new ApiError("undo failed", { status: 409, code }))).toBe(true);
    },
  );

  it("fails unknown client conflicts closed while keeping transport loss retryable", () => {
    expect(
      isRetryableExecuteFailure(
        new ApiError("unknown conflict", { status: 409, code: "action_state_conflict" }),
      ),
    ).toBe(false);
    expect(
      isRetryableUndoFailure(
        new ApiError("unknown conflict", { status: 409, code: "undo_conflict" }),
      ),
    ).toBe(false);
    expect(isRetryableExecuteFailure(new Error("network lost"))).toBe(true);
    expect(isRetryableUndoFailure(new Error("network lost"))).toBe(true);
  });

  it("permits only a fresh exact normal or recovery undo invocation", () => {
    const clean = {
      workflowStatus: "succeeded",
      lastExecutedActionId: "action-a",
      undoRecoveryActionId: null,
      error: null,
    };
    expect(canInvokeAssistantUndo(clean, "action-a", "normal")).toBe(true);
    expect(canInvokeAssistantUndo(clean, "action-b", "normal")).toBe(false);
    expect(
      canInvokeAssistantUndo(
        { ...clean, undoRecoveryActionId: "action-stale" },
        "action-a",
        "normal",
      ),
    ).toBe(false);
    expect(
      canInvokeAssistantUndo(
        {
          ...clean,
          workflowStatus: "error",
          undoRecoveryActionId: "action-a",
          error: "temporary failure",
        },
        "action-a",
        "recovery",
      ),
    ).toBe(true);
    expect(
      canInvokeAssistantUndo(
        {
          ...clean,
          workflowStatus: "error",
          undoRecoveryActionId: "action-stale",
          error: "temporary failure",
        },
        "action-a",
        "recovery",
      ),
    ).toBe(false);
    expect(
      canInvokeAssistantUndo(
        { ...clean, workflowStatus: "error", error: "temporary failure" },
        "action-a",
        "recovery",
      ),
    ).toBe(false);
    expect(
      canInvokeAssistantUndo(
        { ...clean, undoRecoveryActionId: "action-a" },
        "action-a",
        "recovery",
      ),
    ).toBe(false);
    expect(
      canInvokeAssistantUndo({ ...clean, workflowStatus: "error" }, "action-a", "normal"),
    ).toBe(false);
    expect(
      canInvokeAssistantUndo({ ...clean, error: "unrelated failure" }, "action-a", "normal"),
    ).toBe(false);
  });
});

describe("WorkflowSnapshot exact undo binding", () => {
  it("rejects stale normal callbacks after action or recovery context changes", () => {
    setSuccessfulAction("action-a", "task-a");
    render(<WorkflowSnapshot />);
    const staleActionACallback = mocks.executionOnUndo;
    expect(staleActionACallback).toBeTypeOf("function");

    act(() => setSuccessfulAction("action-b", "task-b"));
    staleActionACallback?.();

    expect(mocks.undoAction).not.toHaveBeenCalled();
    expect(useAssistantStore.getState()).toMatchObject({
      lastExecutedActionId: "action-b",
      undoRecoveryActionId: null,
      execution: expect.objectContaining({ record_id: "task-b" }),
    });

    const staleActionBCallback = mocks.executionOnUndo;
    expect(staleActionBCallback).toBeTypeOf("function");
    act(() => useAssistantStore.getState().setUndoRecoveryActionId("action-stale"));
    staleActionBCallback?.();

    expect(mocks.undoAction).not.toHaveBeenCalled();
    expect(useAssistantStore.getState()).toMatchObject({
      lastExecutedActionId: "action-b",
      undoRecoveryActionId: "action-stale",
      execution: expect.objectContaining({ record_id: "task-b" }),
    });
  });
});
