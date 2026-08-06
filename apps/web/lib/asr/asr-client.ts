import type { AsrClientMessage, AsrServerMessage } from "@campusvoice/shared-types";
import { websocketProtocols } from "@/lib/auth";

export interface AsrClientHandlers {
  onMessage: (message: AsrServerMessage) => void;
  onClose: (info: AsrCloseInfo) => void;
  onError: (message: string) => void;
}

export interface AsrCloseInfo {
  stopRequested: boolean;
  code: number;
  wasClean: boolean;
}

export function resolveAsrWebSocketUrl(
  value: string,
  pageUrl = typeof window !== "undefined" ? window.location.href : "http://127.0.0.1/",
) {
  const page = new URL(pageUrl);
  const url = new URL(value, page);
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol === "https:") url.protocol = "wss:";
  if (url.protocol !== "ws:" && url.protocol !== "wss:") {
    throw new Error("ASR endpoint must use HTTP(S) or WebSocket protocol");
  }
  if (page.protocol === "https:" && url.protocol === "ws:") {
    throw new Error("HTTPS pages require a secure WSS speech connection");
  }
  for (const parameter of ["access_token", "token", "ticket"]) {
    if (url.searchParams.has(parameter)) {
      throw new Error("ASR credentials must not be included in the WebSocket URL");
    }
  }
  return url.href;
}

function defaultAsrUrl() {
  const pageUrl = typeof window !== "undefined" ? window.location.href : "http://127.0.0.1:8000/";
  const explicit = process.env.NEXT_PUBLIC_ASR_WS_URL;
  if (explicit) return resolveAsrWebSocketUrl(explicit, pageUrl);
  const httpBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? new URL(pageUrl).origin;
  return resolveAsrWebSocketUrl(new URL("/ws/asr", new URL(httpBase, pageUrl)).href, pageUrl);
}

function normalizeMessage(value: unknown): AsrServerMessage | null {
  if (!value || typeof value !== "object") return null;
  const message = value as Record<string, unknown>;
  const rawType = typeof message.type === "string" ? message.type : "";
  const type = rawType === "partial" ? "interim" : rawType;
  const metadata = {
    session_id: typeof message.session_id === "string" ? message.session_id : undefined,
    sequence: typeof message.sequence === "number" ? message.sequence : undefined,
    provider: typeof message.provider === "string" ? message.provider : undefined,
  };
  if (type === "ready" || type === "finalizing")
    return {
      ...metadata,
      type,
    };
  if (type === "speech_start" || type === "speech_end") {
    return {
      ...metadata,
      type,
      timestamp_ms: typeof message.timestamp_ms === "number" ? message.timestamp_ms : undefined,
    };
  }
  if (type === "interim" || type === "final") {
    return {
      ...metadata,
      type,
      text: typeof message.text === "string" ? message.text : "",
      confidence: typeof message.confidence === "number" ? message.confidence : undefined,
      latency_ms: typeof message.latency_ms === "number" ? message.latency_ms : undefined,
      transcription_id:
        typeof message.transcription_id === "string" ? message.transcription_id : undefined,
    };
  }
  if (type === "pong") {
    return {
      ...metadata,
      type,
    };
  }
  if (type === "error") {
    return {
      ...metadata,
      type,
      code: typeof message.code === "string" ? message.code : undefined,
      message: typeof message.message === "string" ? message.message : "语音识别失败",
      recoverable: typeof message.recoverable === "boolean" ? message.recoverable : undefined,
    };
  }
  return null;
}

export class AsrWebSocketClient {
  private static readonly DEFAULT_READY_TIMEOUT_MS = 15_000;
  private socket: WebSocket | null = null;
  private pendingConnect: { socket: WebSocket; cancel: () => void } | null = null;
  private stopRequested = false;
  private readonly handlers: AsrClientHandlers;
  private readonly url: string;
  private readonly hotwords: string[];
  private readonly ticket: string;
  private readonly readyTimeoutMs: number;

