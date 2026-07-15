import type { AsrServerMessage, AsrTranscriptReference } from "@campusvoice/shared-types";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VoicePage from "@/app/voice/page";
import { useAsr } from "@/hooks/use-asr";
import { useAssistantStore } from "@/stores/assistant-store";

interface RecorderHandlers {
  onChunk: (chunk: ArrayBuffer) => void;
  onLevel: (level: number) => void;
}

interface ClientHandlers {
  onMessage: (message: AsrServerMessage) => void;
  onClose: (expected: boolean) => void;
  onError: (message: string) => void;
}

interface RecorderProps {
  onTranscriptChange?: (text: string, confidence: number | null) => void;
  onSourceChange?: (source: AsrTranscriptReference) => void;
  onReset?: () => void;
}

const mocks = vi.hoisted(() => ({
  recorderStart: vi.fn(),
  recorderPause: vi.fn(),
  recorderResume: vi.fn(),
  recorderStop: vi.fn(),
  recorderHandlers: null as RecorderHandlers | null,
  clientConstructed: vi.fn(),
  clientConnect: vi.fn(),
  clientSendAudio: vi.fn(),
  clientPause: vi.fn(),
  clientResume: vi.fn(),
  clientStop: vi.fn(),
  clientClose: vi.fn(),
  clientHandlers: null as ClientHandlers | null,
  clientOptions: null as { ticket: string; hotwords?: string[] } | null,
  websocketTicket: vi.fn(),
  listHotwords: vi.fn(),
  getSettings: vi.fn(),
  previewCorrection: vi.fn(),
  decideCorrection: vi.fn(),
  parseIntent: vi.fn(),
  prepareAction: vi.fn(),
  executeAction: vi.fn(),
  confirmAction: vi.fn(),
  cancelAction: vi.fn(),
  undoAction: vi.fn(),
  askKnowledge: vi.fn(),
  listEvents: vi.fn(),
  listActionLogs: vi.fn(),
}));

vi.mock("@/lib/asr/audio-recorder", () => ({
  PcmAudioRecorder: class MockPcmAudioRecorder {
    start(handlers: RecorderHandlers) {
      mocks.recorderHandlers = handlers;
      return mocks.recorderStart(handlers);
    }

    pause() {
      return mocks.recorderPause();
    }

    resume() {
      return mocks.recorderResume();
    }

    stop() {
      return mocks.recorderStop();
    }
  },
}));

vi.mock("@/lib/asr/asr-client", () => ({
  AsrWebSocketClient: class MockAsrWebSocketClient {
    constructor(handlers: ClientHandlers, options: { ticket: string; hotwords?: string[] }) {
      mocks.clientHandlers = handlers;
      mocks.clientOptions = options;
      mocks.clientConstructed(handlers, options);
    }

    connect() {
      return mocks.clientConnect();
    }

    sendAudio(chunk: ArrayBuffer) {
      mocks.clientSendAudio(chunk);
    }

    pause() {
      mocks.clientPause();
    }

    resume() {
      mocks.clientResume();
    }

    stop() {
      mocks.clientStop();
    }

    close() {
      mocks.clientClose();
    }
  },
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: class ApiError extends Error {
    userMessage = this.message;
  },
  api: {
    auth: { websocketTicket: mocks.websocketTicket },
    hotwords: { list: mocks.listHotwords },
    settings: { get: mocks.getSettings },
    correction: { preview: mocks.previewCorrection, decide: mocks.decideCorrection },
    intent: { parse: mocks.parseIntent },
    actions: {
      prepare: mocks.prepareAction,
      execute: mocks.executeAction,
      confirm: mocks.confirmAction,
      cancel: mocks.cancelAction,
      undo: mocks.undoAction,
    },
    knowledge: { ask: mocks.askKnowledge },
    events: { list: mocks.listEvents },
    actionLogs: { list: mocks.listActionLogs },
  },
}));

vi.mock("@/components/voice/asr-recorder", () => ({
  AsrRecorder: ({ onTranscriptChange, onSourceChange, onReset }: RecorderProps) => (
    <div>
      <button
        type="button"
        onClick={() => {
          onSourceChange?.({
            sessionId: "voice-session-1",
            transcriptionId: "transcription-1",
            originalText: "创建复习机器学西待办",
          });
          onTranscriptChange?.("创建复习机器学西待办", 0.64);
        }}
      >
        提交语音转写
      </button>
      <button type="button" onClick={onReset}>
        重置录音
      </button>
    </div>
  ),
}));

