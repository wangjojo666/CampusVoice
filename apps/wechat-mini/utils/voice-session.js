const DEFAULT_STATUS = "点击开始，说出你的任务、日程或校园问题";
const READY_TIMEOUT_MS = 15000;
const FINAL_FILE_READ_TIMEOUT_MS = 5000;
const FINALIZATION_IDLE_TIMEOUT_MS = 15000;
const FINALIZATION_HARD_TIMEOUT_MS = 180000;
const MAX_AUDIO_CHUNK_BYTES = 1024;

function createVoiceSessionController(options) {
  return new VoiceSessionController(options);
}

class VoiceSessionController {
  constructor(options) {
    this.wx = options.wxApi;
    this.recorder = options.recorder;
    this.setTimer = options.setTimer || setTimeout;
    this.clearTimer = options.clearTimer || clearTimeout;
    this.observer = null;
    this.phase = "idle";
    this.statusText = DEFAULT_STATUS;
    this.error = "";
    this.finalSegments = [];
    this.interimTranscript = "";
    this.generation = 0;
    this.connection = null;
    this.recorderActive = false;
    this.recorderStopping = false;
    this.interrupted = false;
    this.stopSent = false;
    this.serverComplete = false;
    this.terminalEmitted = false;
    this.finalFilePending = false;
    this.finalFileTimer = null;
    this.finalizationIdleTimer = null;
    this.finalizationHardTimer = null;
    this.audioFrames = [];
    this.installRecorderListeners();
  }

  installRecorderListeners() {
    this.recorder.onFrameRecorded((event) => this.handleFrame(event || {}));
    this.recorder.onError(() => this.handleRecorderError());
    this.recorder.onInterruptionBegin(() => {
      this.interrupted = true;
      this.failClosed("录音已被系统中断，请返回后重新开始");
    });
    if (this.recorder.onInterruptionEnd) {
      this.recorder.onInterruptionEnd(() => {
        this.interrupted = false;
      });
    }
    this.recorder.onStop((event) => this.handleRecorderStop(event || {}));
  }

  attach(observer) {
    this.observer = observer;
    this.notify();
  }

  detach(observer) {
    if (this.observer !== observer) return;
    this.observer = null;
    if (!["idle", "done"].includes(this.phase)) {
      this.failClosed("页面已关闭，录音已安全停止");
    } else {
      this.reset();
    }
  }

  snapshot() {
    return {
      phase: this.phase,
      statusText: this.statusText,
      transcript: [...this.finalSegments, this.interimTranscript].filter(Boolean).join(""),
      error: this.error,
    };
  }

  notify() {
    if (this.observer && this.observer.onState) this.observer.onState(this.snapshot());
  }

  begin() {
    if (this.phase !== "idle" || this.recorderActive || this.recorderStopping) return null;
    this.generation += 1;
    this.phase = "connecting";
    this.statusText = "正在建立安全识别连接…";
    this.error = "";
    this.finalSegments = [];
    this.interimTranscript = "";
    this.stopSent = false;
    this.serverComplete = false;
    this.terminalEmitted = false;
    this.clearFinalFileDrain();
    this.clearFinalizationTimer();
    this.audioFrames = [];
    this.notify();
    return this.generation;
  }

  isCurrent(generation) {
    return generation === this.generation && this.phase !== "idle" && this.phase !== "done";
  }

  cancel(generation, message) {
    if (this.isCurrent(generation)) this.failClosed(message);
  }

  connect(url, ticket, generation) {
    if (!this.isCurrent(generation) || this.phase !== "connecting") return false;
    let task;
    try {
      task = this.wx.connectSocket({
        url,
        protocols: ["campusvoice", "campusvoice.ticket." + ticket],
        timeout: READY_TIMEOUT_MS,
      });
    } catch (_error) {
      this.failClosed("无法建立语音识别连接");
      return false;
    }
    const connection = {
      task,
      generation,
      closed: false,
      ready: false,
      readyTimer: null,
    };
    this.connection = connection;
    connection.readyTimer = this.setTimer(() => {
      if (this.isCurrentConnection(connection) && !connection.ready) {
        this.failClosed("语音识别连接准备超时，请重试");
      }
    }, READY_TIMEOUT_MS);

    task.onOpen(() => {
      if (!this.isCurrentConnection(connection)) return;
      this.sendControl(
        connection,
        {
          type: "start",
          audio_format: "mp3",
          sample_rate_hz: 16000,
          channels: 1,
          sample_width_bytes: 2,
          language: "zh",
          hotwords: [],
        },
        "无法启动语音识别会话",
      );
    });
    task.onMessage(({ data }) => this.handleSocketMessage(connection, data));
    task.onError(() => {
      if (this.isCurrentConnection(connection)) {
        this.failClosed(connection.ready ? "语音连接已中断，请重试" : "无法建立语音识别连接");
      }
    });
    task.onClose((event) => this.handleSocketClose(connection, event || {}));
    return true;
  }

