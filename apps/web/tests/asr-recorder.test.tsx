import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("checks the parent guard before starting or resetting a completed recording", () => {
    const start = vi.fn();
    const reset = vi.fn();
    const onStart = vi.fn(() => false);
    const onReset = vi.fn(() => false);
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["旧录音"],
        editableTranscript: "旧录音",
      },
      start,
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset,
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder onStart={onStart} onReset={onReset} />);
    fireEvent.click(screen.getByRole("button", { name: "重新开始录音" }));
    fireEvent.click(screen.getByRole("button", { name: "清空重录" }));

    expect(onStart).toHaveBeenCalledOnce();
    expect(onReset).toHaveBeenCalledOnce();
    expect(start).not.toHaveBeenCalled();
    expect(reset).not.toHaveBeenCalled();
  });

  it("runs start and reset only after their parent guards allow them", async () => {
    const start = vi.fn();
    const reset = vi.fn();
    const onStart = vi.fn(() => true);
    const onReset = vi.fn(() => true);
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["旧录音"],
        editableTranscript: "旧录音",
      },
      start,
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset,
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder onStart={onStart} onReset={onReset} />);
    fireEvent.click(screen.getByRole("button", { name: "重新开始录音" }));
    fireEvent.click(screen.getByRole("button", { name: "清空重录" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledOnce();
      expect(reset).toHaveBeenCalledOnce();
    });
    expect(onStart).toHaveBeenCalledOnce();
    expect(onReset).toHaveBeenCalledOnce();
  });

  it.each([false, true])(
    "keeps completed recording entry points disabled in compact=%s",
    (compact) => {
      const start = vi.fn();
      const reset = vi.fn();
      const onStart = vi.fn();
      const onReset = vi.fn();
      mocks.useAsr.mockReturnValue({
        state: {
          ...initialAsrState,
          phase: "completed",
          finalSegments: ["旧录音"],
          editableTranscript: "旧录音",
        },
        start,
        pause: vi.fn(),
        resume: vi.fn(),
        stop: vi.fn(),
        reset,
        editTranscript: vi.fn(),
      });

      render(<AsrRecorder compact={compact} disabled onStart={onStart} onReset={onReset} />);
      const restartButton = screen.getByRole("button", { name: "重新开始录音" });
      const resetButton = screen.getByRole("button", { name: "清空重录" });
      expect(restartButton).toBeDisabled();
      expect(resetButton).toBeDisabled();
      if (!compact) expect(screen.getByDisplayValue("旧录音")).toHaveAttribute("readonly");

      fireEvent.click(restartButton);
      fireEvent.click(resetButton);

      expect(onStart).not.toHaveBeenCalled();
      expect(onReset).not.toHaveBeenCalled();
      expect(start).not.toHaveBeenCalled();
      expect(reset).not.toHaveBeenCalled();
    },
  );

  it("hides ASR error retry while the recorder is disabled", () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "error",
        error: { message: "连接中断", code: "socket_closed", retryable: true },
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder disabled />);

    expect(screen.getByRole("button", { name: "开始录音" })).toBeDisabled();
    expect(
      within(screen.getByRole("alert")).queryByRole("button", { name: "重试" }),
    ).not.toBeInTheDocument();
  });

  it("guards both retry entry points before restarting after an ASR error", () => {
    const start = vi.fn();
    const onStart = vi.fn(() => false);
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "error",
        error: { message: "连接中断", code: "socket_closed", retryable: true },
      },
      start,
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    render(<AsrRecorder onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "重试" }));

    expect(onStart).toHaveBeenCalledTimes(2);
    expect(start).not.toHaveBeenCalled();
  });

  it("reports activity from permission request through finalization", () => {
    const onActiveChange = vi.fn();
    const controls = {
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    };
    const setPhase = (phase: typeof initialAsrState.phase) => {
      mocks.useAsr.mockReturnValue({
        state: { ...initialAsrState, phase },
        ...controls,
      });
    };

    setPhase("idle");
    const { rerender } = render(<AsrRecorder onActiveChange={onActiveChange} />);

    for (const phase of [
      "requesting_permission",
      "connecting",
      "recording",
      "paused",
      "finalizing",
    ] as const) {
      setPhase(phase);
      rerender(<AsrRecorder onActiveChange={onActiveChange} />);
    }

    setPhase("completed");
    rerender(<AsrRecorder onActiveChange={onActiveChange} />);

    expect(onActiveChange.mock.calls.map(([active]) => active)).toEqual([false, true, false]);
  });
});
