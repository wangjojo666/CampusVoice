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
    postMessage: vi.fn((message: { type: string; requestId?: number }) => {
      if (message.type !== "drain") return;
      queueMicrotask(() => {
        this.port.onmessage?.(
          new MessageEvent("message", { data: { type: "audio", buffer: new ArrayBuffer(4) } }),
        );
        this.port.onmessage?.(
          new MessageEvent("message", { data: { type: "drained", requestId: message.requestId } }),
        );
      });
    }),
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
  static moduleLoad: Promise<void> = Promise.resolve();
  state = "running";
  readonly destination = new FakeAudioNode();
  readonly source = new FakeAudioNode();
  readonly gain = Object.assign(new FakeAudioNode(), { gain: { value: 1 } });
  readonly audioWorklet = { addModule: vi.fn(() => FakeAudioContext.moduleLoad) };
  readonly createMediaStreamSource = vi.fn(() => this.source);
  readonly createGain = vi.fn(() => this.gain);
  readonly resume = vi.fn(async () => {
    this.state = "running";
  });
  readonly suspend = vi.fn(async () => {
    this.state = "suspended";
  });
  readonly close = vi.fn(async () => {
    this.state = "closed";
  });

  constructor(readonly options: unknown) {
    FakeAudioContext.instance = this;
  }
}

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("PCM recorder lifecycle", () => {
  const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");

  beforeEach(() => {
    FakeAudioContext.instance = null;
    FakeAudioContext.moduleLoad = Promise.resolve();
    FakeWorkletNode.instance = null;
    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    if (originalMediaDevices) {
      Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
    } else {
      Reflect.deleteProperty(navigator, "mediaDevices");
    }
  });

  it("cancels a pending permission request without reacquiring the microphone", async () => {
    const permission = deferred<MediaStream>();
    const stopTrack = vi.fn();
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream;
    const getUserMedia = vi.fn(() => permission.promise);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    const recorder = new PcmAudioRecorder();

    const started = recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    await Promise.resolve();
    expect(getUserMedia).toHaveBeenCalledOnce();
    await recorder.stop();
    permission.resolve(stream);

    await expect(started).rejects.toMatchObject({ name: "AbortError" });
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance).toBeNull();
  });

  it("cancels startup while the AudioWorklet module is still loading", async () => {
    const moduleLoad = deferred<void>();
    FakeAudioContext.moduleLoad = moduleLoad.promise;
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();

    const started = recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    await vi.waitFor(() => expect(FakeAudioContext.instance).not.toBeNull());
    expect(FakeAudioContext.instance?.audioWorklet.addModule).toHaveBeenCalledOnce();
    await recorder.stop();
    moduleLoad.resolve();

    await expect(started).rejects.toMatchObject({ name: "AbortError" });
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.createMediaStreamSource).not.toHaveBeenCalled();
    expect(FakeWorkletNode.instance).toBeNull();
  });

  it("bounds a pending startup resume after cancellation", async () => {
    vi.useFakeTimers();
    const resume = deferred<void>();
    class PendingResumeAudioContext extends FakeAudioContext {
      override readonly resume = vi.fn(() => resume.promise);
    }
    vi.stubGlobal("AudioContext", PendingResumeAudioContext);
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();

    const started = recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    const startFailure = started.catch((reason: unknown) => reason);
    await vi.waitFor(() => expect(FakeAudioContext.instance?.resume).toHaveBeenCalledOnce());
    const stopped = recorder.stop();
    await vi.advanceTimersByTimeAsync(2_000);

    await expect(startFailure).resolves.toMatchObject({ name: "AbortError" });
    await stopped;
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
    resume.resolve();
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
    await recorder.pause();
    expect(FakeAudioContext.instance?.suspend).toHaveBeenCalledTimes(2);
    expect(FakeAudioContext.instance?.resume).toHaveBeenCalledTimes(2);

    const firstStop = recorder.stop();
    const secondStop = recorder.stop();
    expect(secondStop).toBe(firstStop);
    await Promise.all([firstStop, secondStop]);

    expect(FakeWorkletNode.instance?.port.postMessage).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.port.postMessage).toHaveBeenCalledWith({
      type: "drain",
      requestId: 1,
    });
    expect(FakeAudioContext.instance?.resume).toHaveBeenCalledTimes(3);
    expect((onChunk.mock.calls.at(-1)?.[0] as ArrayBuffer).byteLength).toBe(4);
    expect(onChunk.mock.invocationCallOrder.at(-1)).toBeLessThan(
      FakeWorkletNode.instance!.disconnect.mock.invocationCallOrder[0]!,
    );
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.gain.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.port.onmessage).toBeNull();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
  });

  it("times out a missing drain acknowledgement and still releases every resource", async () => {
    vi.useFakeTimers();
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    FakeWorkletNode.instance?.port.postMessage.mockImplementationOnce(() => undefined);

    const stopOperation = recorder.stop();
    const stopped = expect(stopOperation).rejects.toThrow(
      "AudioWorklet drain acknowledgement timed out",
    );
    expect(recorder.stop()).toBe(stopOperation);
    await vi.advanceTimersByTimeAsync(2_000);
    await stopped;

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.gain.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.port.onmessage).toBeNull();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
    expect(recorder.stop()).toBe(stopOperation);
    await expect(recorder.stop()).rejects.toThrow("AudioWorklet drain acknowledgement timed out");
  });

  it("releases resources when posting the drain request fails synchronously", async () => {
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    const failure = new DOMException("MessagePort is closed", "InvalidStateError");
    FakeWorkletNode.instance?.port.postMessage.mockImplementationOnce(() => {
      throw failure;
    });

    await expect(recorder.stop()).rejects.toBe(failure);

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.gain.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.port.onmessage).toBeNull();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
  });

  it("waits for the matching drain ACK before releasing upstream resources", async () => {
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    FakeWorkletNode.instance?.port.postMessage.mockImplementationOnce(() => undefined);

    const stopped = recorder.stop();
    const requestId = FakeWorkletNode.instance?.port.postMessage.mock.calls[0]?.[0].requestId;
    expect(stopTrack).not.toHaveBeenCalled();
    expect(FakeAudioContext.instance?.source.disconnect).not.toHaveBeenCalled();

    FakeWorkletNode.instance?.port.onmessage?.(
      new MessageEvent("message", { data: { type: "drained", requestId: (requestId ?? 0) + 1 } }),
    );
    await Promise.resolve();
    expect(stopTrack).not.toHaveBeenCalled();

    FakeWorkletNode.instance?.port.onmessage?.(
      new MessageEvent("message", { data: { type: "drained", requestId } }),
    );
    await stopped;

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
  });

  it("accepts a drain ACK without waiting for an auxiliary resume", async () => {
    const resume = deferred<void>();
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    await recorder.pause();
    FakeAudioContext.instance?.resume.mockImplementationOnce(() => resume.promise);

    await recorder.stop();

    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
    resume.resolve();
    await Promise.resolve();
  });

  it("bounds both a missing drain ACK and a suspended context resume", async () => {
    vi.useFakeTimers();
    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    await recorder.pause();
    FakeWorkletNode.instance?.port.postMessage.mockImplementationOnce(() => undefined);
    FakeAudioContext.instance?.resume.mockImplementationOnce(() => new Promise<void>(() => {}));

    const failure = recorder.stop().catch((reason: unknown) => reason);
    await vi.advanceTimersByTimeAsync(2_000);
    const error = await failure;

    expect(error).toBeInstanceOf(AggregateError);
    expect((error as AggregateError).errors).toEqual([
      expect.objectContaining({ message: "AudioWorklet drain acknowledgement timed out" }),
      expect.objectContaining({ message: "AudioContext resume timed out" }),
    ]);
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.close).toHaveBeenCalledOnce();
  });

  it("bounds an AudioContext close that never settles", async () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    FakeAudioContext.instance?.close.mockImplementationOnce(() => new Promise<void>(() => {}));

    const stopped = expect(recorder.stop()).rejects.toThrow("AudioContext close timed out");
    await vi.advanceTimersByTimeAsync(1_000);
    await stopped;

    expect(FakeAudioContext.instance?.source.disconnect).toHaveBeenCalledOnce();
    expect(FakeWorkletNode.instance?.disconnect).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instance?.gain.disconnect).toHaveBeenCalledOnce();
  });

  it("preserves ordered failures while attempting every cleanup resource", async () => {
    const drainFailure = new DOMException("MessagePort is closed", "InvalidStateError");
    const firstTrackFailure = new Error("first track failed");
    const sourceFailure = new Error("source disconnect failed");
    const workletFailure = new Error("worklet disconnect failed");
    const closeFailure = new Error("context close failed");
    const firstTrack = vi.fn(() => {
      throw firstTrackFailure;
    });
    const secondTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: firstTrack }, { stop: secondTrack }],
        }),
      },
    });
    const recorder = new PcmAudioRecorder();
    await recorder.start({ onChunk: vi.fn(), onLevel: vi.fn() });
    FakeWorkletNode.instance?.port.postMessage.mockImplementationOnce(() => {
      throw drainFailure;
    });
    FakeAudioContext.instance?.source.disconnect.mockImplementationOnce(() => {
      throw sourceFailure;
    });
    FakeWorkletNode.instance?.disconnect.mockImplementationOnce(() => {
      throw workletFailure;
    });
    FakeAudioContext.instance?.close.mockRejectedValueOnce(closeFailure);

    const error = await recorder.stop().catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(AggregateError);
    expect((error as AggregateError).errors).toEqual([
      drainFailure,
      firstTrackFailure,
      sourceFailure,
      workletFailure,
      closeFailure,
    ]);
    expect((error as Error & { cause?: unknown }).cause).toBe(drainFailure);
    expect(secondTrack).toHaveBeenCalledOnce();
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
