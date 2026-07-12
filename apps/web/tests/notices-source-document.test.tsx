import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NoticesPage from "@/app/notices/page";
import { useAssistantStore } from "@/stores/assistant-store";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  listDocuments: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    documents: { list: mocks.listDocuments, upload: vi.fn() },
    knowledge: { ask: mocks.ask, search: vi.fn() },
  },
}));

describe("NoticesPage source document handoff", () => {
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
});
