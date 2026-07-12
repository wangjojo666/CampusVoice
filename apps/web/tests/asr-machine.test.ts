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
    state = asrReducer(state, { type: "COMPLETED" });
    expect(state.phase).toBe("completed");
    expect(state.confidence).toBe(0.91);
    expect(state.latencyMs).toBe(180);
    expect(state.transcriptionId).toBe("trn-1");
  });

  it("does not silently recover from an unexpected WebSocket close", () => {
    const connecting = { ...initialAsrState, phase: "connecting" as const };
    const state = asrReducer(connecting, { type: "SOCKET_CLOSED", expected: false });
    expect(state.phase).toBe("error");
    expect(state.error?.code).toBe("socket_closed");
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
