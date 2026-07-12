"use client";

import type { Hotword, UserSettings } from "@campusvoice/shared-types";
import {
  Check,
  Cpu,
  Plus,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiError, api } from "@/lib/api-client";

const blankSettings: UserSettings = {
  major: "",
  grade: "",
  current_courses: [],
  teacher_names: [],
  default_reminder_minutes: 30,
  timezone: "Asia/Shanghai",
  asr_provider: "funasr",
  asr_model: "paraformer-zh-streaming",
  asr_device: "cpu",
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

  const load = useCallback(async () => {
    setLoading(true);
    const [settingsResult, hotwordsResult] = await Promise.allSettled([
      api.settings.get(),
      api.hotwords.list(),
    ]);
    const failures: string[] = [];
    if (settingsResult.status === "fulfilled") setSettings(settingsResult.value);
    else
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
      setSettings(await api.settings.update(settings));
      setNotice("设置已保存，后续识别会使用最新配置。");
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

  const removeHotword = async (word: Hotword) => {
    if (!window.confirm(`删除热词“${word.value}”会改变识别词表。请再次确认是否删除。`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.hotwords.remove(word.id);
      setHotwords((current) => current.filter((item) => item.id !== word.id));
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
  const splitCsv = (value: string) =>
    value
      .split(/[，,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  const updateTeachers = (value: string) =>
    setSettings((current) => ({
      ...current,
      teacher_names: splitCsv(value),
    }));
  const updateCourses = (value: string) =>
    setSettings((current) => ({
      ...current,
      current_courses: splitCsv(value).map((name) => ({ name })),
    }));

  return (
    <div>
      <PageHeader
        eyebrow="Personal context"
        title="热词与设置"
        description="课程、教师和专业术语只用于提升识别与理解，不保存真实学生隐私。关键字段的低置信度纠错仍会要求确认。"
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
              <label className="mt-4 block">
                <span className="mb-1.5 block text-sm font-bold text-ink-700">当前课程</span>
                <textarea
                  rows={3}
                  value={settings.current_courses
                    .map((course) => course.name ?? course.code ?? course.id ?? "")
                    .filter(Boolean)
                    .join("，")}
                  onChange={(input) => updateCourses(input.target.value)}
                  className="field resize-y"
                  placeholder="机器学习，数据结构，大学英语"
                />
                <span className="mt-1 block text-xs text-ink-400">使用逗号或换行分隔</span>
              </label>
              <label className="mt-4 block">
                <span className="mb-1.5 block text-sm font-bold text-ink-700">教师姓名</span>
                <textarea
                  rows={2}
                  value={settings.teacher_names.join("，")}
                  onChange={(input) => updateTeachers(input.target.value)}
                  className="field resize-y"
                  placeholder="张老师，李教授"
                />
              </label>
              <p className="mt-4 rounded-xl bg-mist-50 p-3 text-xs leading-5 text-ink-500">
                专业术语请在右侧“自定义热词”中选择“专业术语”分类维护，它们会进入同一套可审计热词记录。
              </p>
            </section>

            <section className="surface p-5 sm:p-6">
              <div className="mb-5 flex items-center gap-3">
                <span className="flex size-10 items-center justify-center rounded-2xl bg-mist-100 text-ink-600">
                  <Cpu size={20} />
                </span>
                <div>
                  <h2 className="font-extrabold text-ink-950">ASR 配置</h2>
                  <p className="text-xs text-ink-400">前端始终通过 WebSocket 连接服务端模型</p>
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <label>
                  <span className="mb-1.5 block text-sm font-bold text-ink-700">识别提供方</span>
                  <select
                    value={settings.asr_provider}
                    onChange={(input) =>
                      setSettings({ ...settings, asr_provider: input.target.value })
                    }
                    className="field"
                  >
                    <option value="funasr">FunASR（实时中文）</option>
                    <option value="whisper">Whisper（离线基线）</option>
                    <option value="disabled">停用</option>
                  </select>
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-bold text-ink-700">模型</span>
                  <input
                    value={settings.asr_model}
                    onChange={(input) =>
                      setSettings({ ...settings, asr_model: input.target.value })
                    }
                    className="field"
                  />
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-bold text-ink-700">运行设备</span>
                  <input
                    value={settings.asr_device}
                    onChange={(input) =>
                      setSettings({ ...settings, asr_device: input.target.value })
                    }
                    className="field"
                    placeholder="cpu 或 cuda:0"
                  />
                </label>
                <label>
                  <span className="mb-1.5 block text-sm font-bold text-ink-700">时区</span>
                  <select
                    value={settings.timezone}
                    onChange={(input) => setSettings({ ...settings, timezone: input.target.value })}
                    className="field"
                  >
                    <option value="Asia/Shanghai">Asia/Shanghai (UTC+8)</option>
                    <option value="UTC">UTC</option>
                  </select>
                </label>
              </div>
              <label className="mt-4 block">
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
            </section>
          </div>

          <aside className="space-y-6">
            <section className="surface p-5 sm:p-6">
              <div className="mb-4 flex items-center gap-3">
                <span className="flex size-10 items-center justify-center rounded-2xl bg-gold-100/70 text-amber-700">
                  <Volume2 size={20} />
                </span>
                <div>
                  <h2 className="font-extrabold text-ink-950">自定义热词</h2>
                  <p className="text-xs text-ink-400">加入常用课程与术语</p>
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
                    onChange={(input) => setNewCategory(input.target.value as Hotword["category"])}
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
                              onClick={() => void removeHotword(word)}
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
      )}
    </div>
  );
}
