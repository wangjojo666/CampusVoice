"use client";

import { FileUp, Upload } from "lucide-react";
import { useState } from "react";

export function UploadForm({
  busy,
  onSubmit,
  onCancel,
}: Readonly<{
  busy: boolean;
  onSubmit: (
    file: File,
    metadata: {
      title: string;
      department: string | null;
      publish_date: string | null;
      applicable_group: string | null;
      version: string | null;
    },
  ) => void | Promise<void>;
  onCancel: () => void;
}>) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [department, setDepartment] = useState("");
  const [publishDate, setPublishDate] = useState("");
  const [applicableGroup, setApplicableGroup] = useState("");
  const [version, setVersion] = useState("");
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (file && title.trim())
          void onSubmit(file, {
            title: title.trim(),
            department: department.trim() || null,
            publish_date: publishDate || null,
            applicable_group: applicableGroup.trim() || null,
            version: version.trim() || null,
          });
      }}
      className="space-y-4"
    >
      <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-mist-200 bg-mist-50 px-4 text-center transition-colors hover:border-teal-500 hover:bg-teal-50/40">
        <FileUp className="mb-2 text-teal-600" size={27} />
        <span className="text-sm font-bold text-ink-800">
          {file ? file.name : "选择 PDF、DOCX、TXT 或 Markdown"}
        </span>
        <span className="mt-1 text-xs text-ink-400">文档会在后端解析、切片并建立检索索引</span>
        <input
          type="file"
          className="sr-only"
          accept=".pdf,.docx,.txt,.md,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={(event) => {
            const selected = event.target.files?.[0] ?? null;
            setFile(selected);
            if (selected && !title) setTitle(selected.name.replace(/\.[^.]+$/, ""));
          }}
        />
      </label>
      <label className="block">
        <span className="mb-1.5 block text-sm font-bold text-ink-700">文件标题 *</span>
        <input
          required
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          className="field"
        />
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">发布部门</span>
          <input
            value={department}
            onChange={(event) => setDepartment(event.target.value)}
            className="field"
            placeholder="例如：教务处"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">发布日期</span>
          <input
            type="date"
            value={publishDate}
            onChange={(event) => setPublishDate(event.target.value)}
            className="field"
          />
        </label>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">适用群体</span>
          <input
            value={applicableGroup}
            onChange={(event) => setApplicableGroup(event.target.value)}
            className="field"
            placeholder="例如：2024 级本科生"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm font-bold text-ink-700">版本</span>
          <input
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            className="field"
            placeholder="例如：v2"
          />
        </label>
      </div>
      <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancel} className="btn-secondary">
          取消
        </button>
        <button type="submit" disabled={busy || !file || !title.trim()} className="btn-primary">
          <Upload size={16} />
          {busy ? "正在上传并解析" : "上传文档"}
        </button>
      </div>
    </form>
  );
}
