"use client";

import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  Database,
  FileText,
  GitCompareArrows,
  Link2,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { ApiError, api } from "@/lib/api-client";
import type {
  ImpactCase,
  MigrationPlan,
  MigrationReceipt,
  NoticeChangeItem,
  NoticeChangeSet,
} from "@/lib/api-client";
import { formatDateTime, formatTime, toLocalInputValue } from "@/lib/format";

const FIELD_LABELS: Record<string, string> = {
  "event.start_at": "开始时间",
  "event.end_at": "结束时间",
  "event.location": "地点",
  "task.due_at": "截止时间",
  audience: "适用群体",
  required_materials: "所需材料",
  action_requirement: "操作要求",
  "reminder.minutes": "提醒建议",
};

const IMPACT_ACTION_LABELS: Record<string, string> = {
  apply: "建议更新",
  keep: "建议保留",
  cancel: "建议取消（需确认）",
  manual_review: "需要人工判断",
};

function displayValue(value: Record<string, unknown> | undefined | null) {
  if (!value) return "—";
  if (typeof value.iso === "string") return formatDateTime(value.iso);
  if (typeof value.text === "string") return value.text;
  if (typeof value.minutes === "number") return `提前 ${value.minutes} 分钟`;
  return Object.values(value)
    .filter((item) => typeof item === "string" || typeof item === "number")
    .join(" · ");
}

