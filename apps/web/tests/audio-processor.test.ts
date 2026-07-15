import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

type WorkletMessage = { type: "audio"; buffer: ArrayBuffer } | { type: "level"; level: number };

type PostedMessage = { message: WorkletMessage; transfer?: Transferable[] };

type WorkletProcessor = {
  port: {
    messages: PostedMessage[];
    onmessage: ((event: MessageEvent) => void) | null;
  };
  process(inputs: Float32Array[][]): boolean;
};

const processorSource = readFileSync(resolve(process.cwd(), "public/audio-processor.js"), "utf8");

function createProcessor(sourceRate: number): WorkletProcessor {
  let Processor: (new () => WorkletProcessor) | undefined;

  class FakeAudioWorkletProcessor {
    readonly port = {
      messages: [] as PostedMessage[],
      onmessage: null as ((event: MessageEvent) => void) | null,
      postMessage: (message: WorkletMessage, transfer?: Transferable[]) => {
        this.port.messages.push({ message, transfer });
      },
    };
  }

  vm.runInNewContext(processorSource, {
    ArrayBuffer,
    AudioWorkletProcessor: FakeAudioWorkletProcessor,
    Float32Array,
    Int16Array,
    Math,
    registerProcessor: (_name: string, implementation: new () => WorkletProcessor) => {
      Processor = implementation;
    },
    sampleRate: sourceRate,
  });

  if (!Processor) throw new Error("AudioWorklet processor was not registered");
  return new Processor();
}

function isAudioMessage(
  entry: PostedMessage,
): entry is PostedMessage & { message: { type: "audio"; buffer: ArrayBuffer } } {
  return entry.message.type === "audio";
}

function runProcessor(
  sourceRate: number,
  input: Float32Array,
  partitionSizes: number[],
): { audio: Int16Array; messages: WorkletProcessor["port"]["messages"] } {
  const processor = createProcessor(sourceRate);
  let offset = 0;
  let partition = 0;
  while (offset < input.length) {
    const requestedSize = partitionSizes[partition % partitionSizes.length] ?? 128;
    const end = Math.min(input.length, offset + requestedSize);
    expect(processor.process([[input.slice(offset, end)]])).toBe(true);
    offset = end;
    partition += 1;
  }

  processor.port.onmessage?.(new MessageEvent("message", { data: { type: "flush" } }));
  processor.port.onmessage?.(new MessageEvent("message", { data: { type: "flush" } }));

  const chunks = processor.port.messages.filter(isAudioMessage);
  const sampleCount = chunks.reduce(
    (total, { message }) => total + new Int16Array(message.buffer).length,
    0,
  );
  const audio = new Int16Array(sampleCount);
  let writeOffset = 0;
  for (const { message } of chunks) {
    const chunk = new Int16Array(message.buffer);
    audio.set(chunk, writeOffset);
    writeOffset += chunk.length;
  }

  return { audio, messages: processor.port.messages };
}

function sineWave(sampleRate: number): Float32Array {
  return Float32Array.from(
    { length: sampleRate },
    (_, index) => Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 0.5,
  );
}

function seededSignal(length: number): Float32Array {
  let state = 0x12345678;
  return Float32Array.from({ length }, () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return (state / 0xffffffff - 0.5) * 0.8;
  });
}

describe("CampusVoice PCM AudioWorklet", () => {
  it("emits exactly 16 kHz without losing samples at 48 kHz render boundaries", () => {
    const result = runProcessor(48000, sineWave(48000), [128]);
    const audioMessages = result.messages.filter(isAudioMessage);

    expect(result.audio).toHaveLength(16000);
    expect(audioMessages).toHaveLength(10);
    for (const { message, transfer } of audioMessages) {
      expect(new Int16Array(message.buffer)).toHaveLength(1600);
      expect(transfer).toEqual([message.buffer]);
    }
    const levels = result.messages
      .filter(({ message }) => message.type === "level")
      .map(({ message }) => (message.type === "level" ? message.level : -1));
    expect(levels.length).toBeGreaterThan(0);
    expect(levels.every((level) => level >= 0 && level <= 1)).toBe(true);
  });

  it("is independent of block partitioning at 44.1 kHz", () => {
    const input = seededSignal(44100);
    const regular = runProcessor(44100, input, [128]);
    const varied = runProcessor(44100, input, [17, 211, 64, 509, 3, 128]);

    expect(regular.audio).toHaveLength(16000);
    expect(varied.audio).toHaveLength(16000);
    expect(Array.from(varied.audio)).toEqual(Array.from(regular.audio));
    expect(varied.messages.filter(isAudioMessage)).toHaveLength(10);
  });

  it("flushes a partial PCM chunk once and starts a clean resampling epoch", () => {
    const processor = createProcessor(44100);
    expect(processor.process([[seededSignal(5000)]])).toBe(true);

    processor.port.onmessage?.(new MessageEvent("message", { data: { type: "flush" } }));
    const firstEpoch = processor.port.messages.filter(isAudioMessage);
    expect(firstEpoch.map(({ message }) => new Int16Array(message.buffer).length)).toEqual([
      1600, 214,
    ]);

    processor.port.onmessage?.(new MessageEvent("message", { data: { type: "flush" } }));
    expect(processor.port.messages.filter(isAudioMessage)).toHaveLength(2);

    expect(processor.process([[seededSignal(4410)]])).toBe(true);
    processor.port.onmessage?.(new MessageEvent("message", { data: { type: "flush" } }));
    expect(
      processor.port.messages
        .filter(isAudioMessage)
        .map(({ message }) => new Int16Array(message.buffer).length),
    ).toEqual([1600, 214, 1600]);
  });
});
