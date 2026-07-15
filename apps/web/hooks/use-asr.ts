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

export function useAsr() {
  const [state, dispatch] = useReducer(asrReducer, initialAsrState);
  const recorderRef = useRef<PcmAudioRecorder | null>(null);
  const clientRef = useRef<AsrWebSocketClient | null>(null);
  const mountedRef = useRef(true);
  const lifecycleRef = useRef(0);

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
    async (client: AsrWebSocketClient, { closeClient }: { closeClient: boolean }) => {
      if (clientRef.current !== client) return;
      lifecycleRef.current += 1;
      clientRef.current = null;
      if (closeClient) client.close();
      const recorder = recorderRef.current;
      recorderRef.current = null;
      await recorder?.stop();
    },
    [],
  );

  const cleanup = useCallback(async () => {
    lifecycleRef.current += 1;
    clientRef.current?.close();
    clientRef.current = null;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    await recorder?.stop();
  }, []);

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
      await recorder.stop();
    };
    try {
      await recorder.start({
        onChunk: (chunk) => {
          if (!startWasCancelled()) clientRef.current?.sendAudio(chunk);
        },
        onLevel: (level) => {
          if (!startWasCancelled()) dispatch({ type: "LEVEL", level });
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
        client.close();
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
  }, [cleanup, handleMessage, state.phase, terminateCurrent]);

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

  const stop = useCallback(async () => {
    if (!["recording", "paused"].includes(state.phase)) return;
    dispatch({ type: "STOP" });
    const recorder = recorderRef.current;
    recorderRef.current = null;
    await recorder?.stop();
    clientRef.current?.stop();
  }, [state.phase]);

  const reset = useCallback(async () => {
    await cleanup();
    dispatch({ type: "RESET" });
  }, [cleanup]);

  const editTranscript = useCallback((text: string) => dispatch({ type: "EDIT", text }), []);

  return { state, start, pause, resume, stop, reset, editTranscript };
}
