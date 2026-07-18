import type { VerificationResult } from "@campusvoice/shared-types";
import {
  AlertCircle,
  BellRing,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Database,
  ListTodo,
  MapPin,
  RotateCcw,
  XCircle,
} from "lucide-react";
import Link from "next/link";

import { VerifiedFinish } from "@/components/actions/verified-finish";
import { formatDateTime } from "@/lib/format";
import type { VerifiedFinishEvent } from "@/lib/verified-finish";

const sideEffectLabels: Record<string, string> = {
  duplicate_task_created: "创建后检测到重复待办",
  duplicate_event_created: "创建后检测到重复日程",
  time_conflict: "新日程与已有安排存在时间冲突",
};

export function ExecutionResult({
  result,
  onRetry,
  onUndo,
  verifiedFinish,
}: Readonly<{
  result: VerificationResult;
  onRetry?: () => void;
  onUndo?: () => void;
  verifiedFinish?: VerifiedFinishEvent | null;
}>) {
  const record = result.record ?? null;
  const eventRecord = record && "start_at" in record ? record : null;
  const taskRecord = record && "due_at" in record ? record : null;

  return (
    <section
      aria-live="polite"
      className={`surface overflow-hidden border ${result.success ? "!border-teal-100" : "!border-coral-100"}`}
    >
      <div
        className={`flex items-start gap-3 p-5 ${result.success ? "bg-teal-50/70" : "bg-coral-50/80"}`}
      >
        {result.success ? (
          <CheckCircle2 className="mt-0.5 shrink-0 text-teal-600" size={24} />
        ) : (
          <XCircle className="mt-0.5 shrink-0 text-coral-600" size={24} />
        )}
        <div>
          <p className={`font-extrabold ${result.success ? "text-teal-700" : "text-coral-600"}`}>
            {result.success ? "数据库验证成功" : "操作未能验证成功"}
          </p>
          <p className="mt-1 text-sm leading-6 text-ink-700">{result.message}</p>
        </div>
      </div>

      <div className="p-5">
        {result.success && verifiedFinish ? (
          <div className="mb-4">
            <VerifiedFinish key={verifiedFinish.id} event={verifiedFinish} />
          </div>
        ) : null}
        {result.success && record ? (
          <div className="mb-4 rounded-2xl border border-teal-100 bg-teal-50/45 p-4">
            <p className="text-base font-extrabold text-ink-900">{record.title}</p>
            <div className="mt-3 grid gap-2 text-xs text-ink-600 sm:grid-cols-2">
              <p className="flex items-start gap-2">
                <Clock3 className="mt-0.5 shrink-0 text-teal-600" size={14} />
                {eventRecord
                  ? `${formatDateTime(eventRecord.start_at)} 至 ${formatDateTime(eventRecord.end_at)}`
                  : taskRecord?.due_at
                    ? `截止 ${formatDateTime(taskRecord.due_at)}`
                    : "未设置截止时间"}
              </p>
              {eventRecord?.location ? (
                <p className="flex items-start gap-2">
                  <MapPin className="mt-0.5 shrink-0 text-teal-600" size={14} />
                  {eventRecord.location}
                </p>
              ) : null}
              <p className="flex items-start gap-2">
                <BellRing className="mt-0.5 shrink-0 text-teal-600" size={14} />
                {eventRecord
                  ? eventRecord.reminder_minutes === null ||
                    eventRecord.reminder_minutes === undefined
                    ? "未设置提醒"
                    : `提前 ${eventRecord.reminder_minutes} 分钟提醒`
                  : taskRecord?.reminder_at
                    ? formatDateTime(taskRecord.reminder_at)
                    : "未设置提醒"}
              </p>
            </div>
            <Link href={eventRecord ? "/calendar" : "/tasks"} className="mt-3 btn-secondary">
              {eventRecord ? <CalendarDays size={15} /> : <ListTodo size={15} />}
              {eventRecord ? "查看日历" : "查看待办"}
            </Link>
          </div>
        ) : null}
        {result.record_id ? (
          <p className="mb-4 flex items-center gap-2 text-sm text-ink-500">
            <Database size={16} /> 记录 ID：
            <code className="rounded bg-mist-100 px-1.5 py-0.5 text-xs">{result.record_id}</code>
          </p>
        ) : null}
        {Object.keys(result.verified_fields).length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-bold text-ink-400">重新查询后的字段核验</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(result.verified_fields).map(([field, verified]) => (
                <span
                  key={field}
                  className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${verified ? "bg-teal-50 text-teal-700" : "bg-coral-50 text-coral-600"}`}
                >
                  {verified ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                  {field}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {result.side_effects.length > 0 ? (
          <div className="mt-4 rounded-xl bg-gold-100/45 p-3 text-sm text-amber-800">
            <p className="font-bold">检测到附带影响</p>
            <ul className="mt-1 list-inside list-disc space-y-1">
              {result.side_effects.map((effect) => (
                <li key={effect}>{sideEffectLabels[effect] ?? effect}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {!result.success && result.failure_reason ? (
          <p className="mt-4 text-sm text-coral-600">原因：{result.failure_reason}</p>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          {!result.success && result.retryable && onRetry ? (
            <button type="button" onClick={onRetry} className="btn-primary">
              <RotateCcw size={16} /> 重试一次
            </button>
          ) : null}
          {result.success && onUndo ? (
            <button type="button" onClick={onUndo} className="btn-secondary">
              <RotateCcw size={16} /> 撤销本次操作
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
