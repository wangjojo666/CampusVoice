/* global AudioWorkletProcessor, registerProcessor, sampleRate */

class CampusVoicePcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.chunkSize = 1600;
    this.samples = [];
    this.levelTick = 0;
    this.port.onmessage = (event) => {
      if (event.data?.type === "flush") this.flush();
    };
  }

  downsample(input) {
    if (sampleRate === this.targetRate) return input;
    const ratio = sampleRate / this.targetRate;
    const outputLength = Math.max(1, Math.floor(input.length / ratio));
    const output = new Float32Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const start = Math.floor(index * ratio);
      const end = Math.min(input.length, Math.floor((index + 1) * ratio));
      let sum = 0;
      for (let cursor = start; cursor < end; cursor += 1) sum += input[cursor] ?? 0;
      output[index] = sum / Math.max(1, end - start);
    }
    return output;
  }

  emitChunk(floatSamples) {
    const pcm = new Int16Array(floatSamples.length);
    for (let index = 0; index < floatSamples.length; index += 1) {
      const value = Math.max(-1, Math.min(1, floatSamples[index] ?? 0));
      pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    this.port.postMessage({ type: "audio", buffer: pcm.buffer }, [pcm.buffer]);
  }

  flush() {
    if (this.samples.length > 0) {
      this.emitChunk(new Float32Array(this.samples));
      this.samples = [];
    }
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const downsampled = this.downsample(input);
    let levelSum = 0;
    for (const sample of downsampled) {
      this.samples.push(sample);
      levelSum += sample * sample;
      if (this.samples.length >= this.chunkSize) {
        this.emitChunk(new Float32Array(this.samples.splice(0, this.chunkSize)));
      }
    }
    this.levelTick += 1;
    if (this.levelTick >= 4) {
      const rms = Math.sqrt(levelSum / Math.max(1, downsampled.length));
      this.port.postMessage({ type: "level", level: Math.min(1, rms * 5) });
      this.levelTick = 0;
    }
    return true;
  }
}

registerProcessor("campusvoice-pcm-processor", CampusVoicePcmProcessor);
