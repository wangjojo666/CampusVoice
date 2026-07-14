"use client";

import type { AsrTranscriptReference } from "@campusvoice/shared-types";
import { Check, Mic, Pause, Play, RotateCcw, Square } from "lucide-react";
import { useEffect, useRef } from "react";

import { ErrorState } from "@/components/ui/error-state";
import { Waveform } from "@/components/voice/waveform";
import { useAsr } from "@/hooks/use-asr";
import { asrPhaseLabel } from "@/lib/asr/machine";

export function AsrRecorder({
  compact = false,
  onTranscriptChange,
  onSessionChange,
  onSourceChange,
  onReset,
}: Readonly<{
  compact?: boolean;
  onTranscriptChange?: (text: string, confidence: number | null) => void;
  onSessionChange?: (sessionId: string | null) => void;
  onSourceChange?: (source: AsrTranscriptReference) => void;
  onReset?: () => void;
}>) {
  const { state, start, pause, resume, stop, reset, editTranscript } = useAsr();
  const transcriptCallback = useRef(onTranscriptChange);
  const sessionCallback = useRef(onSessionChange);
  const sourceCallback = useRef(onSourceChange);

  useEffect(() => {
    transcriptCallback.current = onTranscriptChange;
  }, [onTranscriptChange]);

  useEffect(() => {
    sessionCallback.current = onSessionChange;
  }, [onSessionChange]);

  useEffect(() => {
    sourceCallback.current = onSourceChange;
  }, [onSourceChange]);

  useEffect(() => {
    if (state.editableTranscript) {
      transcriptCallback.current?.(state.editableTranscript, state.confidence);
    }
  }, [state.confidence, state.editableTranscript]);

  useEffect(() => {
    sessionCallback.current?.(state.sessionId);
  }, [state.sessionId]);

  useEffect(() => {
    sourceCallback.current?.({
      sessionId: state.sessionId,
      transcriptionId: state.transcriptionId,
      originalText: state.finalSegments.join(""),
    });
  }, [state.finalSegments, state.sessionId, state.transcriptionId]);

  const resetAll = async () => {
    await reset();
    onReset?.();
  };
  const active = state.phase === "recording";
  const busy = ["requesting_permission", "connecting", "finalizing"].includes(state.phase);
  const canStart = ["idle", "completed", "error"].includes(state.phase);

  return (
    <section className={compact ? "" : "surface overflow-hidden"} aria-label="语音转写">
      <div className={compact ? "" : "p-5 sm:p-7"}>
        <div className="flex flex-col items-center text-center">
          <div
            className="mb-2 flex items-center gap-2 text-sm font-bold text-ink-700"
            aria-live="polite"
          >
            <span
              className={`size-2.5 rounded-full ${active ? "animate-pulse bg-teal-500" : state.phase === "error" ? "bg-coral-500" : busy ? "animate-pulse bg-gold-500" : "bg-mist-200"}`}
            />
            {asrPhaseLabel[state.phase]}
          </div>
          {state.speechActive ? (
            <p className="text-xs font-semibold text-teal-600">检测到说话</p>
          ) : null}
          <div className={`w-full ${compact ? "max-w-lg" : "max-w-xl"}`}>
            <Waveform level={state.level} active={active} />
          </div>

          <div className="mt-1 flex items-center justify-center gap-2">
            {canStart ? (
              <button
                type="button"
                onClick={() => void start()}
                className={`flex items-center justify-center rounded-full border-teal-100 bg-teal-600 text-white shadow-[0_12px_30px_rgba(14,127,109,.28)] transition-transform hover:scale-105 ${compact ? "size-20 border-[6px]" : "size-16 border-[5px]"}`}
                aria-label={state.phase === "completed" ? "重新开始录音" : "开始录音"}
              >
                <Mic size={compact ? 30 : 26} />
              </button>
            ) : null}
            {state.phase === "recording" ? (
              <>
                <button
                  type="button"
                  onClick={() => void pause()}
                  className="btn-secondary"
                  aria-label="暂停录音"
                >
                  <Pause size={17} />
                  暂停
                </button>
                <button
                  type="button"
                  onClick={() => void stop()}
                  className="btn-primary"
                  aria-label="停止并完成转写"
                >
                  <Square size={15} fill="currentColor" />
                  完成
                </button>
              </>
            ) : null}
            {state.phase === "paused" ? (
              <>
                <button type="button" onClick={() => void resume()} className="btn-secondary">
                  <Play size={17} />
                  继续
                </button>
                <button type="button" onClick={() => void stop()} className="btn-primary">
                  <Square size={15} fill="currentColor" />
                  完成
                </button>
              </>
            ) : null}
            {busy ? (
              <div
                className="size-8 animate-spin rounded-full border-2 border-mist-200 border-t-teal-600"
                aria-label="处理中"
              />
            ) : null}
          </div>
          {state.phase === "idle" ? (
            <p className="mt-3 text-xs text-ink-400">点击麦克风后，请允许浏览器使用麦克风</p>
          ) : null}
        </div>

        {state.error ? (
          <div className="mt-5">
            <ErrorState
              title="语音识别未完成"
              message={state.error.message}
              onRetry={state.error.retryable ? () => void start() : undefined}
              compact
            />
          </div>
        ) : null}

        {(state.editableTranscript ||
          ["recording", "paused", "finalizing"].includes(state.phase)) &&
        !compact ? (
          <div className="mt-6 border-t border-mist-200 pt-5">
            <div className="mb-2 flex items-center justify-between gap-3">
              <label htmlFor="asr-transcript" className="text-sm font-bold text-ink-800">
                {state.phase === "completed" ? "最终转写（可编辑）" : "实时转写"}
              </label>
              {state.phase === "completed" ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-teal-50 px-2.5 py-1 text-xs font-bold text-teal-700">
                  <Check size={13} /> 已完成
                </span>
              ) : null}
            </div>
            <textarea
              id="asr-transcript"
              value={state.editableTranscript}
              onChange={(event) => {
                editTranscript(event.target.value);
                onTranscriptChange?.(event.target.value, state.confidence);
              }}
              readOnly={state.phase !== "completed"}
              rows={4}
              placeholder="识别结果会在这里实时出现…"
              className="field resize-y text-base leading-7 read-only:bg-mist-50"
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-ink-400">
              <div className="flex gap-4">
                <span>
                  置信度：
                  {state.confidence === null ? "—" : `${Math.round(state.confidence * 100)}%`}
                </span>
                <span>
                  识别延迟：{state.latencyMs === null ? "—" : `${Math.round(state.latencyMs)} ms`}
                </span>
              </div>
              <button
                type="button"
                onClick={() => void resetAll()}
                className="inline-flex items-center gap-1.5 font-bold text-ink-500 hover:text-ink-800"
              >
                <RotateCcw size={14} />
                清空重录
              </button>
            </div>
          </div>
        ) : null}

        {compact &&
        (state.editableTranscript ||
          ["recording", "paused", "finalizing"].includes(state.phase)) ? (
          <div className="mt-5 rounded-2xl border border-mist-100 bg-mist-50/80 p-4 text-left">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-xs font-bold text-ink-500">
                {state.phase === "completed" ? "最终转写" : "实时转写"}
              </p>
              {state.phase === "completed" ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-teal-50 px-2.5 py-1 text-[0.7rem] font-bold text-teal-700">
                  <Check size={12} /> 已完成
                </span>
              ) : null}
            </div>
            <p className="line-clamp-3 min-h-6 text-sm leading-6 text-ink-800" aria-live="polite">
              {state.editableTranscript || "正在聆听，识别结果会实时显示在这里…"}
            </p>
            {state.phase === "completed" ? (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-mist-200 pt-3">
                <div className="flex flex-wrap gap-2 text-xs font-semibold text-ink-500">
                  <span className="rounded-lg bg-white px-2.5 py-1.5">
                    置信度：
                    {state.confidence === null ? "—" : `${Math.round(state.confidence * 100)}%`}
                  </span>
                  <span className="rounded-lg bg-white px-2.5 py-1.5">
                    识别延迟：
                    {state.latencyMs === null ? "—" : `${Math.round(state.latencyMs)} ms`}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => void resetAll()}
                  className="inline-flex items-center gap-1.5 text-xs font-bold text-ink-500 hover:text-ink-800"
                >
                  <RotateCcw size={13} />
                  清空重录
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