vi.mock("@/components/layout/page-header", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: ReactNode }) => (
    <header>
      <h1>{title}</h1>
      {actions}
    </header>
  ),
}));

vi.mock("@/components/actions/confirmation-card", () => ({
  ConfirmationCard: ({ action }: { action: { id: string } }) => (
    <div data-testid="pending-action">{action.id}</div>
  ),
}));

vi.mock("@/components/actions/execution-result", () => ({
  ExecutionResult: ({ result }: { result: { message: string } }) => <div>{result.message}</div>,
}));

vi.mock("@/components/ui/error-state", () => ({
  ErrorState: ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
    <div role="alert">
      {message}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </div>
  ),
}));

vi.mock("@/components/voice/clarification-card", () => ({
  ClarificationCard: () => <div>需要补充信息</div>,
}));

vi.mock("@/components/voice/correction-diff", () => ({
  CorrectionDiff: () => <div>已生成纠错预览</div>,
}));

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function resetMock(mock: ReturnType<typeof vi.fn>) {
  mock.mockReset();
}

beforeEach(() => {
  useAssistantStore.getState().reset();
  mocks.recorderHandlers = null;
  mocks.clientHandlers = null;
  mocks.clientOptions = null;
  [
    mocks.recorderStart,
    mocks.recorderPause,
    mocks.recorderResume,
    mocks.recorderStop,
    mocks.clientConstructed,
    mocks.clientConnect,
    mocks.clientSendAudio,
    mocks.clientPause,
    mocks.clientResume,
    mocks.clientStop,
    mocks.clientClose,
    mocks.websocketTicket,
    mocks.listHotwords,
    mocks.getSettings,
    mocks.previewCorrection,
    mocks.decideCorrection,
    mocks.parseIntent,
    mocks.prepareAction,
    mocks.executeAction,
    mocks.confirmAction,
    mocks.cancelAction,
    mocks.undoAction,
    mocks.askKnowledge,
    mocks.listEvents,
    mocks.listActionLogs,
  ].forEach(resetMock);

  mocks.recorderStart.mockResolvedValue(undefined);
  mocks.recorderPause.mockResolvedValue(undefined);
  mocks.recorderResume.mockResolvedValue(undefined);
  mocks.recorderStop.mockResolvedValue(undefined);
  mocks.websocketTicket.mockResolvedValue({
    ticket: "one-time-ticket",
    expires_at: "2026-07-12T12:01:00Z",
  });
  mocks.listHotwords.mockResolvedValue({
    items: [{ value: "机器学习", category: "custom" }],
    total: 1,
  });
  mocks.getSettings.mockResolvedValue({
    major: "人工智能",
    grade: "2024",
    current_courses: [{ code: "AI301", name: "机器学习", teacher: "张老师" }],
    teacher_names: ["李老师"],
    default_reminder_minutes: 30,
    timezone: "Asia/Shanghai",
    asr_provider: "funasr",
    asr_model: "paraformer-zh-streaming",
    asr_device: "cpu",
  });
  mocks.clientConnect.mockImplementation(async () => {
    mocks.clientHandlers?.onMessage({ type: "ready", session_id: "voice-session-1" });
  });

  mocks.previewCorrection.mockResolvedValue({
    record_id: "correction-1",
    original_text: "创建复习机器学西待办",
    corrected_text: "创建复习机器学习待办",
    changes: [],
    requires_user_input: false,
  });
  mocks.parseIntent.mockResolvedValue({
    intent: "create_task",
    confidence: 0.96,
    slots: { title: "复习机器学习" },
    missing_fields: [],
    ambiguities: [],
    source_text: "创建复习机器学习待办",
    requires_confirmation: true,
    conversation_id: "conversation-1",
  });
  mocks.prepareAction.mockResolvedValue({
    id: "action-from-voice-1",
    action: "create_task",
    risk_level: "medium",
    risk_reasons: ["writes_data"],
    payload: { title: "复习机器学习", source_type: "voice" },
    status: "awaiting_confirmation",
    confirmation_count: 0,
    confirmations_required: 1,
  });
});

