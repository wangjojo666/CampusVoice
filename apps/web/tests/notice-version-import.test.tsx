import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  series: vi.fn(),
  createSeries: vi.fn(),
  timeline: vi.fn(),
  addVersion: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    radar: {
      series: mocks.series,
      createSeries: mocks.createSeries,
      timeline: mocks.timeline,
      addVersion: mocks.addVersion,
    },
  },
}));

import { NoticeVersionImport } from "@/components/notices/notice-version-import";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";

const series = {
  id: "series-1",
  canonical_key: "exam.ai.2026",
  normalized_title: "人工智能专业考试安排",
  department: "教务处",
  source_key: "jw-exam-ai",
  version_count: 0,
  current_document_id: null,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const versionOne = {
  id: "doc-v1",
  series_id: "series-1",
  supersedes_document_id: null,
  revision_number: 1,
  title: "人工智能专业考试安排",
  version_label: "v1",
  effective_at: null,
  publish_date: "2026-07-10",
  is_current: true,
  ingest_source: "manual",
  claims: [],
  created_at: "2026-07-10T00:00:00Z",
};

afterEach(() => {
  cleanup();
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
});

describe("NoticeVersionImport", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.series.mockResolvedValue([series]);
    mocks.createSeries.mockResolvedValue(series);
    mocks.timeline.mockResolvedValue({ series, versions: [] });
    mocks.addVersion.mockResolvedValue(versionOne);
  });

  it("creates an explicit NoticeSeries and imports v1 with no predecessor", async () => {
    const user = userEvent.setup();
    setCurrentUserSettings({
      ...DEFAULT_USER_SETTINGS,
      timezone: "America/Los_Angeles",
    });
    render(<NoticeVersionImport />);

    expect(mocks.series).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("系列唯一键"), "exam.ai.2026");
    await user.type(screen.getByLabelText("通知标题"), "人工智能专业考试安排");
    await user.type(screen.getByLabelText("发布部门（可选）"), "教务处");
    await user.click(screen.getByRole("button", { name: "创建明确系列" }));

    await waitFor(() =>
      expect(mocks.createSeries).toHaveBeenCalledWith({
        canonical_key: "exam.ai.2026",
        title: "人工智能专业考试安排",
        department: "教务处",
        source_key: null,
      }),
    );
    expect(await screen.findByText(/现在可显式导入 v1/)).toBeInTheDocument();
    expect(screen.getByLabelText("精确前驱")).toBeDisabled();

    await user.type(
      screen.getByLabelText("通知完整原文"),
      "考试时间为 2026-07-18 09:00 至 11:00，地点 A302。",
    );
    await user.type(screen.getByLabelText("生效时间（可选）"), "2026-07-18T09:30");
    await user.click(screen.getByRole("checkbox", { name: /我已核对同名或相似通知/ }));
    await user.click(screen.getByRole("button", { name: "导入并提取证据 claim" }));

    await waitFor(() => expect(mocks.addVersion).toHaveBeenCalledTimes(1));
    expect(mocks.addVersion).toHaveBeenCalledWith(
      "series-1",
      expect.objectContaining({
        revision_number: 1,
        version_label: "v1",
        supersedes_document_id: null,
        effective_at: "2026-07-18T16:30:00.000Z",
        ingest_source: "manual",
      }),
    );
  });

  it("requires an exact current predecessor and ambiguity acknowledgement for v2", async () => {
    const user = userEvent.setup();
    const versionedSeries = { ...series, version_count: 1, current_document_id: "doc-v1" };
    mocks.series.mockResolvedValue([versionedSeries]);
    mocks.timeline.mockResolvedValue({ series: versionedSeries, versions: [versionOne] });
    mocks.addVersion.mockResolvedValue({
      ...versionOne,
      id: "doc-v2",
      supersedes_document_id: "doc-v1",
      revision_number: 2,
      version_label: "v2",
    });
    render(<NoticeVersionImport />);

    await user.click(screen.getByRole("button", { name: "加载系列" }));
    await user.selectOptions(screen.getByLabelText("目标系列（必选）"), "series-1");
    expect(await screen.findByDisplayValue("v2")).toBeInTheDocument();
    await user.type(
      screen.getByLabelText("通知完整原文"),
      "考试时间改为 2026-07-18 14:00 至 16:00，地点 B205。",
    );

    const submit = screen.getByRole("button", { name: "导入并提取证据 claim" });
    expect(submit).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("精确前驱"), "doc-v1");
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /精确前驱为“v1 · 文档 doc-v1”/ }));
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() => expect(mocks.addVersion).toHaveBeenCalledTimes(1));
    expect(mocks.addVersion).toHaveBeenCalledWith(
      "series-1",
      expect.objectContaining({
        revision_number: 2,
        version_label: "v2",
        supersedes_document_id: "doc-v1",
      }),
    );
    expect(await screen.findByText(/没有按标题静默关联/)).toBeInTheDocument();
  });

  it("rejects a non-monotonic revision before making an API request", async () => {
    const user = userEvent.setup();
    const versionedSeries = { ...series, version_count: 1, current_document_id: "doc-v1" };
    mocks.series.mockResolvedValue([versionedSeries]);
    mocks.timeline.mockResolvedValue({ series: versionedSeries, versions: [versionOne] });
    render(<NoticeVersionImport />);

    await user.click(screen.getByRole("button", { name: "加载系列" }));
    await user.selectOptions(screen.getByLabelText("目标系列（必选）"), "series-1");
    await user.clear(screen.getByLabelText("修订号"));
    await user.type(screen.getByLabelText("修订号"), "4");

    expect(await screen.findByRole("alert")).toHaveTextContent("下一修订号必须是 2");
    expect(screen.getByRole("button", { name: "导入并提取证据 claim" })).toBeDisabled();
    expect(mocks.addVersion).not.toHaveBeenCalled();
  });
});
