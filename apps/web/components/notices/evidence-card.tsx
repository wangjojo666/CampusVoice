import type { KnowledgeEvidence } from "@campusvoice/shared-types";
import { CalendarPlus, FileText, ListPlus, Quote } from "lucide-react";

export function EvidenceCard({
  evidence,
  onCreateTask,
  onCreateEvent,
  conversionDisabled = false,
}: Readonly<{
  evidence: KnowledgeEvidence;
  onCreateTask: () => void;
  onCreateEvent: () => void;
  conversionDisabled?: boolean;
}>) {
  return (
    <article className="rounded-2xl border border-mist-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs font-bold text-teal-700">
            <FileText size={14} /> {evidence.document_title}
          </p>
          <p className="mt-1 text-xs text-ink-400">
            {evidence.publish_date ?? "发布日期未知"} ·{" "}
            {evidence.page === null ? "无天然页码" : `第 ${evidence.page} 页`}
            {evidence.version ? ` · ${evidence.version}` : ""}
            {evidence.applicable_group ? ` · ${evidence.applicable_group}` : ""}
          </p>
        </div>
        <span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-bold text-teal-700">
          相似度 {Math.round(evidence.similarity * 100)}%
        </span>
      </div>
      <blockquote className="mt-3 flex gap-2 rounded-xl bg-mist-50 p-3 text-sm leading-6 text-ink-700">
        <Quote className="mt-1 shrink-0 text-ink-300" size={15} />
        <span>{evidence.content}</span>
      </blockquote>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onCreateTask}
          disabled={conversionDisabled}
          title={conversionDisabled ? "请先指定通知版本和适用群体" : undefined}
          className="btn-secondary !min-h-9 !px-3 !py-1.5 text-xs"
        >
          <ListPlus size={15} /> 转为待办草稿
        </button>
        <button
          type="button"
          onClick={onCreateEvent}
          disabled={conversionDisabled}
          title={conversionDisabled ? "请先指定通知版本和适用群体" : undefined}
          className="btn-secondary !min-h-9 !px-3 !py-1.5 text-xs"
        >
          <CalendarPlus size={15} /> 转为日程草稿
        </button>
      </div>
    </article>
  );
}
