const assert = require("node:assert/strict");
const { test } = require("node:test");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function installModule(resolvedPath, exports) {
  require.cache[resolvedPath] = {
    id: resolvedPath,
    filename: resolvedPath,
    loaded: true,
    exports,
  };
}

function instantiatePage(definition) {
  const page = Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setDataCalls: [],
  });
  page.setData = (patch) => {
    page.setDataCalls.push(patch);
    Object.assign(page.data, patch);
  };
  return page;
}

test("home read promises cannot setData after the page hides", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/home/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.onHide();
  const callsAfterHide = page.setDataCalls.length;
  operation.resolve({ data: { items: [] } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.setDataCalls.length, callsAfterHide);
  assert.equal(page.data.loading, true);
});

test("privacy challenge completion cannot open a modal or setData after hide", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  let modalCalls = 0;
  global.wx = {
    showModal: () => {
      modalCalls += 1;
    },
  };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  page.onHide();
  const callsAfterHide = page.setDataCalls.length;
  operation.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(modalCalls, 0);
  assert.equal(page.setDataCalls.length, callsAfterHide);
  assert.equal(page._deletionPending, false);
});
test("privacy final modal always releases pending state across hide and show", async () => {
  const operation = deferred();
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, { request: () => operation.promise });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  operation.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(modalOptions.length, 1);

  page.onHide();
  page.onShow();
  assert.equal(page.data.deleting, true);
  modalOptions[0].success({ confirm: false });

  assert.equal(page._deletionPending, false);
  assert.equal(page.data.deleting, false);
  assert.match(page.data.error, /页面状态已变化/);
});

test("privacy deletion terminal result reaches the new visible generation", async () => {
  const challenge = deferred();
  const confirmation = deferred();
  let requestCount = 0;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return requestCount === 1 ? challenge.promise : confirmation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const page = instantiatePage(definition);

  page.onShow();
  page.issueDeletionChallenge();
  challenge.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  modalOptions[0].success({ confirm: true });
  assert.equal(requestCount, 2);

  page.onHide();
  page.onShow();
  confirmation.resolve({ data: { verified: true } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page._deletionPending, false);
  assert.equal(page.data.deleting, false);
  assert.match(page.data.message, /已删除并由服务端核验/);
});
test("privacy deletion result survives old Page unload and reaches a new instance", async () => {
  const challenge = deferred();
  const confirmation = deferred();
  let requestCount = 0;
  const requestPath = require.resolve("../utils/request");
  const configPath = require.resolve("../utils/config");
  installModule(requestPath, {
    request: () => {
      requestCount += 1;
      return requestCount === 1 ? challenge.promise : confirmation.promise;
    },
  });
  installModule(configPath, { configuredApiBase: () => "https://api.example.edu" });

  const modalOptions = [];
  global.wx = { showModal: (options) => modalOptions.push(options) };
  let definition;
  global.Page = (value) => {
    definition = value;
  };
  const pagePath = require.resolve("../pages/privacy/index");
  delete require.cache[pagePath];
  require(pagePath);
  const oldPage = instantiatePage(definition);

  oldPage.onShow();
  oldPage.issueDeletionChallenge();
  challenge.resolve({ data: { id: "challenge-id", challenge: "challenge-value" } });
  await new Promise((resolve) => setImmediate(resolve));
  modalOptions[0].success({ confirm: true });
  oldPage.onUnload();

  const newPage = instantiatePage(definition);
  newPage.onShow();
  assert.equal(newPage.data.deleting, true);
  confirmation.resolve({ data: { verified: true } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(newPage.data.deleting, false);
  assert.match(newPage.data.message, /已删除并由服务端核验/);
});
