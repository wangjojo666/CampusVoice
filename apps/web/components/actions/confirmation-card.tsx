"use client";

import type { PendingAction } from "@campusvoice/shared-types";
import { AlertOctagon, AlertTriangle, Check, Clock3, ShieldCheck, X } from "lucide-react";

import { formatDateTime } from "@/lib/format";

const riskStyle = {
  low: { label: "低风险", icon: ShieldCheck, classes: "bg-teal-50 text-teal-700 border-teal-100" },
  medium: {
    label: "中风险",
    icon: AlertTriangle,
    classes: "bg-gold-100/55 text-amber-700 border-gold-100",
  },
  high: {
    label: "高风险",
    icon: AlertOctagon,
    classes: "bg-coral-50 text-coral-600 border-coral-100",
  },
} as const;

const fieldLabels: Record<string, string> = {
  title: "标题",
  description: "说明",
  course: "课程",
  due_at: "截止时间",
  reminder_at: "提醒时间",
  priority: "优先级",
  start_at: "开始时间",
  end_at: "结束时间",
  location: "地点",
  reminder_minutes: "提前提醒",
  target_id: "目标记录",
};

function renderValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "未设置";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ConfirmationCard({
  action,
  busy = false,
  onConfirm,
  onCancel,
}: Readonly<{
  action: PendingAction;
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void | Promise<void>;
}>) {
  const risk = riskStyle[action.risk_level];
  const RiskIcon = risk.icon;
  const secondStep = action.status === "awaiting_second_confirmation";
  const canConfirm = action.status === "awaiting_confirmation" || secondStep;

  return (
    <section className="surface overflow-hidden" aria-labelledby="confirmation-title">
      <div
        className={`border-b p-5 ${secondStep ? "border-coral-100 bg-coral-50/70" : "border-mist-200 bg-white/70"}`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">执行前确认</p>
            <h2 id="confirmation-title" className="mt-1 text-lg font-extrabold text-ink-950">
              {action.title ?? action.summary ?? "请确认本次操作"}
            </h2>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-bold ${risk.classes}`}
          >
            <RiskIcon size={15} />
            {risk.label}
          </span>
        </div>
        {secondStep ? (
          <div
            className="mt-4 flex items-start gap-2 rounded-xl border border-coral-100 bg-white/75 p-3 text-sm font-semibold leading-5 text-coral-600"
            role="alert"
          >
            <AlertOctagon className="mt-0.5 shrink-0" size={17} />
            这是高风险操作。请再次核对目标与字段，第二次确认后才会执行。
          </div>
        ) : null}
      </div>

      <div className="p-5">
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {Object.entries(action.payload).map(([key, value]) => (
            <div key={key} className="border-b border-mist-100 pb-2.5">
              <dt className="text-xs font-bold text-ink-400">{fieldLabels[key] ?? key}</dt>
              <dd className="mt-1 break-words text-sm font-semibold text-ink-800">
                {renderValue(value)}
              </dd>
            </div>
          ))}
        </dl>

        {action.risk_reasons.length > 0 ? (
          <div className="mt-4 rounded-xl bg-mist-50 p-3.5">
            <p className="mb-2 text-xs font-bold text-ink-500">风险判断依据</p>
            <ul className="space-y-1.5 text-sm text-ink-600">
              {action.risk_reasons.map((reason) => (
                <li key={reason} className="flex items-start gap-2">
                  <span className="mt-2 size-1.5 shrink-0 rounded-full bg-ink-300" />
                  {reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {action.expires_at ? (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-ink-400">
            <Clock3 size={14} /> 确认请求有效期至 {formatDateTime(action.expires_at)}
          </p>
        ) : null}

        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            disabled={busy}
            onClick={() => void onCancel()}
            className="btn-secondary"
          >
            <X size={17} />
            取消操作
          </button>
          <button
            type="button"
            disabled={busy || !canConfirm}
            onClick={() => void onConfirm()}
            className={secondStep ? "btn-danger" : "btn-primary"}
          >
            {busy ? (
              <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : (
              <Check size={17} />
            )}
            {busy ? "处理中" : secondStep ? "再次确认并执行" : "确认操作"}
          </button>
        </div>
      </div>
    </section>
  );
}
