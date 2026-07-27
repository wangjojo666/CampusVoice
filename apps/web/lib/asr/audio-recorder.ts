export interface RecorderHandlers {
  onChunk: (chunk: ArrayBuffer) => void;
  onLevel: (level: number) => void;
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

  async start(handlers: RecorderHandlers) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new DOMException("当前浏览器不支持麦克风采集。", "NotSupportedError");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    this.context = new AudioContext({ latencyHint: "interactive" });
    await this.context.audioWorklet.addModule("/audio-processor.js");
    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, "campusvoice-pcm-processor", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.mutedOutput = this.context.createGain();
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
    this.source.connect(this.worklet).connect(this.mutedOutput).connect(this.context.destination);
    await this.context.resume();
  }

  private waitForDrain(worklet: AudioWorkletNode) {
    const requestId = (this.drainRequestId += 1);
    return new Promise<void>((resolve, reject) => {
      const timeoutId = window.setTimeout(() => {
        if (this.pendingDrain?.requestId === requestId) this.pendingDrain = null;
        reject(new Error("AudioWorklet drain acknowledgement timed out"));
      }, 2_000);

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

  async stop() {
    if (this.stopPromise) return this.stopPromise;

    const operation = this.stopResources();
    this.stopPromise = operation;
    try {
      await operation;
    } finally {
      if (this.stopPromise === operation) this.stopPromise = null;
    }
  }

  private async stopResources() {
    const context = this.context;
    const stream = this.stream;
    const source = this.source;
    const worklet = this.worklet;
    const mutedOutput = this.mutedOutput;

    this.context = null;
    this.stream = null;
    this.source = null;
    this.worklet = null;
    this.mutedOutput = null;

    try {
      stream?.getTracks().forEach((track) => track.stop());
      source?.disconnect();
      if (worklet && context && context.state !== "closed") {
        if (context.state === "suspended") await context.resume();
        await this.waitForDrain(worklet);
      }
    } finally {
      if (worklet) worklet.port.onmessage = null;
      worklet?.disconnect();
      mutedOutput?.disconnect();
      if (context && context.state !== "closed") await context.close();
    }
  }
}
