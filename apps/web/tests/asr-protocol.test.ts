import { describe, expect, it, vi } from "vitest";

import { AsrWebSocketClient } from "@/lib/asr/asr-client";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instance: FakeWebSocket | null = null;
  readyState = FakeWebSocket.OPEN;
  binaryType = "";
  sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number; wasClean: boolean }) => void) | null = null;

  constructor(
    readonly url: string,
    readonly protocols?: string | string[],
  ) {
    FakeWebSocket.instance = this;
  }

  send(value: string | ArrayBuffer) {
    this.sent.push(value);
  }

  close(code = 1000) {
    this.onclose?.({ code, wasClean: code === 1000 });
  }
}

describe("ASR WebSocket protocol", () => {
  it("starts the real server protocol with 16 kHz mono PCM and configured hotwords", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose: vi.fn(), onError: vi.fn() },
      { url: "ws://localhost/ws/asr", hotwords: ["机器学习"], ticket: "short-lived-ticket" },
    );
    const connected = client.connect();
    const socket = FakeWebSocket.instance;
    expect(socket).not.toBeNull();
    expect(socket?.protocols).toEqual(["campusvoice", "campusvoice.ticket.short-lived-ticket"]);
    socket?.onopen?.();
    expect(JSON.parse(String(socket?.sent[0]))).toEqual({
      type: "start",
      sample_rate_hz: 16000,
      channels: 1,
      sample_width_bytes: 2,
      language: "zh",
      hotwords: ["机器学习"],
    });
    socket?.onmessage?.({ data: JSON.stringify({ type: "ready", session_id: "voice-1" }) });
    await connected;
    client.pause();
    expect(JSON.parse(String(socket?.sent[1]))).toEqual({ type: "flush" });
  });

  it("reports stop intent together with the actual WebSocket close result", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onClose = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose, onError: vi.fn() },
      { url: "ws://localhost/ws/asr", ticket: "short-lived-ticket" },
    );
    const connected = client.connect();
    const socket = FakeWebSocket.instance;
    socket?.onopen?.();
    socket?.onmessage?.({ data: JSON.stringify({ type: "ready", session_id: "voice-1" }) });
    await connected;

    client.stop();
    socket?.onclose?.({ code: 1006, wasClean: false });

    expect(onClose).toHaveBeenCalledWith({
      stopRequested: true,
      code: 1006,
      wasClean: false,
    });
  });
});
