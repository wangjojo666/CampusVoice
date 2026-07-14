import type {
  CalendarEvent,
  CorrectionResult,
  Task,
  VerificationResult,
} from "@campusvoice/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExecutionResult } from "@/components/actions/execution-result";
import { EventForm } from "@/components/calendar/event-form";
import { UploadForm } from "@/components/notices/upload-form";
import { TaskForm } from "@/components/tasks/task-form";
import { CorrectionDiff } from "@/components/voice/correction-diff";

afterEach(cleanup);

const task: Task = {
  id: "task-1",
  title: "旧标题",
  description: "旧说明",
  course: "机器学习",
  course_id: null,
  due_at: "2026-07-15T02:00:00.000Z",
  reminder_at: null,
  priority: "medium",
  status: "pending",
  source_type: "manual",
  source_document_id: null,
  created_at: "2026-07-12T00:00:00.000Z",
  updated_at: "2026-07-12T00:00:00.000Z",
  version: 1,
};

const event: CalendarEvent = {
  id: "event-1",
  title: "课程答疑",
  description: null,
  course: "算法",
  course_id: null,
  start_at: "2026-07-20T01:00:00.000Z",
  end_at: "2026-07-20T02:00:00.000Z",
  location: "A101",
  reminder_minutes: 30,
  source_type: "manual",
  source_document_id: null,
  created_at: "2026-07-12T00:00:00.000Z",
  updated_at: "2026-07-12T00:00:00.000Z",
  version: 1,
};

