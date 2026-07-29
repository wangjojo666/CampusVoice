import type { AsrServerMessage, AsrTranscriptReference } from "@campusvoice/shared-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VoicePage from "@/app/voice/page";
import { useAsr } from "@/hooks/use-asr";
import { DEFAULT_USER_SETTINGS, setCurrentUserSettings } from "@/lib/user-settings";
import { useAssistantStore } from "@/stores/assistant-store";

interface RecorderHandlers {
  onChunk: (chunk: ArrayBuffer) => void;
  onLevel: (level: number) => void;
}

interface ClientHandlers {
  onMessage: (message: AsrServerMessage) => void;
  onClose: (info: { stopRequested: boolean; code: number; wasClean: boolean }) => void;
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
  ExecutionResult: ({
    result,
    onRetry,
    onUndo,
    undoBusy,
  }: {
    result: { message: string; retryable?: boolean };
    onRetry?: () => void;
    onUndo?: () => void;
    undoBusy?: boolean;
  }) => (
    <div>
      {result.message}
      {result.retryable && onRetry ? (
        <button type="button" onClick={onRetry}>
          重试一次
        </button>
      ) : null}
      {onUndo ? (
        <button type="button" disabled={undoBusy} onClick={onUndo}>
          {undoBusy ? "正在撤销" : "撤销本次操作"}
        </button>
      ) : null}
    </div>
  ),
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

afterEach(() => {
  cleanup();
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
});

beforeEach(() => {
  setCurrentUserSettings(DEFAULT_USER_SETTINGS);
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

    await act(async () => {
      mocks.clientHandlers?.onClose({ stopRequested: true, code: 1000, wasClean: true });
    });
    expect(result.current.state.phase).toBe("completed");
    expect(mocks.clientClose).not.toHaveBeenCalled();
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
      firstClientHandlers?.onError("过期错误");
      firstClientHandlers?.onClose({ stopRequested: false, code: 1006, wasClean: false });
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

  it("marks an unexpected socket close as an error and releases the recorder immediately", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());
    expect(result.current.state.phase).toBe("recording");

    await act(async () => {
      mocks.clientHandlers?.onClose({ stopRequested: false, code: 1006, wasClean: false });
    });
    expect(result.current.state).toMatchObject({
      phase: "error",
      error: { code: "socket_closed", retryable: true },
    });

    expect(mocks.clientClose).not.toHaveBeenCalled();
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    unmount();
  });

  it("keeps recoverable server errors active and accepts the following final transcript", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());

    act(() => {
      mocks.clientHandlers?.onMessage({
        type: "error",
        code: "vad_fallback",
        message: "已切换到能量 VAD",
        recoverable: true,
      });
    });
    expect(result.current.state).toMatchObject({
      phase: "recording",
      error: { code: "vad_fallback", retryable: true },
    });
    expect(mocks.recorderStop).not.toHaveBeenCalled();
    expect(mocks.clientClose).not.toHaveBeenCalled();

