import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NoticesPage from "@/app/notices/page";
import { useAssistantStore } from "@/stores/assistant-store";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  listDocuments: vi.fn(),
  ask: vi.fn(),
  search: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    documents: { list: mocks.listDocuments, upload: vi.fn() },
    knowledge: { ask: mocks.ask, search: mocks.search },
  },
}));

describe("NoticesPage source document handoff", () => {
  afterEach(cleanup);
  beforeEach(() => {
    useAssistantStore.getState().reset();
    mocks.push.mockReset();
    mocks.listDocuments.mockReset().mockResolvedValue({ items: [], total: 0 });
    mocks.ask.mockReset().mockResolvedValue({
      answer: "考试安排见原文。",
      sufficient: true,
      evidence: [
        {
          document_id: "doc-1",
          chunk_id: "chunk-1",
          content: "机器学习考试时间为 7 月 18 日上午九点。",
          page: null,
          similarity: 0.92,
          document_title: "考试通知",
          publish_date: "2026-07-01",
        },
      ],
    });
    mocks.search.mockReset().mockResolvedValue({
      evidence: [],
      version_conflicts: [],
      applicability_conflicts: [],
    });
  });

  it("stores the evidence document id before navigating to voice", async () => {
    render(<NoticesPage />);
    await waitFor(() => expect(mocks.listDocuments).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("输入校园通知问题"), {
      target: { value: "考试什么时候？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "基于证据回答" }));
    fireEvent.click(await screen.findByRole("button", { name: "转为待办草稿" }));

    expect(useAssistantStore.getState().sourceDocumentId).toBe("doc-1");
    expect(useAssistantStore.getState().transcript).toContain("考试通知");
    expect(mocks.push).toHaveBeenCalledWith("/voice");
  });

  it("shows the empty result state after a successful original-text search", async () => {
    render(<NoticesPage />);
    await waitFor(() => expect(mocks.listDocuments).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "原文检索" }));
    fireEvent.change(screen.getByLabelText("输入检索关键词"), {
      target: { value: "不存在的通知关键词" },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索原文" }));

    await waitFor(() => expect(mocks.search).toHaveBeenCalled());
    expect(await screen.findByRole("status")).toHaveTextContent("没有找到相关原文");

    fireEvent.click(screen.getByRole("tab", { name: "证据问答" }));
    expect(screen.queryByText("没有找到相关原文")).not.toBeInTheDocument();
  });

  it("ignores an original-text response after switching modes", async () => {
    let resolveSearch!: (value: {
      evidence: [];
      version_conflicts: [];
      applicability_conflicts: [];
    }) => void;
    mocks.search.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSearch = resolve;
      }),
    );
    render(<NoticesPage />);
    await waitFor(() => expect(mocks.listDocuments).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "原文检索" }));
    fireEvent.change(screen.getByLabelText("输入检索关键词"), {
      target: { value: "迟到的检索" },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索原文" }));
    await waitFor(() => expect(mocks.search).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "证据问答" }));

    await act(async () => {
      resolveSearch({
        evidence: [],
        version_conflicts: [],
        applicability_conflicts: [],
      });
    });

    expect(screen.getByRole("tab", { name: "证据问答" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("没有找到相关原文")).not.toBeInTheDocument();
  });
});