  constructor(
    handlers: AsrClientHandlers,
    options: { ticket: string; url?: string; hotwords?: string[]; readyTimeoutMs?: number },
  ) {
    this.handlers = handlers;
    this.url = resolveAsrWebSocketUrl(options.url ?? defaultAsrUrl());
    this.hotwords = options.hotwords ?? [];
    this.ticket = options.ticket;
    this.readyTimeoutMs = options.readyTimeoutMs ?? AsrWebSocketClient.DEFAULT_READY_TIMEOUT_MS;
  }

  connect(): Promise<void> {
    if (this.socket || this.pendingConnect) throw new Error("ASR WebSocket is already connected");
    this.stopRequested = false;
    return new Promise((resolve, reject) => {
      let settled = false;
      let readyTimeoutId: number | null = null;
      let closeRequested = false;
      const socket = new WebSocket(this.url, websocketProtocols(this.ticket));

      const clearReadyTimeout = () => {
        if (readyTimeoutId === null) return;
        window.clearTimeout(readyTimeoutId);
        readyTimeoutId = null;
      };
      const settle = (reason?: Error) => {
        if (settled) return false;
        settled = true;
        clearReadyTimeout();
        if (this.pendingConnect?.socket === socket) this.pendingConnect = null;
        if (reason) reject(reason);
        else resolve();
        return true;
      };
      const detachSocket = () => {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
      };
      const abandonSocket = (reason: string) => {
        if (this.socket === socket) this.socket = null;
        detachSocket();
        if (
          closeRequested ||
          socket.readyState === WebSocket.CLOSING ||
          socket.readyState === WebSocket.CLOSED
        )
          return;
        closeRequested = true;
        try {
          socket.close(1000, reason);
        } catch {
          // The promise has already settled; cleanup remains best-effort.
        }
      };
      const rejectAndClose = (error: Error, closeReason: string) => {
        if (!settle(error)) return;
        abandonSocket(closeReason);
      };

      socket.binaryType = "arraybuffer";
      this.socket = socket;
      this.pendingConnect = {
        socket,
        cancel: () =>
          rejectAndClose(new Error("ASR WebSocket connection cancelled"), "client closed"),
      };
      readyTimeoutId = window.setTimeout(
        () => rejectAndClose(new Error("ASR WebSocket ready handshake timed out"), "ready timeout"),
        this.readyTimeoutMs,
      );

      socket.onopen = () => {
        if (this.socket !== socket) return;
        this.sendControl(
          {
            type: "start",
            sample_rate_hz: 16000,
            channels: 1,
            sample_width_bytes: 2,
            language: "zh",
            hotwords: this.hotwords.slice(0, 500),
          },
          socket,
        );
      };
      socket.onmessage = (event) => {
        if (this.socket !== socket) return;
        try {
          const message = normalizeMessage(JSON.parse(String(event.data)));
          if (!message) return;
          if (message.type === "ready" && !settled) {
            settle();
          }
          this.handlers.onMessage(message);
        } catch {
          this.handlers.onError("识别服务返回了无法解析的数据。");
        }
      };
      socket.onerror = () => {
        if (this.socket !== socket) return;
        this.handlers.onError("无法建立语音识别连接，请确认服务已启动。");
        if (!settled) {
          rejectAndClose(new Error("WebSocket connection failed"), "connection failed");
        }
      };
      socket.onclose = (event) => {
        if (this.socket !== socket) return;
        this.socket = null;
        detachSocket();
        this.handlers.onClose({
          stopRequested: this.stopRequested,
          code: event.code,
          wasClean: event.wasClean,
        });
        if (!settled) {
          settle(new Error("WebSocket closed before ready"));
        }
      };
    });
  }

  sendAudio(chunk: ArrayBuffer) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(chunk);
  }

  pause() {
    this.sendControl({ type: "flush" });
  }

  resume() {}

  stop() {
    if (!this.sendControl({ type: "stop" })) {
      throw new Error("ASR WebSocket is not open for stop");
    }
    this.stopRequested = true;
  }

  close() {
    const pendingConnect = this.pendingConnect;
    if (pendingConnect) {
      pendingConnect.cancel();
      return;
    }
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    socket.close(1000, "client closed");
  }

  private sendControl(message: AsrClientMessage, socket = this.socket) {
    if (!socket || socket !== this.socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }
}