  isCurrentConnection(connection) {
    return (
      this.connection === connection &&
      !connection.closed &&
      connection.generation === this.generation
    );
  }

  clearReadyTimer(connection) {
    if (!connection || connection.readyTimer === null) return;
    this.clearTimer(connection.readyTimer);
    connection.readyTimer = null;
  }

  send(connection, data, failureMessage) {
    if (!this.isCurrentConnection(connection)) return false;
    try {
      connection.task.send({
        data,
        fail: () => {
          if (this.isCurrentConnection(connection)) this.failClosed(failureMessage);
        },
      });
      return true;
    } catch (_error) {
      if (this.isCurrentConnection(connection)) this.failClosed(failureMessage);
      return false;
    }
  }

  sendAudio(connection, data, failureMessage) {
    if (!(data instanceof ArrayBuffer)) return false;
    if (data.byteLength <= MAX_AUDIO_CHUNK_BYTES) {
      return this.send(connection, data, failureMessage);
    }
    for (let offset = 0; offset < data.byteLength; offset += MAX_AUDIO_CHUNK_BYTES) {
      if (
        !this.send(connection, data.slice(offset, offset + MAX_AUDIO_CHUNK_BYTES), failureMessage)
      ) {
        return false;
      }
    }
    return true;
  }

  sendControl(connection, message, failureMessage) {
    return this.send(connection, JSON.stringify(message), failureMessage);
  }

  handleSocketMessage(connection, data) {
    if (!this.isCurrentConnection(connection) || typeof data !== "string") return;
    let message;
    try {
      message = JSON.parse(data);
    } catch (_error) {
      this.failClosed("识别服务返回了无法解析的数据");
      return;
    }
    if (!message || typeof message.type !== "string") return;
    if (message.type === "ready") {
      if (connection.ready || this.phase !== "connecting") return;
      connection.ready = true;
      this.clearReadyTimer(connection);
      this.startRecorder(connection.generation);
      return;
    }
    if (message.type === "interim") {
      if (!["recording", "finalizing"].includes(this.phase)) return;
      this.interimTranscript = typeof message.text === "string" ? message.text : "";
      if (this.phase === "finalizing" && this.stopSent) {
        this.scheduleFinalizationTimeout(connection);
      }
      this.notify();
      return;
    }
    if (message.type === "final") {
      if (!["recording", "finalizing"].includes(this.phase)) return;
      if (typeof message.text === "string" && message.text) this.finalSegments.push(message.text);
      this.interimTranscript = "";
      if (this.phase === "finalizing" && this.stopSent) {
        this.scheduleFinalizationTimeout(connection);
      }
      this.notify();
      return;
    }
    if (message.type === "finalizing") {
      if (this.phase !== "finalizing" || !this.stopSent) return;
      this.statusText = "正在完成识别…";
      this.scheduleFinalizationTimeout(connection);
      this.notify();
      return;
    }
    if (message.type === "error") {
      this.failClosed(
        typeof message.message === "string" && message.message ? message.message : "语音识别失败",
      );
      return;
    }
    if (message.type === "complete") {
      this.serverComplete = true;
      this.clearFinalizationTimer();
      if (this.recorderActive || this.recorderStopping) {
        this.phase = "finalizing";
        this.statusText = "正在完成识别…";
        this.notify();
        this.requestRecorderStop();
      } else {
        this.completeOnce();
      }
    }
  }

  startRecorder(generation) {
    if (!this.isCurrent(generation) || this.phase !== "connecting") return;
    this.recorderActive = true;
    this.recorderStopping = false;
    try {
      this.recorder.start({
        duration: 300000,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
        format: "mp3",
        frameSize: 4,
      });
    } catch (_error) {
      this.recorderActive = false;
      this.failClosed("麦克风采集失败，请检查权限后重试");
      return;
    }
    if (!this.isCurrent(generation) || this.phase !== "connecting") return;
    this.phase = "recording";
    this.statusText = "正在聆听，点击停止完成识别";
    this.notify();
  }

  handleFrame(event) {
    if (this.finalFilePending || this.stopSent) return;
    if (
      !this.recorderActive ||
      !["recording", "finalizing"].includes(this.phase) ||
      !event.frameBuffer
    ) {
      return;
    }
    const connection = this.connection;
    if (!connection) return;
    if (!this.sendAudio(connection, event.frameBuffer, "语音数据发送失败，本次录音已停止")) return;
    this.audioFrames.push(event.frameBuffer);
    if (event.isLastFrame) {
      if (this.recorderActive) this.recorderStopping = true;
      this.clearFinalFileDrain();
      if (this.phase === "recording") {
        this.phase = "finalizing";
        this.statusText = "正在完成识别…";
        this.notify();
      }
      this.sendStopOnce();
    }
  }

