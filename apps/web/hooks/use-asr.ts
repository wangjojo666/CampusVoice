"use client";

import type { AsrServerMessage } from "@campusvoice/shared-types";
import { useCallback, useEffect, useReducer, useRef } from "react";

import { AsrWebSocketClient } from "@/lib/asr/asr-client";
import { PcmAudioRecorder } from "@/lib/asr/audio-recorder";
import { asrHotwordValues } from "@/lib/asr/context-hotwords";
import { asrReducer, initialAsrState } from "@/lib/asr/machine";
import { api } from "@/lib/api-client";

function microphoneError(reason: unknown) {
  if (reason instanceof DOMException) {
    if (reason.name === "NotAllowedError" || reason.name === "SecurityError") {
      return "麦克风权限被拒绝。请在浏览器地址栏旁的权限设置中允许麦克风。";
    }
    if (reason.name === "NotFoundError") return "没有检测到可用麦克风，请连接设备后重试。";
    if (reason.name === "NotReadableError") return "麦克风正被其他应用占用，请关闭占用后重试。";
    return reason.message;
  }
  return "无法启动麦克风，请检查设备与浏览器权限。";
}

type RecorderStopResult = { ok: true } | { ok: false; reason: unknown };

interface StopOperation {
  lifecycle: number;
  client: AsrWebSocketClient | null;
  promise: Promise<void>;
}

async function settleRecorderStop(recorder: PcmAudioRecorder | null): Promise<RecorderStopResult> {
  try {
    await recorder?.stop();
    return { ok: true };
  } catch (reason) {
    return { ok: false, reason };
  }
}

function closeClientSafely(client: AsrWebSocketClient | null) {
  try {
    client?.close();
  } catch {
    // Resource cleanup must remain best-effort and must not reject detached tasks.
  }
}

async function settleOperation(operation: Promise<void> | null) {
  try {
    await operation;
  } catch {
    // A detached lifecycle cleanup must never surface an unhandled rejection.
  }
}

