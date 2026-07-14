"use client";

import type {
  AsrTranscriptReference,
  CalendarEvent,
  CorrectionResult,
  IntentResult,
  PendingAction,
} from "@campusvoice/shared-types";
import { ArrowRight, BrainCircuit, RotateCcw, ShieldCheck, Sparkles } from "lucide-react";
import { useCallback, useState } from "react";

import { ConfirmationCard } from "@/components/actions/confirmation-card";
import { ExecutionResult } from "@/components/actions/execution-result";
import { PageHeader } from "@/components/layout/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { AsrRecorder } from "@/components/voice/asr-recorder";
import { ClarificationCard } from "@/components/voice/clarification-card";
import { CorrectionDiff } from "@/components/voice/correction-diff";
import { ApiError, api } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";
import { actionRequestFrom } from "@/lib/voice/action-request";
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
  const [scheduleResults, setScheduleResults] = useState<CalendarEvent[] | null>(null);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [transcriptionId, setTranscriptionId] = useState<string | null>(null);
  const [originalTranscript, setOriginalTranscript] = useState("");
  const [asrConfidence, setAsrConfidence] = useState<number | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const busy = ["analyzing", "preparing", "confirming", "executing"].includes(store.workflowStatus);

  const execute = useCallback(
    async (action: PendingAction) => {
      store.setWorkflowStatus("executing");
      try {
        const result = await api.actions.execute(action.id);
        store.setExecution(result);
        store.setPendingAction(result.success ? null : action);
        store.setWorkflowStatus(result.success ? "succeeded" : "error");
        if (result.success) store.setSourceDocumentId(null);
        else store.setError(result.message);
      } catch (reason) {
        store.setWorkflowStatus("error");
        store.setError(reason instanceof ApiError ? reason.userMessage : "执行失败，请重试。");
      }
    },
    [store],
  );

  const prepareIntent = useCallback(
    async (intent: IntentResult, audit?: { sourceText: string; correctedText: string }) => {
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
          store.setKnowledgeAnswer(answer);
          store.setWorkflowStatus("succeeded");
          store.setSourceDocumentId(null);
        } catch (reason) {
          store.setWorkflowStatus("error");
          store.setError(reason instanceof ApiError ? reason.userMessage : "校园通知查询失败。");
        }
        return;
      }
      if (intent.intent === "query_schedule") {
        store.setWorkflowStatus("executing");
        try {
          const response = await api.events.list();
          setScheduleResults(response.items);
          store.setWorkflowStatus("succeeded");
          store.setSourceDocumentId(null);
        } catch (reason) {
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
      const normalized = actionRequestFrom(intent, store.sourceDocumentId, userSettings);
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
        store.setPendingAction(action);
        if (action.status === "ready") await execute(action);
        else store.setWorkflowStatus("idle");
      } catch (reason) {
        store.setWorkflowStatus("error");
        store.setError(reason instanceof ApiError ? reason.userMessage : "无法准备操作，请重试。");
      }
    },
    [
      asrConfidence,
      execute,
      originalTranscript,
      store,
      transcriptionId,
      userSettings,
      voiceSessionId,
    ],
  );

  const analyze = useCallback(
    async (text = store.transcript) => {
      if (!text.trim()) return;
      store.clearResult();
      setScheduleResults(null);
      store.setTranscript(text.trim());
      store.setWorkflowStatus("analyzing");
      try {
        const correction = await api.correction.preview(
          text.trim(),
          asrConfidence ?? 1,
          transcriptionId ?? undefined,
        );
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
        setConversationId(parsed.conversation_id ?? conversationId);
        store.setIntent(parsed);
        await prepareIntent(parsed, {
          sourceText: originalTranscript.trim() || correction.original_text,
          correctedText: correction.corrected_text || text.trim(),
        });
      } catch (reason) {
        store.setWorkflowStatus("error");
        store.setError(
          reason instanceof ApiError ? reason.userMessage : "无法理解这条指令，请重试。",
        );
      }
    },
    [asrConfidence, conversationId, originalTranscript, prepareIntent, store, transcriptionId],
  );

  const selectTarget = async (targetId: string) => {
    const pending = store.pendingAction;
    if (!pending) return;
    store.setWorkflowStatus("preparing");
    try {
      await api.actions.cancel(pending.id);
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
      store.setPendingAction(prepared);
      store.setWorkflowStatus("idle");
    } catch (reason) {
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "无法选择目标，请重试。");
    }
  };

  const chooseCorrection = async (changeIndex: number, value: string) => {
    const correction = store.correction;
    const change = correction?.changes[changeIndex];
    if (!correction || !change) return;
    const selected = `${correction.original_text.slice(0, change.start)}${value}${correction.original_text.slice(change.end)}`;
    try {
      await api.correction.decide(correction.record_id, selected, true);
      store.setError(null);
      store.setCorrection(null);
      store.setTranscript(selected);
      await analyze(selected);
    } catch (reason) {
      store.setWorkflowStatus("error");
      store.setError(
        reason instanceof ApiError ? reason.userMessage : "无法保存术语确认结果，请重试。",
      );
    }
  };

  const confirm = async () => {
    const action = store.pendingAction;
    if (!action) return;
    store.setWorkflowStatus("confirming");
    try {
      const updated = await api.actions.confirm(action.id, true);
      store.setPendingAction(updated);
      if (updated.status === "ready") await execute(updated);
      else store.setWorkflowStatus("idle");
    } catch (reason) {
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "确认失败，请重试。");
    }
  };

  const cancel = async () => {
    const action = store.pendingAction;
    if (!action) return;
    store.setWorkflowStatus("confirming");
    try {
      await api.actions.cancel(action.id);
      store.setPendingAction(null);
      store.setWorkflowStatus("idle");
      store.setSourceDocumentId(null);
    } catch (reason) {
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "取消失败，请重试。");
    }
  };

  const undo = async () => {
    const actionId = store.pendingAction?.id;
    if (!actionId) {
      try {
        const logs = await api.actionLogs.list(10);
        const latest = logs.items.find(
          (log) => log.undoable && !log.undone && (log.action_id || log.id),
        );
        if (!latest) {
          store.setError("没有可撤销的最近操作。");
          return;
        }
        const result = await api.actions.undo(latest.action_id ?? latest.id);
        store.setExecution(result);
      } catch (reason) {
        store.setError(reason instanceof ApiError ? reason.userMessage : "撤销失败，请重试。");
      }
      return;
    }
    const result = await api.actions.undo(actionId);
    store.setExecution(result);
  };

  const clarificationQuestion =
    store.pendingAction?.clarification_question ??
    (store.pendingAction?.missing_fields?.length
      ? `请补充${store.pendingAction.missing_fields[0]}。`
      : "请补充最关键的信息。");

  const handleAsrSource = useCallback(
    (source: AsrTranscriptReference) => {
      setVoiceSessionId(source.sessionId);
      setTranscriptionId(source.transcriptionId);
      setOriginalTranscript(source.originalText);
      if (source.sessionId) store.setSourceDocumentId(null);
    },
    [store],
  );

  return (
    <div>
      <PageHeader
        eyebrow="Voice workflow"
        title="语音助手"
        description="从实时转写到数据库验证，每一步都可见、可确认。涉及写入的数据不会只凭 AI 回答就宣告成功。"
        actions={
          store.transcript ? (
            <button
              type="button"
              onClick={() => {
                store.reset();
                setConversationId(null);
                setScheduleResults(null);
                setVoiceSessionId(null);
                setTranscriptionId(null);
                setOriginalTranscript("");
              }}
              className="btn-secondary"
            >
              <RotateCcw size={16} /> 新指令
            </button>
          ) : undefined
        }
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,.85fr)]">
        <div className="space-y-6">
          <AsrRecorder
            onTranscriptChange={(text, confidence) => {
              store.setTranscript(text);
              setAsrConfidence(confidence);
            }}
            onSourceChange={handleAsrSource}
            onReset={() => {
              setVoiceSessionId(null);
              setTranscriptionId(null);
              setOriginalTranscript("");
              setAsrConfidence(null);
              setConversationId(null);
              store.reset();
            }}
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
              <textarea
                value={store.transcript}
                onChange={(event) => store.setTranscript(event.target.value)}
                className="field resize-y leading-7"
                rows={3}
                aria-label="待解析的转写文字"
              />
              <div className="mt-4 flex justify-end">
                <button
                  type="button"
                  disabled={busy || !store.transcript.trim()}
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
                  {!busy ? <ArrowRight size={16} /> : null}
                </button>
              </div>
            </section>
          ) : null}

          {store.correction ? (
            <CorrectionDiff correction={store.correction} onChoose={chooseCorrection} />
          ) : null}
          {store.error ? (
            <ErrorState
              title="流程暂未完成"
              message={store.error}
              onRetry={store.transcript ? () => void analyze() : undefined}
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
              onRetry={
                store.execution.retryable
                  ? () => store.pendingAction && void execute(store.pendingAction)
                  : undefined
              }
              onUndo={store.execution.success ? () => void undo() : undefined}
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
          {scheduleResults !== null ? (
            <section className="surface p-5">
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">日程查询</p>
              {scheduleResults.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {scheduleResults.slice(0, 8).map((event) => (
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
