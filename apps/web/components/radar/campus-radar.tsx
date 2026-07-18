"use client";

import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CalendarSync,
  FilePlus2,
  Radar,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, type RadarCard } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format";

export function CampusRadar() {
  const [items, setItems] = useState<RadarCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.radar.list(4);
      setItems(result.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "校园变化雷达暂时无法加载。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  return (
    <section className="surface mb-6 overflow-hidden" aria-labelledby="campus-radar-title">
      <div className="flex items-center justify-between gap-4 border-b border-mist-100 p-5 sm:p-6">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
            <Radar size={21} aria-hidden="true" />
          </span>
          <div>
            <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">校园情报</p>
            <h2 id="campus-radar-title" className="text-xl font-extrabold text-ink-950">
              与你有关的变化与截止
            </h2>
          </div>
        </div>
        <Link
          href="/notices"
          className="inline-flex min-h-11 items-center rounded-xl px-3 text-sm font-bold text-teal-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        >
          查看全部
        </Link>
      </div>

      <div className="p-4 sm:p-5" aria-live="polite">
        {loading ? <p className="text-sm text-ink-500">正在检查通知版本与个人安排…</p> : null}
        {error ? (
          <div role="status" className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-coral-600">{error}</p>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              重试
            </button>
          </div>
        ) : null}
        {!loading && !error && items.length === 0 ? (
          <p className="text-sm text-ink-500">
            暂无需要处理的版本变化。导入显式关联的新版本后会显示在这里。
          </p>
        ) : null}
        <div className="grid gap-3 lg:grid-cols-2">
          {items.map((item) => {
            const cardType = item.card_type ?? "version_change";
            const canInspectChange =
              (cardType === "version_change" || cardType === "needs_review") &&
              Boolean(item.change_set_id);
            const href = canInspectChange
              ? `/radar/${encodeURIComponent(item.change_set_id!)}`
              : "/notices#notice-version-library";
            const cardLabel =
              cardType === "new_notice"
                ? "与我有关的新通知"
                : cardType === "upcoming_deadline"
                  ? "即将截止"
                  : cardType === "needs_review"
                    ? "需人工确认"
                    : "通知版本变化";
            const Icon =
              cardType === "new_notice"
                ? FilePlus2
                : cardType === "upcoming_deadline"
                  ? CalendarClock
                  : cardType === "needs_review"
                    ? AlertTriangle
                    : CalendarSync;
            return (
              <Link
                key={`${cardType}-${item.change_set_id ?? item.document_id ?? item.series_id}-${item.created_at}`}
                href={href}
                aria-label={`${cardLabel}：${item.title}。${canInspectChange ? "查看版本差异" : "前往通知库查看"}`}
                className="group min-w-0 rounded-2xl border border-mist-100 bg-white p-4 transition-colors hover:border-teal-100 hover:bg-teal-50/30"
              >
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
                    <Icon size={17} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="mb-1 text-[0.68rem] font-bold tracking-wider text-teal-700 uppercase">
                      {cardLabel}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="truncate font-bold text-ink-800">{item.title}</h3>
                      {item.needs_review || item.applicability === "needs_review" ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-gold-100 px-2 py-0.5 text-[0.68rem] font-bold text-amber-800">
                          <AlertTriangle size={11} aria-hidden="true" /> 需审核
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 text-sm leading-6 text-ink-600">{item.message}</p>
                    {cardType === "version_change" ? (
                      <p className="mt-2 text-xs font-semibold text-ink-600">
                        v{item.from_revision} → v{item.to_revision} · {item.change_count}{" "}
                        项结构化变化
                      </p>
                    ) : null}
                    {item.deadline_at ? (
                      <p className="mt-2 text-xs font-semibold text-coral-600">
                        截止：{formatDateTime(item.deadline_at)}
                      </p>
                    ) : null}
                    {item.affected_events > 0 || item.affected_tasks > 0 ? (
                      <p className="mt-1 text-xs font-semibold text-ink-500">
                        影响安排：{item.affected_events} 个日程、{item.affected_tasks} 个待办
                      </p>
                    ) : null}
                    {item.applicability_reason ? (
                      <p className="mt-1 text-xs text-ink-600">
                        适用说明：{item.applicability_reason}
                      </p>
                    ) : null}
                  </div>
                  <ArrowRight
                    className="mt-2 shrink-0 text-ink-300 group-hover:text-teal-600"
                    size={17}
                  />
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}
