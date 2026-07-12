export type AsrPhase =
  | "idle"
  | "requesting_permission"
  | "connecting"
  | "recording"
  | "paused"
  | "finalizing"
  | "completed"
  | "error";

export interface AsrMachineState {
  phase: AsrPhase;
  interimTranscript: string;
  finalSegments: string[];
  editableTranscript: string;
  confidence: number | null;
  latencyMs: number | null;
  level: number;
  speechActive: boolean;
  sessionId: string | null;
  transcriptionId: string | null;
  error: { code?: string; message: string; retryable: boolean } | null;
}

export type AsrMachineEvent =
  | { type: "START" }
  | { type: "PERMISSION_GRANTED" }
  | { type: "PERMISSION_DENIED"; message: string }
  | { type: "SOCKET_READY"; sessionId?: string }
  | { type: "SPEECH_START" }
  | { type: "SPEECH_END" }
  | { type: "INTERIM"; text: string; confidence?: number; latencyMs?: number }
  | {
      type: "FINAL";
      text: string;
      confidence?: number;
      latencyMs?: number;
      transcriptionId?: string;
    }
  | { type: "LEVEL"; level: number }
  | { type: "PAUSE" }
  | { type: "RESUME" }
  | { type: "STOP" }
  | { type: "COMPLETED" }
  | { type: "EDIT"; text: string }
  | { type: "FAIL"; code?: string; message: string; retryable?: boolean }
  | { type: "SOCKET_CLOSED"; expected: boolean }
  | { type: "RESET" };

export const initialAsrState: AsrMachineState = {
  phase: "idle",
  interimTranscript: "",
  finalSegments: [],
  editableTranscript: "",
  confidence: null,
  latencyMs: null,
  level: 0,
  speechActive: false,
  sessionId: null,
  transcriptionId: null,
  error: null,
};

function transcriptFrom(segments: string[], interim = "") {
  return [...segments, interim].filter(Boolean).join("");
}

export function asrReducer(state: AsrMachineState, event: AsrMachineEvent): AsrMachineState {
  switch (event.type) {
    case "START":
      if (!["idle", "completed", "error"].includes(state.phase)) return state;
      return { ...initialAsrState, phase: "requesting_permission" };
    case "PERMISSION_GRANTED":
      return state.phase === "requesting_permission" ? { ...state, phase: "connecting" } : state;
    case "PERMISSION_DENIED":
      return {
        ...state,
        phase: "error",
        error: { code: "microphone_denied", message: event.message, retryable: true },
      };
    case "SOCKET_READY":
      return state.phase === "connecting"
        ? { ...state, phase: "recording", sessionId: event.sessionId ?? null, error: null }
        : state;
    case "SPEECH_START":
      return { ...state, speechActive: true };
    case "SPEECH_END":
      return { ...state, speechActive: false };
    case "INTERIM":
      if (!["recording", "paused", "finalizing"].includes(state.phase)) return state;
      return {
        ...state,
        interimTranscript: event.text,
        editableTranscript: transcriptFrom(state.finalSegments, event.text),
        confidence: event.confidence ?? state.confidence,
        latencyMs: event.latencyMs ?? state.latencyMs,
      };
    case "FINAL": {
      if (!["recording", "paused", "finalizing"].includes(state.phase)) return state;
      const finalSegments = event.text ? [...state.finalSegments, event.text] : state.finalSegments;
      return {
        ...state,
        finalSegments,
        interimTranscript: "",
        editableTranscript: transcriptFrom(finalSegments),
        confidence: event.confidence ?? state.confidence,
        latencyMs: event.latencyMs ?? state.latencyMs,
        transcriptionId: event.transcriptionId ?? state.transcriptionId,
      };
    }
    case "LEVEL":
      return { ...state, level: Math.max(0, Math.min(1, event.level)) };
    case "PAUSE":
      return state.phase === "recording"
        ? { ...state, phase: "paused", speechActive: false, level: 0 }
        : state;
    case "RESUME":
      return state.phase === "paused" ? { ...state, phase: "recording" } : state;
    case "STOP":
      return ["recording", "paused"].includes(state.phase)
        ? { ...state, phase: "finalizing", speechActive: false, level: 0 }
        : state;
    case "COMPLETED":
      return state.phase === "finalizing"
        ? {
            ...state,
            phase: "completed",
            interimTranscript: "",
            editableTranscript: transcriptFrom(state.finalSegments, state.interimTranscript),
            level: 0,
          }
        : state;
    case "EDIT":
      return ["idle", "completed", "error"].includes(state.phase)
        ? { ...state, editableTranscript: event.text }
        : state;
    case "FAIL":
      return {
        ...state,
        phase: "error",
        speechActive: false,
        level: 0,
        error: { code: event.code, message: event.message, retryable: event.retryable ?? true },
      };
    case "SOCKET_CLOSED":
      if (event.expected && state.phase === "finalizing")
        return { ...state, phase: "completed", level: 0 };
      if (["idle", "completed", "error"].includes(state.phase)) return state;
      return {
        ...state,
        phase: "error",
        level: 0,
        speechActive: false,
        error: { code: "socket_closed", message: "语音连接意外断开，请重试。", retryable: true },
      };
    case "RESET":
      return initialAsrState;
  }
}

export const asrPhaseLabel: Record<AsrPhase, string> = {
  idle: "等待开始",
  requesting_permission: "正在请求麦克风权限",
  connecting: "正在连接识别服务",
  recording: "正在聆听",
  paused: "录音已暂停",
  finalizing: "正在完成转写",
  completed: "转写已完成",
  error: "识别遇到问题",
};