describe("useAsr orchestration", () => {
  it("waits for microphone permission before fetching a ticket and drives the recording session", async () => {
    const permission = deferred();
    mocks.recorderStart.mockImplementation(async () => permission.promise);
    const { result, unmount } = renderHook(() => useAsr());

    let startPromise: Promise<void> | undefined;
    act(() => {
      startPromise = result.current.start();
    });

    await waitFor(() => expect(result.current.state.phase).toBe("requesting_permission"));
    expect(mocks.recorderStart).toHaveBeenCalledOnce();
    expect(mocks.websocketTicket).not.toHaveBeenCalled();

    await act(async () => {
      permission.resolve();
      await startPromise;
    });

    expect(mocks.websocketTicket).toHaveBeenCalledOnce();
    expect(mocks.clientOptions).toEqual({
      ticket: "one-time-ticket",
      hotwords: ["机器学习", "AI301", "张老师", "李老师"],
    });
    expect(result.current.state).toMatchObject({
      phase: "recording",
      sessionId: "voice-session-1",
    });

    const audio = new ArrayBuffer(8);
    act(() => {
      mocks.recorderHandlers?.onChunk(audio);
      mocks.recorderHandlers?.onLevel(1.4);
      mocks.clientHandlers?.onMessage({ type: "speech_start" });
      mocks.clientHandlers?.onMessage({
        type: "interim",
        text: "复习机器",
        confidence: 0.71,
        latency_ms: 80,
      });
      mocks.clientHandlers?.onMessage({
        type: "final",
        text: "复习机器学习",
        confidence: 0.93,
        latency_ms: 145,
        transcription_id: "transcription-1",
      });
    });

    expect(mocks.clientSendAudio).toHaveBeenCalledWith(audio);
    expect(result.current.state).toMatchObject({
      phase: "recording",
      editableTranscript: "复习机器学习",
      confidence: 0.93,
      latencyMs: 145,
      transcriptionId: "transcription-1",
      level: 1,
      speechActive: true,
    });

    await act(async () => result.current.pause());
    expect(result.current.state.phase).toBe("paused");
    expect(mocks.recorderPause).toHaveBeenCalledOnce();
    expect(mocks.clientPause).toHaveBeenCalledOnce();

    await act(async () => result.current.resume());
    expect(result.current.state.phase).toBe("recording");
    expect(mocks.recorderResume).toHaveBeenCalledOnce();
    expect(mocks.clientResume).toHaveBeenCalledOnce();

    await act(async () => result.current.stop());
    expect(result.current.state.phase).toBe("finalizing");
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    expect(mocks.clientStop).toHaveBeenCalledOnce();

    act(() => mocks.clientHandlers?.onMessage({ type: "completed" }));
    expect(result.current.state.phase).toBe("completed");
    expect(mocks.clientClose).toHaveBeenCalledOnce();
    unmount();
  });

  it("keeps only the latest start when two attempts race during cleanup", async () => {
    const { result, unmount } = renderHook(() => useAsr());

    let firstStart: Promise<void> | undefined;
    let secondStart: Promise<void> | undefined;
    act(() => {
      firstStart = result.current.start();
      secondStart = result.current.start();
    });

    await act(async () => Promise.all([firstStart, secondStart]));

    expect(mocks.recorderStart).toHaveBeenCalledOnce();
    expect(mocks.websocketTicket).toHaveBeenCalledOnce();
    expect(mocks.clientConstructed).toHaveBeenCalledOnce();
    expect(result.current.state.phase).toBe("recording");
    unmount();
  });

  it("surfaces a denied microphone permission without consuming a WebSocket ticket", async () => {
    mocks.recorderStart.mockRejectedValue(new DOMException("Permission denied", "NotAllowedError"));
    const { result, unmount } = renderHook(() => useAsr());

    await act(async () => result.current.start());

    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toMatchObject({
      code: "microphone_denied",
      retryable: true,
    });
    expect(result.current.state.error?.message).toContain("麦克风权限被拒绝");
    expect(mocks.websocketTicket).not.toHaveBeenCalled();
    expect(mocks.clientConstructed).not.toHaveBeenCalled();
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    unmount();
  });

  it("releases a recorder that finishes starting after the component unmounts", async () => {
    const permission = deferred();
    mocks.recorderStart.mockImplementation(async () => permission.promise);
    const { result, unmount } = renderHook(() => useAsr());

    let startPromise: Promise<void> | undefined;
    act(() => {
      startPromise = result.current.start();
    });

    await waitFor(() => expect(result.current.state.phase).toBe("requesting_permission"));
    unmount();
    await waitFor(() => expect(mocks.recorderStop).toHaveBeenCalledOnce());

    await act(async () => {
      permission.resolve();
      await startPromise;
    });

    expect(mocks.recorderStop).toHaveBeenCalledTimes(2);
    expect(mocks.websocketTicket).not.toHaveBeenCalled();
    expect(mocks.clientConstructed).not.toHaveBeenCalled();
  });

  it("ignores recorder and socket callbacks from an earlier lifecycle", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());
    const firstRecorderHandlers = mocks.recorderHandlers;
    const firstClientHandlers = mocks.clientHandlers;

    await act(async () => result.current.reset());
    await act(async () => result.current.start());
    const closeCountBeforeStaleCallbacks = mocks.clientClose.mock.calls.length;

    act(() => {
      firstRecorderHandlers?.onChunk(new ArrayBuffer(8));
      firstRecorderHandlers?.onLevel(0.91);
      firstClientHandlers?.onMessage({ type: "final", text: "过期转写" });
      firstClientHandlers?.onMessage({ type: "completed" });
      firstClientHandlers?.onError("过期错误");
      firstClientHandlers?.onClose(false);
    });

    expect(mocks.clientSendAudio).not.toHaveBeenCalled();
    expect(mocks.clientClose).toHaveBeenCalledTimes(closeCountBeforeStaleCallbacks);
    expect(result.current.state).toMatchObject({
      phase: "recording",
      editableTranscript: "",
      level: 0,
      error: null,
    });
    unmount();
  });

  it("marks an unexpected socket close as an error and releases both resources on unmount", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());
    expect(result.current.state.phase).toBe("recording");

    act(() => mocks.clientHandlers?.onClose(false));
    expect(result.current.state).toMatchObject({
      phase: "error",
      error: { code: "socket_closed", retryable: true },
    });

    unmount();
    await waitFor(() => {
      expect(mocks.clientClose).not.toHaveBeenCalled();
      expect(mocks.recorderStop).toHaveBeenCalledOnce();
    });
  });
});

