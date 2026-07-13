import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  changeSet: vi.fn(),
  impacts: vi.fn(),
  preview: vi.fn(),
  execute: vi.fn(),
  beginExecute: vi.fn(),
  finishExecute: vi.fn(),
  resumeExecute: vi.fn(),
  plan: vi.fn(),
  receipt: vi.fn(),
  beginUndo: vi.fn(),
  finishUndo: vi.fn(),
  resumeUndo: vi.fn(),
  reviewChange: vi.fn(),
  detectImpacts: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ changeSetId: "ncs_1" }),
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    userMessage = this.message;

    constructor(message: string, options: { status?: number } = {}) {
      super(message);
      this.status = options.status ?? 0;
    }
  },
  api: {
    radar: {
      changeSet: mocks.changeSet,
      impacts: mocks.impacts,
      preview: mocks.preview,
      execute: mocks.execute,
      beginExecute: mocks.beginExecute,
      finishExecute: mocks.finishExecute,
      resumeExecute: mocks.resumeExecute,
      plan: mocks.plan,
      receipt: mocks.receipt,
      beginUndo: mocks.beginUndo,
      finishUndo: mocks.finishUndo,
      resumeUndo: mocks.resumeUndo,
      reviewChange: mocks.reviewChange,
      detectImpacts: mocks.detectImpacts,
    },
  },
}));

import RadarDetailPage from "@/app/radar/[changeSetId]/page";
import { ApiError } from "@/lib/api-client";

const changePayload = {
  id: "ncs_1",
  series_id: "nss_1",
  from_document_id: "doc_1",
  to_document_id: "doc_2",
  algorithm_version: "normalized-diff-v1",
  status: "ready",
  created_at: "2026-07-13T00:00:00Z",
  items: [
    {
      id: "nci_1",
      claim_key: "event.start_at",
      change_type: "changed",
      severity: "high",
      confidence: 0.98,
      review_state: "approved",
      before: {
        claim_id: "old",
        document_id: "doc_1",
        chunk_id: "chunk_1",
        value: { start: "09:00" },
        normalized_value: { iso: "2026-07-18T09:00:00+08:00" },
        evidence_text: "考试时间：2026-07-18 09:00–11:00",
        evidence_start: 20,
        evidence_end: 55,
      },
      after: {
        claim_id: "new",
        document_id: "doc_2",
        chunk_id: "chunk_2",
        value: { start: "14:00" },
        normalized_value: { iso: "2026-07-18T14:00:00+08:00" },
        evidence_text: "考试时间：2026-07-18 14:00–16:00",
        evidence_start: 20,
        evidence_end: 55,
      },
    },
  ],
};

const impactPayload = {
  id: "imp_1",
  change_item_id: "nci_1",
  entity_type: "event",
  entity_id: "evt_1",
  entity_version: 1,
  reason: "event.start_at changed in the explicit successor notice",
  severity: "high",
  current_snapshot: { title: "人工智能专业考试", start_at: "2026-07-18T09:00:00+08:00" },
  proposed_patch: { start_at: "2026-07-18T14:00:00+08:00" },
  recommended_action: "apply",
  requires_manual_review: false,
  status: "open",
  migration_plan_id: null,
};

const migrationItem = {
  id: "mpi_1",
  entity_type: "event",
  entity_id: "evt_1",
  expected_version: 1,
  before: {
    title: "人工智能专业考试",
    start_at: "2026-07-18T09:00:00+08:00",
    end_at: "2026-07-18T11:00:00+08:00",
    location: "教学楼 A302",
    course: "人工智能",
  },
  after: {
    title: "人工智能专业考试",
    start_at: "2026-07-18T14:00:00+08:00",
    end_at: "2026-07-18T16:00:00+08:00",
    location: "教学楼 B205",
    course: "人工智能",
  },
  source_claim_ids: ["new"],
  verification: { verified: true, database_version: 2 },
  execute_verification: { verified: true, database_version: 2 },
  undo_verification: {},
};

const highRiskPlan = {
  id: "mpl_1",
  change_set_id: "ncs_1",
  status: "ready",
  risk_level: "high",
  required_confirmations: 2,
  conflicts: [{ conflicting_event_id: "evt_other", title: "已有课程" }],
  verification: {},
  execute_receipt: {},
  undo_receipt: {},
  generation: 1,
  version: 1,
  executed_at: null,
  undone_at: null,
  items: [migrationItem],
};

