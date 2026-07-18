import type {
  ActionLog,
  CalendarEvent,
  DocumentRecord,
  KnowledgeEvidence,
  Task,
} from "@campusvoice/shared-types";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import NoticesPage from "@/app/notices/page";
import { ApiError } from "@/lib/api-client";
import { fromLocalInputValue, toLocalInputValue } from "@/lib/format";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  listTasks: vi.fn(),
  listEvents: vi.fn(),
  listActionLogs: vi.fn(),
  listRadar: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
  askKnowledge: vi.fn(),
  searchKnowledge: vi.fn(),
  undoAction: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
    "aria-label": ariaLabel,
  }: {
    href: string;
    children: ReactNode;
    className?: string;
    "aria-label"?: string;
  }) => (
    <a href={href} className={className} aria-label={ariaLabel}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));

vi.mock("@/components/voice/asr-recorder", () => ({
  AsrRecorder: ({ onTranscriptChange }: { onTranscriptChange: (value: string) => void }) => (
    <button type="button" onClick={() => onTranscriptChange("明天完成数据库作业")}>
      模拟语音识别
    </button>
  ),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    readonly status: number;
    readonly userMessage: string;

    constructor(message: string, options: { status: number }) {
      super(message);
      this.status = options.status;
      this.userMessage = options.status >= 500 ? "服务暂时不可用，请稍后重试。" : message;
    }
  },
  api: {
    tasks: { list: mocks.listTasks },
    events: { list: mocks.listEvents },
    actionLogs: { list: mocks.listActionLogs },
    actions: { undo: mocks.undoAction },
    radar: { list: mocks.listRadar },
    documents: { list: mocks.listDocuments, upload: mocks.uploadDocument },
    knowledge: { ask: mocks.askKnowledge, search: mocks.searchKnowledge },
  },
}));

function localTime(dayOffset: number, hour: number) {
  const timezone = DEFAULT_USER_SETTINGS.timezone;
  const localToday = toLocalInputValue(new Date().toISOString(), timezone).slice(0, 10);
  const shiftedDay = new Date(`${localToday}T00:00:00.000Z`);
  shiftedDay.setUTCDate(shiftedDay.getUTCDate() + dayOffset);
  const localValue = `${shiftedDay.toISOString().slice(0, 10)}T${String(hour).padStart(2, "0")}:00`;
  const instant = fromLocalInputValue(localValue, timezone);
  if (!instant) throw new Error(`Unable to build ${timezone} test fixture: ${localValue}`);
  return instant;
}

function task(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    title: "完成数据库作业",
    description: null,
    course: "数据库",
    course_id: null,
    due_at: null,
    reminder_at: null,
    priority: "high",
    status: "pending",
    source_type: "manual",
    source_document_id: null,
    created_at: "2026-07-12T00:00:00.000Z",
    updated_at: "2026-07-12T00:00:00.000Z",
    version: 1,
    ...overrides,
  };
}

function event(overrides: Partial<CalendarEvent>): CalendarEvent {
  return {
    id: "event-1",
    title: "算法答疑",
    description: null,
    course: "算法",
    course_id: null,
    start_at: localTime(0, 9),
    end_at: localTime(0, 10),
    location: "A101",
    reminder_minutes: 30,
    source_type: "manual",
    source_document_id: null,
    created_at: "2026-07-12T00:00:00.000Z",
    updated_at: "2026-07-12T00:00:00.000Z",
    version: 1,
    ...overrides,
  };
}

