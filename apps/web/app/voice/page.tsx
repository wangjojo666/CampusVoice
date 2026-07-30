"use client";

import type {
  AsrTranscriptReference,
  CorrectionResult,
  IntentResult,
  PendingAction,
} from "@campusvoice/shared-types";
import {
  ArrowRight,
  BrainCircuit,
  Keyboard,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmationCard } from "@/components/actions/confirmation-card";
import { ExecutionResult } from "@/components/actions/execution-result";
import { PageHeader } from "@/components/layout/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { AsrRecorder } from "@/components/voice/asr-recorder";
import { ClarificationCard } from "@/components/voice/clarification-card";
import { CorrectionDiff } from "@/components/voice/correction-diff";
import { ApiError, api } from "@/lib/api-client";
import {
  addLocalDays,
  firstValidInstantOfExactLocalDay,
  firstValidInstantOfLocalDay,
} from "@/lib/dashboard/local-days";
import { formatDateTime } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
import { createVerifiedFinishEvent, type VerifiedFinishEvent } from "@/lib/verified-finish";
import { actionRequestFrom } from "@/lib/voice/action-request";
import {
  canInvokeAssistantUndo,
  hasUnsettledAssistantMutation,
  isRetryableExecuteFailure,
  isRetryableUndoFailure,
} from "@/lib/voice/workflow-recovery";
import { useAssistantStore } from "@/stores/assistant-store";

type IntentWithCorrection = IntentResult & { correction?: CorrectionResult };

const intentLabels: Record<string, string> = {
  create_task: "创建待办",
  update_task: "修改待办",
  delete_task: "删除待办",
  create_event: "创建日历事件",
  update_event: "修改日历事件",
  delete_event: "删除日历事件",
  search_notice: "查询校园通知",
  query_schedule: "查询日程",
  unknown: "暂未识别",
};

const mutationIntents = new Set([
  "create_task",
  "update_task",
  "delete_task",
  "create_event",
  "update_event",
  "delete_event",
]);

