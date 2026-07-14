"use client";

import type { CorrectionResult } from "@campusvoice/shared-types";
import { ArrowRight, CheckCircle2, HelpCircle } from "lucide-react";

export function CorrectionDiff({
  correction,
  onChoose,
}: Readonly<{
  correction: CorrectionResult;
  onChoose?: (changeIndex: number, value: string) => void;
}>) {
  if (correction.changes.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-teal-100 bg-teal-50 p-4 text-sm text-teal-700">
        <CheckCircle2 size={18} />
        校园术语检查完成，未发现需要修改的内容。
      </div>
    );
  }

  return (
    <section className="surface p-5" aria-labelledby="correction-title">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">术语纠错</p>
          <h2 id="correction-title" className="mt-1 text-lg font-extrabold text-ink-950">
            请核对识别修正
          </h2>
        </div>
        <span className="rounded-full bg-mist-100 px-3 py-1 text-xs font-bold text-ink-500">
          {correction.changes.length} 处变化
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
        <div className="rounded-2xl bg-coral-50 p-4">
          <p className="mb-1.5 text-xs font-bold text-coral-600">原始转写</p>
          <p className="leading-7 text-ink-700">{correction.original_text}</p>
        </div>
        <ArrowRight className="mx-auto rotate-90 text-ink-300 sm:rotate-0" size={20} />
        <div className="rounded-2xl bg-teal-50 p-4">
          <p className="mb-1.5 text-xs font-bold text-teal-700">纠正结果</p>
          <p className="leading-7 text-ink-900">{correction.corrected_text}</p>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {correction.changes.map((change, index) => (
          <div
            key={`${change.start}-${change.end}-${index}`}
            className="rounded-xl border border-mist-200 p-3.5"
          >
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <del className="rounded bg-coral-50 px-2 py-1 text-coral-600">{change.original}</del>
              <ArrowRight size={14} className="text-ink-300" />
              <ins className="rounded bg-teal-50 px-2 py-1 font-bold text-teal-700 no-underline">
                {change.corrected}
              </ins>
              <span className="ml-auto text-xs font-semibold text-ink-400">
                置信度 {Math.round(change.confidence * 100)}%
              </span>
            </div>
            <p className="mt-2 text-xs leading-5 text-ink-500">{change.reason}</p>
            {change.requires_confirmation && change.candidates.length > 0 ? (
              <div
                className="mt-3 flex flex-wrap items-center gap-2"
                aria-label={`“${change.original}”的候选词`}
              >
                <span className="inline-flex items-center gap-1 text-xs font-bold text-gold-500">
                  <HelpCircle size={14} /> 请选择
                </span>
                {change.candidates.map((candidate) => (
                  <button
                    key={candidate}
                    type="button"
                    onClick={() => onChoose?.(index, candidate)}
                    className="btn-secondary !min-h-8 !px-2.5 !py-1 text-xs"
                  >
                    {candidate}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
