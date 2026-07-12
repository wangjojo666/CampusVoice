"use client";

import type {
  DocumentRecord,
  KnowledgeAnswer,
  KnowledgeApplicabilityConflict,
  KnowledgeEvidence,
  KnowledgeVersionConflict,
} from "@campusvoice/shared-types";
import { AlertTriangle, Check, FileSearch, FileText, Search, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { EvidenceCard } from "@/components/notices/evidence-card";
import { UploadForm } from "@/components/notices/upload-form";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Modal } from "@/components/ui/modal";
import { ApiError, api } from "@/lib/api-client";
import { formatDate } from "@/lib/format";
import { useAssistantStore } from "@/stores/assistant-store";

export default function NoticesPage() {
  const router = useRouter();
  const setTranscript = useAssistantStore((state) => state.setTranscript);
  const setSourceDocumentId = useAssistantStore((state) => state.setSourceDocumentId);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [evidence, setEvidence] = useState<KnowledgeEvidence[]>([]);
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [query, setQuery] = useState("");
  const [version, setVersion] = useState("");
  const [applicableGroup, setApplicableGroup] = useState("");
  const [versionConflicts, setVersionConflicts] = useState<KnowledgeVersionConflict[]>([]);
  const [applicabilityConflicts, setApplicabilityConflicts] = useState<
    KnowledgeApplicabilityConflict[]
  >([]);
  const [mode, setMode] = useState<"ask" | "search">("ask");
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.documents.list();
      setDocuments(response.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法加载校园通知文档。");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    setAnswer(null);
    setEvidence([]);
    setVersionConflicts([]);
    setApplicabilityConflicts([]);
    try {
      const filters = {
        version: version.trim() || undefined,
        applicable_group: applicableGroup.trim() || undefined,
      };
      if (mode === "ask") {
        const response = await api.knowledge.ask(query.trim(), filters);
        setAnswer(response);
        setEvidence(response.evidence);
        setVersionConflicts(response.version_conflicts ?? []);
        setApplicabilityConflicts(response.applicability_conflicts ?? []);
      } else {
        const response = await api.knowledge.search(query.trim(), 8, filters);
        setEvidence(response.evidence);
        setVersionConflicts(response.version_conflicts);
        setApplicabilityConflicts(response.applicability_conflicts);
      }
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "检索失败，请重试。");
    } finally {
      setSearching(false);
    }
  };

  const upload = async (file: File, metadata: Parameters<typeof api.documents.upload>[1]) => {
    setBusy(true);
    setError(null);
    try {
      await api.documents.upload(file, metadata);
      setNotice("文档已上传。索引完成后即可检索。 ");
      setUploadOpen(false);
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "文档上传失败。");
    } finally {
      setBusy(false);
    }
  };

  const convert = (item: KnowledgeEvidence, target: "待办" | "日历") => {
    setSourceDocumentId(item.document_id);
    setTranscript(
      `根据校园通知《${item.document_title}》中的这段内容，创建${target}：${item.content}`,
    );
    router.push("/voice");
  };

  const conversionBlocked = versionConflicts.length > 0 || applicabilityConflicts.length > 0;

  return (
    <div>
      <PageHeader
        eyebrow="Campus knowledge"
        title="校园通知"
        description="上传校园文件，基于检索证据搜索或问答。没有充分证据时，系统会明确说明无法确定。"
        actions={
          <button type="button" onClick={() => setUploadOpen(true)} className="btn-primary">
            <Upload size={17} /> 上传文档
          </button>
        }
      />
      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={loading ? undefined : () => void load()} compact />
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          className="mb-5 flex items-center gap-2 rounded-2xl border border-teal-100 bg-teal-50 p-4 text-sm font-semibold text-teal-700"
        >
          <Check size={17} />
          {notice}
        </div>
      ) : null}

      <section className="surface mb-6 overflow-hidden">
        <div className="border-b border-mist-200 p-5 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                Evidence first
              </p>
              <h2 className="mt-1 text-xl font-extrabold text-ink-950">从真实通知中找答案</h2>
            </div>
            <div className="flex rounded-xl bg-mist-100 p-1" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={mode === "ask"}
                onClick={() => setMode("ask")}
                className={`rounded-lg px-3 py-2 text-xs font-bold ${mode === "ask" ? "bg-white text-teal-700 shadow-sm" : "text-ink-500"}`}
              >
                证据问答
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === "search"}
                onClick={() => setMode("search")}
                className={`rounded-lg px-3 py-2 text-xs font-bold ${mode === "search" ? "bg-white text-teal-700 shadow-sm" : "text-ink-500"}`}
              >
                原文检索
              </button>
            </div>
          </div>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
          className="p-5 sm:p-6"
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="relative flex-1">
              <span className="sr-only">
                {mode === "ask" ? "输入校园通知问题" : "输入检索关键词"}
              </span>
              <Search
                className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-ink-300"
                size={18}
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="field !pl-10"
                placeholder={
                  mode === "ask" ? "例如：奖学金申请什么时候截止？" : "输入课程、考试或报名关键词"
                }
              />
            </label>
            <button
              type="submit"
              disabled={searching || !query.trim()}
              className="btn-primary shrink-0"
            >
              {searching ? (
                <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              ) : (
                <FileSearch size={17} />
              )}
              {searching ? "正在检索" : mode === "ask" ? "基于证据回答" : "搜索原文"}
            </button>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-bold text-ink-500">
              指定版本（冲突时必填）
              <input
                value={version}
                onChange={(event) => setVersion(event.target.value)}
                className="field mt-1"
                placeholder="例如：v2"
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              指定适用群体（冲突时必填）
              <input
                value={applicableGroup}
                onChange={(event) => setApplicableGroup(event.target.value)}
                className="field mt-1"
                placeholder="例如：2024级人工智能专业"
              />
            </label>
          </div>
        </form>
      </section>

      {answer || evidence.length > 0 || searching ? (
        <section className="mb-6">
          <h2 className="mb-3 text-lg font-extrabold text-ink-950">检索结果</h2>
          {searching ? (
            <LoadingState rows={3} />
          ) : (
            <>
              {answer ? (
                <div className={`surface mb-4 p-5 ${answer.sufficient ? "" : "!border-gold-100"}`}>
                  <div className="flex items-start gap-3">
                    {answer.sufficient ? (
                      <Check className="mt-0.5 shrink-0 text-teal-600" size={20} />
                    ) : (
                      <AlertTriangle className="mt-0.5 shrink-0 text-amber-700" size={20} />
                    )}
                    <div>
                      <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">
                        回答
                      </p>
                      <p className="mt-2 leading-7 text-ink-800">
                        {answer.sufficient && answer.answer
                          ? answer.answer
                          : (answer.message ?? "现有检索证据不足，无法确定答案。")}
                      </p>
                    </div>
                  </div>
                </div>
              ) : null}
              {conversionBlocked ? (
                <div
                  role="alert"
                  className="mb-4 rounded-xl bg-gold-100/55 p-3 text-sm text-amber-800"
                >
                  {versionConflicts.length ? (
                    <p>
                      <strong>发现多个版本：</strong>
                      {versionConflicts
                        .map((item) => `${item.document_title}（${item.versions.join("、")}）`)
                        .join("；")}
                    </p>
                  ) : null}
                  {applicabilityConflicts.length ? (
                    <p className={versionConflicts.length ? "mt-1" : undefined}>
                      <strong>发现多个适用群体：</strong>
                      {applicabilityConflicts
                        .map(
                          (item) =>
                            `${item.document_title}（${item.applicable_groups.join("、")}）`,
                        )
                        .join("；")}
                    </p>
                  ) : null}
                  <p className="mt-1">请在上方指定条件并重新检索，消歧前不能转为待办或日历。</p>
                </div>
              ) : null}
              <div className="space-y-3">
                {evidence.map((item) => (
                  <EvidenceCard
                    key={`${item.document_id}-${item.chunk_id}`}
                    evidence={item}
                    onCreateTask={() => convert(item, "待办")}
                    onCreateEvent={() => convert(item, "日历")}
                    conversionDisabled={conversionBlocked}
                  />
                ))}
              </div>
              {!answer && evidence.length === 0 ? (
                <EmptyState
                  title="没有找到相关原文"
                  description="请尝试更具体的课程、部门或日期关键词。"
                />
              ) : null}
            </>
          )}
        </section>
      ) : null}

      <section className="surface p-5 sm:p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-bold tracking-wider text-ink-400 uppercase">Documents</p>
            <h2 className="mt-1 text-lg font-extrabold text-ink-950">已导入文档</h2>
          </div>
          <span className="rounded-full bg-mist-100 px-3 py-1 text-xs font-bold text-ink-500">
            {documents.length} 份
          </span>
        </div>
        {loading ? (
          <LoadingState rows={3} />
        ) : documents.length === 0 ? (
          <EmptyState
            title="还没有校园通知"
            description="上传 PDF、DOCX、TXT 或 Markdown，元数据和原文引用会随检索结果返回。"
            action={
              <button type="button" onClick={() => setUploadOpen(true)} className="btn-primary">
                <Upload size={16} /> 上传第一份文档
              </button>
            }
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {documents.map((document) => (
              <article key={document.id} className="rounded-2xl border border-mist-100 p-4">
                <div className="flex items-start gap-3">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
                    <FileText size={17} />
                  </span>
                  <div className="min-w-0">
                    <h3 className="line-clamp-2 font-bold leading-5 text-ink-800">
                      {document.title}
                    </h3>
                    <p className="mt-1 text-xs text-ink-400">
                      {document.department ?? "部门未知"} · {formatDate(document.publish_date)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className="rounded-full bg-mist-100 px-2 py-0.5 text-[0.65rem] font-bold uppercase text-ink-500">
                        {document.file_type}
                      </span>
                      {document.version ? (
                        <span className="rounded-full bg-mist-100 px-2 py-0.5 text-[0.65rem] font-bold text-ink-500">
                          {document.version}
                        </span>
                      ) : null}
                      {document.status ? (
                        <span
                          className={`rounded-full px-2 py-0.5 text-[0.65rem] font-bold ${document.status === "ready" ? "bg-teal-50 text-teal-700" : document.status === "failed" ? "bg-coral-50 text-coral-600" : "bg-gold-100/60 text-amber-700"}`}
                        >
                          {document.status === "ready"
                            ? "可检索"
                            : document.status === "failed"
                              ? "解析失败"
                              : "处理中"}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <Modal
        open={uploadOpen}
        title="上传校园通知"
        description="只上传公开或合成的校园资料，不要包含真实学生隐私。"
        onClose={() => !busy && setUploadOpen(false)}
        wide
      >
        <UploadForm busy={busy} onSubmit={upload} onCancel={() => setUploadOpen(false)} />
      </Modal>
    </div>
  );
}