function slotText(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function localDayQueryWindow(dateKey: string, timezone: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateKey)) {
    throw new RangeError("Schedule date must use YYYY-MM-DD");
  }
  const parsed = new Date(`${dateKey}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== dateKey) {
    throw new RangeError("Schedule date must be a real calendar date");
  }
  const startMs = firstValidInstantOfExactLocalDay(dateKey, timezone);
  if (startMs === null) throw new RangeError("Schedule date does not exist in this timezone");
  const endMs = firstValidInstantOfLocalDay(addLocalDays(dateKey, 1), timezone);
  if (endMs <= startMs) throw new RangeError("Schedule date window must be increasing");
  return { start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString() };
}

function targetCandidates(action: PendingAction | null) {
  const value = action?.diagnostics?.target_candidates;
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object") return [];
    const record = candidate as Record<string, unknown>;
    return typeof record.id === "string" && typeof record.label === "string"
      ? [{ id: record.id, label: record.label }]
      : [];
  });
}

export default function VoicePage() {
  const store = useAssistantStore();
  const userSettings = useUserSettings();

  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [transcriptionId, setTranscriptionId] = useState<string | null>(null);
  const [originalTranscript, setOriginalTranscript] = useState("");
  const [asrConfidence, setAsrConfidence] = useState<number | null>(null);
  const [asrInProgress, setAsrInProgress] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [verifiedFinish, setVerifiedFinish] = useState<VerifiedFinishEvent | null>(null);
  const [undoingActionId, setUndoingActionId] = useState<string | null>(null);
  const undoInFlight = useRef(false);
  const asrInProgressRef = useRef(false);
  const mounted = useRef(true);
  const startOperation = useCallback(() => {
    const operationId = crypto.randomUUID();
    const currentStore = useAssistantStore.getState();
    currentStore.setActiveOperationId(operationId);
    currentStore.setUndoRecoveryActionId(null);
    undoInFlight.current = false;
    setUndoingActionId(null);
    return operationId;
  }, []);
  const isOperationCurrent = useCallback(
    (operationId: string) => useAssistantStore.getState().activeOperationId === operationId,
    [],
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      undoInFlight.current = false;
    };
  }, []);

  const undoRetryActionId =
    store.undoRecoveryActionId !== null && store.undoRecoveryActionId === store.lastExecutedActionId
      ? store.undoRecoveryActionId
      : null;
  const undoBusy =
    store.workflowStatus === "executing" &&
    (undoRetryActionId !== null || undoingActionId === store.lastExecutedActionId);
  const undoRecoveryMatchesCurrent =
    store.undoRecoveryActionId === null ||
    store.undoRecoveryActionId === store.lastExecutedActionId;
  const canOfferUndo =
    store.execution?.success === true &&
    store.lastExecutedActionId !== null &&
    !store.error &&
    undoRecoveryMatchesCurrent &&
    (store.workflowStatus === "succeeded" || undoBusy);
  const undoActionId = canOfferUndo ? store.lastExecutedActionId : null;
  const mutationInFlight = hasUnsettledAssistantMutation(store);
  const busy =
    ["analyzing", "preparing", "confirming", "executing"].includes(store.workflowStatus) ||
    undoBusy;
  const inputBusy = busy || mutationInFlight || asrInProgress;

  const execute = useCallback(
    async (action: PendingAction, generation = startOperation()) => {
      if (!isOperationCurrent(generation)) return;
      if (mounted.current) setVerifiedFinish(null);
      store.setError(null);
      store.setWorkflowStatus("executing");
      try {
        const result = await api.actions.execute(action.id);
        if (!isOperationCurrent(generation)) return;
        store.setExecution(result);
        store.setLastExecutedActionId(result.success ? action.id : null);
        store.setPendingAction(!result.success && result.retryable ? action : null);
        store.setWorkflowStatus(result.success ? "succeeded" : "error");
        if (result.success) {
          store.setSourceDocumentId(null);
          if (mounted.current) setVerifiedFinish(createVerifiedFinishEvent(result, "execute"));
        } else store.setError(result.message);
      } catch (reason) {
        if (!isOperationCurrent(generation)) return;
        const message =
          reason instanceof ApiError
            ? reason.userMessage
            : "\u6267\u884c\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002";
        const retryable = isRetryableExecuteFailure(reason);
        if (retryable) {
          store.setPendingAction(action);
          store.setExecution(null);
        } else {
          store.setPendingAction(null);
          store.setExecution({
            success: false,
            action: action.action,
            record_id: null,
            verified_fields: {},
            side_effects: [],
            message,
            failure_reason: reason instanceof ApiError ? reason.code : null,
            retryable: false,
          });
        }
        store.setWorkflowStatus("error");
        store.setError(reason instanceof ApiError ? reason.userMessage : "执行失败，请重试。");
      }
    },
    [isOperationCurrent, startOperation, store],
  );

  const retryExecution = useCallback(
    (expectedActionId: string) => {
      const latest = useAssistantStore.getState();
      const action = latest.pendingAction;
      const execution = latest.execution;
      const retryableState =
        !execution ||
        (!execution.success && execution.retryable === true && action?.action === execution.action);
      if (
        latest.workflowStatus !== "error" ||
        latest.undoRecoveryActionId !== null ||
        !retryableState ||
        !action ||
        action.id !== expectedActionId ||
        action.status !== "ready"
      ) {
        return;
      }
      void execute(action);
    },
    [execute],
  );

  const prepareIntent = useCallback(
    async (
      intent: IntentResult,
      audit?: { sourceText: string; correctedText: string },
      generation?: string,
    ) => {
      const activeGeneration = generation ?? startOperation();
      if (!isOperationCurrent(activeGeneration)) return;
      if (intent.intent === "unknown") {
        store.setWorkflowStatus("error");
        store.setError("我还不能确定你想做什么。请换一种说法，并说明是待办、日历还是校园通知。");
        return;
      }
      if (intent.intent === "search_notice") {
        store.setWorkflowStatus("executing");
        try {
          const answer = await api.knowledge.ask(
            slotText(intent.slots.query) ?? intent.source_text,
          );
          if (!isOperationCurrent(activeGeneration)) return;
          store.setKnowledgeAnswer(answer);
          store.setWorkflowStatus("succeeded");
          store.setSourceDocumentId(null);
        } catch (reason) {
          if (!isOperationCurrent(activeGeneration)) return;
          store.setWorkflowStatus("error");
          store.setError(reason instanceof ApiError ? reason.userMessage : "校园通知查询失败。");
        }
        return;
      }
      if (intent.intent === "query_schedule") {
        store.setWorkflowStatus("executing");
        try {
          const requestedDate = slotText(intent.slots.date);
          const window = requestedDate
            ? localDayQueryWindow(requestedDate, userSettings.timezone)
            : null;
          const response = await api.events.list(window ? { ...window, limit: 8 } : { limit: 8 });
          if (!isOperationCurrent(activeGeneration)) return;
          store.setScheduleResults(response.items);
          store.setWorkflowStatus("succeeded");
          store.setSourceDocumentId(null);
        } catch (reason) {
          if (!isOperationCurrent(activeGeneration)) return;
          store.setWorkflowStatus("error");
          store.setError(reason instanceof ApiError ? reason.userMessage : "日程查询失败。");
        }
        return;
      }
      if (!mutationIntents.has(intent.intent)) {
        store.setWorkflowStatus("error");
        store.setError("当前意图不能作为可靠操作执行。");
        return;
      }
      const normalized = actionRequestFrom(
        intent,
        store.sourceDocumentId,
        userSettings,
        store.inputMode === "text_demo" ? "manual" : "voice",
      );
      store.setWorkflowStatus("preparing");
      try {
        const action = await api.actions.prepare({
          action: intent.intent,
          target_id: normalized.targetId,
          target_title: normalized.targetTitle,
          payload: normalized.payload,
          asr_confidence: asrConfidence ?? 1,
          missing_fields: intent.missing_fields,
          ambiguities: intent.ambiguities,
          idempotency_key: crypto.randomUUID(),
          source_text: audit?.sourceText ?? (originalTranscript.trim() || intent.source_text),
          corrected_text: audit?.correctedText ?? intent.source_text,
          voice_session_id: voiceSessionId ?? undefined,
          transcription_id: transcriptionId ?? undefined,
        });
        if (!isOperationCurrent(activeGeneration)) return;
        store.setPendingAction(action);
        if (action.status === "ready") await execute(action, activeGeneration);
        else store.setWorkflowStatus("idle");
      } catch (reason) {
        if (!isOperationCurrent(activeGeneration)) return;
        store.setWorkflowStatus("error");
        store.setError(reason instanceof ApiError ? reason.userMessage : "无法准备操作，请重试。");
      }
    },
    [
      asrConfidence,
      execute,
      isOperationCurrent,
      originalTranscript,
      startOperation,
      store,
      transcriptionId,
      userSettings,
      voiceSessionId,
    ],
  );

  const analyze = useCallback(
    async (text = store.transcript, operationId?: string) => {
      if (!text.trim()) return;
      if (
        !operationId &&
        (hasUnsettledAssistantMutation(useAssistantStore.getState()) || asrInProgressRef.current)
      ) {
        return;
      }
      if (operationId && !isOperationCurrent(operationId)) return;
      if (mounted.current) setVerifiedFinish(null);
      store.clearResult();
      const generation = operationId ?? startOperation();
      if (operationId) store.setActiveOperationId(operationId);
      store.setTranscript(text.trim());
      store.setWorkflowStatus("analyzing");
      try {
        const correction = await api.correction.preview(
          text.trim(),
          asrConfidence ?? 1,
          transcriptionId ?? undefined,
        );
        if (!isOperationCurrent(generation)) return;
        store.setCorrection(correction);
        if (correction.requires_user_input) {
          store.setWorkflowStatus("idle");
          store.setError("这处术语可能影响关键字段，请选择候选词或直接编辑转写后再继续。");
          return;
        }
        const parsed = (await api.intent.parse(
          correction.corrected_text || text.trim(),
          undefined,
          asrConfidence ?? undefined,
          conversationId ?? undefined,
        )) as IntentWithCorrection;
        if (!isOperationCurrent(generation)) return;
        if (mounted.current) setConversationId(parsed.conversation_id ?? conversationId);
        store.setIntent(parsed);
        await prepareIntent(
          parsed,
          {
            sourceText: originalTranscript.trim() || correction.original_text,
            correctedText: correction.corrected_text || text.trim(),
          },
          generation,
        );
      } catch (reason) {
        if (!isOperationCurrent(generation)) return;
        store.setWorkflowStatus("error");
        store.setError(
          reason instanceof ApiError ? reason.userMessage : "无法理解这条指令，请重试。",
        );
      }
    },
    [
      asrConfidence,
      conversationId,
      isOperationCurrent,
      originalTranscript,
      prepareIntent,
      startOperation,
      store,
      transcriptionId,
    ],
  );

  const selectTarget = async (targetId: string) => {
    const pending = store.pendingAction;
    if (!pending) return;
    const generation = startOperation();
    store.setWorkflowStatus("preparing");
    try {
      await api.actions.cancel(pending.id);
      if (!isOperationCurrent(generation)) return;
      const prepared = await api.actions.prepare({
        action: pending.action,
        target_id: targetId,
        payload: pending.payload,
        asr_confidence: asrConfidence ?? 1,
        idempotency_key: crypto.randomUUID(),
        source_text:
          originalTranscript.trim() || store.correction?.original_text || store.transcript,
        corrected_text: store.intent?.source_text ?? store.transcript,
        voice_session_id: voiceSessionId ?? undefined,
        transcription_id: transcriptionId ?? undefined,
      });
      if (!isOperationCurrent(generation)) return;
      store.setPendingAction(prepared);
      store.setWorkflowStatus("idle");
    } catch (reason) {
      if (!isOperationCurrent(generation)) return;
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "无法选择目标，请重试。");
    }
  };

  const chooseCorrection = async (changeIndex: number, value: string) => {
    const correction = store.correction;
    const change = correction?.changes[changeIndex];
    if (!correction || !change) return;
    const generation = startOperation();
    const selected = `${correction.original_text.slice(0, change.start)}${value}${correction.original_text.slice(change.end)}`;
    try {
      await api.correction.decide(correction.record_id, selected, true);
      if (!isOperationCurrent(generation)) return;
      store.setError(null);
      store.setCorrection(null);
      store.setTranscript(selected);
      await analyze(selected, generation);
    } catch (reason) {
      if (!isOperationCurrent(generation)) return;
      store.setWorkflowStatus("error");
      store.setError(
        reason instanceof ApiError ? reason.userMessage : "无法保存术语确认结果，请重试。",
      );
    }
  };

  const confirm = async () => {
    const action = store.pendingAction;
    if (!action) return;
    const generation = startOperation();
    store.setWorkflowStatus("confirming");
    try {
      const updated = await api.actions.confirm(action.id, true);
      if (!isOperationCurrent(generation)) return;
      store.setPendingAction(updated);
      if (updated.status === "ready") await execute(updated, generation);
      else store.setWorkflowStatus("idle");
    } catch (reason) {
      if (!isOperationCurrent(generation)) return;
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "确认失败，请重试。");
    }
  };

  const cancel = async () => {
    const action = store.pendingAction;
    if (!action) return;
    const generation = startOperation();
    setVerifiedFinish(null);
    store.setWorkflowStatus("confirming");
    try {
      await api.actions.cancel(action.id);
      if (!isOperationCurrent(generation)) return;
      store.setPendingAction(null);
      store.setWorkflowStatus("idle");
      store.setSourceDocumentId(null);
    } catch (reason) {
      if (!isOperationCurrent(generation)) return;
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "取消失败，请重试。");
    }
  };

  const undo = async (expectedActionId: string, mode: "normal" | "recovery") => {
    const currentState = useAssistantStore.getState();
    const currentActionId = currentState.lastExecutedActionId;
    if (!currentActionId || !canInvokeAssistantUndo(currentState, expectedActionId, mode)) return;
    if (
      undoInFlight.current ||
      (currentState.workflowStatus === "executing" &&
        currentState.undoRecoveryActionId === currentActionId)
    ) {
      return;
    }

    const actionId = expectedActionId;
    const generation = startOperation();
    store.setUndoRecoveryActionId(actionId);
    undoInFlight.current = true;
    setUndoingActionId(actionId);
    setVerifiedFinish(null);
    store.setError(null);
    store.setWorkflowStatus("executing");
    try {
      const result = await api.actions.undo(actionId);
      if (!isOperationCurrent(generation)) return;
      if (useAssistantStore.getState().lastExecutedActionId !== actionId) return;
      const retryable = !result.success && result.retryable === true;
      store.setUndoRecoveryActionId(retryable ? actionId : null);
      store.setExecution(result);
      if (result.success || !retryable) store.setLastExecutedActionId(null);
      if (result.success) {
        if (mounted.current) setVerifiedFinish(createVerifiedFinishEvent(result, "undo"));
      }
      store.setWorkflowStatus(result.success ? "succeeded" : "error");
    } catch (reason) {
      if (!isOperationCurrent(generation)) return;
      if (useAssistantStore.getState().lastExecutedActionId !== actionId) return;
      const retryable = isRetryableUndoFailure(reason);
      store.setUndoRecoveryActionId(retryable ? actionId : null);
      if (!retryable) store.setLastExecutedActionId(null);
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "撤销失败，请重试。");
    } finally {
      if (isOperationCurrent(generation)) {
        undoInFlight.current = false;
        if (mounted.current) setUndoingActionId(null);
      }
    }
  };

  const clarificationQuestion =
    store.pendingAction?.clarification_question ??
    (store.pendingAction?.missing_fields?.length
      ? `请补充${store.pendingAction.missing_fields[0]}。`
      : "请补充最关键的信息。");

  const handleAsrSource = useCallback(
    (source: AsrTranscriptReference) => {
      if (hasUnsettledAssistantMutation(useAssistantStore.getState())) return;
      setVoiceSessionId(source.sessionId);
      setTranscriptionId(source.transcriptionId);
      setOriginalTranscript(source.originalText);
      if (source.sessionId) store.setSourceDocumentId(null);
    },
    [store],
  );

  const setAsrActive = useCallback((active: boolean) => {
    asrInProgressRef.current = active;
    setAsrInProgress(active);
  }, []);

  const resetWorkflowContext = useCallback(() => {
    const currentStore = useAssistantStore.getState();
    if (hasUnsettledAssistantMutation(currentStore)) return false;
    currentStore.reset();
    undoInFlight.current = false;
    setUndoingActionId(null);
    setConversationId(null);
    setVoiceSessionId(null);
    setTranscriptionId(null);
    setOriginalTranscript("");
    setAsrConfidence(null);
    setAsrActive(false);
    setVerifiedFinish(null);
    return true;
  }, [setAsrActive]);

  const startAsrWorkflow = useCallback(() => {
    if (!resetWorkflowContext()) return false;
    setAsrActive(true);
    return true;
  }, [resetWorkflowContext, setAsrActive]);

  return (
    <div>
      <PageHeader
        eyebrow="问声程"
        title="一句话，把校园安排接住"
        description="说出 DDL、复习或日程，先核对转写和风险，再由你确认写入；系统会在数据库中复查结果，不会只凭 AI 回答就宣告成功。"
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/settings" className="btn-secondary">
              <Settings2 size={16} /> 识别增强
            </Link>
            {store.transcript ? (
              <button
                type="button"
                disabled={mutationInFlight || asrInProgress}
                onClick={() => void resetWorkflowContext()}
                className="btn-secondary"
              >
                <RotateCcw size={16} /> 新指令
              </button>
            ) : null}
          </div>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,.85fr)]">
        <div className="space-y-6">
          <AsrRecorder
            disabled={mutationInFlight}
            onTranscriptChange={(text, confidence) => {
              if (hasUnsettledAssistantMutation(useAssistantStore.getState())) return;
              setVerifiedFinish(null);
              store.setTranscript(text);
              store.setInputMode("voice");
              setAsrConfidence(confidence);
            }}
            onActiveChange={setAsrActive}
            onSourceChange={handleAsrSource}
            onStart={startAsrWorkflow}
            onReset={resetWorkflowContext}
          />

          {store.transcript ? (
            <section className="surface p-5">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">
                    准备理解
                  </p>
                  <h2 className="mt-1 font-extrabold text-ink-950">确认要解析的文字</h2>
                </div>
                <Sparkles className="text-teal-500" size={20} />
              </div>
              {store.inputMode === "text_demo" ? (
                <div className="mb-3 flex items-start gap-2 rounded-xl border border-gold-100 bg-gold-100/45 p-3 text-xs font-semibold leading-5 text-amber-800">
                  <Keyboard className="mt-0.5 shrink-0" size={15} />
                  文本指令演示：这段文字由你点击示例填入，不是 ASR
                  转写。后续纠错、理解、确认、写入和数据库验证仍调用真实服务。
                </div>
              ) : null}
              <textarea
                value={store.transcript}
                onChange={(event) => {
                  if (hasUnsettledAssistantMutation(useAssistantStore.getState())) return;
                  store.setTranscript(event.target.value);
                }}
                disabled={inputBusy}
                className="field resize-y leading-7"
                rows={3}
                aria-label="待解析的转写文字"
              />
              <div className="mt-4 flex justify-end">
                <button
                  type="button"
                  disabled={inputBusy || !store.transcript.trim()}
                  onClick={() => void analyze()}
                  className="btn-primary"
                >
                  {store.workflowStatus === "analyzing" || store.workflowStatus === "preparing" ? (
                    <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                  ) : (
                    <BrainCircuit size={17} />
                  )}
                  {store.workflowStatus === "analyzing"
                    ? "正在理解"
                    : store.workflowStatus === "preparing"
                      ? "正在检查风险"
                      : "解析并检查"}
                  {!inputBusy ? <ArrowRight size={16} /> : null}
                </button>
              </div>
            </section>
          ) : null}

          {store.correction ? (
            <CorrectionDiff correction={store.correction} onChoose={chooseCorrection} />
          ) : null}
          {store.error && (!store.execution || store.execution.success) ? (
            <ErrorState
              title="流程暂未完成"
              message={store.error}
              onRetry={
                undoRetryActionId && !undoBusy
                  ? () => void undo(undoRetryActionId, "recovery")
                  : !store.execution && store.pendingAction?.status === "ready"
                    ? () => retryExecution(store.pendingAction?.id ?? "")
                    : !store.execution && !store.pendingAction && store.transcript
                      ? () => void analyze()
                      : undefined
              }
            />
          ) : null}
        </div>

        <aside className="space-y-6">
          <section className="surface p-5">
            <div className="flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
                <ShieldCheck size={20} />
              </span>
              <div>
                <p className="font-extrabold text-ink-950">可靠执行轨迹</p>
                <p className="text-xs text-ink-400">转写 → 理解 → 风险 → 确认 → 验证</p>
              </div>
            </div>
            <ol className="mt-5 grid grid-cols-5 gap-1" aria-label="执行进度">
              {["转写", "理解", "风险", "确认", "验证"].map((step, index) => {
                const reached =
                  Boolean(store.transcript) &&
                  (index === 0 ||
                    (Boolean(store.intent) && index <= 2) ||
                    (Boolean(store.pendingAction) && index <= 3) ||
                    Boolean(store.execution));
                return (
                  <li key={step} className="text-center">
                    <span
                      className={`mx-auto block h-1.5 rounded-full ${reached ? "bg-teal-500" : "bg-mist-200"}`}
                    />
                    <span
                      className={`mt-1.5 block text-[0.65rem] font-bold ${reached ? "text-teal-700" : "text-ink-300"}`}
                    >
                      {step}
                    </span>
                  </li>
                );
              })}
            </ol>
            {store.intent ? (
              <div className="mt-5 rounded-2xl bg-mist-50 p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-extrabold text-ink-800">
                    {intentLabels[store.intent.intent]}
                  </span>
                  <span className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-teal-700">
                    {Math.round(store.intent.confidence * 100)}%
                  </span>
                </div>
                {store.intent.ambiguities.length > 0 ? (
                  <p className="mt-2 text-xs leading-5 text-amber-700">
                    存在歧义：{store.intent.ambiguities.join("、")}
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="mt-5 text-sm leading-6 text-ink-400">
                完成一段转写后，这里会显示真实的意图、风险和验证结果。
              </p>
            )}
          </section>

          {store.pendingAction?.status === "needs_input" ? (
            <ClarificationCard
              question={clarificationQuestion}
              missingFields={store.pendingAction.missing_fields}
              candidates={targetCandidates(store.pendingAction)}
              busy={busy}
              onSubmit={(answer) => void analyze(answer)}
              onSelectCandidate={(id) => void selectTarget(id)}
            />
          ) : null}
          {store.pendingAction &&
          ["awaiting_confirmation", "awaiting_second_confirmation"].includes(
            store.pendingAction.status,
          ) ? (
            <ConfirmationCard
              action={store.pendingAction}
              busy={busy}
              onConfirm={confirm}
              onCancel={cancel}
            />
          ) : null}
          {store.workflowStatus === "executing" ? (
            <div className="surface flex items-center gap-3 p-5" aria-live="polite">
              <span className="size-5 animate-spin rounded-full border-2 border-mist-200 border-t-teal-600" />
              <div>
                <p className="font-bold text-ink-800">正在执行并重新查询数据库</p>
                <p className="text-xs text-ink-400">验证完成前不会显示成功</p>
              </div>
            </div>
          ) : null}
          {store.execution ? (
            <ExecutionResult
              result={store.execution}
              verifiedFinish={verifiedFinish}
              undoBusy={undoBusy}
              onRetry={
                store.workflowStatus !== "executing" &&
                store.execution.retryable &&
                undoRetryActionId
                  ? () => void undo(undoRetryActionId, "recovery")
                  : store.workflowStatus !== "executing" &&
                      store.execution.retryable &&
                      store.undoRecoveryActionId === null &&
                      store.pendingAction?.status === "ready" &&
                      store.pendingAction.action === store.execution.action
                    ? () => retryExecution(store.pendingAction?.id ?? "")
                    : undefined
              }
              onUndo={undoActionId ? () => void undo(undoActionId, "normal") : undefined}
            />
          ) : null}
          {store.knowledgeAnswer ? (
            <section className="surface p-5">
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">通知回答</p>
              <p className="mt-2 text-sm leading-7 text-ink-800">
                {store.knowledgeAnswer.sufficient
                  ? store.knowledgeAnswer.answer
                  : (store.knowledgeAnswer.message ?? "检索证据不足，无法确定。")}
              </p>
              <div className="mt-3 space-y-2">
                {store.knowledgeAnswer.evidence.map((item) => (
                  <blockquote
                    key={`${item.document_id}-${item.chunk_id}`}
                    className="rounded-xl bg-mist-50 p-3 text-xs leading-5 text-ink-600"
                  >
                    <strong>{item.document_title}</strong>
                    {item.page ? ` · 第 ${item.page} 页` : " · 无天然页码"}
                    <br />
                    {item.content}
                  </blockquote>
                ))}
              </div>
            </section>
          ) : null}
          {store.scheduleResults !== null ? (
            <section className="surface p-5">
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">日程查询</p>
              {store.scheduleResults.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {store.scheduleResults.slice(0, 8).map((event) => (
                    <div key={event.id} className="rounded-xl bg-mist-50 p-3">
                      <p className="text-sm font-bold text-ink-800">{event.title}</p>
                      <p className="mt-1 text-xs text-ink-400">
                        {formatDateTime(event.start_at, { timeZone: userSettings.timezone })}
                        {event.location ? ` · ${event.location}` : ""}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-ink-500">当前没有日程记录。</p>
              )}
            </section>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
