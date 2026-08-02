import { afterEach, describe, expect, it, vi } from "vitest";

import { AsrWebSocketClient } from "@/lib/asr/asr-client";

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instance: FakeWebSocket | null = null;
  readyState: number = FakeWebSocket.OPEN;
  binaryType = "";
  sent: Array<string | ArrayBuffer> = [];
  closeCode: number | null = null;
  closeReason: string | null = null;
  closeCallCount = 0;
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

  close(code = 1000, reason = "") {
    this.closeCallCount += 1;
    if (this.readyState === FakeWebSocket.CLOSING || this.readyState === FakeWebSocket.CLOSED)
      return;
    this.readyState = FakeWebSocket.CLOSING;
    this.closeCode = code;
    this.closeReason = reason;
    const onclose = this.onclose;
    this.readyState = FakeWebSocket.CLOSED;
    onclose?.({ code, wasClean: code === 1000 });
  }
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.instance = null;
});

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

  it("refuses to mark stop requested when the WebSocket is already closing", async () => {
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

    if (socket) socket.readyState = FakeWebSocket.CLOSING;
    expect(() => client.stop()).toThrow("ASR WebSocket is not open for stop");
    socket?.onclose?.({ code: 1000, wasClean: true });

    expect(onClose).toHaveBeenCalledWith({
      stopRequested: false,
      code: 1000,
      wasClean: true,
    });
    expect(socket?.sent.some((message) => String(message).includes('"type":"stop"'))).toBe(false);
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

  it("keeps the socket active and clears the deadline when ready arrives in time", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onMessage = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage, onClose: vi.fn(), onError: vi.fn() },
      {
        url: "ws://localhost/ws/asr",
        ticket: "short-lived-ticket",
        readyTimeoutMs: 1_000,
      },
    );
    const connected = client.connect();
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");
    socket.onopen?.();

    await vi.advanceTimersByTimeAsync(999);
    socket.onmessage?.({ data: JSON.stringify({ type: "ready", session_id: "voice-1" }) });
    await connected;
    expect(vi.getTimerCount()).toBe(0);

    await vi.advanceTimersByTimeAsync(1);
    const audio = new ArrayBuffer(8);
    client.sendAudio(audio);
    expect(socket.sent.at(-1)).toBe(audio);
    expect(socket.closeCallCount).toBe(0);
    expect(onMessage).toHaveBeenCalledOnce();
    client.close();
  });

  it("closes and rejects exactly once when the ready handshake times out", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onClose = vi.fn();
    const onError = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose, onError },
      {
        url: "ws://localhost/ws/asr",
        ticket: "short-lived-ticket",
        readyTimeoutMs: 1_000,
      },
    );
    const connected = client.connect();
    const rejection = expect(connected).rejects.toThrow("ASR WebSocket ready handshake timed out");
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");
    socket.onopen?.();

    await vi.advanceTimersByTimeAsync(999);
    expect(socket.closeCallCount).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    await rejection;

    expect(socket.closeCallCount).toBe(1);
    expect(socket.closeCode).toBe(1000);
    expect(socket.closeReason).toBe("ready timeout");
    expect(onClose).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(socket.onopen).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.onerror).toBeNull();
    expect(socket.onclose).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("settles once and clears resources when error wins the timeout race", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onClose = vi.fn();
    const onError = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose, onError },
      {
        url: "ws://localhost/ws/asr",
        ticket: "short-lived-ticket",
        readyTimeoutMs: 1_000,
      },
    );
    const connected = client.connect();
    const rejection = expect(connected).rejects.toThrow("WebSocket connection failed");
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");

    socket.onerror?.();
    await rejection;
    await vi.advanceTimersByTimeAsync(1_000);

    expect(onError).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
    expect(socket.closeCallCount).toBe(1);
    expect(socket.closeReason).toBe("connection failed");
    expect(socket.onopen).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.onerror).toBeNull();
    expect(socket.onclose).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("settles once and clears the deadline when close wins the timeout race", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onClose = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose, onError: vi.fn() },
      {
        url: "ws://localhost/ws/asr",
        ticket: "short-lived-ticket",
        readyTimeoutMs: 1_000,
      },
    );
    const connected = client.connect();
    const rejection = expect(connected).rejects.toThrow("WebSocket closed before ready");
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");
    const serverClose = socket.onclose;

    socket.readyState = FakeWebSocket.CLOSED;
    serverClose?.({ code: 1006, wasClean: false });
    await rejection;
    await vi.advanceTimersByTimeAsync(1_000);

    expect(onClose).toHaveBeenCalledOnce();
    expect(socket.closeCallCount).toBe(0);
    expect(socket.onopen).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.onerror).toBeNull();
    expect(socket.onclose).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cancels a pending connect and isolates every stale event from a reconnect", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onMessage = vi.fn();
    const onClose = vi.fn();
    const onError = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage, onClose, onError },
      {
        url: "ws://localhost/ws/asr",
        ticket: "short-lived-ticket",
        readyTimeoutMs: 1_000,
      },
    );
    const firstConnect = client.connect();
    const firstRejection = expect(firstConnect).rejects.toThrow(
      "ASR WebSocket connection cancelled",
    );
    const firstSocket = FakeWebSocket.instance;
    if (!firstSocket) throw new Error("first fake socket was not created");
    const staleOpen = firstSocket.onopen;
    const staleMessage = firstSocket.onmessage;
    const staleError = firstSocket.onerror;
    const staleClose = firstSocket.onclose;

    client.close();
    await firstRejection;
    expect(firstSocket.closeCallCount).toBe(1);
    expect(firstSocket.closeReason).toBe("client closed");
    expect(vi.getTimerCount()).toBe(0);

    const secondConnect = client.connect();
    const secondSocket = FakeWebSocket.instance;
    if (!secondSocket || secondSocket === firstSocket)
      throw new Error("second fake socket was not created");

    staleOpen?.();
    staleMessage?.({ data: JSON.stringify({ type: "ready", session_id: "stale" }) });
    staleError?.();
    staleClose?.({ code: 1006, wasClean: false });
    expect(secondSocket.sent).toEqual([]);
    expect(onMessage).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();

    secondSocket.onopen?.();
    secondSocket.onmessage?.({
      data: JSON.stringify({ type: "ready", session_id: "current" }),
    });
    await secondConnect;
    await vi.advanceTimersByTimeAsync(1_000);

    expect(secondSocket.closeCallCount).toBe(0);
    expect(onMessage).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    client.close();
  });

  it("rejects concurrent connects without changing the active attempt", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose: vi.fn(), onError: vi.fn() },
      { url: "ws://localhost/ws/asr", ticket: "short-lived-ticket" },
    );
    const connected = client.connect();
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");

    expect(() => client.connect()).toThrow("ASR WebSocket is already connected");
    socket.onmessage?.({ data: JSON.stringify({ type: "ready", session_id: "voice-1" }) });
    await connected;
    expect(() => client.connect()).toThrow("ASR WebSocket is already connected");
    client.close();
  });

  it("keeps a successful connect settled after later error and close events", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onClose = vi.fn();
    const onError = vi.fn();
    const client = new AsrWebSocketClient(
      { onMessage: vi.fn(), onClose, onError },
      { url: "ws://localhost/ws/asr", ticket: "short-lived-ticket" },
    );
    const connected = client.connect();
    const socket = FakeWebSocket.instance;
    if (!socket) throw new Error("fake socket was not created");
    socket.onmessage?.({ data: JSON.stringify({ type: "ready", session_id: "voice-1" }) });
    await connected;

    socket.onerror?.();
    socket.onclose?.({ code: 1006, wasClean: false });

    await expect(connected).resolves.toBeUndefined();
    expect(onError).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });
});
