const assert = require("node:assert/strict");
const { test } = require("node:test");

const { createVoiceSessionController } = require("../utils/voice-session");

class FakeRecorder {
  constructor() {
    this.handlers = {};
    this.listenerCounts = {};
    this.startCalls = [];
    this.stopCount = 0;
  }

  listen(name, callback) {
    this.handlers[name] = callback;
    this.listenerCounts[name] = (this.listenerCounts[name] || 0) + 1;
  }

  onFrameRecorded(callback) {
    this.listen("frame", callback);
  }

  onError(callback) {
    this.listen("error", callback);
  }

  onInterruptionBegin(callback) {
    this.listen("interruptionBegin", callback);
  }

  onInterruptionEnd(callback) {
    this.listen("interruptionEnd", callback);
  }

  onStop(callback) {
    this.listen("stop", callback);
  }

  start(options) {
    this.startCalls.push(options);
  }

  stop() {
    this.stopCount += 1;
  }

  emit(name, value) {
    const event =
      name === "stop" && value === undefined ? { tempFilePath: "recording.mp3" } : value;
    this.handlers[name](event);
  }
}

class FakeSocket {
  constructor() {
    this.handlers = {};
    this.sent = [];
    this.sendCalls = [];
    this.closeCalls = [];
  }

  onOpen(callback) {
    this.handlers.open = callback;
  }

  onMessage(callback) {
    this.handlers.message = callback;
  }

  onError(callback) {
    this.handlers.error = callback;
  }

  onClose(callback) {
    this.handlers.close = callback;
  }

  send(options) {
    this.sendCalls.push(options);
    this.sent.push(options.data);
  }

  close(options) {
    this.closeCalls.push(options);
  }

  emitOpen() {
    this.handlers.open();
  }

  emitMessage(message) {
    this.handlers.message({ data: JSON.stringify(message) });
  }

  emitError() {
    this.handlers.error();
  }

  emitClose(event) {
    this.handlers.close(event);
  }
}

