"use client";

import type {
  CorrectionResult,
  Hotword,
  UserSettings,
  UserSettingsUpdate,
} from "@campusvoice/shared-types";
import {
  BookOpenCheck,
  Check,
  ChevronDown,
  Cpu,
  FlaskConical,
  Plus,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Tags,
  Trash2,
  Volume2,
  WandSparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Modal } from "@/components/ui/modal";
import { ApiError, api } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";

const blankSettings: UserSettings = {
  ...DEFAULT_USER_SETTINGS,
  major: "",
  grade: "",
};

const categoryLabel = {
  course: "课程",
  course_code: "课程编号",
  teacher: "教师",
  ai_term: "专业术语",
  custom: "自定义",
  document: "文档",
} as const;

export default function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings>(blankSettings);
  const [hotwords, setHotwords] = useState<Hotword[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newWord, setNewWord] = useState("");
  const [newCategory, setNewCategory] = useState<Hotword["category"]>("custom");
  const [courseInput, setCourseInput] = useState("");
  const [teacherInput, setTeacherInput] = useState("");
  const [previewText, setPreviewText] = useState("周五上午九点有机器学西考试，提前一天提醒我。");
  const [previewResult, setPreviewResult] = useState<CorrectionResult | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [pendingHotwordRemoval, setPendingHotwordRemoval] = useState<{
    word: Hotword;
    challenge: string;
    expiresAt: string;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [settingsResult, hotwordsResult] = await Promise.allSettled([
      api.settings.get(),
      api.hotwords.list(),
    ]);
    const failures: string[] = [];
    if (settingsResult.status === "fulfilled") {
      setSettings(settingsResult.value);
      setCurrentUserSettings(settingsResult.value);
    } else
      failures.push(
        settingsResult.reason instanceof ApiError
          ? settingsResult.reason.userMessage
          : "设置加载失败",
      );
    if (hotwordsResult.status === "fulfilled") setHotwords(hotwordsResult.value.items);
    else
      failures.push(
        hotwordsResult.reason instanceof ApiError
          ? hotwordsResult.reason.userMessage
          : "热词加载失败",
      );
    setError(failures.length ? [...new Set(failures)].join(" ") : null);
    setLoading(false);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const saveSettings = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const update: UserSettingsUpdate = {
        major: settings.major,
        grade: settings.grade,
        current_courses: settings.current_courses,
        teacher_names: settings.teacher_names,
        default_reminder_minutes: settings.default_reminder_minutes,
        timezone: settings.timezone,
      };
      const saved = await api.settings.update(update);
      setSettings(saved);
      setCurrentUserSettings(saved);
      setNotice("设置已保存，后续日期解析、显示和新建日程会使用最新配置。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "设置保存失败。");
    } finally {
      setBusy(false);
    }
  };

  const addHotword = async () => {
    if (!newWord.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.hotwords.create({ value: newWord.trim(), category: newCategory });
      setHotwords((current) => [created, ...current]);
      setNewWord("");
      setNotice("热词已添加。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "热词添加失败。");
    } finally {
      setBusy(false);
    }
  };

  const beginRemoveHotword = async (word: Hotword) => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.hotwords.beginRemove(word.id);
      setPendingHotwordRemoval({
        word,
        challenge: next.challenge,
        expiresAt: next.expires_at,
      });
      setNotice("第一次删除确认已记录。请在弹窗中完成独立的第二次确认。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "无法开始热词删除确认。");
    } finally {
      setBusy(false);
    }
  };

  const finishRemoveHotword = async () => {
    if (!pendingHotwordRemoval) return;
    setBusy(true);
    setError(null);
    try {
      await api.hotwords.finishRemove(
        pendingHotwordRemoval.word.id,
        pendingHotwordRemoval.challenge,
      );
      setHotwords((current) => current.filter((item) => item.id !== pendingHotwordRemoval.word.id));
      setPendingHotwordRemoval(null);
      setNotice("热词已删除。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "热词删除失败。");
    } finally {
      setBusy(false);
    }
  };

  const byCategory = useMemo(
    () =>
      Object.entries(
        hotwords.reduce<Record<string, Hotword[]>>((groups, word) => {
          (groups[word.category] ??= []).push(word);
          return groups;
        }, {}),
      ),
    [hotwords],
  );
  const contextTerms = useMemo(() => {
    const candidates = [
      ...settings.current_courses.flatMap((course) =>
        [
          course.name ? { value: course.name, label: "课程" } : null,
          course.code ? { value: course.code, label: "课程编号" } : null,
          course.teacher ? { value: course.teacher, label: "教师" } : null,
        ].filter((item): item is { value: string; label: string } => Boolean(item)),
      ),
      ...settings.teacher_names.map((value) => ({ value, label: "教师" })),
      ...hotwords.map((word) => ({
        value: word.value,
        label: categoryLabel[word.category] ?? word.category,
      })),
    ];
    return candidates.filter(
      (item, index) =>
        candidates.findIndex((candidate) => candidate.value === item.value) === index,
    );
  }, [hotwords, settings.current_courses, settings.teacher_names]);
  const matchedContext = useMemo(() => {
    if (!previewResult) return [];
    return contextTerms.filter(
      (item) =>
        previewResult.corrected_text.includes(item.value) ||
        previewResult.changes.some((change) => change.corrected === item.value),
    );
  }, [contextTerms, previewResult]);

  const addCourse = () => {
    const value = courseInput.trim();
    if (!value || settings.current_courses.some((course) => course.name === value)) return;
    setSettings((current) => ({
      ...current,
      current_courses: [...current.current_courses, { name: value }],
    }));
    setCourseInput("");
  };
  const addTeacher = () => {
    const value = teacherInput.trim();
    if (!value || settings.teacher_names.includes(value)) return;
    setSettings((current) => ({
      ...current,
      teacher_names: [...current.teacher_names, value],
    }));
    setTeacherInput("");
  };
  const runPreview = async () => {
    if (!previewText.trim()) return;
    setPreviewBusy(true);
    setPreviewResult(null);
    setError(null);
    try {
      setPreviewResult(await api.correction.preview(previewText.trim(), 1));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.userMessage : "纠错预览失败。");
    } finally {
      setPreviewBusy(false);
    }
  };
  const applySyntheticPreset = () => {
    setSettings((current) => ({
      ...current,
      major: "人工智能",
      grade: "2024 级",
      current_courses: [
        { code: "AI2401", name: "机器学习", teacher: "林知远" },
        { code: "AI2402", name: "自然语言处理", teacher: "周明澜" },
      ],
      teacher_names: ["林知远", "周明澜"],
      default_reminder_minutes: 1440,
      timezone: "Asia/Shanghai",
    }));
    setPreviewText("周五上午九点有机器学西考试，提前一天提醒我。");
    setPreviewResult(null);
    setNotice("已载入合成校园上下文预设；点击“保存设置”后才会写入。所有名称均为演示数据。");
  };

  return (
    <div>
      <PageHeader
        eyebrow="Recognition enhancement"
        title="识别增强"
        description="用合成校园上下文和个人热词提升候选排序与术语纠错。这里不会切换或下载 ASR 模型，关键字段的低置信度修正仍会要求确认。"
        actions={
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void saveSettings()}
            className="btn-primary"
          >
            <Save size={17} />
            {busy ? "正在保存" : "保存设置"}
          </button>
        }
      />
      {error ? (
        <div className="mb-5">
          <ErrorState message={error} onRetry={() => void load()} compact />
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
      {loading ? (
        <LoadingState rows={6} />
      ) : (
        <>
          <section className="surface mb-6 overflow-hidden" aria-labelledby="settings-impact-title">
            <div className="border-b border-mist-100 p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
                    <WandSparkles size={21} />
                  </span>
                  <div>
                    <p className="text-xs font-bold tracking-wider text-teal-600 uppercase">
                      Effect preview
                    </p>
                    <h2
                      id="settings-impact-title"
                      className="mt-1 text-xl font-extrabold text-ink-950"
                    >
                      这些设置会影响什么
                    </h2>
                    <p className="mt-1 text-sm leading-6 text-ink-500">
                      热词进入真实纠错请求；课程、教师与专业背景参与候选排序；时区与默认提醒进入后续结构化执行。
                    </p>
                  </div>
                </div>
                <button type="button" className="btn-secondary" onClick={applySyntheticPreset}>
                  <FlaskConical size={16} /> 应用合成校园预设
                </button>
              </div>
              <div className="mt-5 grid gap-3 md:grid-cols-3">
                {[
                  {
                    title: "校园术语纠错",
                    description: "课程、教师和专业词用于修正真实转写候选",
                    Icon: Tags,
                  },
                  {
                    title: "结构化理解",
                    description: "专业、年级与课程帮助判断指令上下文",
                    Icon: BookOpenCheck,
                  },
                  {
                    title: "日程执行",
                    description: "时区与默认提醒进入待确认的写入字段",
                    Icon: SlidersHorizontal,
                  },
                ].map(({ title, description, Icon }) => (
                  <div key={title} className="rounded-2xl border border-mist-100 bg-mist-50/60 p-4">
                    <Icon className="text-teal-600" size={18} />
                    <p className="mt-2 text-sm font-extrabold text-ink-800">{title}</p>
                    <p className="mt-1 text-xs leading-5 text-ink-500">{description}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="grid gap-5 p-5 sm:p-6 lg:grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)]">
              <div>
                <label htmlFor="correction-preview" className="text-sm font-extrabold text-ink-800">
                  真实纠错效果预览
                </label>
                <p className="mt-1 text-xs leading-5 text-ink-400">
                  点击后会调用当前 `/api/correction/preview`，不会硬编码成功结果。
                </p>
                <textarea
                  id="correction-preview"
                  rows={3}
                  value={previewText}
                  onChange={(event) => {
                    setPreviewText(event.target.value);
                    setPreviewResult(null);
                  }}
                  className="field mt-3 resize-y"
                />
                <button
                  type="button"
                  disabled={previewBusy || !previewText.trim()}
                  onClick={() => void runPreview()}
                  className="btn-primary mt-3"
                >
                  <Sparkles size={16} /> {previewBusy ? "正在调用纠错服务" : "运行真实预览"}
                </button>
              </div>
              <div
                className="rounded-2xl border border-mist-100 bg-mist-50/60 p-4"
                aria-live="polite"
              >
                {previewResult ? (
                  <div>
                    <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
                      <div className="rounded-xl bg-coral-50 p-3">
                        <p className="text-[0.68rem] font-bold text-coral-600">原始候选</p>
                        <p className="mt-1 text-sm leading-6 text-ink-700">
                          {previewResult.original_text}
                        </p>
                      </div>
                      <span className="text-center font-bold text-ink-300">→</span>
                      <div className="rounded-xl bg-teal-50 p-3">
                        <p className="text-[0.68rem] font-bold text-teal-700">校园术语纠错</p>
                        <p className="mt-1 text-sm leading-6 text-ink-900">
                          {previewResult.corrected_text}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {matchedContext.length > 0 ? (
                        matchedContext.map((item) => (
                          <span
                            key={`${item.label}-${item.value}`}
                            className="rounded-full bg-white px-2.5 py-1 text-xs font-bold text-teal-700"
                          >
                            命中{item.label}：{item.value}
                          </span>
                        ))
                      ) : (
                        <span className="text-xs text-ink-400">未命中当前校园上下文词条</span>
                      )}
                    </div>
                    {previewResult.changes.length > 0 ? (
                      <ul className="mt-3 space-y-1 text-xs leading-5 text-ink-500">
                        {previewResult.changes.map((change, index) => (
                          <li key={`${change.start}-${change.end}-${index}`}>
                            {change.original} → {change.corrected}：{change.reason}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : (
                  <div className="flex min-h-36 flex-col items-center justify-center text-center">
                    <Sparkles className="text-ink-300" size={22} />
                    <p className="mt-2 text-sm font-bold text-ink-500">等待真实预览结果</p>
                    <p className="mt-1 text-xs text-ink-400">未调用 API 前不会展示纠错成功。</p>
                  </div>
                )}
              </div>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(340px,.78fr)]">
            <div className="space-y-6">
              <section className="surface p-5 sm:p-6">
                <div className="mb-5 flex items-center gap-3">
                  <span className="flex size-10 items-center justify-center rounded-2xl bg-teal-50 text-teal-700">
                    <SlidersHorizontal size={20} />
                  </span>
                  <div>
                    <h2 className="font-extrabold text-ink-950">学习背景</h2>
                    <p className="text-xs text-ink-400">用于候选词排序与校园上下文</p>
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label>
                    <span className="mb-1.5 block text-sm font-bold text-ink-700">专业</span>
                    <input
                      value={settings.major ?? ""}
                      onChange={(input) => setSettings({ ...settings, major: input.target.value })}
                      className="field"
                      placeholder="例如：人工智能"
                    />
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-bold text-ink-700">年级</span>
                    <input
                      value={settings.grade ?? ""}
                      onChange={(input) => setSettings({ ...settings, grade: input.target.value })}
                      className="field"
                      placeholder="例如：2024 级"
                    />
                  </label>
                </div>
                <div className="mt-4">
                  <label
                    htmlFor="course-tag-input"
                    className="mb-1.5 block text-sm font-bold text-ink-700"
                  >
                    当前课程
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="course-tag-input"
                      value={courseInput}
                      onChange={(event) => setCourseInput(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          addCourse();
                        }
                      }}
                      className="field"
                      placeholder="输入课程后按回车"
                    />
                    <button type="button" className="btn-secondary shrink-0" onClick={addCourse}>
                      <Plus size={16} /> 添加
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {settings.current_courses.map((course, index) => {
                      const label = course.name ?? course.code ?? course.id ?? `课程 ${index + 1}`;
                      return (
                        <span
                          key={`${label}-${index}`}
                          className="inline-flex items-center gap-1 rounded-full border border-teal-100 bg-teal-50 py-1 pr-1 pl-2.5 text-xs font-bold text-teal-700"
                        >
                          {label}
                          <button
                            type="button"
                            aria-label={`移除课程${label}`}
                            onClick={() =>
                              setSettings((current) => ({
                                ...current,
                                current_courses: current.current_courses.filter(
                                  (_, courseIndex) => courseIndex !== index,
                                ),
                              }))
                            }
                            className="flex size-6 items-center justify-center rounded-full hover:bg-white"
                          >
                            ×
                          </button>
                        </span>
                      );
                    })}
                  </div>
                </div>
                <div className="mt-4">
                  <label
                    htmlFor="teacher-tag-input"
                    className="mb-1.5 block text-sm font-bold text-ink-700"
                  >
                    教师姓名
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="teacher-tag-input"
                      value={teacherInput}
                      onChange={(event) => setTeacherInput(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          addTeacher();
                        }
                      }}
                      className="field"
                      placeholder="输入教师后按回车"
                    />
                    <button type="button" className="btn-secondary shrink-0" onClick={addTeacher}>
                      <Plus size={16} /> 添加
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {settings.teacher_names.map((teacher) => (
                      <span
                        key={teacher}
                        className="inline-flex items-center gap-1 rounded-full border border-gold-100 bg-gold-100/50 py-1 pr-1 pl-2.5 text-xs font-bold text-amber-800"
                      >
                        {teacher}
                        <button
                          type="button"
                          aria-label={`移除教师${teacher}`}
                          onClick={() =>
                            setSettings((current) => ({
                              ...current,
                              teacher_names: current.teacher_names.filter(
                                (item) => item !== teacher,
                              ),
                            }))
                          }
                          className="flex size-6 items-center justify-center rounded-full hover:bg-white"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
                <p className="mt-4 rounded-xl bg-mist-50 p-3 text-xs leading-5 text-ink-500">
                  专业术语请在右侧“自定义热词”中选择“专业术语”分类维护，它们会进入同一套可审计热词记录。
                </p>
              </section>

              <section className="surface p-5 sm:p-6">
                <div className="mb-5 flex items-center gap-3">
                  <span className="flex size-10 items-center justify-center rounded-2xl bg-mist-100 text-ink-600">
                    <SlidersHorizontal size={20} />
                  </span>
                  <div>
                    <h2 className="font-extrabold text-ink-950">日程默认值</h2>
                    <p className="text-xs text-ink-400">进入待确认卡片，不会静默写入</p>
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label>
                    <span className="mb-1.5 block text-sm font-bold text-ink-700">时区</span>
                    <select
                      value={settings.timezone}
                      onChange={(input) =>
                        setSettings({ ...settings, timezone: input.target.value })
                      }
                      className="field"
                    >
                      <option value="Asia/Shanghai">Asia/Shanghai (UTC+8)</option>
                      <option value="UTC">UTC</option>
                    </select>
                  </label>
                  <label>
                    <span className="mb-1.5 block text-sm font-bold text-ink-700">默认提醒</span>
                    <select
                      value={settings.default_reminder_minutes}
                      onChange={(input) =>
                        setSettings({
                          ...settings,
                          default_reminder_minutes: Number(input.target.value),
                        })
                      }
                      className="field"
                    >
                      <option value={10}>提前 10 分钟</option>
                      <option value={30}>提前 30 分钟</option>
                      <option value={60}>提前 1 小时</option>
                      <option value={1440}>提前 1 天</option>
                    </select>
                  </label>
                </div>
                <details className="group mt-5 rounded-2xl border border-mist-100 bg-mist-50/60 p-4">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                    <span className="flex items-center gap-2 text-sm font-extrabold text-ink-800">
                      <Cpu size={17} /> 技术状态
                    </span>
                    <ChevronDown
                      className="text-ink-400 transition-transform group-open:rotate-180"
                      size={17}
                    />
                  </summary>
                  <div className="mt-4 border-t border-mist-200 pt-4">
                    {settings.asr_provider === "disabled" ? (
                      <div className="rounded-xl border border-gold-100 bg-gold-100/55 p-3 text-sm leading-6 text-amber-800">
                        <strong>模型未配置。</strong> 当前服务端 ASR provider 为
                        disabled，因此录音不会产生伪造转写。若本机已具备 FunASR
                        依赖与模型缓存，请设置
                        <code className="mx-1 rounded bg-white px-1">
                          CAMPUSVOICE_ASR_PROVIDER=funasr
                        </code>
                        并重启 API；首次启用前需人工确认不会触发模型下载。
                      </div>
                    ) : (
                      <div className="rounded-xl border border-teal-100 bg-teal-50 p-3 text-sm text-teal-700">
                        服务端已配置 {settings.asr_provider}
                        。页面只展示配置状态，是否真正可运行仍以实时连接和模型预热结果为准。
                      </div>
                    )}
                    <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
                      <div>
                        <dt className="font-bold text-ink-400">识别提供方</dt>
                        <dd className="mt-1 font-semibold text-ink-700">{settings.asr_provider}</dd>
                      </div>
                      <div>
                        <dt className="font-bold text-ink-400">模型配置</dt>
                        <dd className="mt-1 font-semibold text-ink-700">
                          {settings.asr_provider === "disabled" ? "未加载" : settings.asr_model}
                        </dd>
                      </div>
                      <div>
                        <dt className="font-bold text-ink-400">运行设备</dt>
                        <dd className="mt-1 font-semibold text-ink-700">
                          {settings.asr_provider === "disabled" ? "未启用" : settings.asr_device}
                        </dd>
                      </div>
                    </dl>
                  </div>
                </details>
              </section>
            </div>

            <aside className="space-y-6">
              <section className="surface p-5 sm:p-6">
                <div className="mb-4 flex items-center gap-3">
                  <span className="flex size-10 items-center justify-center rounded-2xl bg-gold-100/70 text-amber-700">
                    <Volume2 size={20} />
                  </span>
                  <div>
                    <h2 className="font-extrabold text-ink-950">校园术语标签</h2>
                    <p className="text-xs text-ink-400">按课程、教师和专业术语分类管理</p>
                  </div>
                </div>
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void addHotword();
                  }}
                  className="space-y-2"
                >
                  <input
                    value={newWord}
                    onChange={(input) => setNewWord(input.target.value)}
                    className="field"
                    placeholder="输入热词"
                    aria-label="新热词"
                  />
                  <div className="flex gap-2">
                    <select
                      value={newCategory}
                      onChange={(input) =>
                        setNewCategory(input.target.value as Hotword["category"])
                      }
                      className="field"
                    >
                      <option value="custom">自定义</option>
                      <option value="course">课程</option>
                      <option value="course_code">课程编号</option>
                      <option value="teacher">教师</option>
                      <option value="ai_term">专业术语</option>
                    </select>
                    <button
                      type="submit"
                      disabled={busy || !newWord.trim()}
                      className="btn-primary shrink-0 !px-3"
                    >
                      <Plus size={17} />
                      <span className="sr-only">添加热词</span>
                    </button>
                  </div>
                </form>
                <div className="mt-5 space-y-4">
                  {hotwords.length === 0 ? (
                    <EmptyState title="还没有热词" description="添加后会同步到识别服务。" />
                  ) : (
                    byCategory.map(([category, words]) => (
                      <div key={category}>
                        <p className="mb-2 text-xs font-bold text-ink-400">
                          {categoryLabel[category as keyof typeof categoryLabel] ?? category}
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {words?.map((word) => (
                            <span
                              key={word.id}
                              className="inline-flex items-center gap-1 rounded-full border border-mist-200 bg-mist-50 py-1 pr-1 pl-2.5 text-xs font-bold text-ink-600"
                            >
                              {word.value}
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void beginRemoveHotword(word)}
                                className="flex size-6 items-center justify-center rounded-full text-ink-300 hover:bg-coral-50 hover:text-coral-600"
                                aria-label={`删除热词${word.value}`}
                              >
                                <Trash2 size={12} />
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </section>
              <section className="rounded-3xl border border-teal-100 bg-teal-50/70 p-5">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 shrink-0 text-teal-700" size={21} />
                  <div>
                    <h2 className="font-extrabold text-teal-700">隐私边界</h2>
                    <ul className="mt-2 space-y-2 text-sm leading-5 text-ink-600">
                      <li>默认不持久化完整录音</li>
                      <li>日志不记录密钥和完整音频</li>
                      <li>日期、课程和删除目标不会被静默纠正</li>
                      <li>仅使用合成或明确授权的数据</li>
                    </ul>
                  </div>
                </div>
              </section>
            </aside>
          </div>
        </>
      )}
      <Modal
        open={Boolean(pendingHotwordRemoval)}
        title="第二次确认：删除热词"
        description="第一次确认已经由服务端记录。只有本次独立点击后，热词才会被删除。"
        onClose={() => !busy && setPendingHotwordRemoval(null)}
      >
        {pendingHotwordRemoval ? (
          <div>
            <div className="rounded-2xl border border-coral-100 bg-coral-50 p-4 text-sm text-coral-600">
              即将删除热词：<strong>{pendingHotwordRemoval.word.value}</strong>
            </div>
            <p className="mt-3 text-xs text-ink-400">
              第二阶段确认有效期至
              {formatDateTime(pendingHotwordRemoval.expiresAt, { timeZone: settings.timezone })}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => setPendingHotwordRemoval(null)}
                className="btn-secondary"
              >
                取消
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void finishRemoveHotword()}
                className="btn-danger"
              >
                <Trash2 size={16} /> {busy ? "正在删除并验证" : "第二次确认并删除"}
              </button>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
