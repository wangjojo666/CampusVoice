import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AsrRecorder } from "@/components/voice/asr-recorder";
import { initialAsrState } from "@/lib/asr/machine";

const mocks = vi.hoisted(() => ({ useAsr: vi.fn() }));

vi.mock("@/hooks/use-asr", () => ({ useAsr: mocks.useAsr }));

afterEach(() => cleanup());

describe("AsrRecorder", () => {
  it("forwards the real ASR confidence with the editable transcript", async () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["复习机器学习"],
        editableTranscript: "复习机器学习",
        confidence: 0.42,
        latencyMs: 180,
        sessionId: "voice-1",
        transcriptionId: "trn-1",
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });
    const onTranscriptChange = vi.fn();
    const onSourceChange = vi.fn();

    render(<AsrRecorder onTranscriptChange={onTranscriptChange} onSourceChange={onSourceChange} />);

    await waitFor(() => expect(onTranscriptChange).toHaveBeenCalledWith("复习机器学习", 0.42));
    expect(onSourceChange).toHaveBeenCalledWith({
      sessionId: "voice-1",
      transcriptionId: "trn-1",
      originalText: "复习机器学习",
    });
  });

  it("shows the completed transcript, confidence, and recognition latency in compact mode", () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["周五上午九点有机器学习考试"],
        editableTranscript: "周五上午九点有机器学习考试",
        confidence: 0.94,
        latencyMs: 128,
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder compact />);

    expect(screen.getByText("转写已完成")).toBeInTheDocument();
    expect(screen.getByText("最终转写")).toBeInTheDocument();
    expect(screen.getByText("周五上午九点有机器学习考试")).toBeInTheDocument();
    expect(screen.getByText("置信度：94%")).toBeInTheDocument();
    expect(screen.getByText("识别延迟：128 ms")).toBeInTheDocument();
  });

  it.each([
    ["麦克风权限被拒绝。请在浏览器设置中允许麦克风。", "microphone_denied", true],
    ["无法连接语音识别服务，请确认后端已启动。", "socket_closed", true],
    ["语音识别模型未配置，请联系管理员完成配置。", "model_not_configured", false],
  ])("keeps the real ASR error visible: %s", (message, code, retryable) => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "error",
        error: { message, code, retryable },
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder compact />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(message);
    if (retryable) expect(within(alert).getByRole("button", { name: "重试" })).toBeInTheDocument();
    else expect(within(alert).queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("does not notify the same transcript again when a parent callback identity changes", async () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["复习机器学习"],
        editableTranscript: "复习机器学习",
        confidence: 0.9,
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    function ParentWithInlineCallback() {
      const [notifications, setNotifications] = useState(0);
      return (
        <>
          <span data-testid="notifications">{notifications}</span>
          <AsrRecorder onTranscriptChange={() => setNotifications((count) => count + 1)} />
        </>
      );
    }

    render(<ParentWithInlineCallback />);
    await waitFor(() => expect(screen.getByTestId("notifications")).toHaveTextContent("1"));
  });
});