const ordinaryPlan = {
  ...highRiskPlan,
  risk_level: "low",
  required_confirmations: 1,
  conflicts: [],
};

const executeReceipt = {
  plan_id: "mpl_1",
  status: "verified",
  operation: "execute",
  verified_count: 1,
  total_count: 1,
  all_verified: true,
  items: [migrationItem],
  verified_at: "2026-07-13T00:01:00Z",
};

const undoReceipt = {
  ...executeReceipt,
  status: "undone",
  operation: "undo",
};

afterEach(cleanup);

describe("RadarDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.changeSet.mockResolvedValue(changePayload);
    mocks.impacts.mockResolvedValue({ items: [impactPayload], total: 1 });
    mocks.preview.mockResolvedValue(highRiskPlan);
    mocks.execute.mockResolvedValue(executeReceipt);
    mocks.beginExecute.mockResolvedValue({
      challenge: "execute-stage-2",
      stage: 2,
      required_stages: 2,
      expires_at: "2026-07-13T00:05:00Z",
    });
    mocks.finishExecute.mockResolvedValue(executeReceipt);
    mocks.resumeExecute.mockResolvedValue(executeReceipt);
    mocks.plan.mockResolvedValue({ ...highRiskPlan, status: "verified", version: 2 });
    mocks.receipt.mockResolvedValue(executeReceipt);
    mocks.beginUndo.mockResolvedValue({
      challenge: "undo-stage-2",
      stage: 2,
      required_stages: 2,
      expires_at: "2026-07-13T00:05:00Z",
    });
    mocks.finishUndo.mockResolvedValue(undoReceipt);
    mocks.resumeUndo.mockResolvedValue(undoReceipt);
  });

  it("shows evidence-linked claims, a calendar timeline, two confirmations, receipt, and group undo", async () => {
    const user = userEvent.setup();
    render(<RadarDetailPage />);

    expect(await screen.findByText("v1 → v2 结构化变化")).toBeInTheDocument();
    expect(screen.getByText("旧值与旧版原文")).toBeInTheDocument();
    expect(screen.getByText("证据如何传导到个人安排")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    const preview = await screen.findByRole("region", {
      name: "确认前预览，不会写入真实安排",
    });
    expect(preview).toHaveFocus();
    expect(within(preview).getByText(/Before ghost：旧安排/)).toBeInTheDocument();
    expect(within(preview).getByText(/New block：建议新安排/)).toBeInTheDocument();
    expect(within(preview).getByText(/Conflicts：1/)).toBeInTheDocument();
    expect(within(preview).getByText("Unchanged：")).toBeInTheDocument();
    expect(
      within(preview).getByRole("link", { name: /开始时间 · 新版证据 · new/ }),
    ).toHaveAttribute("href", "#claim-evidence-new");
    expect(within(preview).getByRole("alert")).toHaveTextContent("默认阻止执行");

    await user.click(within(preview).getByRole("checkbox", { name: /仍要覆盖写入/ }));
    await user.click(within(preview).getByRole("button", { name: "确认迁移" }));
    expect(
      within(preview).getByRole("button", { name: /再次确认并执行整组迁移/ }),
    ).toBeInTheDocument();
    expect(mocks.execute).not.toHaveBeenCalled();
    expect(mocks.finishExecute).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.beginExecute).toHaveBeenCalledWith(highRiskPlan, true));
    await user.click(within(preview).getByRole("button", { name: /再次确认并执行整组迁移/ }));

    const receipt = await screen.findByRole("status", {
      name: /1\/1 项安排已更新并通过数据库验证/,
    });
    expect(receipt).toHaveFocus();
    expect(mocks.finishExecute).toHaveBeenCalledWith(highRiskPlan, true, "execute-stage-2");
    await user.click(within(receipt).getByRole("button", { name: "撤销整个迁移" }));
    await user.click(await within(receipt).findByRole("button", { name: /再次确认撤销整组迁移/ }));
    await waitFor(() => expect(mocks.finishUndo).toHaveBeenCalled());
  });

  it("does not turn a lost first-stage response into a one-click recovery write", async () => {
    const user = userEvent.setup();
    mocks.beginExecute.mockRejectedValueOnce(new ApiError("第一阶段响应中断", { status: 0 }));
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    const preview = await screen.findByRole("region", {
      name: "确认前预览，不会写入真实安排",
    });
    await user.click(within(preview).getByRole("checkbox", { name: /仍要覆盖写入/ }));
    await user.click(within(preview).getByRole("button", { name: "确认迁移" }));

    expect(await screen.findByText("第一阶段响应中断")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新生成迁移预览" }));

    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(2));
    expect(mocks.finishExecute).not.toHaveBeenCalled();
    expect(mocks.resumeExecute).not.toHaveBeenCalled();
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("executes an ordinary plan after exactly one user confirmation", async () => {
    const user = userEvent.setup();
    mocks.preview.mockResolvedValue(ordinaryPlan);
    mocks.plan.mockResolvedValue({ ...ordinaryPlan, status: "verified", version: 2 });
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    await user.click(await screen.findByRole("button", { name: "确认迁移" }));

    await waitFor(() => expect(mocks.execute).toHaveBeenCalledTimes(1));
    expect(mocks.execute).toHaveBeenCalledWith(ordinaryPlan, false);
    expect(screen.queryByRole("button", { name: /再次确认并执行/ })).not.toBeInTheDocument();
  });

  it("never presents a failed database verification as success and still allows safe undo", async () => {
    const user = userEvent.setup();
    const failedItem = {
      ...migrationItem,
      verification: {
        verified: false,
        reason: "fresh database query returned the old start_at",
        expected: "2026-07-18T14:00:00+08:00",
        actual: "2026-07-18T09:00:00+08:00",
      },
    };
    const failedReceipt = {
      ...executeReceipt,
      status: "verification_failed",
      verified_count: 0,
      all_verified: false,
      items: [failedItem],
    };
    mocks.preview.mockResolvedValue(ordinaryPlan);
    mocks.execute.mockResolvedValue(failedReceipt);
    mocks.plan.mockResolvedValue({
      ...ordinaryPlan,
      status: "verification_failed",
      version: 2,
    });
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    await user.click(await screen.findByRole("button", { name: "确认迁移" }));

    const receipt = await screen.findByRole("status", { name: /执行后数据库复验未全部通过/ });
    expect(within(receipt).queryByText(/已更新并通过数据库验证/)).not.toBeInTheDocument();
    await user.click(within(receipt).getByText("查看技术验证详情"));
    expect(within(receipt).getAllByText(/fresh database query returned/)).toHaveLength(2);
    expect(within(receipt).getByText(/这不是成功回执/)).toBeInTheDocument();
    await user.click(within(receipt).getByRole("button", { name: "继续数据库验证" }));
    await waitFor(() => expect(mocks.resumeExecute).toHaveBeenCalledTimes(1));
    await user.click(within(receipt).getByRole("button", { name: "撤销整个迁移" }));
    await user.click(await within(receipt).findByRole("button", { name: /再次确认撤销整组迁移/ }));
    await waitFor(() => expect(mocks.finishUndo).toHaveBeenCalledTimes(1));
  });

  it("offers a clear retry when detail loading fails", async () => {
    const user = userEvent.setup();
    mocks.changeSet
      .mockRejectedValueOnce(new ApiError("版本差异暂时不可用", { status: 503 }))
      .mockResolvedValueOnce(changePayload);
    render(<RadarDetailPage />);

    expect(await screen.findByText("版本差异暂时不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试加载" }));
    expect(await screen.findByText("v1 → v2 结构化变化")).toBeInTheDocument();
    expect(mocks.changeSet).toHaveBeenCalledTimes(2);
  });

  it("offers to regenerate a preview after an execution error", async () => {
    const user = userEvent.setup();
    mocks.preview.mockResolvedValue(ordinaryPlan);
    mocks.execute.mockRejectedValueOnce(new ApiError("计划版本已过期", { status: 409 }));
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    await user.click(await screen.findByRole("button", { name: "确认迁移" }));
    expect(await screen.findByText("计划版本已过期")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新生成迁移预览" }));
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(2));
  });

  it("retries post-write verification after a lost response without creating a new plan", async () => {
    const user = userEvent.setup();
    mocks.preview.mockResolvedValue(ordinaryPlan);
    mocks.execute.mockRejectedValueOnce(new ApiError("执行响应中断", { status: 0 }));
    mocks.resumeExecute.mockResolvedValueOnce(executeReceipt);
    mocks.plan.mockResolvedValue({ ...ordinaryPlan, status: "verified", version: 2 });
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    await user.click(await screen.findByRole("button", { name: "确认迁移" }));
    expect(await screen.findByText("执行响应中断")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续执行后数据库验证" }));

    expect(
      await screen.findByRole("status", { name: /1\/1 项安排已更新并通过数据库验证/ }),
    ).toBeInTheDocument();
    expect(mocks.execute).toHaveBeenCalledTimes(1);
    expect(mocks.receipt).toHaveBeenCalledWith(ordinaryPlan.id, "execute");
    expect(mocks.resumeExecute).not.toHaveBeenCalled();
    expect(mocks.preview).toHaveBeenCalledTimes(1);
  });

  it("recovers a lost undo response without starting another business mutation", async () => {
    const user = userEvent.setup();
    const verifiedPlan = { ...ordinaryPlan, status: "verified", version: 2 };
    const undonePlan = { ...ordinaryPlan, status: "undone", version: 3 };
    mocks.preview.mockResolvedValue(ordinaryPlan);
    mocks.plan
      .mockResolvedValueOnce(verifiedPlan)
      .mockResolvedValueOnce(verifiedPlan)
      .mockResolvedValueOnce(undonePlan);
    mocks.finishUndo.mockRejectedValueOnce(new ApiError("撤销响应中断", { status: 0 }));
    mocks.receipt.mockResolvedValue(undoReceipt);
    render(<RadarDetailPage />);

    await screen.findByText("v1 → v2 结构化变化");
    await user.click(screen.getByRole("button", { name: /生成迁移预览/ }));
    await user.click(await screen.findByRole("button", { name: "确认迁移" }));
    const receipt = await screen.findByRole("status", {
      name: /1\/1 项安排已更新并通过数据库验证/,
    });
    await user.click(within(receipt).getByRole("button", { name: "撤销整个迁移" }));
    await user.click(await within(receipt).findByRole("button", { name: /再次确认撤销整组迁移/ }));

    expect(await screen.findByText("撤销响应中断")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试整组撤销" }));

    await waitFor(() => expect(mocks.receipt).toHaveBeenCalledWith(ordinaryPlan.id, "undo"));
    expect(mocks.beginUndo).toHaveBeenCalledTimes(1);
    expect(mocks.finishUndo).toHaveBeenCalledTimes(1);
    expect(mocks.resumeUndo).not.toHaveBeenCalled();
  });

  it("restores an applied plan after reload and resumes only its verification", async () => {
    const user = userEvent.setup();
    const appliedPlan = { ...ordinaryPlan, status: "applied", version: 2 };
    mocks.impacts.mockResolvedValue({
      items: [{ ...impactPayload, migration_plan_id: appliedPlan.id }],
      total: 1,
    });
    mocks.plan
      .mockResolvedValueOnce(appliedPlan)
      .mockResolvedValueOnce(appliedPlan)
      .mockResolvedValueOnce({ ...appliedPlan, status: "verified", version: 3 });

    render(<RadarDetailPage />);

    expect(await screen.findByText("业务写入已发生，但成功回执尚未确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续数据库验证" }));

    await waitFor(() => expect(mocks.resumeExecute).toHaveBeenCalledWith(appliedPlan, false));
    expect(mocks.preview).not.toHaveBeenCalled();
  });

  it("restores undo_applied and resumes symmetric undo verification", async () => {
    const user = userEvent.setup();
    const undoAppliedPlan = {
      ...ordinaryPlan,
      status: "undo_applied",
      version: 3,
      execute_receipt: { operation: "execute", status: "verified" },
    };
    mocks.impacts.mockResolvedValue({
      items: [{ ...impactPayload, migration_plan_id: undoAppliedPlan.id }],
      total: 1,
    });
    mocks.plan
      .mockResolvedValueOnce(undoAppliedPlan)
      .mockResolvedValueOnce(undoAppliedPlan)
      .mockResolvedValueOnce({ ...undoAppliedPlan, status: "undone", version: 4 });

    render(<RadarDetailPage />);

    expect(await screen.findByText("整组恢复已写入，但撤销回执尚未确认")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续撤销后数据库验证" }));

    await waitFor(() => expect(mocks.resumeUndo).toHaveBeenCalledWith(undoAppliedPlan));
    expect(mocks.preview).not.toHaveBeenCalled();
  });
});
