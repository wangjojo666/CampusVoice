import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PcmAudioRecorder } from "@/lib/asr/audio-recorder";

class FakeAudioNode {
  readonly connect = vi.fn(<T>(target: T) => target);
  readonly disconnect = vi.fn();
}

class FakeWorkletNode extends FakeAudioNode {
  static instance: FakeWorkletNode | null = null;
  readonly port = {
    onmessage: null as ((event: MessageEvent) => void) | null,
    postMessage: vi.fn(),
  };

  constructor(
    readonly context: unknown,
    readonly processorName: string,
    readonly options: unknown,
  ) {
    super();
    FakeWorkletNode.instance = this;
  }
}

class FakeAudioContext {
  static instance: FakeAudioContext | null = null;
  state = "running";
  readonly destination = new FakeAudioNode();
  readonly source = new FakeAudioNode();
  readonly gain = Object.assign(new FakeAudioNode(), { gain: { value: 1 } });
  readonly audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  readonly createMediaStreamSource = vi.fn(() => this.source);
  readonly createGain = vi.fn(() => this.gain);
  readonly resume = vi.fn().mockResolvedValue(undefined);
  readonly suspend = vi.fn().mockResolvedValue(undefined);
  readonly close = vi.fn(async () => {
    this.state = "closed";
  });

  constructor(readonly options: unknown) {
    FakeAudioContext.instance = this;
  }
}

describe("PCM recorder lifecycle", () => {
  const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");

  beforeEach(() => {
    FakeAudioContext.instance = null;
    FakeWorkletNode.instance = null;
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalMediaDevices) {
      Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
    } else {
      Reflect.deleteProperty(navigator, "mediaDevices");
    }
  });

  it("captures mono microphone frames, reports levels, and releases every media resource", async () => {
    const stopTrack = vi.fn();
    const stream = { getTracks: () => [{ stop: stopTrack }] };
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const onChunk = vi.fn();
    const onLevel = vi.fn();
    const recorder = new PcmAudioRecorder();

    await recorder.start({ onChunk, onLevel });

    expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    expect(FakeAudioContext.instance?.options).toEqual({ latencyHint: "interactive" });
    expect(FakeAudioContext.instance?.audioWorklet.addModule).toHaveBeenCalledWith(
      "/audio-processor.js",
    );
    expect(FakeWorkletNode.instance).toMatchObject({
      processorName: "campusvoice-pcm-processor",
      options: {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      },
    });
    expect(FakeAudioContext.instance?.gain.gain.value).toBe(0);

    const buffer = new ArrayBuffer(8);
    FakeWorkletNode.instance?.port.onmessage?.(
      new MessageEvent("message", { data: { type: "level", level: 0.42 } }),
    );
    FakeWorkletNode.instance?.port.onmessage?.(
      new MessageEvent("message", { data: { type: "audio", buffer } }),
    );
    FakeWorkletNode.instance?.port.onmessage?.(
      new MessageEvent("message", { data: { type: "ignored" } }),
    );
    expect(onLevel).toHaveBeenCalledWith(0.42);
    expect(onChunk).toHaveBeenCalledWith(buffer);

    await recorder.pause();
    await recorder.resume();
    expect(FakeAudioContext.instance?.suspend).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.resume).toHaveBeenCalledTimes(2);

    await recorder.stop();

    expect(FakeWorkletNode.instance?.port.postMessage).toHaveBeenCalledWith({ type: "flush" });
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.gain.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
  });

  it("fails before opening a session when microphone capture is unavailable", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: undefined,
    });

    await expect(
      new PcmAudioRecorder().start({ onChunk: vi.fn(), onLevel: vi.fn() }),
    ).rejects.toMatchObject({
      name: "NotSupportedError",
      message: "当前浏览器不支持麦克风采集。",
    });
    expect(FakeAudioContext.instance).toBeNull();
  });
});
