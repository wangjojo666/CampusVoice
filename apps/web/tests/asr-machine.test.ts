import { describe, expect, it } from "vitest";

import { asrReducer, initialAsrState } from "@/lib/asr/machine";

describe("ASR state machine", () => {
  it("follows permission, socket, streaming and finalization states", () => {
    let state = asrReducer(initialAsrState, { type: "START" });
    expect(state.phase).toBe("requesting_permission");
    state = asrReducer(state, { type: "PERMISSION_GRANTED" });
    expect(state.phase).toBe("connecting");
    state = asrReducer(state, { type: "SOCKET_READY", sessionId: "voice-1" });
    expect(state.phase).toBe("recording");
    state = asrReducer(state, { type: "INTERIM", text: "周五上午" });
    expect(state.editableTranscript).toBe("周五上午");
    state = asrReducer(state, {
      type: "FINAL",
      text: "周五上午九点。",
      confidence: 0.91,
      latencyMs: 180,
      transcriptionId: "trn-1",
    });
    expect(state.editableTranscript).toBe("周五上午九点。");
    state = asrReducer(state, { type: "STOP" });
    expect(state.phase).toBe("finalizing");
    state = asrReducer(state, {
      type: "SOCKET_CLOSED",
      stopRequested: true,
      code: 1000,
      wasClean: true,
    });
    expect(state.phase).toBe("completed");
    expect(state.confidence).toBe(0.91);
    expect(state.latencyMs).toBe(180);
    expect(state.transcriptionId).toBe("trn-1");
  });

  it("does not silently recover from an unexpected WebSocket close", () => {
    const connecting = { ...initialAsrState, phase: "connecting" as const };
    const state = asrReducer(connecting, {
      type: "SOCKET_CLOSED",
      stopRequested: false,
      code: 1006,
      wasClean: false,
    });
    expect(state.phase).toBe("error");
    expect(state.error?.code).toBe("socket_closed");
  });

  it("keeps an explicitly recoverable provider error non-terminal and accepts later finals", () => {
    const recording = { ...initialAsrState, phase: "recording" as const };
    let state = asrReducer(recording, {
      type: "FAIL",
      code: "vad_fallback",
      message: "已切换到本地语音活动检测",
      recoverable: true,
    });
    expect(state.phase).toBe("recording");
    expect(state.error).toMatchObject({ code: "vad_fallback", retryable: true });

    state = asrReducer(state, { type: "FINAL", text: "恢复后的最终转写" });
    expect(state.phase).toBe("recording");
    expect(state.editableTranscript).toBe("恢复后的最终转写");
    expect(state.error).toBeNull();
  });

  it("rejects an abnormal close after stop instead of completing an interim transcript", () => {
    const finalizing = {
      ...initialAsrState,
      phase: "finalizing" as const,
      interimTranscript: "未确认的临时转写",
      editableTranscript: "未确认的临时转写",
    };
    const state = asrReducer(finalizing, {
      type: "SOCKET_CLOSED",
      stopRequested: true,
      code: 1006,
      wasClean: false,
    });
    expect(state.phase).toBe("error");
    expect(state.error?.code).toBe("socket_closed_during_finalize");
    expect(state.editableTranscript).toBe("未确认的临时转写");
  });

  it("reports microphone denial as a retryable error", () => {
    const requesting = asrReducer(initialAsrState, { type: "START" });
    const state = asrReducer(requesting, {
      type: "PERMISSION_DENIED",
      message: "麦克风权限被拒绝",
    });
    expect(state.phase).toBe("error");
    expect(state.error).toMatchObject({ code: "microphone_denied", retryable: true });
  });
});
