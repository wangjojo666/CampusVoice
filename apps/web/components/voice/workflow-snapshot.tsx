"use client";

import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  FileCheck2,
  RotateCcw,
  ScanText,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";

import { ExecutionResult } from "@/components/actions/execution-result";
import { ApiError, api } from "@/lib/api-client";
import { useAssistantStore } from "@/stores/assistant-store";

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

function stateTone(ready: boolean) {
  return ready
    ? "border-teal-100 bg-teal-50/70 text-teal-700"
    : "border-mist-100 bg-mist-50/70 text-ink-400";
}

export function WorkflowSnapshot() {
  const store = useAssistantStore();
  const correctionReady = Boolean(store.correction);
  const intentReady = Boolean(store.intent);
  const confirmationReady = Boolean(store.pendingAction || store.execution?.success);
  const verificationReady = Boolean(store.execution);
  const missing = [
    ...(store.intent?.missing_fields ?? []),
    ...(store.pendingAction?.missing_fields ?? []),
  ];
  const risks = [
    ...(store.intent?.ambiguities ?? []),
    ...(store.pendingAction?.risk_reasons ?? []),
  ];

  const undo = async () => {
    const actionId = store.lastExecutedActionId;
    if (!actionId) return;
    store.setWorkflowStatus("executing");
    try {
      const result = await api.actions.undo(actionId);
      store.setExecution(result);
      if (result.success) store.setLastExecutedActionId(null);
      store.setWorkflowStatus(result.success ? "succeeded" : "error");
      if (!result.success) store.setError(result.message);
    } catch (reason) {
      store.setWorkflowStatus("error");
      store.setError(reason instanceof ApiError ? reason.userMessage : "撤销失败，请重试。");
    }
  };

  return (
    <section className="mt-5 border-t border-mist-100 pt-5" aria-labelledby="workflow-status-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[0.68rem] font-bold tracking-[0.12em] text-teal-600 uppercase">
            Live workflow state
          </p>
          <h3 id="workflow-status-title" className="mt-1 font-extrabold text-ink-900">
            可验证执行状态
          </h3>
        </div>
        <span className="rounded-full bg-mist-100 px-2.5 py-1 text-[0.68rem] font-bold text-ink-500">
          仅显示真实 store 与 API 回执
        </span>
      </div>

      <ol className="mt-4 grid gap-2 sm:grid-cols-2">
        <li className={`rounded-xl border p-3 ${stateTone(Boolean(store.transcript))}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <ScanText size={14} /> 原始转写
          </div>
          <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-ink-700">
            {store.transcript || "等待真实语音转写或文本演示输入"}
          </p>
          {store.inputMode === "text_demo" ? (
            <p className="mt-1 text-[0.68rem] font-bold text-amber-700">
              文本指令演示，不是语音识别结果
            </p>
          ) : null}
        </li>
        <li className={`rounded-xl border p-3 ${stateTone(correctionReady)}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <FileCheck2 size={14} /> 校园术语纠错
          </div>
          <p className="mt-1.5 text-xs leading-5 text-ink-700">
            {store.correction
              ? store.correction.original_text === store.correction.corrected_text
                ? "已检查，未发现需要修改的术语"
                : `${store.correction.original_text} → ${store.correction.corrected_text}`
              : "尚未调用纠错 API"}
          </p>
        </li>
        <li className={`rounded-xl border p-3 ${stateTone(intentReady)}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <ShieldCheck size={14} /> 结构化理解与风险
          </div>
          <p className="mt-1.5 text-xs leading-5 text-ink-700">
            {store.intent ? intentLabels[store.intent.intent] : "尚未解析为待办或日程"}
          </p>
          {missing.length > 0 || risks.length > 0 ? (
            <p className="mt-1 flex items-start gap-1 text-[0.68rem] text-amber-700">
              <AlertTriangle className="mt-0.5 shrink-0" size={11} />
              {[...new Set([...missing.map((item) => `缺少 ${item}`), ...risks])].join("；")}
            </p>
          ) : store.intent ? (
            <p className="mt-1 text-[0.68rem] font-bold text-teal-700">字段完整；无新增风险</p>
          ) : null}
        </li>
        <li className={`rounded-xl border p-3 ${stateTone(confirmationReady)}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <CheckCircle2 size={14} /> 用户确认
          </div>
          <p className="mt-1.5 text-xs leading-5 text-ink-700">
            {store.pendingAction
              ? store.pendingAction.status === "needs_input"
                ? "等待补充缺失字段"
                : store.pendingAction.status === "awaiting_second_confirmation"
                  ? "等待第二次确认"
                  : store.pendingAction.status === "awaiting_confirmation"
                    ? "等待确认后写入"
                    : `服务端状态：${store.pendingAction.status}`
              : store.execution?.success
                ? "已确认并完成数据库写入"
                : "尚未生成待确认操作"}
          </p>
        </li>
        <li className={`rounded-xl border p-3 ${stateTone(verificationReady)}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <Database size={14} /> 数据库写入与复验
          </div>
          <p className="mt-1.5 text-xs leading-5 text-ink-700">
            {store.workflowStatus === "executing"
              ? "正在事务写入并重新查询数据库"
              : store.execution
                ? store.execution.success
                  ? `已验证记录 ${store.execution.record_id ?? ""}`.trim()
                  : "写入未通过数据库复验"
                : "尚无执行回执"}
          </p>
        </li>
        <li className={`rounded-xl border p-3 ${stateTone(Boolean(store.execution?.success))}`}>
          <div className="flex items-center gap-2 text-xs font-bold">
            <RotateCcw size={14} /> 撤销
          </div>
          <p className="mt-1.5 text-xs leading-5 text-ink-700">
            {store.execution?.success ? "已开放真实撤销入口" : "仅在验证成功后开放"}
          </p>
        </li>
      </ol>

      {store.execution ? (
        <div className="mt-4">
          <ExecutionResult
            result={store.execution}
            onUndo={
              store.execution.success && store.lastExecutedActionId ? () => void undo() : undefined
            }
          />
        </div>
      ) : null}

      {store.transcript ? (
        <div className="mt-4 flex justify-end">
          <Link href="/voice" className="btn-primary">
            进入完整确认流程 <ArrowRight size={16} />
          </Link>
        </div>
      ) : null}
    </section>
  );
}