    act(() => {
      mocks.clientHandlers?.onMessage({
        type: "final",
        text: "降级后仍可转写",
        transcription_id: "transcription-recovered",
      });
    });
    expect(result.current.state).toMatchObject({
      phase: "recording",
      editableTranscript: "降级后仍可转写",
      transcriptionId: "transcription-recovered",
      error: null,
    });
    unmount();
  });

  it("stops both resources immediately for a fatal server error", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());

    await act(async () => {
      mocks.clientHandlers?.onMessage({
        type: "error",
        code: "provider_disabled",
        message: "识别服务未启用",
        recoverable: false,
      });
    });

    expect(result.current.state).toMatchObject({
      phase: "error",
      error: { code: "provider_disabled", retryable: false },
    });
    expect(mocks.clientClose).toHaveBeenCalledOnce();
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    unmount();
  });

  it("stops both resources immediately for a transport error", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());

    await act(async () => {
      mocks.clientHandlers?.onError("网络连接失败");
    });

    expect(result.current.state.phase).toBe("error");
    expect(mocks.clientClose).toHaveBeenCalledOnce();
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    unmount();
  });

  it("does not complete when a requested stop ends with an abnormal close", async () => {
    const { result, unmount } = renderHook(() => useAsr());
    await act(async () => result.current.start());
    act(() => {
      mocks.clientHandlers?.onMessage({ type: "interim", text: "尚未最终确认" });
    });
    await act(async () => result.current.stop());

    await act(async () => {
      mocks.clientHandlers?.onClose({ stopRequested: true, code: 1006, wasClean: false });
    });

    expect(result.current.state).toMatchObject({
      phase: "error",
      editableTranscript: "尚未最终确认",
      error: { code: "socket_closed_during_finalize", retryable: true },
    });
    expect(mocks.recorderStop).toHaveBeenCalledOnce();
    unmount();
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

    expect(screen.getByRole("heading", { name: "一句话，把校园安排接住" })).toBeInTheDocument();
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

  it("uses the requested local date as the schedule query window", async () => {
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-schedule-1",
      original_text: "查询 2026-07-18 日程",
      corrected_text: "查询 2026-07-18 日程",
      changes: [],
      requires_user_input: false,
    });
    mocks.parseIntent.mockResolvedValueOnce({
      intent: "query_schedule",
      confidence: 0.99,
      slots: { date: "2026-07-18" },
      missing_fields: [],
      ambiguities: [],
      source_text: "查询 2026-07-18 日程",
      requires_confirmation: false,
      conversation_id: "conversation-schedule-1",
    });
    mocks.listEvents.mockResolvedValueOnce({ items: [], total: 0 });
    useAssistantStore.getState().setTranscript("查询 2026-07-18 日程");
    useAssistantStore.getState().setInputMode("text_demo");
    render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));

    await waitFor(() =>
      expect(mocks.listEvents).toHaveBeenCalledWith({
        start: "2026-07-17T16:00:00.000Z",
        end: "2026-07-18T16:00:00.000Z",
        limit: 8,
      }),
    );
    expect(await screen.findByText("当前没有日程记录。")).toBeInTheDocument();
  });

  it("binds undo only to the current executed action and blocks duplicate requests", async () => {
    const undoResponse = {
      success: true,
      action: "undo_create_task",
      record_id: "task-current",
      verified_fields: { deleted: true },
      side_effects: [],
      message: "当前操作已撤销并复验",
    };
    let resolveUndo!: (value: typeof undoResponse) => void;
    mocks.undoAction.mockImplementationOnce(
      () =>
        new Promise<typeof undoResponse>((resolve) => {
          resolveUndo = resolve;
        }),
    );
    const store = useAssistantStore.getState();
    store.setExecution({
      success: true,
      action: "create_task",
      record_id: "task-current",
      verified_fields: { title: true },
      side_effects: [],
      message: "当前操作已写入并复验",
    });
    store.setLastExecutedActionId("action-current");
    store.setWorkflowStatus("succeeded");
    const view = render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "撤销本次操作" }));
    const busyButton = screen.getByRole("button", { name: "正在撤销" });
    expect(busyButton).toBeDisabled();
    fireEvent.click(busyButton);

    expect(mocks.undoAction).toHaveBeenCalledTimes(1);
    expect(mocks.undoAction).toHaveBeenCalledWith("action-current");
    expect(mocks.listActionLogs).not.toHaveBeenCalled();

    view.unmount();
    render(<VoicePage />);
    const remountedBusyButton = screen.getByRole("button", { name: "正在撤销" });
    expect(remountedBusyButton).toBeDisabled();
    fireEvent.click(remountedBusyButton);
    expect(mocks.undoAction).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveUndo(undoResponse);
      await Promise.resolve();
    });

    expect(await screen.findByText("当前操作已撤销并复验")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "撤销本次操作" })).not.toBeInTheDocument(),
    );
    expect(useAssistantStore.getState().lastExecutedActionId).toBeNull();
  });
  it("retries a failed undo with the same current action id", async () => {
    const failedUndo = {
      success: false,
      action: "undo_create_task",
      record_id: "task-current",
      verified_fields: { deleted: false },
      side_effects: [],
      message: "撤销后的数据库复验失败",
      retryable: true,
    };
    const successfulUndo = {
      ...failedUndo,
      success: true,
      verified_fields: { deleted: true },
      message: "当前操作已撤销并复验",
      retryable: false,
    };
    mocks.undoAction.mockResolvedValueOnce(failedUndo).mockResolvedValueOnce(successfulUndo);
    const store = useAssistantStore.getState();
    store.setExecution({
      success: true,
      action: "create_task",
      record_id: "task-current",
      verified_fields: { title: true },
      side_effects: [],
      message: "当前操作已写入并复验",
    });
    store.setLastExecutedActionId("action-current");
    store.setWorkflowStatus("succeeded");
    const view = render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "撤销本次操作" }));

    expect(await screen.findByText("撤销后的数据库复验失败")).toBeInTheDocument();
    view.unmount();
    render(<VoicePage />);
    expect(screen.getByText("撤销后的数据库复验失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试一次" }));

    await waitFor(() => expect(mocks.undoAction).toHaveBeenCalledTimes(2));
    expect(mocks.undoAction).toHaveBeenNthCalledWith(1, "action-current");
    expect(mocks.undoAction).toHaveBeenNthCalledWith(2, "action-current");
    expect(await screen.findByText("当前操作已撤销并复验")).toBeInTheDocument();
    expect(useAssistantStore.getState().lastExecutedActionId).toBeNull();
  });
  it("rejects invalid calendar dates without querying another day", async () => {
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-invalid-date",
      original_text: "查询 2026-02-30 日程",
      corrected_text: "查询 2026-02-30 日程",
      changes: [],
      requires_user_input: false,
    });
    mocks.parseIntent.mockResolvedValueOnce({
      intent: "query_schedule",
      confidence: 0.99,
      slots: { date: "2026-02-30" },
      missing_fields: [],
      ambiguities: [],
      source_text: "查询 2026-02-30 日程",
      requires_confirmation: false,
      conversation_id: "conversation-invalid-date",
    });
    useAssistantStore.getState().setTranscript("查询 2026-02-30 日程");
    render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));

    expect(await screen.findByText("日程查询失败。")).toBeInTheDocument();
    expect(mocks.listEvents).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().scheduleResults).toBeNull();
  });

  it("does not refill a reset workflow from a stale schedule response", async () => {
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-stale-schedule",
      original_text: "查询 2026-07-18 日程",
      corrected_text: "查询 2026-07-18 日程",
      changes: [],
      requires_user_input: false,
    });
    mocks.parseIntent.mockResolvedValueOnce({
      intent: "query_schedule",
      confidence: 0.99,
      slots: { date: "2026-07-18" },
      missing_fields: [],
      ambiguities: [],
      source_text: "查询 2026-07-18 日程",
      requires_confirmation: false,
      conversation_id: "conversation-stale-schedule",
    });
    let resolveEvents!: (value: { items: []; total: number }) => void;
    mocks.listEvents.mockImplementationOnce(
      () =>
        new Promise<{ items: []; total: number }>((resolve) => {
          resolveEvents = resolve;
        }),
    );
    const store = useAssistantStore.getState();
    store.setTranscript("查询 2026-07-18 日程");
    store.setInputMode("text_demo");
    render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "新指令" }));

    await act(async () => {
      resolveEvents({ items: [], total: 0 });
      await Promise.resolve();
    });

    expect(useAssistantStore.getState()).toMatchObject({
      transcript: "",
      workflowStatus: "idle",
      activeOperationId: null,
      scheduleResults: null,
    });
    expect(screen.queryByText("当前没有日程记录。")).not.toBeInTheDocument();
  });

  it("does not let a stale undo response overwrite a reset workflow or newer action", async () => {
    const staleUndo = {
      success: true,
      action: "undo_create_task",
      record_id: "task-old",
      verified_fields: { deleted: true },
      side_effects: [],
      message: "旧操作已撤销",
    };
    let resolveUndo!: (value: typeof staleUndo) => void;
    mocks.undoAction.mockImplementationOnce(
      () =>
        new Promise<typeof staleUndo>((resolve) => {
          resolveUndo = resolve;
        }),
    );
    const store = useAssistantStore.getState();
    store.setExecution({
      success: true,
      action: "create_task",
      record_id: "task-old",
      verified_fields: { title: true },
      side_effects: [],
      message: "旧操作已写入",
    });
    store.setLastExecutedActionId("action-old");
    store.setWorkflowStatus("succeeded");
    render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "撤销本次操作" }));
    fireEvent.click(screen.getByRole("button", { name: "重置录音" }));
    act(() => {
      const current = useAssistantStore.getState();
      current.setActiveOperationId("operation-new");
      current.setExecution({
        success: true,
        action: "create_task",
        record_id: "task-new",
        verified_fields: { title: true },
        side_effects: [],
        message: "新操作已写入",
      });
      current.setLastExecutedActionId("action-new");
      current.setWorkflowStatus("succeeded");
    });

    await act(async () => {
      resolveUndo(staleUndo);
      await Promise.resolve();
    });

    expect(useAssistantStore.getState()).toMatchObject({
      activeOperationId: "operation-new",
      lastExecutedActionId: "action-new",
      execution: expect.objectContaining({ record_id: "task-new", message: "新操作已写入" }),
      undoRecoveryActionId: null,
    });
    expect(screen.queryByText("旧操作已撤销")).not.toBeInTheDocument();
  });

  it("keeps a transport-failed undo recoverable after unmount and remount", async () => {
    const successfulUndo = {
      success: true,
      action: "undo_create_task",
      record_id: "task-current",
      verified_fields: { deleted: true },
      side_effects: [],
      message: "当前操作已撤销并复验",
      retryable: false,
    };
    mocks.undoAction.mockRejectedValueOnce(new Error("network unavailable"));
    mocks.undoAction.mockResolvedValueOnce(successfulUndo);
    const store = useAssistantStore.getState();
    store.setTranscript("创建当前待办");
    store.setExecution({
      success: true,
      action: "create_task",
      record_id: "task-current",
      verified_fields: { title: true },
      side_effects: [],
      message: "当前操作已写入并复验",
    });
    store.setLastExecutedActionId("action-current");
    store.setWorkflowStatus("succeeded");
    const view = render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "撤销本次操作" }));
    expect(await screen.findByText("撤销失败，请重试。")).toBeInTheDocument();
    expect(useAssistantStore.getState().undoRecoveryActionId).toBe("action-current");

    view.unmount();
    render(<VoicePage />);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(mocks.undoAction).toHaveBeenCalledTimes(2));
    expect(mocks.undoAction).toHaveBeenNthCalledWith(1, "action-current");
    expect(mocks.undoAction).toHaveBeenNthCalledWith(2, "action-current");
    expect(mocks.previewCorrection).not.toHaveBeenCalled();
    expect(await screen.findByText("当前操作已撤销并复验")).toBeInTheDocument();
    expect(useAssistantStore.getState()).toMatchObject({
      lastExecutedActionId: null,
      undoRecoveryActionId: null,
      error: null,
    });
  });
  it("rejects a civil date that does not exist in the user's timezone", async () => {
    setCurrentUserSettings({ ...DEFAULT_USER_SETTINGS, timezone: "Pacific/Apia" });
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-missing-day",
      original_text: "查询 2011-12-30 日程",
      corrected_text: "查询 2011-12-30 日程",
      changes: [],
      requires_user_input: false,
    });
    mocks.parseIntent.mockResolvedValueOnce({
      intent: "query_schedule",
      confidence: 0.99,
      slots: { date: "2011-12-30" },
      missing_fields: [],
      ambiguities: [],
      source_text: "查询 2011-12-30 日程",
      requires_confirmation: false,
      conversation_id: "conversation-missing-day",
    });
    useAssistantStore.getState().setTranscript("查询 2011-12-30 日程");
    render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));

    expect(await screen.findByText("日程查询失败。")).toBeInTheDocument();
    expect(mocks.listEvents).not.toHaveBeenCalled();
  });

  it("finishes a schedule query in the shared store after unmount and remount", async () => {
    mocks.previewCorrection.mockResolvedValueOnce({
      record_id: "correction-remount-schedule",
      original_text: "查询 2026-07-18 日程",
      corrected_text: "查询 2026-07-18 日程",
      changes: [],
      requires_user_input: false,
    });
    mocks.parseIntent.mockResolvedValueOnce({
      intent: "query_schedule",
      confidence: 0.99,
      slots: { date: "2026-07-18" },
      missing_fields: [],
      ambiguities: [],
      source_text: "查询 2026-07-18 日程",
      requires_confirmation: false,
      conversation_id: "conversation-remount-schedule",
    });
    let resolveEvents!: (value: { items: []; total: number }) => void;
    mocks.listEvents.mockImplementationOnce(
      () =>
        new Promise<{ items: []; total: number }>((resolve) => {
          resolveEvents = resolve;
        }),
    );
    const store = useAssistantStore.getState();
    store.setTranscript("查询 2026-07-18 日程");
    const view = render(<VoicePage />);

    fireEvent.click(screen.getByRole("button", { name: "解析并检查" }));
    await waitFor(() => expect(mocks.listEvents).toHaveBeenCalledOnce());
    view.unmount();
    render(<VoicePage />);
    expect(useAssistantStore.getState().workflowStatus).toBe("executing");

    await act(async () => {
      resolveEvents({ items: [], total: 0 });
      await Promise.resolve();
    });

    expect(await screen.findByText("当前没有日程记录。")).toBeInTheDocument();
    expect(useAssistantStore.getState()).toMatchObject({
      workflowStatus: "succeeded",
      scheduleResults: [],
    });
  });

  it("does not offer analyze as a retry for a non-retryable undo failure", () => {
    const store = useAssistantStore.getState();
    store.setTranscript("创建当前待办");
    store.setExecution({
      success: false,
      action: "undo_create_task",
      record_id: "task-current",
      verified_fields: { deleted: false },
      side_effects: [],
      message: "当前撤销无法安全重试",
      retryable: false,
    });
    store.setLastExecutedActionId("action-current");
    store.setError("当前撤销无法安全重试");
    store.setWorkflowStatus("error");

    render(<VoicePage />);

    expect(screen.getByRole("alert")).toHaveTextContent("当前撤销无法安全重试");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试一次" })).not.toBeInTheDocument();
    expect(mocks.previewCorrection).not.toHaveBeenCalled();
  });
  it("clears a failed execution error when its exact pending action retry succeeds", async () => {
    mocks.executeAction.mockResolvedValueOnce({
      success: true,
      action: "create_task",
      record_id: "task-current",
      verified_fields: { title: true },
      side_effects: [],
      message: "当前操作已写入并复验",
      retryable: false,
    });
    const store = useAssistantStore.getState();
    store.setTranscript("创建当前待办");
    store.setPendingAction({
      id: "action-current",
      action: "create_task",
      risk_level: "medium",
      risk_reasons: ["writes_data"],
      payload: { title: "当前待办" },
      status: "ready",
      confirmation_count: 1,
      confirmations_required: 1,
    });
    store.setExecution({
      success: false,
      action: "create_task",
      record_id: "task-current",
      verified_fields: { title: false },
      side_effects: [],
      message: "首次数据库复验失败",
      retryable: true,
    });
    store.setError("首次数据库复验失败");
    store.setWorkflowStatus("error");
    render(<VoicePage />);

    expect(screen.getByRole("alert")).toHaveTextContent("首次数据库复验失败");
    fireEvent.click(screen.getByRole("button", { name: "重试一次" }));

    expect(mocks.executeAction).toHaveBeenCalledWith("action-current");
    expect(await screen.findByText("当前操作已写入并复验")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(useAssistantStore.getState()).toMatchObject({
      workflowStatus: "succeeded",
      pendingAction: null,
      lastExecutedActionId: "action-current",
      error: null,
    });
  });
});