function actionLog(overrides: Partial<ActionLog>): ActionLog {
  return {
    id: "log-1",
    action: "create_task",
    risk_level: "low",
    confirmed: true,
    success: true,
    message: "待办写入后已重新查询验证",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const uploadedDocument: DocumentRecord = {
  id: "doc-1",
  title: "2026 奖学金通知",
  department: "学生处",
  publish_date: "2026-07-12",
  applicable_group: "2026级本科生",
  source_url: null,
  version: "v2",
  file_type: "md",
  status: "ready",
  chunk_count: 3,
  created_at: "2026-07-12T00:00:00.000Z",
};

const scholarshipEvidence: KnowledgeEvidence = {
  document_id: "doc-1",
  chunk_id: "chunk-1",
  content: "奖学金申请截止时间为 7 月 31 日。",
  page: 2,
  similarity: 0.94,
  document_title: "2026 奖学金通知",
  publish_date: "2026-07-12",
  version: "v2",
  applicable_group: "2026级本科生",
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
});

beforeEach(() => {
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
  useAssistantStore.getState().reset();
  mocks.push.mockReset();
  mocks.listTasks.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.listEvents.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.listActionLogs.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.listRadar.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.listDocuments.mockReset().mockResolvedValue({ items: [], total: 0 });
  mocks.uploadDocument.mockReset().mockResolvedValue(uploadedDocument);
  mocks.askKnowledge.mockReset();
  mocks.searchKnowledge.mockReset();
  mocks.undoAction.mockReset();
});

describe("dashboard business states", () => {
  it("places Today before the student voice entry and fills a full prompt into the shared workflow", async () => {
    const user = userEvent.setup();

    render(<HomePage />);

    expect(screen.getByRole("heading", { name: "今天先把最重要的事接住" })).toBeInTheDocument();
    const today = screen.getByRole("region", { name: "今天" });
    const voiceEntry = screen.getByRole("region", { name: "问声程" });
    const campusRadar = screen.getByRole("region", { name: "与你有关的变化与截止" });
    expect(within(today).getByText("正在加载下一安排")).toBeInTheDocument();
    expect(within(today).queryByText("暂时没有排定日程")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "看看已有日程" })).toHaveAttribute(
      "title",
      "查询我的日程。",
    );
    expect(screen.getByRole("button", { name: "考试地点变了吗" })).toHaveAttribute(
      "title",
      "查询校园通知：人工智能考试地点有没有变化。",
    );
    expect(today.compareDocumentPosition(voiceEntry) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
      0,
    );
    expect(
      voiceEntry.compareDocumentPosition(campusRadar) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);

    await user.click(screen.getByRole("button", { name: "盯住实验报告截止" }));
    expect(useAssistantStore.getState().transcript).toBe(
      "新建待办：后天晚上八点提交实验报告，提前一天提醒我。",
    );
    expect(useAssistantStore.getState().inputMode).toBe("text_demo");
    expect(screen.getByText("文本指令演示，不是语音识别结果")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "进入完整确认流程" })).toHaveAttribute(
      "href",
      "/voice",
    );
  });

  it("clears stale workflow state when a new Home command starts", async () => {
    const user = userEvent.setup();
    const store = useAssistantStore.getState();
    store.setCorrection({
      record_id: "correction-old",
      original_text: "旧指令",
      corrected_text: "旧指令",
      changes: [],
    });
    store.setIntent({
      intent: "create_task",
      confidence: 0.9,
      slots: { title: "旧待办" },
      missing_fields: [],
      ambiguities: [],
      source_text: "旧指令",
      requires_confirmation: true,
    });
    store.setExecution({
      success: true,
      action: "create_task",
      record_id: "task-old",
      verified_fields: { title: true },
      side_effects: [],
      message: "旧记录已验证",
    });
    store.setLastExecutedActionId("action-old");

    render(<HomePage />);
    expect(screen.queryByText("这一步稳稳落地了")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "盯住实验报告截止" }));

    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "新建待办：后天晚上八点提交实验报告，提前一天提醒我。",
      inputMode: "text_demo",
      correction: null,
      intent: null,
      execution: null,
      lastExecutedActionId: null,
    });
    expect(screen.queryByText("旧记录已验证")).not.toBeInTheDocument();
  });

  it("summarizes the next schedule, near-term deadlines, overdue work, and unscheduled work", async () => {
    mocks.listTasks.mockResolvedValue({
      items: [
        task({ id: "due-tomorrow", title: "提交实验报告", due_at: localTime(1, 18) }),
        task({ id: "due-day-after", title: "准备答辩材料", due_at: localTime(2, 18) }),
        task({
          id: "overdue",
          title: "补交课程小测",
          due_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
        }),
        task({ id: "unscheduled", title: "整理课堂笔记", due_at: null }),
        task({
          id: "completed",
          title: "已经完成的任务",
          due_at: localTime(1, 10),
          status: "completed",
        }),
      ],
      total: 5,
    });
    mocks.listEvents.mockResolvedValue({
      items: [
        event({
          id: "ended",
          title: "已经结束的课程",
          start_at: localTime(-1, 9),
          end_at: localTime(-1, 10),
        }),
        event({
          id: "next",
          title: "机器学习研讨课",
          start_at: localTime(1, 9),
          end_at: localTime(1, 10),
          location: "教学楼 B205",
        }),
      ],
      total: 2,
    });

    render(<HomePage />);

    const today = await screen.findByRole("region", { name: "今天" });
    const weekly = await screen.findByRole("region", { name: "一周节奏" });
    const voice = screen.getByRole("region", { name: "问声程" });
    expect(today.compareDocumentPosition(weekly) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(weekly.compareDocumentPosition(voice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() =>
      expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("2"),
    );
    expect(within(today).getByText("需要补救").previousElementSibling).toHaveTextContent("1");
    expect(within(today).getByText("7 天任务与日程").closest("a")).toBeNull();
    expect(within(today).getByText(/1 项还没设置截止时间/)).toBeInTheDocument();
    expect(within(today).getByRole("link", { name: /下一安排：机器学习研讨课/ })).toHaveAttribute(
      "href",
      "/calendar",
    );
    expect(within(today).queryByText("已经结束的课程")).not.toBeInTheDocument();
    expect(within(today).queryByText("已经完成的任务")).not.toBeInTheDocument();
  });

  it("shows the verified Home record and binds undo to its executed action", async () => {
    const user = userEvent.setup();
    mocks.undoAction.mockResolvedValue({
      success: true,
      action: "undo_create_event",
      record_id: "event-verified",
      verified_fields: { deleted: true },
      side_effects: [],
      message: "日程已撤销并复验",
    });
    const store = useAssistantStore.getState();
    store.setTranscript("创建机器学习考试日程");
    store.setIntent({
      intent: "create_event",
      confidence: 0.98,
      slots: { title: "机器学习考试" },
      missing_fields: [],
      ambiguities: [],
      source_text: "创建机器学习考试日程",
      requires_confirmation: true,
    });
    store.setExecution({
      success: true,
      action: "create_event",
      record_id: "event-verified",
      verified_fields: { title: true, start_at: true },
      side_effects: [],
      message: "日程已写入并复验",
      record: event({
        id: "event-verified",
        title: "机器学习考试",
        location: "教学楼 B205",
        reminder_minutes: 1440,
      }),
    });
    store.setLastExecutedActionId("action-verified");

    render(<HomePage />);

    expect(screen.getByText("已确认并完成数据库写入")).toBeInTheDocument();
    expect(screen.getByText("字段完整；无新增风险")).toBeInTheDocument();
    expect(screen.getByText("机器学习考试")).toBeInTheDocument();
    expect(screen.getByText("教学楼 B205")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看日历" })).toHaveAttribute("href", "/calendar");

    await user.click(screen.getByRole("button", { name: "撤销本次操作" }));
    expect(mocks.undoAction).toHaveBeenCalledWith("action-verified");
    expect(await screen.findByText("日程已撤销并复验")).toBeInTheDocument();
    expect(useAssistantStore.getState().lastExecutedActionId).toBeNull();
  });

  it("shows upcoming work, progressively discloses verification records, and continues voice", async () => {
    const user = userEvent.setup();
    mocks.listTasks.mockResolvedValue({
      items: [
        task({ id: "task-today", due_at: localTime(1, 18) }),
        task({ id: "task-unscheduled", title: "整理课程笔记", due_at: null }),
        task({ id: "task-complete", title: "已完成报告", status: "completed" }),
        task({ id: "task-future", title: "下周任务", due_at: localTime(2, 18) }),
      ],
      total: 4,
    });
    mocks.listEvents.mockResolvedValue({
      items: [
        event({
          id: "event-today",
          start_at: localTime(1, 9),
          end_at: localTime(1, 10),
        }),
        event({
          id: "event-future",
          title: "明天班会",
          start_at: localTime(2, 9),
          end_at: localTime(2, 10),
        }),
      ],
      total: 2,
    });
    mocks.listActionLogs.mockResolvedValue({
      items: [
        actionLog({ id: "log-success" }),
        actionLog({ id: "log-failed", success: false, message: "日程验证失败" }),
      ],
      total: 2,
    });

    render(<HomePage />);

    const today = await screen.findByRole("region", { name: "今天" });
    const upNext = screen.getByRole("region", { name: "接下来" });
    await waitFor(() => expect(within(today).getByText("完成数据库作业")).toBeInTheDocument());
    expect(within(upNext).getByText("整理课程笔记")).toBeInTheDocument();
    expect(within(today).getByText("算法答疑")).toBeInTheDocument();
    expect(screen.queryByText("已完成报告")).not.toBeInTheDocument();
    expect(within(upNext).getByText("下周任务")).toBeInTheDocument();
    expect(within(upNext).getByText("明天班会")).toBeInTheDocument();
    const disclosure = screen.getByRole("button", { name: "查看执行详情" });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("待办写入后已重新查询验证")).not.toBeInTheDocument();
    expect(screen.queryByText("日程验证失败")).not.toBeInTheDocument();
    await user.click(disclosure);
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("待办写入后已重新查询验证")).toBeInTheDocument();
    expect(screen.getByText("日程验证失败")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看确认与验证流程" })).toHaveClass("min-h-11");

    await user.click(screen.getByRole("button", { name: "模拟语音识别" }));
    expect(useAssistantStore.getState().transcript).toBe("明天完成数据库作业");
    expect(useAssistantStore.getState().inputMode).toBe("voice");
    expect(screen.getByRole("link", { name: "进入完整确认流程" })).toHaveAttribute(
      "href",
      "/voice",
    );
  });

  it("keeps successful sections usable after a partial load error and retries all data", async () => {
    const user = userEvent.setup();
    mocks.listTasks
      .mockRejectedValueOnce(new ApiError("待办服务异常", { status: 503 }))
      .mockResolvedValueOnce({ items: [task({ id: "task-retry" })], total: 1 });
    mocks.listActionLogs.mockRejectedValueOnce(new ApiError("操作记录服务异常", { status: 503 }));

    render(<HomePage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("服务暂时不可用，请稍后重试。");
    const today = screen.getByRole("region", { name: "今天" });
    expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("—");
    expect(within(today).getByText("待办数据暂不可用")).toBeInTheDocument();
    expect(screen.queryByText("还没有近期安排")).not.toBeInTheDocument();
    expect(screen.getByText("部分安排暂不可用")).toBeInTheDocument();
    expect(screen.getByText("操作记录暂不可用")).toBeInTheDocument();
    expect(screen.queryByText("还没有操作记录")).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "重试" }));
    expect(await screen.findByText("完成数据库作业")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByText("还没有操作记录")).toBeInTheDocument();
    expect(mocks.listTasks).toHaveBeenCalledTimes(2);
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
    expect(mocks.listActionLogs).toHaveBeenCalledTimes(2);
  });

  it("advances one shared clock across a local day, task deadline, and event end", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-17T15:59:30.000Z"));
    mocks.listTasks.mockResolvedValue({
      items: [task({ id: "clock-task", due_at: "2026-07-17T16:00:15.000Z" })],
      total: 1,
    });
    mocks.listEvents.mockResolvedValue({
      items: [
        event({
          id: "clock-event",
          title: "跨日答疑",
          start_at: "2026-07-17T15:58:30.000Z",
          end_at: "2026-07-17T16:00:15.000Z",
        }),
      ],
      total: 1,
    });

    render(<HomePage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const today = screen.getByRole("region", { name: "今天" });
    expect(screen.getByText(/今天 · 7月17日/)).toBeInTheDocument();
    expect(within(today).getByText("跨日答疑")).toBeInTheDocument();
    expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getByText(/今天 · 7月18日/)).toBeInTheDocument();
    expect(within(today).queryByText("跨日答疑")).not.toBeInTheDocument();
    expect(within(today).getByText("暂时没有排定日程")).toBeInTheDocument();
    expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("0");
    expect(within(today).getByText("需要补救").previousElementSibling).toHaveTextContent("1");
  });

  it("marks retained task data as stale when a retry fails instead of presenting it as fresh", async () => {
    const user = userEvent.setup();
    mocks.listTasks
      .mockResolvedValueOnce({
        items: [task({ id: "task-stale", due_at: localTime(1, 18) })],
        total: 1,
      })
      .mockRejectedValueOnce(new ApiError("待办刷新异常", { status: 503 }));
    mocks.listEvents
      .mockRejectedValueOnce(new ApiError("日程服务异常", { status: 503 }))
      .mockResolvedValueOnce({ items: [], total: 0 });

    render(<HomePage />);

    const firstAlert = await screen.findByRole("alert");
    const today = screen.getByRole("region", { name: "今天" });
    await waitFor(() =>
      expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("1"),
    );
    expect(within(today).getByRole("status")).toHaveTextContent("日程数据暂不可用");

    await user.click(within(firstAlert).getByRole("button", { name: "重试" }));
    expect(await within(today).findByText("待办为上次同步数据")).toBeInTheDocument();
    expect(within(today).getByText("3 天内截止").previousElementSibling).toHaveTextContent("1");
    expect(mocks.listTasks).toHaveBeenCalledTimes(2);
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
  });
});

