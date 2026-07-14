"use client";

import { AlertTriangle, CheckCircle2, GitBranchPlus, RefreshCcw } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";

import { ApiError, api, type NoticeSeries, type NoticeTimeline } from "@/lib/api-client";
import { fromLocalInputValue } from "@/lib/format";
import { useUserSettings } from "@/lib/user-settings";

function optional(value: string) {
  const normalized = value.trim();
  return normalized || null;
}

export function NoticeVersionImport() {
  const userSettings = useUserSettings();
  const [series, setSeries] = useState<NoticeSeries[]>([]);
  const [timeline, setTimeline] = useState<NoticeTimeline | null>(null);
  const [selectedSeriesId, setSelectedSeriesId] = useState("");
  const [seriesLoaded, setSeriesLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [canonicalKey, setCanonicalKey] = useState("");
  const [seriesTitle, setSeriesTitle] = useState("");
  const [department, setDepartment] = useState("");
  const [sourceKey, setSourceKey] = useState("");
  const [versionTitle, setVersionTitle] = useState("");
  const [versionLabel, setVersionLabel] = useState("v1");
  const [revision, setRevision] = useState(1);
  const [content, setContent] = useState("");
  const [publishDate, setPublishDate] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [applicableGroup, setApplicableGroup] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [predecessorId, setPredecessorId] = useState("");
  const [ambiguityConfirmed, setAmbiguityConfirmed] = useState(false);

  const selectedSeries = series.find((item) => item.id === selectedSeriesId) ?? null;
  const expectedRevision = (timeline?.versions.length ?? 0) + 1;
  const currentDocumentId = timeline?.series.current_document_id ?? null;
  const revisionValid = revision === expectedRevision;
  const predecessorValid = revision === 1 ? !predecessorId : predecessorId === currentDocumentId;
  const canImport = Boolean(
    selectedSeries &&
    versionTitle.trim().length >= 2 &&
    versionLabel.trim() &&
    content.trim().length >= 10 &&
    revisionValid &&
    predecessorValid &&
    ambiguityConfirmed,
  );

  const predecessorLabel = useMemo(() => {
    if (revision === 1) return "无前驱（首版）";
    const version = timeline?.versions.find((item) => item.id === predecessorId);
    return version ? `${version.version_label} · 文档 ${version.id}` : "尚未选择前驱";
  }, [predecessorId, revision, timeline]);

  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "通知版本操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const loadSeries = async (preferredId = selectedSeriesId) => {
    const result = await api.radar.series();
    setSeries(result);
    setSeriesLoaded(true);
    if (preferredId && result.some((item) => item.id === preferredId)) {
      await selectSeries(preferredId, result);
    } else if (preferredId) {
      setSelectedSeriesId("");
      setTimeline(null);
      setPredecessorId("");
      setAmbiguityConfirmed(false);
    }
  };

  const selectSeries = async (id: string, available = series) => {
    setSelectedSeriesId(id);
    setTimeline(null);
    setPredecessorId("");
    setAmbiguityConfirmed(false);
    const selected = available.find((item) => item.id === id);
    if (!selected) return;
    const result = await api.radar.timeline(id);
    setTimeline(result);
    const nextRevision = result.versions.length + 1;
    setRevision(nextRevision);
    setVersionLabel(`v${nextRevision}`);
    setVersionTitle(result.versions.at(-1)?.title ?? selected.normalized_title);
  };

  const createSeries = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(async () => {
      const created = await api.radar.createSeries({
        canonical_key: canonicalKey.trim(),
        title: seriesTitle.trim(),
        department: optional(department),
        source_key: optional(sourceKey),
      });
      setCanonicalKey("");
      setSeriesTitle("");
      setDepartment("");
      setSourceKey("");
      setNotice(`已创建通知系列“${created.normalized_title}”，现在可显式导入 v1。`);
      await loadSeries(created.id);
    });
  };

  const importVersion = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canImport || !selectedSeries) return;
    void run(async () => {
      const imported = await api.radar.addVersion(selectedSeries.id, {
        title: versionTitle.trim(),
        content: content.trim(),
        revision_number: revision,
        version_label: versionLabel.trim(),
        supersedes_document_id: revision === 1 ? null : predecessorId,
        department: selectedSeries.department,
        publish_date: optional(publishDate),
        effective_at: fromLocalInputValue(effectiveAt, userSettings.timezone),
        applicable_group: optional(applicableGroup),
        source_url: optional(sourceUrl),
        ingest_source: "manual",
      });
      setNotice(
        `已导入 ${imported.version_label}；系列与前驱均按你的选择写入，没有按标题静默关联。`,
      );
      setContent("");
      setPublishDate("");
      setEffectiveAt("");
      setApplicableGroup("");
      setSourceUrl("");
      setAmbiguityConfirmed(false);
      await loadSeries(selectedSeries.id);
    });
  };

  return (
    <section
      id="notice-version-library"
      className="surface mb-6 overflow-hidden"
      aria-labelledby="notice-version-import-title"
    >
      <div className="border-b border-mist-200 p-5 sm:p-6">
        <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">Version-aware</p>
        <h2 id="notice-version-import-title" className="mt-1 text-xl font-extrabold text-ink-950">
          显式通知系列与版本导入
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-500">
          先创建或选择唯一系列，再指定 v1/v2 修订号与精确前驱。系统不会仅凭相似标题合并通知。
        </p>
      </div>

      <div className="grid gap-6 p-5 lg:grid-cols-2 sm:p-6">
        <form onSubmit={createSeries} aria-labelledby="create-series-title">
          <h3 id="create-series-title" className="font-extrabold text-ink-900">
            1. 创建 NoticeSeries
          </h3>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-bold text-ink-500">
              系列唯一键
              <input
                className="field mt-1"
                value={canonicalKey}
                onChange={(event) => setCanonicalKey(event.target.value)}
                placeholder="exam.ai.2026"
                required
                minLength={2}
                pattern="[\w.:-]+"
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              通知标题
              <input
                className="field mt-1"
                value={seriesTitle}
                onChange={(event) => setSeriesTitle(event.target.value)}
                required
                minLength={2}
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              发布部门（可选）
              <input
                className="field mt-1"
                value={department}
                onChange={(event) => setDepartment(event.target.value)}
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              来源唯一键（可选）
              <input
                className="field mt-1"
                value={sourceKey}
                onChange={(event) => setSourceKey(event.target.value)}
              />
            </label>
          </div>
          <button
            type="submit"
            className="btn-secondary mt-4"
            disabled={busy || canonicalKey.trim().length < 2 || seriesTitle.trim().length < 2}
          >
            <GitBranchPlus size={16} aria-hidden="true" /> 创建明确系列
          </button>
        </form>

        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="font-extrabold text-ink-900">2. 选择系列与精确前驱</h3>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void run(() => loadSeries())}
            >
              <RefreshCcw size={15} aria-hidden="true" />
              {seriesLoaded ? "刷新系列" : "加载系列"}
            </button>
          </div>
          {!seriesLoaded ? (
            <p className="mt-3 text-sm text-ink-500">
              加载后由你明确选择系列，页面不会按标题自动匹配。
            </p>
          ) : series.length === 0 ? (
            <p className="mt-3 text-sm text-ink-500">尚无系列，请先在左侧创建。</p>
          ) : (
            <label className="mt-4 block text-xs font-bold text-ink-500">
              目标系列（必选）
              <select
                className="field mt-1"
                value={selectedSeriesId}
                onChange={(event) => void run(() => selectSeries(event.target.value))}
              >
                <option value="">请选择，不自动推断</option>
                {series.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.normalized_title} · {item.canonical_key} · {item.department ?? "部门未知"}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {selectedSeries && timeline ? (
        <form
          onSubmit={importVersion}
          className="border-t border-mist-200 p-5 sm:p-6"
          aria-labelledby="import-version-title"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 id="import-version-title" className="font-extrabold text-ink-900">
                3. 导入 v1 / v2 原文
              </h3>
              <p className="mt-1 text-sm text-ink-500">
                当前系列：{selectedSeries.normalized_title}（{selectedSeries.canonical_key}）
              </p>
            </div>
            <span className="rounded-full bg-mist-100 px-3 py-1 text-xs font-bold text-ink-600">
              已有 {timeline.versions.length} 个版本
            </span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs font-bold text-ink-500">
              修订号
              <input
                type="number"
                min={1}
                className="field mt-1"
                value={revision}
                onChange={(event) => {
                  setRevision(Number(event.target.value));
                  setPredecessorId("");
                  setAmbiguityConfirmed(false);
                }}
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              版本标签
              <input
                className="field mt-1"
                value={versionLabel}
                onChange={(event) => setVersionLabel(event.target.value)}
                required
              />
            </label>
            <label className="text-xs font-bold text-ink-500 sm:col-span-2">
              本版标题
              <input
                className="field mt-1"
                value={versionTitle}
                onChange={(event) => setVersionTitle(event.target.value)}
                required
                minLength={2}
              />
            </label>
            <label className="text-xs font-bold text-ink-500 sm:col-span-2">
              精确前驱
              <select
                className="field mt-1"
                value={predecessorId}
                disabled={revision === 1}
                onChange={(event) => {
                  setPredecessorId(event.target.value);
                  setAmbiguityConfirmed(false);
                }}
              >
                <option value="">
                  {revision === 1 ? "首版必须无前驱" : "请选择被本版取代的文档"}
                </option>
                {timeline.versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.version_label} · revision {item.revision_number} · {item.id}
                    {item.is_current ? "（当前版）" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs font-bold text-ink-500">
              发布日期（可选）
              <input
                type="date"
                className="field mt-1"
                value={publishDate}
                onChange={(event) => setPublishDate(event.target.value)}
              />
            </label>
            <label className="text-xs font-bold text-ink-500">
              生效时间（可选）
              <input
                type="datetime-local"
                className="field mt-1"
                value={effectiveAt}
                onChange={(event) => setEffectiveAt(event.target.value)}
              />
            </label>
            <label className="text-xs font-bold text-ink-500 sm:col-span-2">
              适用群体（可选）
              <input
                className="field mt-1"
                value={applicableGroup}
                onChange={(event) => setApplicableGroup(event.target.value)}
                placeholder="例如：2024级人工智能专业"
              />
            </label>
            <label className="text-xs font-bold text-ink-500 sm:col-span-2">
              来源 URL（可选）
              <input
                type="url"
                className="field mt-1"
                value={sourceUrl}
                onChange={(event) => setSourceUrl(event.target.value)}
              />
            </label>
            <label className="text-xs font-bold text-ink-500 sm:col-span-2 lg:col-span-4">
              通知完整原文
              <textarea
                className="field mt-1 min-h-40 resize-y"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                required
                minLength={10}
              />
            </label>
          </div>

          {!revisionValid || !predecessorValid ? (
            <div
              role="alert"
              className="mt-4 flex gap-2 rounded-xl bg-gold-100/60 p-3 text-sm text-amber-900"
            >
              <AlertTriangle className="mt-0.5 shrink-0" size={17} aria-hidden="true" />
              <p>
                下一修订号必须是 {expectedRevision}；
                {expectedRevision === 1
                  ? "首版不得指定前驱。"
                  : `前驱必须明确选择当前文档 ${currentDocumentId ?? "（缺失）"}。`}
              </p>
            </div>
          ) : null}

          <label className="mt-4 flex items-start gap-2 rounded-xl border border-mist-200 p-3 text-sm font-semibold text-ink-700">
            <input
              type="checkbox"
              className="mt-1"
              checked={ambiguityConfirmed}
              onChange={(event) => setAmbiguityConfirmed(event.target.checked)}
            />
            <span>
              我已核对同名或相似通知，确认目标系列为“{selectedSeries.normalized_title}”，精确前驱为“
              {predecessorLabel}”。
            </span>
          </label>
          <button type="submit" className="btn-primary mt-4" disabled={busy || !canImport}>
            导入并提取证据 claim
          </button>
        </form>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="mx-5 mb-5 rounded-xl bg-coral-50 p-3 text-sm text-coral-700 sm:mx-6"
        >
          {error}
        </div>
      ) : null}
      {notice ? (
        <div
          role="status"
          aria-live="polite"
          className="mx-5 mb-5 flex gap-2 rounded-xl bg-teal-50 p-3 text-sm font-semibold text-teal-700 sm:mx-6"
        >
          <CheckCircle2 size={17} aria-hidden="true" /> {notice}
        </div>
      ) : null}
    </section>
  );
}
