/* global AudioWorkletProcessor, registerProcessor, sampleRate */

class CampusVoicePcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.chunkSize = 1600;
    this.samples = [];
    this.levelTick = 0;
    this.resampleBuffer = new Float32Array(0);
    this.inputSampleCount = 0;
    this.outputSampleCount = 0;
    this.port.onmessage = (event) => {
      if (event.data?.type === "flush") this.flush();
    };
  }

  downsample(input) {
    if (sampleRate === this.targetRate) return input;

    const bufferStart = this.inputSampleCount - this.resampleBuffer.length;
    const combined = new Float32Array(this.resampleBuffer.length + input.length);
    combined.set(this.resampleBuffer);
    combined.set(input, this.resampleBuffer.length);
    this.inputSampleCount += input.length;

    const availableOutputCount = Math.floor((this.inputSampleCount * this.targetRate) / sampleRate);
    const outputLength = availableOutputCount - this.outputSampleCount;
    const output = new Float32Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const outputIndex = this.outputSampleCount + index;
      const start = outputIndex * sampleRate - bufferStart * this.targetRate;
      const end = (outputIndex + 1) * sampleRate - bufferStart * this.targetRate;
      let sum = 0;
      for (
        let cursor = Math.floor(start / this.targetRate);
        cursor < Math.ceil(end / this.targetRate);
        cursor += 1
      ) {
        const overlap =
          Math.min(end, (cursor + 1) * this.targetRate) - Math.max(start, cursor * this.targetRate);
        if (overlap > 0) sum += (combined[cursor] ?? 0) * overlap;
      }
      output[index] = sum / sampleRate;
    }

    this.outputSampleCount = availableOutputCount;
    const nextInputPosition = (this.outputSampleCount * sampleRate) / this.targetRate;
    const discardCount = Math.max(0, Math.floor(nextInputPosition) - bufferStart);
    this.resampleBuffer = combined.slice(Math.min(discardCount, combined.length));
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
    this.resampleBuffer = new Float32Array(0);
    this.inputSampleCount = 0;
    this.outputSampleCount = 0;
    this.levelTick = 0;
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