describe("campus notices workflows", () => {
  it("uploads from the empty state, closes the modal, and reloads the indexed document", async () => {
    const user = userEvent.setup();
    mocks.listDocuments
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [uploadedDocument], total: 1 });

    render(<NoticesPage />);

    expect(await screen.findByText("还没有校园通知")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "上传第一份文档" }));

    const dialog = screen.getByRole("dialog", { name: "上传校园通知" });
    const file = new File(["# synthetic scholarship notice"], "scholarship.md", {
      type: "text/markdown",
    });
    const fileInput = dialog.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    await user.upload(fileInput as HTMLInputElement, file);
    await user.clear(within(dialog).getByLabelText("文件标题 *"));
    await user.type(within(dialog).getByLabelText("文件标题 *"), "2026 奖学金通知");
    await user.type(within(dialog).getByLabelText("发布部门"), " 学生处 ");
    fireEvent.change(within(dialog).getByLabelText("发布日期"), {
      target: { value: "2026-07-12" },
    });
    await user.type(within(dialog).getByLabelText("适用群体"), " 2026级本科生 ");
    await user.type(within(dialog).getByLabelText("版本"), " v2 ");
    await user.click(within(dialog).getByRole("button", { name: "上传文档" }));

    await waitFor(() =>
      expect(mocks.uploadDocument).toHaveBeenCalledWith(file, {
        title: "2026 奖学金通知",
        department: "学生处",
        publish_date: "2026-07-12",
        applicable_group: "2026级本科生",
        version: "v2",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("文档已上传");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("2026 奖学金通知")).toBeInTheDocument();
    expect(screen.getByText("可检索")).toBeInTheDocument();
    expect(mocks.listDocuments).toHaveBeenCalledTimes(2);
  });

  it("blocks conversion on a version conflict, then filters and navigates with source lineage", async () => {
    const user = userEvent.setup();
    mocks.listDocuments.mockResolvedValue({ items: [uploadedDocument], total: 1 });
    mocks.searchKnowledge
      .mockResolvedValueOnce({
        evidence: [scholarshipEvidence],
        version_conflicts: [{ document_title: "2026 奖学金通知", versions: ["v1", "v2"] }],
        applicability_conflicts: [],
      })
      .mockResolvedValueOnce({
        evidence: [scholarshipEvidence],
        version_conflicts: [],
        applicability_conflicts: [],
      });

    render(<NoticesPage />);
    await waitFor(() => expect(mocks.listDocuments).toHaveBeenCalled());
    await user.click(screen.getByRole("tab", { name: "原文检索" }));
    await user.type(screen.getByLabelText("输入检索关键词"), "奖学金截止时间");
    await user.click(screen.getByRole("button", { name: "搜索原文" }));

    const conflict = await screen.findByRole("alert");
    expect(conflict).toHaveTextContent("发现多个版本");
    expect(conflict).toHaveTextContent("v1、v2");
    expect(screen.getByRole("button", { name: "转为待办草稿" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "转为日程草稿" })).toBeDisabled();
    expect(mocks.push).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("指定版本（冲突时必填）"), " v2 ");
    await user.type(screen.getByLabelText("指定适用群体（冲突时必填）"), " 2026级本科生 ");
    await user.click(screen.getByRole("button", { name: "搜索原文" }));

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(mocks.searchKnowledge).toHaveBeenNthCalledWith(1, "奖学金截止时间", 8, {
      version: undefined,
      applicable_group: undefined,
    });
    expect(mocks.searchKnowledge).toHaveBeenNthCalledWith(2, "奖学金截止时间", 8, {
      version: "v2",
      applicable_group: "2026级本科生",
    });
    expect(screen.getByText("奖学金申请截止时间为 7 月 31 日。")).toBeInTheDocument();
    expect(screen.getByText("相似度 94%")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "转为日程草稿" }));
    expect(useAssistantStore.getState().sourceDocumentId).toBe("doc-1");
    expect(useAssistantStore.getState().transcript).toContain(
      "根据校园通知《2026 奖学金通知》中的这段内容，创建日历",
    );
    expect(mocks.push).toHaveBeenCalledWith("/voice");
  });
});
