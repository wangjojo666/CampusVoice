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

function defaultAsrUrl() {
  const explicit = process.env.NEXT_PUBLIC_ASR_WS_URL;
  if (explicit) return explicit;
  const httpBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  return `${httpBase.replace(/^http/, "ws").replace(/\/$/, "")}/ws/asr`;
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
  if (type === "ready")
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
  private socket: WebSocket | null = null;
  private stopRequested = false;
  private readonly handlers: AsrClientHandlers;
  private readonly url: string;
  private readonly hotwords: string[];
  private readonly ticket: string;

  constructor(
    handlers: AsrClientHandlers,
    options: { ticket: string; url?: string; hotwords?: string[] },
  ) {
    this.handlers = handlers;
    this.url = options.url ?? defaultAsrUrl();
    this.hotwords = options.hotwords ?? [];
    this.ticket = options.ticket;
  }

  connect(): Promise<void> {
    if (this.socket) throw new Error("ASR WebSocket is already connected");
    return new Promise((resolve, reject) => {
      let settled = false;
      const socket = new WebSocket(this.url, websocketProtocols(this.ticket));
      socket.binaryType = "arraybuffer";
      this.socket = socket;

      socket.onopen = () => {
        this.sendControl({
          type: "start",
          sample_rate_hz: 16000,
          channels: 1,
          sample_width_bytes: 2,
          language: "zh",
          hotwords: this.hotwords.slice(0, 500),
        });
      };
      socket.onmessage = (event) => {
        try {
          const message = normalizeMessage(JSON.parse(String(event.data)));
          if (!message) return;
          if (message.type === "ready" && !settled) {
            settled = true;
            resolve();
          }
          this.handlers.onMessage(message);
        } catch {
          this.handlers.onError("识别服务返回了无法解析的数据。");
        }
      };
      socket.onerror = () => {
        this.handlers.onError("无法建立语音识别连接，请确认服务已启动。");
        if (!settled) {
          settled = true;
          reject(new Error("WebSocket connection failed"));
        }
      };
      socket.onclose = (event) => {
        if (this.socket === socket) this.socket = null;
        this.handlers.onClose({
          stopRequested: this.stopRequested,
          code: event.code,
          wasClean: event.wasClean,
        });
        if (!settled) {
          settled = true;
          reject(new Error("WebSocket closed before ready"));
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
    this.stopRequested = true;
    this.sendControl({ type: "stop" });
  }

  close() {
    const socket = this.socket;
    this.socket = null;
    socket?.close(1000, "client closed");
  }

  private sendControl(message: AsrClientMessage) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
  }
}