describe("VoicePage ASR lineage", () => {
  it("marks a text demonstration action as manual input", async () => {
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-manual-1",
      original_text: "创建复习机器学习待办",
      corrected_text: "创建复习机器学习待办",
      changes: [],
      requires_user_input: false,
    });
    useAssistantStore.getState().setTranscript("创建复习机器学习待办");
    useAssistantStore.getState().setInputMode("text_demo");
    const { unmount } = render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));

    await waitFor(() => expect(mocks.prepareAction).toHaveBeenCalledOnce());
    expect(mocks.prepareAction).toHaveBeenCalledWith(
      expect.objectContaining({
        payload: expect.objectContaining({
          title: "复习机器学习",
          source_type: "manual",
        }),
        voice_session_id: undefined,
        transcription_id: undefined,
      }),
    );
    unmount();
  });

  it("prepares an action with the real voice lineage and clears the recording workflow", async () => {
    useAssistantStore.getState().setSourceDocumentId("stale-document-1");
    render(<VoicePage />);

    expect(screen.getByRole("heading", { name: "说出安排，确认后再执行" })).toBeInTheDocument();
    expect(screen.getByText("转写 → 理解 → 风险 → 确认 → 验证")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "提交语音转写" }));
    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "创建复习机器学西待办",
      sourceDocumentId: null,
    });

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));

    await waitFor(() => expect(mocks.prepareAction).toHaveBeenCalledOnce());
    expect(mocks.previewCorrection).toHaveBeenCalledWith(
      "创建复习机器学西待办",
      0.64,
      "transcription-1",
    );
    expect(mocks.parseIntent).toHaveBeenCalledWith(
      "创建复习机器学习待办",
      undefined,
      0.64,
      undefined,
    );
    expect(mocks.prepareAction).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "create_task",
        payload: expect.objectContaining({
          title: "复习机器学习",
          source_type: "voice",
        }),
        asr_confidence: 0.64,
        source_text: "创建复习机器学西待办",
        corrected_text: "创建复习机器学习待办",
        voice_session_id: "voice-session-1",
        transcription_id: "transcription-1",
      }),
    );
    expect(await screen.findByTestId("pending-action")).toHaveTextContent("action-from-voice-1");
    expect(useAssistantStore.getState()).toMatchObject({
      workflowStatus: "idle",
      pendingAction: expect.objectContaining({ id: "action-from-voice-1" }),
    });

    fireEvent.click(screen.getByRole("button", { name: "重置录音" }));
    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "",
      sourceDocumentId: null,
      pendingAction: null,
      workflowStatus: "idle",
    });
    expect(screen.queryByLabelText("待解析的转写文字")).not.toBeInTheDocument();
  });
});