function snapshotValue(snapshot: Record<string, unknown>, key: string) {
  const value = snapshot[key];
  if (typeof value === "string" && /_at$/.test(key)) return formatDateTime(value);
  if (Array.isArray(value)) return `${value.length} 条来源历史`;
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function claimAnchor(claimId: string) {
  return `claim-evidence-${claimId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function verificationFailureReason(verification: Record<string, unknown>) {
  for (const key of ["reason", "error", "message", "mismatch"]) {
    const value = verification[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  const expected = verification.expected;
  const actual = verification.actual;
  if (expected !== undefined || actual !== undefined) {
    return `期望 ${JSON.stringify(expected)}，数据库实际 ${JSON.stringify(actual)}`;
  }
  return "数据库快照与迁移后期望值不一致；请展开字段详情核对。";
}

export default function RadarDetailPage() {
  const params = useParams<{ changeSetId: string }>();
  const changeSetId = params.changeSetId;
  const [changeSet, setChangeSet] = useState<NoticeChangeSet | null>(null);
  const [impacts, setImpacts] = useState<ImpactCase[]>([]);
  const [plan, setPlan] = useState<MigrationPlan | null>(null);
  const [receipt, setReceipt] = useState<MigrationReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [allowConflicts, setAllowConflicts] = useState(false);
  const [executeChallenge, setExecuteChallenge] = useState<string | null>(null);
  const [undoChallenge, setUndoChallenge] = useState<string | null>(null);
  const [errorRecovery, setErrorRecovery] = useState<
    "reload" | "preview" | "execute" | "undo" | null
  >(null);
  const [focusTarget, setFocusTarget] = useState<{
    target: "preview" | "receipt";
    request: number;
  } | null>(null);
  const previewRef = useRef<HTMLElement>(null);
  const receiptRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [changes, impactResult] = await Promise.all([
        api.radar.changeSet(changeSetId),
        api.radar.impacts(changeSetId),
      ]);
      const migrationPlanId = impactResult.items.find(
        (impact) => impact.migration_plan_id,
      )?.migration_plan_id;
      const restoredPlan = migrationPlanId ? await api.radar.plan(migrationPlanId) : null;
      let restoredReceipt: MigrationReceipt | null = null;
      if (
        restoredPlan &&
        ["undone", "undo_verification_failed"].includes(restoredPlan.status) &&
        Object.keys(restoredPlan.undo_receipt).length > 0
      ) {
        restoredReceipt = await api.radar.receipt(restoredPlan.id, "undo");
      } else if (
        restoredPlan &&
        ["verified", "verification_failed"].includes(restoredPlan.status) &&
        Object.keys(restoredPlan.execute_receipt).length > 0
      ) {
        restoredReceipt = await api.radar.receipt(restoredPlan.id, "execute");
      }
      setChangeSet(changes);
      setImpacts(impactResult.items);
      setPlan(restoredPlan);
      setReceipt(restoredReceipt);
      setExecuteChallenge(null);
      setUndoChallenge(null);
      setError(null);
      setErrorRecovery(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法加载通知变化。");
      setErrorRecovery("reload");
    } finally {
      setLoading(false);
    }
  }, [changeSetId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (focusTarget?.target === "preview" && plan) previewRef.current?.focus();
    if (focusTarget?.target === "receipt" && receipt) receiptRef.current?.focus();
  }, [focusTarget, plan, receipt]);

  const activeImpacts = useMemo(
    () => impacts.filter((impact) => impact.status !== "dismissed"),
    [impacts],
  );

  const claimEvidence = useMemo(() => {
    const result = new Map<string, { label: string; anchor: string }>();
    for (const item of changeSet?.items ?? []) {
      if (item.before) {
        result.set(item.before.claim_id, {
          label: `${FIELD_LABELS[item.claim_key] ?? item.claim_key} · 旧版证据`,
          anchor: claimAnchor(item.before.claim_id),
        });
      }
      if (item.after) {
        result.set(item.after.claim_id, {
          label: `${FIELD_LABELS[item.claim_key] ?? item.claim_key} · 新版证据`,
          anchor: claimAnchor(item.after.claim_id),
        });
      }
    }
    return result;
  }, [changeSet]);

  const run = async (
    operation: () => Promise<void>,
    recovery: "reload" | "preview" | "execute" | "undo" = "preview",
  ) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
      setErrorRecovery(null);
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.userMessage : "操作失败，请重新生成预览后重试。",
      );
      setErrorRecovery(
        recovery === "execute" &&
          reason instanceof ApiError &&
          reason.status > 0 &&
          reason.status < 500
          ? "preview"
          : recovery,
      );
    } finally {
      setBusy(false);
    }
  };

  const review = (item: NoticeChangeItem, decision: "approved" | "rejected") =>
    run(async () => {
      await api.radar.reviewChange(item.id, decision);
      await load();
    });

  const detect = () =>
    run(async () => {
      const result = await api.radar.detectImpacts(changeSetId);
      setImpacts(result.items);
    });

  const preview = () =>
    run(async () => {
      const result = await api.radar.preview(changeSetId);
      setPlan(result);
      setReceipt(null);
      setExecuteChallenge(null);
      setAllowConflicts(false);
      setUndoChallenge(null);
      setFocusTarget({ target: "preview", request: Date.now() });
    });

  const execute = () => {
    if (!plan) return;
    if (plan.conflicts.length > 0 && !allowConflicts) {
      setError("预览存在日程冲突。系统不会静默执行；请明确勾选冲突覆盖后再继续。");
      setErrorRecovery("preview");
      return;
    }
    if (plan.required_confirmations === 2 && !executeChallenge) {
      void run(async () => {
        const issued = await api.radar.beginExecute(plan, allowConflicts);
        setExecuteChallenge(issued.challenge);
      }, "preview");
      return;
    }
    void run(async () => {
      try {
        const result = executeChallenge
          ? await api.radar.finishExecute(plan, allowConflicts, executeChallenge)
          : await api.radar.execute(plan, allowConflicts);
        setReceipt(result);
        setPlan(await api.radar.plan(plan.id));
        setFocusTarget({ target: "receipt", request: Date.now() });
      } finally {
        setExecuteChallenge(null);
      }
    }, "execute");
  };

  const resumeVerification = () => {
    if (!plan) return;
    void run(async () => {
      const current = await api.radar.plan(plan.id);
      setPlan(current);
      let result: MigrationReceipt;
      if (current.status === "verified") {
        result = await api.radar.receipt(current.id, "execute");
      } else if (["applied", "verification_failed"].includes(current.status)) {
        result = await api.radar.resumeExecute(
          current,
          current.conflicts.length > 0 || allowConflicts,
        );
      } else {
        throw new ApiError("迁移尚未写入；请重新完成确认，系统不会用恢复流程代替执行。", {
          status: 409,
        });
      }
      setReceipt(result);
      if (current.status !== "verified") setPlan(await api.radar.plan(plan.id));
      setFocusTarget({ target: "receipt", request: Date.now() });
    }, "execute");
  };

  const resumeUndoVerification = () => {
    if (!plan) return;
    void run(async () => {
      const current = await api.radar.plan(plan.id);
      setPlan(current);
      let result: MigrationReceipt;
      if (current.status === "undone") {
        result = await api.radar.receipt(current.id, "undo");
      } else if (["undo_applied", "undo_verification_failed"].includes(current.status)) {
        result = await api.radar.resumeUndo(current);
      } else {
        throw new ApiError("撤销尚未写入；请重新完成两次确认，系统不会用恢复流程代替撤销。", {
          status: 409,
        });
      }
      setReceipt(result);
      if (current.status !== "undone") setPlan(await api.radar.plan(plan.id));
      setFocusTarget({ target: "receipt", request: Date.now() });
    }, "undo");
  };

  const undo = () => {
    if (!plan) return;
    if (!undoChallenge) {
      void run(async () => {
        const current = await api.radar.plan(plan.id);
        setPlan(current);
        if (current.status === "undone") {
          setReceipt(await api.radar.receipt(current.id, "undo"));
          setFocusTarget({ target: "receipt", request: Date.now() });
          return;
        }
        if (["undo_applied", "undo_verification_failed"].includes(current.status)) {
          setReceipt(await api.radar.resumeUndo(current));
          setPlan(await api.radar.plan(current.id));
          setFocusTarget({ target: "receipt", request: Date.now() });
          return;
        }
        if (!["applied", "verified", "verification_failed"].includes(current.status)) {
          throw new ApiError("当前迁移状态不可撤销；请重新加载最新计划。", { status: 409 });
        }
        const issued = await api.radar.beginUndo(current);
        setUndoChallenge(issued.challenge);
      }, "undo");
      return;
    }
    void run(async () => {
      try {
        const result = await api.radar.finishUndo(plan, undoChallenge);
        setReceipt(result);
        setPlan(await api.radar.plan(plan.id));
        setFocusTarget({ target: "receipt", request: Date.now() });
      } finally {
        setUndoChallenge(null);
      }
    }, "undo");
  };

  return (
    <div>
      <PageHeader
        eyebrow="Campus Radar"
        title="通知变化如何影响我的安排"
        description="从原文证据到结构化变化、个人影响、迁移预览和数据库复验，每一步都可以追溯。"
      />

      <div aria-live="polite" className="mb-5">
        {loading ? <p className="text-sm text-ink-500">正在加载版本差异与影响图…</p> : null}
        {error ? (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-coral-100 bg-coral-50 p-4 text-sm text-coral-600"
          >
            <p>{error}</p>
            {errorRecovery ? (
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => {
                  if (errorRecovery === "reload") void load();
                  else if (errorRecovery === "undo") {
                    if (plan && ["undo_applied", "undo_verification_failed"].includes(plan.status))
                      resumeUndoVerification();
                    else undo();
                  } else if (errorRecovery === "execute") resumeVerification();
                  else void preview();
                }}
              >
                {errorRecovery === "reload"
                  ? "重试加载"
                  : errorRecovery === "undo"
                    ? "重试整组撤销"
                    : errorRecovery === "execute"
                      ? "继续执行后数据库验证"
                      : "重新生成迁移预览"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {changeSet ? (
        <>
          <section className="surface mb-6 p-5 sm:p-6" aria-labelledby="notice-diff-title">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                  Notice Diff
                </p>
                <h2 id="notice-diff-title" className="text-xl font-extrabold text-ink-950">
                  v1 → v2 结构化变化
                </h2>
              </div>
              <span className="rounded-full bg-mist-100 px-3 py-1 text-xs font-bold text-ink-500">
                算法 {changeSet.algorithm_version}
              </span>
            </div>

            {changeSet.items.length === 0 ? (
              <p className="text-sm text-ink-500">只检测到文字润色，没有产生结构化高优先级变化。</p>
            ) : (
              <div className="space-y-4">
                {changeSet.items.map((item) => (
                  <article key={item.id} className="rounded-2xl border border-mist-100 p-4 sm:p-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <GitCompareArrows className="text-teal-600" size={18} aria-hidden="true" />
                        <h3 className="font-bold text-ink-800">
                          {FIELD_LABELS[item.claim_key] ?? item.claim_key}
                        </h3>
                        <span className="rounded-full bg-mist-100 px-2 py-0.5 text-[0.68rem] font-bold text-ink-500">
                          {item.change_type === "changed"
                            ? "已修改"
                            : item.change_type === "added"
                              ? "新增"
                              : "移除"}
                        </span>
                      </div>
                      <span className="text-xs font-semibold text-ink-400">
                        置信度 {Math.round(item.confidence * 100)}%
                      </span>
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      <EvidencePanel
                        label="旧值与旧版原文"
                        tone="before"
                        evidence={item.before}
                        anchor={item.before ? claimAnchor(item.before.claim_id) : undefined}
                      />
                      <EvidencePanel
                        label="新值与新版原文"
                        tone="after"
                        evidence={item.after}
                        anchor={item.after ? claimAnchor(item.after.claim_id) : undefined}
                      />
                    </div>
                    {item.review_state === "pending" ? (
                      <div
                        role="alert"
                        className="mt-4 flex flex-wrap items-center gap-2 rounded-xl bg-gold-100/60 p-3"
                      >
                        <AlertTriangle size={16} className="text-amber-800" aria-hidden="true" />
                        <p className="mr-auto text-sm text-amber-900">
                          证据或置信度不足，审核前不会传播影响。
                        </p>
                        <button
                          className="btn-secondary"
                          disabled={busy}
                          onClick={() => void review(item, "rejected")}
                        >
                          忽略变化
                        </button>
                        <button
                          className="btn-primary"
                          disabled={busy}
                          onClick={() => void review(item, "approved")}
                        >
                          确认证据
                        </button>
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="surface mb-6 p-5 sm:p-6" aria-labelledby="impact-canvas-title">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                  Impact Canvas
                </p>
                <h2 id="impact-canvas-title" className="text-xl font-extrabold text-ink-950">
                  证据如何传导到个人安排
                </h2>
              </div>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => void detect()}
              >
                重新检测影响
              </button>
            </div>
            <div className="grid items-stretch gap-2 lg:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr]">
              <CanvasNode
                icon={<FileText size={19} />}
                title="原文证据"
                detail={`${changeSet.items.length} 项可追溯 claim`}
              />
              <FlowArrow />
              <CanvasNode
                icon={<GitCompareArrows size={19} />}
                title="结构化变化"
                detail="只比较规范化语义值"
              />
              <FlowArrow />
              <CanvasNode
                icon={<CalendarClock size={19} />}
                title="受影响安排"
                detail={`${activeImpacts.length} 条影响关系`}
              />
              <FlowArrow />
              <CanvasNode
                icon={<Sparkles size={19} />}
                title="迁移建议"
                detail="确认前不写真实数据"
              />
            </div>
            <div className="mt-5 grid gap-3 md:grid-cols-2">
              {activeImpacts.map((impact) => (
                <article key={impact.id} className="rounded-2xl border border-mist-100 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-bold text-ink-800">
                      {impact.entity_type === "event" ? "日程" : "待办"} ·{" "}
                      {String(impact.current_snapshot.title ?? impact.entity_id)}
                    </h3>
                    <span className="rounded-full bg-teal-50 px-2 py-1 text-xs font-bold text-teal-700">
                      {impact.status}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-ink-500">{impact.reason}</p>
                  {impact.recommended_action ? (
                    <p
                      className={`mt-2 rounded-lg px-2.5 py-1.5 text-xs font-bold ${impact.requires_manual_review ? "bg-gold-100 text-amber-900" : "bg-teal-50 text-teal-800"}`}
                    >
                      {IMPACT_ACTION_LABELS[impact.recommended_action] ?? impact.recommended_action}
                      {impact.requires_manual_review ? " · 不会自动写入" : ""}
                    </p>
                  ) : null}
                  <dl className="mt-3 space-y-1 text-sm">
                    {Object.entries(impact.proposed_patch).map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-4">
                        <dt className="text-ink-400">
                          {FIELD_LABELS[`event.${key}`] ?? FIELD_LABELS[`task.${key}`] ?? key}
                        </dt>
                        <dd className="text-right font-semibold text-ink-700">
                          {snapshotValue({ [key]: value }, key)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  {Object.keys(impact.proposed_patch).length === 0 ? (
                    <p className="mt-3 text-xs text-ink-500">
                      本建议没有自动字段补丁；系统只保留、取消或人工复核建议，不会静默删除安排。
                    </p>
                  ) : null}
                </article>
              ))}
            </div>
            {activeImpacts.length === 0 ? (
              <p className="mt-4 text-sm text-ink-500">
                当前用户没有与旧版通知精确关联且适用的任务或日程。
              </p>
            ) : null}
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                className="btn-primary"
                disabled={busy || activeImpacts.length === 0}
                onClick={() => void preview()}
              >
                生成迁移预览 <ArrowRight size={16} aria-hidden="true" />
              </button>
            </div>
          </section>
        </>
      ) : null}

      {plan ? (
        <section
          ref={previewRef}
          id="migration-preview"
          tabIndex={-1}
          className="surface mb-6 p-5 outline-none sm:p-6"
          aria-labelledby="migration-preview-title"
        >
          <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                Before / After Timeline
              </p>
              <h2 id="migration-preview-title" className="text-xl font-extrabold text-ink-950">
                确认前预览，不会写入真实安排
              </h2>
            </div>
            <span
              className={`rounded-full px-3 py-1 text-xs font-bold ${plan.risk_level === "high" ? "bg-coral-50 text-coral-600" : "bg-teal-50 text-teal-700"}`}
            >
              {plan.risk_level === "high" ? "高风险 · 两次确认" : "普通更新 · 一次确认"}
            </span>
          </div>

          <div className="space-y-4">
            {plan.items.map((item) => (
              <article key={item.id} className="min-w-0 rounded-2xl border border-mist-100 p-4">
                <h3 className="font-bold text-ink-800">
                  {item.entity_type === "event" ? "日程" : "待办"} ·{" "}
                  {String(item.before.title ?? item.entity_id)}
                </h3>
                <ScheduleTimeline item={item} conflicts={plan.conflicts} />
                <div className="mt-3 grid gap-3 md:grid-cols-[1fr_auto_1fr]">
                  <SnapshotPanel label="执行前" snapshot={item.before} />
                  <div className="flex items-center justify-center text-teal-600">
                    <ArrowRight className="hidden md:block" size={20} aria-hidden="true" />
                    <ArrowDown className="md:hidden" size={20} aria-hidden="true" />
                  </div>
                  <SnapshotPanel label="建议执行后" snapshot={item.after} />
                </div>
                <div className="mt-3 rounded-xl border border-teal-100 bg-teal-50/50 p-3">
                  <p className="flex items-center gap-1.5 text-xs font-bold text-teal-800">
                    <Link2 size={14} aria-hidden="true" /> 本项迁移引用的 source_claim_ids
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {item.source_claim_ids.length ? (
                      item.source_claim_ids.map((claimId) => {
                        const source = claimEvidence.get(claimId);
                        return source ? (
                          <a
                            key={claimId}
                            href={`#${source.anchor}`}
                            className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-teal-700 underline-offset-2 hover:underline"
                          >
                            {source.label} · {claimId}
                          </a>
                        ) : (
                          <span
                            key={claimId}
                            className="rounded-full bg-gold-100 px-2.5 py-1 text-xs font-bold text-amber-800"
                          >
                            {claimId} · 当前差异中未找到证据
                          </span>
                        );
                      })
                    ) : (
                      <span className="text-xs font-semibold text-coral-600">缺少来源 claim</span>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>

          {plan.conflicts.length > 0 ? (
            <div role="alert" className="mt-5 rounded-2xl border border-coral-100 bg-coral-50 p-4">
              <div className="flex items-start gap-2 text-coral-600">
                <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
                <div>
                  <h3 className="font-bold">检测到 {plan.conflicts.length} 个真实日程冲突</h3>
                  <p className="mt-1 text-sm">
                    默认阻止执行。只有明确选择覆盖并完成两阶段确认后才会继续。
                  </p>
                </div>
              </div>
              <label className="mt-3 flex items-start gap-2 text-sm font-semibold text-ink-700">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={allowConflicts}
                  onChange={(event) => {
                    setAllowConflicts(event.target.checked);
                    setExecuteChallenge(null);
                  }}
                />
                我已查看冲突，仍要覆盖写入
              </label>
            </div>
          ) : null}

          <div className="mt-5 rounded-2xl bg-mist-50 p-4" aria-label="迁移动作确认">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-bold text-ink-800">将更新 {plan.items.length} 项安排</p>
                <p className="mt-1 text-xs text-ink-500">
                  整组同一事务；任一失败则全部回滚。执行后会开启新数据库会话逐项复查。
                </p>
              </div>
              <button
                type="button"
                className={executeChallenge ? "btn-danger" : "btn-primary"}
                disabled={busy || plan.status !== "ready"}
                onClick={execute}
              >
                <ShieldCheck size={17} aria-hidden="true" />
                {executeChallenge ? "再次确认并执行整组迁移" : "确认迁移"}
              </button>
            </div>
          </div>
          {!receipt && ["applied", "verification_failed"].includes(plan.status) ? (
            <div
              role="alert"
              className="mt-5 rounded-2xl border border-gold-200 bg-gold-100/60 p-4"
            >
              <h3 className="font-bold text-amber-950">业务写入已发生，但成功回执尚未确认</h3>
              <p className="mt-1 text-sm leading-6 text-amber-900">
                这可能是执行后连接中断或数据库复验失败。继续验证会复用本计划的幂等键；也可以两阶段撤销整组，系统不会把当前状态冒充成功。
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={resumeVerification}
                >
                  继续数据库验证
                </button>
                <button
                  type="button"
                  className={undoChallenge ? "btn-danger" : "btn-secondary"}
                  disabled={busy}
                  onClick={undo}
                >
                  <RotateCcw size={16} aria-hidden="true" />
                  {undoChallenge ? "再次确认撤销整组迁移" : "撤销整个迁移"}
                </button>
              </div>
            </div>
          ) : null}
          {!receipt && ["undo_applied", "undo_verification_failed"].includes(plan.status) ? (
            <div
              role="alert"
              className="mt-5 rounded-2xl border border-gold-200 bg-gold-100/60 p-4"
            >
              <h3 className="font-bold text-amber-950">整组恢复已写入，但撤销回执尚未确认</h3>
              <p className="mt-1 text-sm leading-6 text-amber-900">
                继续验证会复用原撤销幂等键，只重新读取数据库，不会再次恢复对象。
              </p>
              <button
                type="button"
                className="btn-secondary mt-3"
                disabled={busy}
                onClick={resumeUndoVerification}
              >
                继续撤销后数据库验证
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {receipt ? (
        <section
          ref={receiptRef}
          id="verification-receipt"
          tabIndex={-1}
          className="surface p-5 outline-none sm:p-6"
          aria-labelledby="verification-receipt-title"
          aria-live="polite"
          aria-atomic="true"
          role="status"
        >
          <div className="flex items-start gap-3">
            {receipt.all_verified ? (
              <CheckCircle2
                className="mt-0.5 shrink-0 text-teal-600"
                size={24}
                aria-hidden="true"
              />
            ) : (
              <AlertTriangle
                className="mt-0.5 shrink-0 text-coral-600"
                size={24}
                aria-hidden="true"
              />
            )}
            <div className="flex-1">
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                Verification Receipt
              </p>
              <h2 id="verification-receipt-title" className="text-xl font-extrabold text-ink-950">
                {receipt.all_verified
                  ? `${receipt.verified_count}/${receipt.total_count} 项安排已${receipt.operation === "undo" ? "恢复并" : "更新并"}通过数据库验证`
                  : `${receipt.operation === "undo" ? "撤销后" : "执行后"}数据库复验未全部通过`}
              </h2>
              <p className="mt-1 text-sm text-ink-500">
                {receipt.all_verified
                  ? "技术 ID、对象版本和数据库快照保留在下方可展开详情中。"
                  : `仅 ${receipt.verified_count}/${receipt.total_count} 项通过；这不是成功回执，请查看逐项失败原因。`}
              </p>
            </div>
          </div>
          <details className="mt-4 rounded-2xl border border-mist-100 p-4">
            <summary className="cursor-pointer font-bold text-ink-700">查看技术验证详情</summary>
            <div className="mt-3 space-y-3 text-xs text-ink-500">
              {receipt.items.map((item) => (
                <div
                  key={item.id}
                  className={`rounded-xl p-3 ${item.verification.verified ? "bg-teal-50" : "bg-coral-50"}`}
                >
                  <p className="font-bold text-ink-700">
                    {item.entity_type}:{item.entity_id} ·{" "}
                    {item.verification.verified ? "数据库验证通过" : "数据库验证失败"}
                  </p>
                  {!item.verification.verified ? (
                    <p className="mt-1 font-semibold text-coral-700">
                      原因：{verificationFailureReason(item.verification)}
                    </p>
                  ) : null}
                  <dl className="mt-2 grid gap-1 sm:grid-cols-2">
                    {Object.entries(item.verification).map(([key, value]) => (
                      <div key={key} className="min-w-0">
                        <dt className="font-bold text-ink-400">{key}</dt>
                        <dd className="break-words text-ink-600">
                          {typeof value === "string" ? value : JSON.stringify(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </details>
          {!receipt.all_verified ? (
            <div className="mt-4 rounded-xl border border-coral-100 bg-coral-50 p-3 text-sm text-coral-700">
              <div className="flex gap-2">
                <Database className="mt-0.5 shrink-0" size={17} aria-hidden="true" />
                <p>
                  系统没有把这次操作标记为成功。可以用原幂等键继续数据库验证；执行失败时也仍可发起整组安全撤销。
                </p>
              </div>
              <button
                type="button"
                className="btn-secondary mt-3"
                disabled={busy}
                onClick={
                  receipt.operation === "execute" ? resumeVerification : resumeUndoVerification
                }
              >
                {receipt.operation === "execute" ? "继续数据库验证" : "继续撤销后数据库验证"}
              </button>
            </div>
          ) : null}
          {receipt.operation === "execute" ? (
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-ink-500">
                整组撤销会恢复执行前内容，并再次查询数据库验证。
              </p>
              <button
                type="button"
                className={undoChallenge ? "btn-danger" : "btn-secondary"}
                disabled={busy}
                onClick={undo}
              >
                <RotateCcw size={16} aria-hidden="true" />
                {undoChallenge ? "再次确认撤销整组迁移" : "撤销整个迁移"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function EvidencePanel({
  label,
  tone,
  evidence,
  anchor,
}: {
  label: string;
  tone: "before" | "after";
  evidence: NoticeChangeItem["before"];
  anchor?: string;
}) {
  return (
    <div
      id={anchor}
      className={`rounded-xl border p-3 ${tone === "before" ? "border-coral-100 bg-coral-50/50" : "border-teal-100 bg-teal-50/60"}`}
    >
      <p className="text-xs font-bold text-ink-500">{label}</p>
      <p className="mt-1 font-bold text-ink-800">
        {displayValue(evidence?.normalized_value ?? evidence?.value)}
      </p>
      <blockquote className="mt-2 border-l-2 border-current pl-3 text-sm leading-6 text-ink-600">
        {evidence?.evidence_text ?? "该版本没有对应 claim"}
      </blockquote>
      {evidence ? (
        <div className="mt-2 space-y-1 text-[0.68rem] text-ink-400">
          <p>claim {evidence.claim_id}</p>
          <p>
            证据区间 {evidence.evidence_start}–{evidence.evidence_end}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function CanvasNode({
  icon,
  title,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="rounded-2xl border border-mist-100 bg-white p-4 text-center">
      <span className="mx-auto flex size-10 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
        {icon}
      </span>
      <h3 className="mt-2 font-bold text-ink-800">{title}</h3>
      <p className="mt-1 text-xs text-ink-500">{detail}</p>
    </div>
  );
}

function FlowArrow() {
  return (
    <div className="flex items-center justify-center text-teal-500" aria-hidden="true">
      <ArrowRight className="hidden lg:block" size={18} />
      <ArrowDown className="lg:hidden" size={18} />
    </div>
  );
}

function ScheduleTimeline({
  item,
  conflicts,
}: {
  item: MigrationPlan["items"][number];
  conflicts: MigrationPlan["conflicts"];
}) {
  const dateValue = (snapshot: Record<string, unknown>, keys: string[]) => {
    for (const key of keys) {
      const value = snapshot[key];
      if (typeof value === "string") {
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) return date;
      }
    }
    return null;
  };
  const beforeStart = dateValue(item.before, ["start_at", "due_at", "reminder_at"]);
  const beforeEnd = dateValue(item.before, ["end_at"]);
  const afterStart = dateValue(item.after, ["start_at", "due_at", "reminder_at"]);
  const afterEnd = dateValue(item.after, ["end_at"]);
  const starts = [beforeStart, afterStart].filter((value): value is Date => Boolean(value));
  const ends = [beforeEnd, afterEnd, ...starts].filter((value): value is Date => Boolean(value));
  const localClock = (value: Date) => {
    const local = toLocalInputValue(value.toISOString());
    return { hour: Number(local.slice(11, 13)), minute: Number(local.slice(14, 16)) };
  };
  const startHour = starts.length
    ? Math.max(0, Math.min(...starts.map((value) => localClock(value).hour)) - 1)
    : 8;
  const endHour = ends.length
    ? Math.min(24, Math.max(...ends.map((value) => localClock(value).hour + 1)) + 1)
    : 18;
  const totalMinutes = Math.max(120, (endHour - startHour) * 60);
  const ticks = Array.from({ length: endHour - startHour + 1 }, (_, index) => startHour + index);
  const unchangedKeys = Object.keys(item.before)
    .filter(
      (key) =>
        key in item.after &&
        JSON.stringify(item.before[key]) === JSON.stringify(item.after[key]) &&
        !["id", "version", "source_history"].includes(key),
    )
    .slice(0, 6);
  const blockStyle = (start: Date | null, end: Date | null) => {
    if (!start) return { top: "8%", height: "22%" };
    const startClock = localClock(start);
    const startMinutes = startClock.hour * 60 + startClock.minute - startHour * 60;
    const endMinutes = end
      ? localClock(end).hour * 60 + localClock(end).minute - startHour * 60
      : startMinutes + 45;
    const top = Math.max(0, Math.min(92, (startMinutes / totalMinutes) * 100));
    const height = Math.max(12, Math.min(50, ((endMinutes - startMinutes) / totalMinutes) * 100));
    return { top: `${top}%`, height: `${height}%` };
  };
  const timeLabel = (start: Date | null, end: Date | null) => {
    if (!start) return "未提供时间";
    const startText = formatTime(start.toISOString());
    const endText = end ? formatTime(end.toISOString()) : undefined;
    return endText ? `${startText}–${endText}` : startText;
  };

  return (
    <div className="mt-4 min-w-0 rounded-2xl bg-mist-50 p-3" aria-label="响应式日历迁移时间线">
      <div className="mb-2 flex flex-wrap gap-2 text-[0.68rem] font-bold">
        <span className="rounded-full border border-dashed border-ink-300 bg-white px-2 py-1 text-ink-600">
          Before ghost：旧安排
        </span>
        <span className="rounded-full bg-teal-600 px-2 py-1 text-white">New block：建议新安排</span>
        {conflicts.length ? (
          <span className="rounded-full bg-coral-100 px-2 py-1 text-coral-700">
            Conflicts：{conflicts.length}
          </span>
        ) : (
          <span className="rounded-full bg-white px-2 py-1 text-ink-500">Conflicts：无</span>
        )}
      </div>
      <div className="grid min-w-0 grid-cols-[2.75rem_minmax(0,1fr)] gap-2">
        <div className="relative h-56 text-[0.62rem] text-ink-400" aria-hidden="true">
          {ticks.map((hour, index) => (
            <span
              key={hour}
              className="absolute right-0 -translate-y-1/2"
              style={{ top: `${(index / Math.max(1, ticks.length - 1)) * 100}%` }}
            >
              {String(hour).padStart(2, "0")}:00
            </span>
          ))}
        </div>
        <div className="relative h-56 min-w-0 overflow-hidden rounded-xl border border-mist-200 bg-white">
          {ticks.map((hour, index) => (
            <span
              key={hour}
              className="absolute inset-x-0 border-t border-dashed border-mist-200"
              style={{ top: `${(index / Math.max(1, ticks.length - 1)) * 100}%` }}
              aria-hidden="true"
            />
          ))}
          <div
            className="absolute left-2 w-[calc(50%_-_0.75rem)] overflow-hidden rounded-lg border-2 border-dashed border-ink-300 bg-white/90 p-2 text-[0.68rem] text-ink-600 shadow-sm"
            style={blockStyle(beforeStart, beforeEnd)}
            aria-label={`Before ghost 旧安排，${timeLabel(beforeStart, beforeEnd)}`}
          >
            <strong className="block">Before ghost</strong>
            <span>{timeLabel(beforeStart, beforeEnd)}</span>
          </div>
          <div
            className="absolute right-2 w-[calc(50%_-_0.75rem)] overflow-hidden rounded-lg bg-teal-600 p-2 text-[0.68rem] text-white shadow-sm"
            style={blockStyle(afterStart, afterEnd)}
            aria-label={`New block 建议新安排，${timeLabel(afterStart, afterEnd)}`}
          >
            <strong className="block">New block</strong>
            <span>{timeLabel(afterStart, afterEnd)}</span>
          </div>
        </div>
      </div>
      {conflicts.length ? (
        <div className="mt-2 rounded-lg border-l-4 border-coral-500 bg-coral-50 p-2 text-xs text-coral-700">
          <strong>冲突区：</strong>{" "}
          {conflicts
            .map((conflict) =>
              String(conflict.title ?? conflict.conflicting_event_id ?? "未知日程"),
            )
            .join("、")}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-500">
        <strong>Unchanged：</strong>
        {unchangedKeys.length ? (
          unchangedKeys.map((key) => (
            <span key={key} className="rounded-full bg-white px-2 py-0.5 font-semibold">
              {FIELD_LABELS[`event.${key}`] ?? FIELD_LABELS[`task.${key}`] ?? key}
            </span>
          ))
        ) : (
          <span>无可展示的不变字段</span>
        )}
      </div>
    </div>
  );
}

function SnapshotPanel({ label, snapshot }: { label: string; snapshot: Record<string, unknown> }) {
  const keys = [
    "start_at",
    "end_at",
    "due_at",
    "reminder_at",
    "location",
    "reminder_minutes",
  ].filter((key) => key in snapshot);
  return (
    <div className="rounded-xl bg-mist-50 p-3">
      <p className="text-xs font-bold text-ink-500">{label}</p>
      <dl className="mt-2 space-y-1.5 text-sm">
        {keys.map((key) => (
          <div key={key} className="flex justify-between gap-3">
            <dt className="text-ink-400">
              {FIELD_LABELS[`event.${key}`] ?? FIELD_LABELS[`task.${key}`] ?? key}
            </dt>
            <dd className="text-right font-semibold text-ink-700">
              {snapshotValue(snapshot, key)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