  stop() {
    if (this.phase !== "recording") return false;
    this.phase = "finalizing";
    this.statusText = "正在完成识别…";
    this.error = "";
    this.notify();
    this.requestRecorderStop();
    return true;
  }

  requestRecorderStop() {
    if (!this.recorderActive || this.recorderStopping) return;
    this.recorderStopping = true;
    try {
      this.recorder.stop();
    } catch (_error) {
      this.recorderStopping = false;
      this.handleRecorderError();
    }
  }

  handleRecorderStop(event) {
    if (
      !this.recorderActive &&
      !this.recorderStopping &&
      !["recording", "finalizing", "stopping"].includes(this.phase)
    ) {
      return;
    }
    this.recorderActive = false;
    this.recorderStopping = false;
    this.interrupted = false;
    if (this.phase === "stopping") {
      this.phase = "idle";
      this.statusText = "录音已停止";
      this.notify();
      return;
    }
    if (this.serverComplete) {
      this.completeOnce();
      return;
    }
    if (this.phase === "recording") {
      this.phase = "finalizing";
      this.statusText = "正在完成识别…";
      this.notify();
    }
    if (this.phase === "finalizing" && !this.stopSent) {
      this.drainFinalRecorderFile(event.tempFilePath);
    }
  }

  handleRecorderError() {
    if (!this.recorderActive && !this.recorderStopping) return;
    const wasStopping = this.phase === "stopping";
    this.recorderActive = false;
    this.recorderStopping = false;
    if (wasStopping) {
      this.phase = "idle";
      this.statusText = "录音已停止";
      this.notify();
      return;
    }
    this.failClosed("麦克风采集失败，请检查权限后重试");
  }

  clearFinalFileDrain() {
    this.finalFilePending = false;
    if (this.finalFileTimer === null) return;
    this.clearTimer(this.finalFileTimer);
    this.finalFileTimer = null;
  }

  unsentFileTail(data) {
    if (!(data instanceof ArrayBuffer)) return null;
    const complete = new Uint8Array(data);
    let offset = 0;
    for (const frame of this.audioFrames) {
      if (!(frame instanceof ArrayBuffer) || offset + frame.byteLength > complete.byteLength) {
        return null;
      }
      const bytes = new Uint8Array(frame);
      for (let index = 0; index < bytes.length; index += 1) {
        if (complete[offset + index] !== bytes[index]) return null;
      }
      offset += bytes.length;
    }
    return data.slice(offset);
  }

  drainFinalRecorderFile(tempFilePath) {
    if (this.stopSent || this.serverComplete || this.finalFilePending) return;
    if (!tempFilePath || !this.wx.getFileSystemManager) {
      this.failClosed("录音结束时无法核验完整 MP3 文件，本次识别已安全停止");
      return;
    }
    let manager;
    try {
      manager = this.wx.getFileSystemManager();
    } catch (_error) {
      this.failClosed("录音结束时无法读取完整 MP3 文件，本次识别已安全停止");
      return;
    }
    const generation = this.generation;
    this.finalFilePending = true;
    let timer = null;
    timer = this.setTimer(() => {
      if (this.finalFileTimer !== timer) return;
      this.finalFileTimer = null;
      this.finalFilePending = false;
      if (this.generation === generation && this.phase === "finalizing" && !this.stopSent) {
        this.failClosed("读取完整 MP3 文件超时，本次识别已安全停止");
      }
    }, FINAL_FILE_READ_TIMEOUT_MS);
    this.finalFileTimer = timer;
    try {
      manager.readFile({
        filePath: tempFilePath,
        success: ({ data }) => {
          if (
            !this.finalFilePending ||
            this.generation !== generation ||
            this.phase !== "finalizing" ||
            this.stopSent
          ) {
            return;
          }
          const tail = this.unsentFileTail(data);
          this.clearFinalFileDrain();
          if (tail === null) {
            this.failClosed("完整 MP3 文件与已发送帧不一致，本次识别已安全停止");
            return;
          }
          const connection = this.connection;
          if (
            tail.byteLength &&
            !this.sendAudio(connection, tail, "语音末帧发送失败，本次录音已停止")
          ) {
            return;
          }
          this.sendStopOnce();
        },
        fail: () => {
          if (!this.finalFilePending || this.generation !== generation) return;
          this.clearFinalFileDrain();
          this.failClosed("录音结束时无法读取完整 MP3 文件，本次识别已安全停止");
        },
      });
    } catch (_error) {
      this.clearFinalFileDrain();
      this.failClosed("录音结束时无法读取完整 MP3 文件，本次识别已安全停止");
    }
  }