describe("reviewable mutation forms", () => {
  it("requires a task title, shows a review, and submits a normalized create payload", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(<TaskForm task={null} busy={false} onSubmit={onSubmit} onCancel={onCancel} />);

    expect(screen.getByRole("button", { name: "核对并保存" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledOnce();

    await user.type(screen.getByLabelText("标题 *"), "  完成实验报告  ");
    await user.type(screen.getByLabelText("课程"), "  数据库  ");
    await user.selectOptions(screen.getByLabelText("优先级"), "high");
    await user.type(screen.getByLabelText("说明"), "  附上截图  ");
    fireEvent.change(screen.getByLabelText("截止时间"), {
      target: { value: "2026-07-18T20:30" },
    });
    await user.click(screen.getByRole("button", { name: "核对并保存" }));

    expect(screen.getByText("确认创建这项待办")).toBeInTheDocument();
    expect(screen.getByText("完成实验报告")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并保存" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "完成实验报告",
        course: "数据库",
        description: "附上截图",
        priority: "high",
        due_at: expect.stringContaining("2026-07-18T"),
        reminder_at: null,
        source_type: "manual",
      }),
    );
  });

  it("edits task status and lets the user return from review", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<TaskForm task={task} busy={false} onSubmit={onSubmit} onCancel={vi.fn()} />);

    await user.clear(screen.getByLabelText("标题 *"));
    await user.type(screen.getByLabelText("标题 *"), "新标题");
    await user.selectOptions(screen.getByLabelText("状态"), "completed");
    await user.click(screen.getByRole("button", { name: "核对并保存" }));
    expect(screen.getByText("确认修改这项待办")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回修改" }));
    expect(screen.getByLabelText("状态")).toHaveValue("completed");
    await user.click(screen.getByRole("button", { name: "核对并保存" }));
    await user.click(screen.getByRole("button", { name: "确认并保存" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ title: "新标题", status: "completed" }),
    );
  });

  it("submits a reviewed event only when no conflict exists", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <EventForm
        event={null}
        defaultStart={new Date("2026-07-21T01:00:00.000Z")}
        conflicts={[]}
        busy={false}
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("标题 *"), "  项目例会  ");
    await user.type(screen.getByLabelText("地点"), "  B202  ");
    await user.selectOptions(screen.getByLabelText("提前提醒"), "60");
    await user.click(screen.getByRole("button", { name: "检查冲突并核对" }));

    expect(screen.getByText("未检测到时间冲突")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并保存" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "项目例会",
        location: "B202",
        reminder_minutes: 60,
        source_type: "manual",
      }),
    );
  });

  it("uses the saved reminder only for new events and preserves an edited value", () => {
    const { rerender } = render(
      <EventForm
        event={null}
        defaultStart={new Date("2026-07-21T13:00:00.000Z")}
        timezone="America/New_York"
        defaultReminderMinutes={60}
        conflicts={[]}
        busy={false}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("开始时间 *")).toHaveValue("2026-07-21T09:00");
    expect(screen.getByLabelText("提前提醒")).toHaveValue("60");

    rerender(
      <EventForm
        key="edit"
        event={{ ...event, reminder_minutes: 10 }}
        timezone="America/New_York"
        defaultReminderMinutes={60}
        conflicts={[]}
        busy={false}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("提前提醒")).toHaveValue("10");
  });

  it("leaves an untouched create reminder to the server-side user default", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <EventForm
        event={null}
        defaultStart={new Date("2026-07-21T13:00:00.000Z")}
        timezone="America/New_York"
        defaultReminderMinutes={60}
        conflicts={[]}
        busy={false}
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("标题 *"), "项目例会");
    await user.click(screen.getByRole("button", { name: "检查冲突并核对" }));
    expect(screen.getByText("使用个人默认提醒")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并保存" }));

    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit.mock.calls[0]?.[0]).not.toHaveProperty("reminder_minutes");
  });

  it("renders the conflicting event and blocks an edited event save", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <EventForm
        event={event}
        conflicts={[
          {
            event_id: "event-2",
            title: "冲突课程",
            start_at: "2026-07-20T01:30:00.000Z",
            end_at: "2026-07-20T02:30:00.000Z",
          },
        ]}
        busy={false}
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查冲突并核对" }));
    expect(screen.getByRole("alert")).toHaveTextContent("冲突课程");
    expect(screen.getByRole("button", { name: "确认并保存" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("uploads a selected synthetic document with trimmed metadata", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    const { container } = render(
      <UploadForm busy={false} onSubmit={onSubmit} onCancel={onCancel} />,
    );
    const file = new File(["synthetic notice"], "notice.txt", { type: "text/plain" });
    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    await user.upload(fileInput as HTMLInputElement, file);
    expect(screen.getByLabelText("文件标题 *")).toHaveValue("notice");

    await user.type(screen.getByLabelText("发布部门"), "  教务处  ");
    fireEvent.change(screen.getByLabelText("发布日期"), { target: { value: "2026-07-12" } });
    await user.type(screen.getByLabelText("适用群体"), "  全体学生  ");
    await user.click(screen.getByRole("button", { name: "上传文档" }));
    expect(onSubmit).toHaveBeenCalledWith(file, {
      title: "notice",
      department: "教务处",
      publish_date: "2026-07-12",
      applicable_group: "全体学生",
      version: null,
    });
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

describe("verification presentation", () => {
  it("shows unchanged correction state and invokes an explicit candidate choice", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CorrectionDiff
        correction={{
          record_id: "cor-1",
          original_text: "无需修改",
          corrected_text: "无需修改",
          changes: [],
        }}
      />,
    );
    expect(screen.getByText("校园术语检查完成，未发现需要修改的内容。")).toBeInTheDocument();

    const onChoose = vi.fn();
    const changed: CorrectionResult = {
      record_id: "cor-2",
      original_text: "计组",
      corrected_text: "计组",
      changes: [
        {
          start: 0,
          end: 2,
          original: "计组",
          corrected: "计算机组成原理",
          candidates: ["计算机组成原理", "计算机组织"],
          reason: "校园课程术语候选",
          confidence: 0.72,
          requires_confirmation: true,
        },
      ],
    };
    rerender(<CorrectionDiff correction={changed} onChoose={onChoose} />);
    expect(screen.getByText("置信度 72%")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "计算机组织" }));
    expect(onChoose).toHaveBeenCalledWith(0, "计算机组织");
  });

  it("offers undo for success and retry only for a retryable failure", async () => {
    const user = userEvent.setup();
    const onUndo = vi.fn();
    const success: VerificationResult = {
      success: true,
      action: "create_event",
      record_id: "event-1",
      verified_fields: { title: true, start_at: true },
      side_effects: ["time_conflict"],
      message: "数据库记录已重新查询",
      record: {
        ...event,
        title: "机器学习考试",
        location: "教学楼 B205",
        reminder_minutes: 1440,
      },
    };
    const { rerender } = render(<ExecutionResult result={success} onUndo={onUndo} />);
    expect(screen.getByText("数据库验证成功")).toBeInTheDocument();
    expect(screen.getByText("机器学习考试")).toBeInTheDocument();
    expect(screen.getByText("教学楼 B205")).toBeInTheDocument();
    expect(screen.getByText("提前 1440 分钟提醒")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看日历" })).toHaveAttribute("href", "/calendar");
    expect(screen.getByText("新日程与已有安排存在时间冲突")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "撤销本次操作" }));
    expect(onUndo).toHaveBeenCalledOnce();

    const onRetry = vi.fn();
    rerender(
      <ExecutionResult
        result={{
          ...success,
          success: false,
          record_id: null,
          verified_fields: {},
          side_effects: [],
          message: "验证失败",
          failure_reason: "数据库暂时不可用",
          retryable: true,
        }}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("原因：数据库暂时不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试一次" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