export function useAsr() {
  const [state, dispatch] = useReducer(asrReducer, initialAsrState);
  const recorderRef = useRef<PcmAudioRecorder | null>(null);
  const clientRef = useRef<AsrWebSocketClient | null>(null);
  const mountedRef = useRef(true);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  const lifecycleRef = useRef(0);
  const stopOperationRef = useRef<StopOperation | null>(null);
  const resourceCleanupRef = useRef<Promise<void> | null>(null);
  const interruptionRef = useRef<Promise<void> | null>(null);

  const registerResourceCleanup = useCallback(
    (recorder: PcmAudioRecorder | null, stopOperation: Promise<void> | null) => {
      const previousCleanup = resourceCleanupRef.current;
      const operation = Promise.all([
        settleOperation(previousCleanup),
        settleRecorderStop(recorder),
        settleOperation(stopOperation),
      ]).then(() => undefined);
      resourceCleanupRef.current = operation;
      void operation.then(() => {
        if (resourceCleanupRef.current === operation) resourceCleanupRef.current = null;
      });
      return operation;
    },
    [],
  );

  const handleMessage = useCallback((message: AsrServerMessage) => {
    if (message.type === "ready") dispatch({ type: "SOCKET_READY", sessionId: message.session_id });
    if (message.type === "speech_start") dispatch({ type: "SPEECH_START" });
    if (message.type === "speech_end") dispatch({ type: "SPEECH_END" });
    if (message.type === "interim") {
      dispatch({
        type: "INTERIM",
        text: message.text,
        confidence: message.confidence,
        latencyMs: message.latency_ms,
      });
    }
    if (message.type === "final") {
      dispatch({
        type: "FINAL",
        text: message.text,
        confidence: message.confidence,
        latencyMs: message.latency_ms,
        transcriptionId: message.transcription_id,
      });
    }
    if (message.type === "error") {
      dispatch({
        type: "FAIL",
        code: message.code,
        message: message.message,
        retryable: message.recoverable ?? true,
        recoverable: message.recoverable === true,
      });
    }
  }, []);

  const terminateCurrent = useCallback(
    (client: AsrWebSocketClient, { closeClient }: { closeClient: boolean }) => {
      if (clientRef.current !== client) return Promise.resolve();
      lifecycleRef.current += 1;
      const pendingStop = stopOperationRef.current?.promise ?? null;
      stopOperationRef.current = null;
      clientRef.current = null;
      const recorder = recorderRef.current;
      recorderRef.current = null;
      if (closeClient) closeClientSafely(client);
      return registerResourceCleanup(recorder, pendingStop);
    },
    [registerResourceCleanup],
  );

  const cleanup = useCallback(() => {
    lifecycleRef.current += 1;
    const pendingStop = stopOperationRef.current?.promise ?? null;
    stopOperationRef.current = null;
    const client = clientRef.current;
    const recorder = recorderRef.current;
    clientRef.current = null;
    recorderRef.current = null;
    closeClientSafely(client);
    return registerResourceCleanup(recorder, pendingStop);
  }, [registerResourceCleanup]);

  const interruptActiveSession = useCallback(
    (code: string, message: string) => {
      if (interruptionRef.current) return interruptionRef.current;
      const active =
        recorderRef.current !== null ||
        clientRef.current !== null ||
        ["requesting_permission", "connecting", "recording", "paused", "finalizing"].includes(
          stateRef.current.phase,
        );
      if (!active) return Promise.resolve();

      const operation = cleanup().then(() => {
        if (!mountedRef.current || interruptionRef.current !== operation) return;
        dispatch({ type: "FAIL", code, message, retryable: true });
      });
      interruptionRef.current = operation;
      void operation.finally(() => {
        if (interruptionRef.current === operation) interruptionRef.current = null;
      });
      return operation;
    },
    [cleanup],
  );

  useEffect(() => {
    const pageHidden = () => {
      void interruptActiveSession(
        "page_hidden",
        "页面已进入后台或锁屏，本次录音已停止。返回后请手动重新开始。",
      );
    };
    const visibilityChanged = () => {
      if (document.visibilityState === "hidden") pageHidden();
    };
    const offline = () => {
      void interruptActiveSession(
        "network_offline",
        "网络连接已中断，本次录音未完成。联网后请手动重新开始。",
      );
    };

    document.addEventListener("visibilitychange", visibilityChanged);
    window.addEventListener("pagehide", pageHidden);
    window.addEventListener("beforeunload", pageHidden);
    window.addEventListener("offline", offline);
    return () => {
      document.removeEventListener("visibilitychange", visibilityChanged);
      window.removeEventListener("pagehide", pageHidden);
      window.removeEventListener("beforeunload", pageHidden);
      window.removeEventListener("offline", offline);
    };
  }, [interruptActiveSession]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      void cleanup();
    };
  }, [cleanup]);

  const start = useCallback(async () => {
    if (!["idle", "completed", "error"].includes(state.phase)) return;
    const lifecycle = lifecycleRef.current + 1;
    await cleanup();
    if (!mountedRef.current || lifecycleRef.current !== lifecycle) return;
    dispatch({ type: "START" });
    const hotwordsPromise = Promise.allSettled([api.hotwords.list(), api.settings.get()]).then(
      ([hotwords, settings]) =>
        asrHotwordValues(
          hotwords.status === "fulfilled" ? hotwords.value.items : [],
          settings.status === "fulfilled" ? settings.value : null,
        ),
    );
    const recorder = new PcmAudioRecorder();
    recorderRef.current = recorder;
    const startWasCancelled = () => !mountedRef.current || lifecycleRef.current !== lifecycle;
    const releaseRecorder = async () => {
      if (recorderRef.current === recorder) recorderRef.current = null;
      await settleRecorderStop(recorder);
    };
    try {
      await recorder.start({
        onChunk: (chunk) => {
          if (!startWasCancelled()) clientRef.current?.sendAudio(chunk);
        },
        onLevel: (level) => {
          if (!startWasCancelled()) dispatch({ type: "LEVEL", level });
        },
        onInterruption: (code, message) => {
          if (!startWasCancelled()) void interruptActiveSession(code, message);
        },
      });
      if (startWasCancelled()) {
        await releaseRecorder();
        return;
      }
      dispatch({ type: "PERMISSION_GRANTED" });
      // Request the short-lived ticket only after the user has answered the
      // permission prompt; otherwise it may expire while the prompt is open.
      const ticket = await api.auth.websocketTicket();
      if (startWasCancelled()) {
        await releaseRecorder();
        return;
      }
      const hotwords = await hotwordsPromise;
      if (startWasCancelled()) {
        await releaseRecorder();
        return;
      }
      const client = new AsrWebSocketClient(
        {
          onMessage: (message) => {
            if (startWasCancelled() || clientRef.current !== client) return;
            handleMessage(message);
            if (message.type === "error" && message.recoverable !== true) {
              void terminateCurrent(client, { closeClient: true });
            }
          },
          onClose: (info) => {
            if (startWasCancelled() || clientRef.current !== client) return;
            dispatch({ type: "SOCKET_CLOSED", ...info });
            void terminateCurrent(client, { closeClient: false });
          },
          onError: (message) => {
            if (startWasCancelled() || clientRef.current !== client) return;
            dispatch({ type: "FAIL", message, retryable: true });
            void terminateCurrent(client, { closeClient: true });
          },
        },
        { hotwords, ticket: ticket.ticket },
      );
      clientRef.current = client;
      await client.connect();
      if (startWasCancelled()) {
        closeClientSafely(client);
        if (clientRef.current === client) clientRef.current = null;
        await releaseRecorder();
      }
    } catch (reason) {
      await releaseRecorder();
      if (!startWasCancelled()) {
        if (reason instanceof DOMException)
          dispatch({ type: "PERMISSION_DENIED", message: microphoneError(reason) });
        else
          dispatch({
            type: "FAIL",
            message: "无法连接语音识别服务，请确认后端已启动。",
            retryable: true,
          });
      }
    }
  }, [cleanup, handleMessage, interruptActiveSession, state.phase, terminateCurrent]);

  const pause = useCallback(async () => {
    if (state.phase !== "recording") return;
    await recorderRef.current?.pause();
    clientRef.current?.pause();
    dispatch({ type: "PAUSE" });
  }, [state.phase]);

  const resume = useCallback(async () => {
    if (state.phase !== "paused") return;
    await recorderRef.current?.resume();
    clientRef.current?.resume();
    dispatch({ type: "RESUME" });
  }, [state.phase]);

  const stop = useCallback((): Promise<void> => {
    const lifecycle = lifecycleRef.current;
    const client = clientRef.current;
    const existing = stopOperationRef.current;
    if (existing?.lifecycle === lifecycle && existing.client === client) return existing.promise;
    if (!["recording", "paused"].includes(state.phase)) return Promise.resolve();

    dispatch({ type: "STOP" });
    const recorder = recorderRef.current;
    if (recorderRef.current === recorder) recorderRef.current = null;

    const promise = (async () => {
      const recorderResult = await settleRecorderStop(recorder);
      if (
        !mountedRef.current ||
        lifecycleRef.current !== lifecycle ||
        clientRef.current !== client
      ) {
        return;
      }

      if (!recorderResult.ok) {
        dispatch({
          type: "FAIL",
          code: "audio_drain_failed",
          message: "录音尾段未能完整发送，本次转写未完成，请重试。",
          retryable: true,
        });
      }

      try {
        client?.stop();
      } catch {
        if (recorderResult.ok) {
          dispatch({
            type: "FAIL",
            code: "asr_stop_failed",
            message: "语音连接未能完成停止，本次转写未完成，请重试。",
            retryable: true,
          });
        }
      }
    })();

    stopOperationRef.current = { lifecycle, client, promise };
    return promise;
  }, [state.phase]);

  const reset = useCallback(async () => {
    await cleanup();
    if (mountedRef.current) dispatch({ type: "RESET" });
  }, [cleanup]);

  const editTranscript = useCallback((text: string) => dispatch({ type: "EDIT", text }), []);

  return { state, start, pause, resume, stop, reset, editTranscript };
}
