const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const projectRoot = path.resolve(__dirname, "..");

function productionJavaScript(directory) {
  return fs
    .readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const resolved = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (["scripts", "tests"].includes(entry.name)) return [];
        return productionJavaScript(resolved);
      }
      return path.extname(resolved) === ".js" ? [resolved] : [];
    })
    .sort();
}

test("loads every production module and exercises every page lifecycle without network", () => {
  const pages = new Map();
  const applications = [];
  const recorderListeners = [];
  let activeModule = "";
  let networkCalls = 0;

  const recorder = {
    onError: (callback) => recorderListeners.push(["error", callback]),
    onFrameRecorded: (callback) => recorderListeners.push(["frame", callback]),
    onInterruptionBegin: (callback) => recorderListeners.push(["interruptionBegin", callback]),
    onInterruptionEnd: (callback) => recorderListeners.push(["interruptionEnd", callback]),
    onStop: (callback) => recorderListeners.push(["stop", callback]),
    start: () => {
      networkCalls += 1;
    },
    stop: () => {
      networkCalls += 1;
    },
  };

  global.wx = {
    canIUse: () => false,
    connectSocket: () => {
      networkCalls += 1;
      throw new Error("module smoke must not open a socket");
    },
    getAccountInfoSync: () => ({ miniProgram: { envVersion: "release" } }),
    getRecorderManager: () => recorder,
    getStorageSync: () => undefined,
    removeStorageSync: () => undefined,
    request: () => {
      networkCalls += 1;
      throw new Error("module smoke must not make a request");
    },
    setStorageSync: () => undefined,
  };
  global.App = (definition) => applications.push(definition);
  global.Page = (definition) => pages.set(activeModule, definition);

  const modules = productionJavaScript(projectRoot);
  for (const filePath of modules) {
    activeModule = path.relative(projectRoot, filePath).replaceAll(path.sep, "/");
    delete require.cache[require.resolve(filePath)];
    require(filePath);
  }

  const appJson = JSON.parse(fs.readFileSync(path.join(projectRoot, "app.json"), "utf8"));
  assert.equal(applications.length, 1);
  assert.equal(typeof applications[0].onLaunch, "function");
  applications[0].onLaunch();

  for (const pagePath of appJson.pages) {
    const modulePath = pagePath + ".js";
    const definition = pages.get(modulePath);
    assert.ok(definition, modulePath + " did not register Page()");
    assert.equal(typeof definition.onShow, "function", modulePath + " must define onShow");
    assert.equal(typeof definition.onHide, "function", modulePath + " must define onHide");
    assert.equal(typeof definition.onUnload, "function", modulePath + " must define onUnload");

    const page = Object.assign({}, definition, {
      data: JSON.parse(JSON.stringify(definition.data)),
    });
    page.setData = (patch) => Object.assign(page.data, patch);
    if (typeof page.onLoad === "function") page.onLoad();
    page.onShow();
    page.onHide();
    page.onUnload();
  }

  assert.deepEqual(
    recorderListeners.map(([name]) => name).sort(),
    ["error", "frame", "interruptionBegin", "interruptionEnd", "stop"].sort(),
  );
  assert.equal(networkCalls, 0);
  assert.deepEqual(
    modules.map((filePath) => path.relative(projectRoot, filePath).replaceAll(path.sep, "/")),
    [
      "app.js",
      "config.js",
      "pages/calendar/index.js",
      "pages/home/index.js",
      "pages/notices/index.js",
      "pages/privacy/index.js",
      "pages/settings/index.js",
      "pages/tasks/index.js",
      "pages/voice/index.js",
      "utils/auth.js",
      "utils/config.js",
      "utils/format.js",
      "utils/page-lifecycle.js",
      "utils/request.js",
      "utils/voice-session.js",
    ],
  );
});
