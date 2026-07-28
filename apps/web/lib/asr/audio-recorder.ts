export interface RecorderHandlers {
  onChunk: (chunk: ArrayBuffer) => void;
  onLevel: (level: number) => void;
}

const DRAIN_TIMEOUT_MS = 2_000;
const CONTEXT_CLOSE_TIMEOUT_MS = 1_000;

interface RecorderResources {
  context: AudioContext | null;
  stream: MediaStream | null;
  source: MediaStreamAudioSourceNode | null;
  worklet: AudioWorkletNode | null;
  mutedOutput: GainNode | null;
}

function withTimeout<T>(label: string, timeoutMs: number, operation: () => Promise<T>): Promise<T> {
  let timeoutId: number | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs);
  });
  return Promise.race([Promise.resolve().then(operation), timeout]).finally(() => {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
  });
}

function settled<T>(operation: Promise<T>): Promise<PromiseSettledResult<T>> {
  return operation.then(
    (value) => ({ status: "fulfilled", value }),
    (reason: unknown) => ({ status: "rejected", reason }),
  );
}

function startCancelled() {
  return new DOMException("麦克风启动已取消。", "AbortError");
}

export class PcmAudioRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private mutedOutput: GainNode | null = null;
  private stopPromise: Promise<void> | null = null;
  private drainRequestId = 0;
  private pendingDrain: {
    requestId: number;
    complete: () => void;
  } | null = null;
  private lifecycle = 0;

  async start(handlers: RecorderHandlers) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new DOMException("当前浏览器不支持麦克风采集。", "NotSupportedError");
    }
    const lifecycle = this.lifecycle + 1;
    this.lifecycle = lifecycle;
    const previousStop = this.stopPromise;
    this.stopPromise = null;
    await previousStop?.catch(() => undefined);
    if (this.lifecycle !== lifecycle) throw startCancelled();

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    if (this.lifecycle !== lifecycle) {
      for (const track of stream.getTracks()) track.stop();
      throw startCancelled();
    }
    this.stream = stream;
    const context = new AudioContext({ latencyHint: "interactive" });
    this.context = context;
    await context.audioWorklet.addModule("/audio-processor.js");
    if (this.lifecycle !== lifecycle || this.context !== context || this.stream !== stream) {
      throw startCancelled();
    }
    this.source = context.createMediaStreamSource(stream);
    this.worklet = new AudioWorkletNode(context, "campusvoice-pcm-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.mutedOutput = context.createGain();
    this.mutedOutput.gain.value = 0;
    this.worklet.port.onmessage = (
      event: MessageEvent<{
        type: string;
        level?: number;
        buffer?: ArrayBuffer;
        requestId?: number;
      }>,
    ) => {
      if (event.data.type === "level" && typeof event.data.level === "number")
        handlers.onLevel(event.data.level);
      if (event.data.type === "audio" && event.data.buffer) handlers.onChunk(event.data.buffer);
      if (
        event.data.type === "drained" &&
        typeof event.data.requestId === "number" &&
        this.pendingDrain?.requestId === event.data.requestId
      ) {
        this.pendingDrain.complete();
      }
    };
    this.source.connect(this.worklet).connect(this.mutedOutput).connect(context.destination);
    await context.resume();
    if (this.lifecycle !== lifecycle || this.context !== context || this.stream !== stream) {
      throw startCancelled();
    }
  }

  private waitForDrain(worklet: AudioWorkletNode) {
    const requestId = (this.drainRequestId += 1);
    return new Promise<void>((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        if (this.pendingDrain?.requestId === requestId) this.pendingDrain = null;
        reject(new Error("AudioWorklet drain acknowledgement timed out"));
      }, DRAIN_TIMEOUT_MS);

      this.pendingDrain = {
        requestId,
        complete: () => {
          window.clearTimeout(timeoutId);
          if (this.pendingDrain?.requestId === requestId) this.pendingDrain = null;
          resolve();
        },
      };

      try {
        worklet.port.postMessage({ type: "drain", requestId });
      } catch (reason) {
        window.clearTimeout(timeoutId);
        if (this.pendingDrain?.requestId === requestId) this.pendingDrain = null;
        reject(reason);
      }
    });
  }

  async pause() {
    await this.context?.suspend();
  }

  async resume() {
    await this.context?.resume();
  }

  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    this.lifecycle += 1;
    this.stopPromise = this.stopResources(this.takeResources());
    return this.stopPromise;
  }

  private takeResources(): RecorderResources {
    const resources = {
      context: this.context,
      stream: this.stream,
      source: this.source,
      worklet: this.worklet,
      mutedOutput: this.mutedOutput,
    };
    this.context = null;
    this.stream = null;
    this.source = null;
    this.worklet = null;
    this.mutedOutput = null;
    return resources;
  }

  private async stopResources({
    context,
    stream,
    source,
    worklet,
    mutedOutput,
  }: RecorderResources) {
    const errors: unknown[] = [];

    if (worklet) {
      if (!context || context.state === "closed") {
        errors.push(new Error("AudioWorklet cannot drain because its AudioContext is closed"));
      } else {
        const drainResultPromise = settled(this.waitForDrain(worklet));
        const resumeResultPromise =
          context.state === "running"
            ? Promise.resolve<PromiseSettledResult<void>>({
                status: "fulfilled",
                value: undefined,
              })
            : settled(withTimeout("AudioContext resume", DRAIN_TIMEOUT_MS, () => context.resume()));
        const drainResult = await drainResultPromise;
        if (drainResult.status === "rejected") {
          errors.push(drainResult.reason);
          const resumeResult = await resumeResultPromise;
          if (resumeResult.status === "rejected") errors.push(resumeResult.reason);
        } else {
          void resumeResultPromise;
        }
      }
    }

    const capture = (operation: () => void) => {
      try {
        operation();
      } catch (reason) {
        errors.push(reason);
      }
    };

    if (worklet) {
      capture(() => {
        worklet.port.onmessage = null;
      });
    }
    let tracks: MediaStreamTrack[] = [];
    if (stream) {
      capture(() => {
        tracks = stream.getTracks();
      });
    }
    for (const track of tracks) capture(() => track.stop());
    if (source) capture(() => source.disconnect());
    if (worklet) capture(() => worklet.disconnect());
    if (mutedOutput) capture(() => mutedOutput.disconnect());
    if (context && context.state !== "closed") {
      try {
        await withTimeout("AudioContext close", CONTEXT_CLOSE_TIMEOUT_MS, () => context.close());
      } catch (reason) {
        errors.push(reason);
      }
    }

    if (errors.length === 1) throw errors[0];
    if (errors.length > 1) {
      throw new AggregateError(errors, "PCM recorder stop completed with multiple failures", {
        cause: errors[0],
      });
    }
  }
}