  clearFinalizationTimer() {
    if (this.finalizationIdleTimer !== null) this.clearTimer(this.finalizationIdleTimer);
    if (this.finalizationHardTimer !== null) this.clearTimer(this.finalizationHardTimer);
    this.finalizationIdleTimer = null;
    this.finalizationHardTimer = null;
  }

  scheduleFinalizationTimeout(connection) {
    const generation = this.generation;
    const expire = (kind, timer) => {
      if (this[kind] !== timer) return;
      this[kind] = null;
      if (
        this.connection === connection &&
        this.generation === generation &&
        this.phase === "finalizing" &&
        this.stopSent &&
        !this.serverComplete
      ) {
        this.failClosed("语音识别完成超时，本次结果未保存，请重试");
      }
    };
    if (this.finalizationHardTimer === null) {
      let hardTimer = null;
      hardTimer = this.setTimer(
        () => expire("finalizationHardTimer", hardTimer),
        FINALIZATION_HARD_TIMEOUT_MS,
      );
      this.finalizationHardTimer = hardTimer;
    }
    if (this.finalizationIdleTimer !== null) this.clearTimer(this.finalizationIdleTimer);
    let idleTimer = null;
    idleTimer = this.setTimer(
      () => expire("finalizationIdleTimer", idleTimer),
      FINALIZATION_IDLE_TIMEOUT_MS,
    );
    this.finalizationIdleTimer = idleTimer;
  }

  sendStopOnce() {
    this.clearFinalFileDrain();
    if (this.stopSent || this.serverComplete) return;
    const connection = this.connection;
    if (!connection || !this.isCurrentConnection(connection)) {
      this.failClosed("语音连接已关闭，本次录音未自动恢复");
      return;
    }
    this.stopSent = true;
    this.audioFrames = [];
    if (this.sendControl(connection, { type: "stop" }, "无法完成语音识别会话")) {
      this.scheduleFinalizationTimeout(connection);
    }
  }
  handleSocketClose(connection, event) {
    if (!this.isCurrentConnection(connection)) return;
    connection.closed = true;
    this.clearReadyTimer(connection);
    this.clearFinalizationTimer();
    this.connection = null;
    if (Number(event.code) === 1000 && this.stopSent) {
      this.serverComplete = true;
      this.completeOnce();
      return;
    }
    this.failClosed(
      this.stopSent ? "语音连接在最终转写完成前关闭，请重试" : "语音连接已关闭，本次录音未自动恢复",
    );
  }

  completeOnce() {
    if (this.terminalEmitted) return;
    if (this.recorderActive || this.recorderStopping) {
      this.serverComplete = true;
      this.phase = "finalizing";
      this.statusText = "正在完成识别…";
      this.notify();
      this.requestRecorderStop();
      return;
    }
    this.clearFinalFileDrain();
    this.clearFinalizationTimer();
    this.audioFrames = [];
    this.terminalEmitted = true;
    this.phase = "done";
    this.statusText = "识别完成";
    this.error = "";
    this.closeConnection(this.connection, "recognition complete");
    this.notify();
  }

  failClosed(message) {
    if (this.phase === "stopping") return;
    this.clearFinalFileDrain();
    this.clearFinalizationTimer();
    this.audioFrames = [];
    const connection = this.connection;
    this.generation += 1;
    this.error = message;
    this.serverComplete = false;
    this.closeConnection(connection, "client fail closed");
    if (this.recorderActive || this.recorderStopping) {
      this.phase = "stopping";
      this.statusText = "正在安全停止录音…";
      this.notify();
      this.requestRecorderStop();
      return;
    }
    this.phase = "idle";
    this.statusText = "录音已停止";
    this.notify();
  }

  closeConnection(connection, reason) {
    if (!connection || connection.closed) return;
    connection.closed = true;
    this.clearReadyTimer(connection);
    if (this.connection === connection) this.connection = null;
    try {
      connection.task.close({ code: 1000, reason });
    } catch (_error) {}
  }

  reset() {
    if (!["idle", "done"].includes(this.phase)) return false;
    this.generation += 1;
    this.phase = "idle";
    this.statusText = DEFAULT_STATUS;
    this.error = "";
    this.finalSegments = [];
    this.interimTranscript = "";
    this.stopSent = false;
    this.serverComplete = false;
    this.terminalEmitted = false;
    this.clearFinalFileDrain();
    this.clearFinalizationTimer();
    this.audioFrames = [];
    this.notify();
    return true;
  }
}

module.exports = { createVoiceSessionController };