function setup() {
  const recorder = new FakeRecorder();
  const sockets = [];
  const connectOptions = [];
  const timers = new Map();
  const timerDelays = new Map();
  const fileReads = [];
  let timerSequence = 0;
  const wxApi = {
    connectSocket: (options) => {
      connectOptions.push(options);
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    getFileSystemManager: () => ({
      readFile: (options) => fileReads.push(options),
    }),
  };
  const controller = createVoiceSessionController({
    wxApi,
    recorder,
    setTimer: (callback, delay) => {
      timerSequence += 1;
      timers.set(timerSequence, callback);
      timerDelays.set(timerSequence, delay);
      return timerSequence;
    },
    clearTimer: (timer) => {
      timerDelays.delete(timer);
      return timers.delete(timer);
    },
  });
  const states = [];
  const observer = { onState: (state) => states.push(state) };
  controller.attach(observer);
  const runTimers = () => {
    for (const [timer, callback] of [...timers.entries()]) {
      if (!timers.delete(timer)) continue;
      timerDelays.delete(timer);
      callback();
    }
  };
  return {
    connectOptions,
    controller,
    fileReads,
    observer,
    recorder,
    runTimers,
    sockets,
    states,
    timerDelays,
    timers,
  };
}

function openReady(context, ticket = "one-time-ticket") {
  const generation = context.controller.begin();
  assert.notEqual(generation, null);
  assert.equal(
    context.controller.connect("wss://api.example.edu/ws/asr", ticket, generation),
    true,
  );
  const socket = context.sockets.at(-1);
  socket.emitOpen();
  socket.emitMessage({ type: "ready" });
  return { generation, socket };
}

function controls(socket, type) {
  return socket.sent
    .filter((value) => typeof value === "string")
    .map((value) => JSON.parse(value))
    .filter((value) => value.type === type);
}

test("uses supported MP3 frames and declares the matching ASR wire format", () => {
  const context = setup();
  const { socket } = openReady(context);

  assert.deepEqual(context.connectOptions[0].protocols, [
    "campusvoice",
    "campusvoice.ticket.one-time-ticket",
  ]);
  assert.equal(controls(socket, "start").length, 1);
  assert.equal(controls(socket, "start")[0].audio_format, "mp3");
  assert.equal(context.recorder.startCalls.length, 1);
  assert.equal(context.recorder.startCalls[0].format, "mp3");
  assert.equal(context.recorder.startCalls[0].frameSize, 4);
  assert.equal(context.controller.snapshot().phase, "recording");
});

test("forwards the final frame before sending stop and finalizes each resource once", () => {
  const context = setup();
  const { socket } = openReady(context);
  const firstFrame = new ArrayBuffer(4);
  const finalFrame = new ArrayBuffer(2);

  context.recorder.emit("frame", { frameBuffer: firstFrame, isLastFrame: false });
  context.controller.stop();
  assert.equal(context.recorder.stopCount, 1);
  assert.equal(controls(socket, "stop").length, 0);

  context.recorder.emit("frame", { frameBuffer: finalFrame, isLastFrame: true });
  assert.deepEqual(socket.sent.slice(-2), [finalFrame, JSON.stringify({ type: "stop" })]);
  assert.equal(controls(socket, "stop").length, 1);

  context.recorder.emit("stop");
  context.recorder.emit("stop");
  assert.equal(context.recorder.stopCount, 1);
  assert.equal(controls(socket, "stop").length, 1);

  socket.emitClose({ code: 1000, reason: "complete" });
  socket.emitClose({ code: 1000, reason: "duplicate" });
  socket.emitError();
  assert.equal(context.controller.snapshot().phase, "done");
  assert.equal(context.states.filter((state) => state.phase === "done").length, 1);
  assert.equal(socket.closeCalls.length, 0);
});

test("uses the complete file as the sole source after RecorderManager onStop", () => {
  const context = setup();
  const { socket } = openReady(context);
  const firstFrame = new Uint8Array([1]).buffer;
  const lateLastFrame = new Uint8Array([3]).buffer;
  const completeFile = new Uint8Array([1, 2, 3]).buffer;

  context.recorder.emit("frame", { frameBuffer: firstFrame, isLastFrame: false });
  context.controller.stop();
  context.recorder.emit("stop", { tempFilePath: "complete.mp3" });
  assert.equal(context.fileReads.length, 1);

  context.recorder.emit("frame", { frameBuffer: lateLastFrame, isLastFrame: true });
  assert.equal(controls(socket, "stop").length, 0);
  assert.equal(socket.sent.filter((value) => value instanceof ArrayBuffer).length, 1);

  context.fileReads[0].success({ data: completeFile });
  const binary = socket.sent.filter((value) => value instanceof ArrayBuffer);
  assert.deepEqual(
    binary.map((value) => [...new Uint8Array(value)]),
    [[1], [2, 3]],
  );
  assert.equal(controls(socket, "stop").length, 1);
});

test("fails closed without stop when RecorderManager never reports a final frame", () => {
  const context = setup();
  const { socket } = openReady(context);
  const lateFrame = new ArrayBuffer(2);

  context.controller.stop();
  context.recorder.emit("stop");
  assert.equal(controls(socket, "stop").length, 0);

  context.runTimers();
  context.recorder.emit("frame", { frameBuffer: lateFrame, isLastFrame: true });
  context.recorder.emit("stop");
  assert.equal(controls(socket, "stop").length, 0);
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.match(context.controller.snapshot().error, /完整 MP3|MP3 文件/);
  assert.equal(socket.closeCalls.length, 1);
});

test("fails closed when the server never finalizes after stop", () => {
  const context = setup();
  const { socket } = openReady(context);

  context.controller.stop();
  context.recorder.emit("frame", { frameBuffer: new ArrayBuffer(2), isLastFrame: true });
  context.recorder.emit("stop");
  assert.equal(controls(socket, "stop").length, 1);

  context.runTimers();
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.match(context.controller.snapshot().error, /识别完成超时/);
  assert.equal(socket.closeCalls.length, 1);
  assert.equal(context.recorder.stopCount, 1);
});

test("a stale finalization deadline cannot close a replacement session", () => {
  const context = setup();
  const first = openReady(context, "ticket-one");

  context.controller.stop();
  context.recorder.emit("frame", { frameBuffer: new ArrayBuffer(2), isLastFrame: true });
  context.recorder.emit("stop");
  const staleFinalizationTimers = [...context.timers.values()];
  first.socket.emitClose({ code: 1000, reason: "complete" });
  assert.equal(context.timers.size, 0);

  context.controller.reset();
  const second = openReady(context, "ticket-two");
  context.controller.stop();
  context.recorder.emit("frame", { frameBuffer: new ArrayBuffer(2), isLastFrame: true });
  context.recorder.emit("stop");
  const replacementFinalizationTimers = [...context.timers.values()];

  for (const staleFinalizationTimer of staleFinalizationTimers) staleFinalizationTimer();
  assert.equal(context.controller.snapshot().phase, "finalizing");
  assert.equal(second.socket.closeCalls.length, 0);
  assert.equal(context.timers.size, 2);
  assert.deepEqual([...context.timers.values()], replacementFinalizationTimers);
});
test("a stale final-frame drain callback cannot cancel a replacement drain", () => {
  const context = setup();
  openReady(context, "ticket-one");

  context.controller.stop();
  context.recorder.emit("stop");
  const staleFinalFrameTimer = [...context.timers.values()][0];
  context.controller.failClosed("first session cancelled");

  openReady(context, "ticket-two");
  context.controller.stop();
  context.recorder.emit("stop");
  const replacementFinalFrameTimer = [...context.timers.values()][0];

  staleFinalFrameTimer();
  assert.equal(context.controller.snapshot().phase, "finalizing");
  assert.equal(context.timers.size, 1);
  assert.equal([...context.timers.values()][0], replacementFinalFrameTimer);
});

test("stale timers and send callbacks cannot pollute a replacement session", () => {
  const context = setup();
  const firstGeneration = context.controller.begin();
  context.controller.connect("wss://api.example.edu/ws/asr", "ticket-one", firstGeneration);
  const firstSocket = context.sockets[0];
  const staleReadyTimer = [...context.timers.values()][0];
  firstSocket.emitOpen();
  const staleSendFailure = firstSocket.sendCalls[0].fail;

  context.controller.failClosed("first session cancelled");
  const secondGeneration = context.controller.begin();
  context.controller.connect("wss://api.example.edu/ws/asr", "ticket-two", secondGeneration);
  const secondSocket = context.sockets[1];

  staleReadyTimer();
  staleSendFailure();
  context.controller.cancel(firstGeneration, "stale promise callback");
  assert.equal(context.controller.snapshot().phase, "connecting");
  assert.equal(secondSocket.closeCalls.length, 0);
  secondSocket.emitOpen();
  secondSocket.emitMessage({ type: "ready" });
  assert.equal(context.controller.snapshot().phase, "recording");
});

test("stale socket callbacks cannot pollute a replacement session", () => {
  const context = setup();
  const first = openReady(context, "ticket-one");

  context.controller.failClosed("network changed");
  assert.equal(context.controller.snapshot().phase, "stopping");
  assert.equal(context.recorder.stopCount, 1);
  assert.equal(first.socket.closeCalls.length, 1);
  assert.equal(context.controller.begin(), null);

  context.recorder.emit("stop");
  assert.equal(context.controller.snapshot().phase, "idle");
  const second = openReady(context, "ticket-two");
  first.socket.emitMessage({ type: "final", text: "旧会话" });
  first.socket.emitError();
  first.socket.emitClose({ code: 1006, reason: "late close" });

  assert.equal(context.controller.snapshot().phase, "recording");
  assert.equal(context.controller.snapshot().transcript, "");
  assert.equal(second.socket.closeCalls.length, 0);
});

test("background fail-closed blocks restart until RecorderManager reports stop", () => {
  const context = setup();
  openReady(context);

  context.controller.failClosed("页面进入后台，录音已安全停止");
  assert.equal(context.controller.snapshot().phase, "stopping");
  assert.equal(context.controller.begin(), null);
  assert.equal(context.recorder.stopCount, 1);

  context.recorder.emit("stop");
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.notEqual(context.controller.begin(), null);
});

test("combines interim and final segments and accepts one clean close after stop", () => {
  const context = setup();
  const { socket } = openReady(context);

  socket.emitMessage({ type: "interim", text: "明天" });
  assert.equal(context.controller.snapshot().transcript, "明天");
  socket.emitMessage({ type: "final", text: "明天交作业" });
  socket.emitMessage({ type: "interim", text: "，记得提醒" });
  assert.equal(context.controller.snapshot().transcript, "明天交作业，记得提醒");

  context.controller.stop();
  context.recorder.emit("frame", { frameBuffer: new ArrayBuffer(2), isLastFrame: true });
  context.recorder.emit("stop");
  assert.equal(controls(socket, "stop").length, 1);
  socket.emitClose({ code: 1000, reason: "complete" });
  assert.equal(context.controller.snapshot().phase, "done");
});

test("recorder listeners are installed once across page attachment changes", () => {
  const context = setup();
  context.controller.detach(context.observer);
  context.controller.attach(context.observer);
  context.controller.detach(context.observer);
  context.controller.attach(context.observer);

  assert.deepEqual(context.recorder.listenerCounts, {
    frame: 1,
    error: 1,
    interruptionBegin: 1,
    interruptionEnd: 1,
    stop: 1,
  });
});

test("system interruption stops once, never resumes, and keeps restart behind the stop barrier", () => {
  const context = setup();
  openReady(context);

  context.recorder.emit("interruptionBegin");
  assert.equal(context.controller.snapshot().phase, "stopping");
  assert.equal(context.recorder.stopCount, 1);
  assert.equal(context.controller.begin(), null);
  context.recorder.emit("interruptionEnd");
  assert.equal(context.recorder.stopCount, 1);

  context.recorder.emit("stop");
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.match(context.controller.snapshot().error, /系统中断/);
});

test("uses RecorderManager tempFilePath as the final MP3 completeness barrier", () => {
  const context = setup();
  const { socket } = openReady(context);
  const firstFrame = new Uint8Array([1, 2]).buffer;
  const completeFile = new Uint8Array([1, 2, 3, 4]).buffer;

  context.recorder.emit("frame", { frameBuffer: firstFrame, isLastFrame: false });
  context.controller.stop();
  context.recorder.emit("stop", { tempFilePath: "complete.mp3" });
  assert.equal(context.fileReads.length, 1);
  assert.equal(controls(socket, "stop").length, 0);

  context.fileReads[0].success({ data: completeFile });
  const tail = socket.sent.at(-2);
  assert.deepEqual([...new Uint8Array(tail)], [3, 4]);
  assert.deepEqual(socket.sent.at(-1), JSON.stringify({ type: "stop" }));
  assert.equal(controls(socket, "stop").length, 1);
});

test("splits a large final-file tail below the server WebSocket frame limit", () => {
  const context = setup();
  const { socket } = openReady(context);
  const complete = new Uint8Array(131073);
  for (let index = 0; index < complete.length; index += 1) complete[index] = index % 251;

  context.controller.stop();
  context.recorder.emit("stop", { tempFilePath: "large-tail.mp3" });
  context.fileReads[0].success({ data: complete.buffer });

  const chunks = socket.sent.filter((value) => value instanceof ArrayBuffer);
  assert.equal(chunks.length, 129);
  assert.equal(chunks[0].byteLength, 1024);
  assert.equal(chunks.at(-1).byteLength, 1);
  assert.equal(
    chunks.every((value) => value.byteLength <= 1024),
    true,
  );
  const reconstructed = new Uint8Array(
    chunks.reduce((total, value) => total + value.byteLength, 0),
  );
  let offset = 0;
  for (const chunk of chunks) {
    reconstructed.set(new Uint8Array(chunk), offset);
    offset += chunk.byteLength;
  }
  assert.deepEqual(reconstructed, complete);
  assert.equal(controls(socket, "stop").length, 1);
});
test("fails closed when the completed MP3 file is not prefixed by sent frames", () => {
  const context = setup();
  const { socket } = openReady(context);

  context.recorder.emit("frame", {
    frameBuffer: new Uint8Array([1, 2]).buffer,
    isLastFrame: false,
  });
  context.controller.stop();
  context.recorder.emit("stop", { tempFilePath: "mismatch.mp3" });
  context.fileReads[0].success({ data: new Uint8Array([9, 9, 3]).buffer });

  assert.equal(controls(socket, "stop").length, 0);
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.match(context.controller.snapshot().error, /不一致/);
  assert.equal(socket.closeCalls.length, 1);
});

test("finalization progress refreshes idle time but cannot extend the hard deadline", () => {
  const context = setup();
  const { socket } = openReady(context);

  context.controller.stop();
  context.recorder.emit("stop", { tempFilePath: "complete.mp3" });
  assert.deepEqual([...context.timerDelays.values()], [5000]);
  context.fileReads[0].success({ data: new ArrayBuffer(0) });
  assert.deepEqual(
    [...context.timerDelays.values()].sort((a, b) => a - b),
    [15000, 180000],
  );

  const hardEntry = [...context.timerDelays.entries()].find((entry) => entry[1] === 180000);
  const idleEntry = [...context.timerDelays.entries()].find((entry) => entry[1] === 15000);
  assert.ok(hardEntry);
  assert.ok(idleEntry);
  const hardCallback = context.timers.get(hardEntry[0]);
  const staleIdleCallback = context.timers.get(idleEntry[0]);

  for (let index = 0; index < 1000; index += 1) {
    socket.emitMessage({ type: "finalizing" });
  }
  assert.equal(context.timerDelays.get(hardEntry[0]), 180000);
  assert.equal(context.timers.size, 2);
  staleIdleCallback();
  assert.equal(context.controller.snapshot().phase, "finalizing");
  assert.equal(socket.closeCalls.length, 0);

  context.timers.delete(hardEntry[0]);
  context.timerDelays.delete(hardEntry[0]);
  hardCallback();
  assert.equal(context.controller.snapshot().phase, "idle");
  assert.match(context.controller.snapshot().error, /完成超时/);
  assert.equal(socket.closeCalls.length, 1);
  assert.equal(context.timers.size, 0);
});
