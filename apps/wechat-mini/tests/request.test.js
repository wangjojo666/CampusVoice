const assert = require("node:assert/strict");
const { beforeEach, test } = require("node:test");

const ACCOUNT_ID_A = "usr_" + "a".repeat(48);
const ACCOUNT_ID_B = "usr_" + "b".repeat(48);
const storage = new Map();
let requests = [];
let requestHandler = null;
let loginCalls = 0;
let storageReadError = null;
let storageRemoveError = null;
let accountInfoHandler = () => ({ miniProgram: { envVersion: "develop" } });

global.wx = {
  getAccountInfoSync: () => accountInfoHandler(),
  getStorageSync: (key) => {
    if (storageReadError) throw storageReadError;
    return storage.get(key);
  },
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => {
    if (storageRemoveError) throw storageRemoveError;
    return storage.delete(key);
  },
  login: ({ success }) => {
    loginCalls += 1;
    success({ code: "temporary-code-123" });
  },
  request: (options) => {
    requests.push(options);
    requestHandler(options);
  },
};
const config = require("../utils/config");
const auth = require("../utils/auth");
const api = require("../utils/request");

beforeEach(() => {
  storage.clear();
  requests = [];
  loginCalls = 0;
  storageReadError = null;
  storageRemoveError = null;
  requestHandler = null;
  accountInfoHandler = () => ({ miniProgram: { envVersion: "develop" } });
  config.saveDevelopmentApiBase("https://api.example.edu");
});

test("API base rejects insecure remote URLs and credential-bearing URLs", () => {
  assert.throws(() => config.normalizeApiBase("http://api.example.edu", false), /HTTPS/);
  assert.throws(() => config.normalizeApiBase("https://user@example.edu", false), /凭据/);
  assert.throws(
    () => config.normalizeApiBase("https://api.example.edu?token=secret", false),
    /查询参数/,
  );
  assert.equal(
    config.normalizeApiBase("https://api.example.edu/", false),
    "https://api.example.edu",
  );
});

test("wx.login code exchange stores only the bounded app session", async () => {
  requestHandler = (options) => {
    assert.equal(options.url, "https://api.example.edu/api/auth/wechat/login");
    assert.deepEqual(options.data, { code: "temporary-code-123" });
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1.session-token-value-with-more-than-32-characters",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: "微信用户",
        account_id: ACCOUNT_ID_A,
      },
    });
  };

  const session = await auth.ensureSession(true);
  assert.equal(session.displayName, "微信用户");
  assert.equal(session.accountId, ACCOUNT_ID_A);
  assert.equal(session.logoutGeneration, 0);
  assert.deepEqual(storage.get("campusvoice.wechatAccountScope"), {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  assert.equal(
    storage.get("campusvoice.wechatSession").token,
    "cvwx1.session-token-value-with-more-than-32-characters",
  );
  assert.equal(requests.length, 1);
});

test("login rejects a response without the stable account boundary", async () => {
  requestHandler = (options) =>
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1.session-token-value-with-more-than-32-characters",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: "微信用户",
      },
    });

  await assert.rejects(() => auth.ensureSession(true), /身份验证失败/);
  assert.equal(storage.has("campusvoice.wechatSession"), false);
});

test("login rejects malformed, incomplete, expired, and mistyped credential contracts", async () => {
  const valid = {
    session_token: "cvwx1." + "z".repeat(48),
    account_id: ACCOUNT_ID_A,
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    display_name: "微信用户",
  };
  const invalidPayloads = [
    Object.assign({}, valid, { session_token: "x" }),
    Object.assign({}, valid, { expires_at: undefined }),
    Object.assign({}, valid, { expires_at: new Date(Date.now() - 1_000).toISOString() }),
    Object.assign({}, valid, { display_name: { unsafe: true } }),
    Object.assign({}, valid, { display_name: "" }),
    Object.assign({}, valid, { display_name: "   " }),
    Object.assign({}, valid, { display_name: "名".repeat(121) }),
  ];

  for (const payload of invalidPayloads) {
    requestHandler = (options) => options.success({ statusCode: 200, data: payload });
    await assert.rejects(() => auth.ensureSession(true), /身份验证失败/);
    assert.equal(storage.has("campusvoice.wechatSession"), false);
  }
});

test("login counts supplementary Unicode display names like the backend", async () => {
  const displayName = "😀".repeat(61);
  requestHandler = (options) =>
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1.unicode-display-name-token-value-with-32-characters",
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: displayName,
        account_id: ACCOUNT_ID_A,
      },
    });

  const session = await auth.ensureSession(true);
  assert.equal(session.displayName, displayName);
  assert.equal([...session.displayName].length, 61);
});
test("expired bearer renewal preserves the stable account boundary", async () => {
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1." + "e".repeat(48),
    expiresAt: new Date(Date.now() + 30_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });

  assert.equal(auth.currentSession("https://api.example.edu"), null);
  assert.equal(storage.has("campusvoice.wechatSession"), false);
  assert.deepEqual(auth.currentAccountBoundary("https://api.example.edu"), {
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
  });

  requestHandler = (options) =>
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1." + "r".repeat(48),
        account_id: ACCOUNT_ID_A,
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: "微信用户",
      },
    });
  const renewed = await auth.ensureSession(false);
  assert.equal(renewed.accountId, ACCOUNT_ID_A);
  assert.deepEqual(auth.currentAccountBoundary("https://api.example.edu"), {
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
  });
});

test("explicit logout generation survives same-account login", async () => {
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1." + "l".repeat(48),
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => options.success({ statusCode: 200, data: { success: true } });

  await auth.revokeSession();
  assert.deepEqual(auth.currentAccountBoundary("https://api.example.edu"), {
    accountId: "",
    logoutGeneration: 1,
  });

  requestHandler = (options) =>
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1." + "n".repeat(48),
        account_id: ACCOUNT_ID_A,
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: "微信用户",
      },
    });
  await auth.ensureSession(true);
  assert.deepEqual(auth.currentAccountBoundary("https://api.example.edu"), {
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 1,
  });
});

test("authenticated API requests attach bearer token and reject unsafe paths", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.existing-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    assert.equal(options.url, "https://api.example.edu/api/tasks?limit=5");
    assert.equal(
      options.header.Authorization,
      "Bearer cvwx1.existing-session-token-value-with-32-characters",
    );
    options.success({ statusCode: 200, data: { items: [], total: 0 }, header: {} });
  };

  const response = await api.request("/api/tasks?limit=5");
  assert.deepEqual(response.data, { items: [], total: 0 });
  await assert.rejects(() => api.request("https://evil.example/api/tasks"), /无效 API 路径/);
});

test("anonymous health requests do not force WeChat login", async () => {
  requestHandler = (options) => {
    assert.equal(options.url, "https://api.example.edu/api/health");
    assert.equal(options.header.Authorization, undefined);
    options.success({ statusCode: 200, data: { status: "ok" }, header: {} });
  };

  const response = await api.request("/api/health", { auth: false });
  assert.equal(response.data.status, "ok");
  assert.equal(loginCalls, 0);
});

test("verified writes consume a server challenge and keep idempotency headers", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.existing-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "复习机器学习", priority: "high", source_type: "manual" };
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      assert.deepEqual(options.data, { method: "POST", path: "/api/tasks", body });
      options.success({
        statusCode: 200,
        data: {
          challenge: "single-use-challenge-value-with-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    assert.equal(options.url, "https://api.example.edu/api/tasks");
    assert.equal(
      options.header["X-Write-Challenge"],
      "single-use-challenge-value-with-32-characters",
    );
    assert.match(options.header["Idempotency-Key"], /^wx-/);
    options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await api.verifiedRequest("POST", "/api/tasks", body);
  assert.equal(requests.length, 2);
});

test("logout clears local credentials even when remote revocation fails", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.existing-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => options.fail({ errMsg: "network unavailable" });

  await assert.rejects(() => auth.revokeSession(), /撤销会话/);
  assert.equal(storage.has("campusvoice.wechatSession"), false);
});

test("unqualified local clear invalidates a stored token when removal fails", () => {
  const session = {
    token: "cvwx1.unqualified-clear-failure-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  };
  storage.set("campusvoice.wechatSession", session);
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  storageRemoveError = new Error("storage is locked");

  assert.equal(auth.clearSession(), false);
  assert.equal(storage.get("campusvoice.wechatSession"), "");
  assert.equal(auth.currentSession("https://api.example.edu"), null);
  storageRemoveError = null;
  const authPath = require.resolve("../utils/auth");
  delete require.cache[authPath];
  const restartedAuth = require("../utils/auth");
  assert.equal(restartedAuth.currentSession("https://api.example.edu"), null);
  require.cache[authPath].exports = auth;
});

test("explicit logout remains fail-closed when local session deletion fails", async () => {
  const session = {
    token: "cvwx1.logout-removal-failure-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  };
  storage.set("campusvoice.wechatSession", session);
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  let logoutRequest;
  requestHandler = (options) => {
    logoutRequest = options;
    options.success({ statusCode: 204, data: null, header: {} });
  };
  storageRemoveError = new Error("storage is locked");

  await assert.rejects(() => auth.revokeSession(), /无法从本机删除登录凭证/);

  assert.equal(storage.get("campusvoice.explicitLogoutGeneration"), 1);
  assert.equal(storage.get("campusvoice.wechatSession"), "");
  assert.equal(auth.currentSession("https://api.example.edu"), null);
  assert.equal(logoutRequest.url, "https://api.example.edu/api/auth/wechat/logout");
  assert.equal(logoutRequest.header.Authorization, "Bearer " + session.token);
  storageRemoveError = null;
});
test("environment detection fails closed and only develop accepts API overrides", () => {
  assert.equal(config.configuredApiBase(), "https://api.example.edu");

  accountInfoHandler = () => {
    throw new Error("account info unavailable");
  };
  assert.equal(config.envVersion(), "unknown");
  assert.equal(config.configuredApiBase(), "");
  assert.throws(() => config.saveDevelopmentApiBase("http://localhost:8000"), /仅开发版/);

  accountInfoHandler = () => ({ miniProgram: { envVersion: "trial" } });
  assert.equal(config.configuredApiBase(), "");
  assert.throws(() => config.saveDevelopmentApiBase("https://trial-api.example.edu"), /仅开发版/);
});

test("verified writes cannot cross an automatic re-login into another account", async () => {
  const accountB = "usr_" + "b".repeat(48);
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
  });
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1." + "o".repeat(48),
    expiresAt: new Date(Date.now() + 30_000).toISOString(),
    displayName: "旧用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    assert.equal(options.url, "https://api.example.edu/api/auth/wechat/login");
    options.success({
      statusCode: 200,
      data: {
        session_token: "cvwx1." + "p".repeat(48),
        account_id: accountB,
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
        display_name: "新用户",
      },
    });
  };

  await assert.rejects(
    () =>
      api.verifiedRequest("POST", "/api/tasks", {
        title: "旧账号草稿",
        priority: "medium",
        source_type: "manual",
      }),
    /账号状态已变化/,
  );
  assert.equal(requests.length, 1);
  assert.equal(storage.has("campusvoice.pendingWriteIntents.v1"), false);
});
test("an uncertain write retry reuses its key and clears it only after success", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.retry-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "网络不确定写入", priority: "medium", source_type: "manual" };
  const writeKeys = [];
  let writeAttempt = 0;
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "retry-challenge-value-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeKeys.push(options.header["Idempotency-Key"]);
    writeAttempt += 1;
    if (writeAttempt === 1) options.fail({ errMsg: "connection reset" });
    else options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  assert.equal(pending.length, 1);
  assert.deepEqual(Object.keys(pending[0]).sort(), [
    "accountId",
    "apiBase",
    "createdAt",
    "fingerprint",
    "key",
    "logoutGeneration",
  ]);
  assert.equal(pending[0].accountId, ACCOUNT_ID_A);
  assert.equal(pending[0].apiBase, "https://api.example.edu");
  assert.equal(pending[0].logoutGeneration, 0);
  assert.equal(pending[0].key, writeKeys[0]);
  assert.equal(JSON.stringify(pending).includes(body.title), false);
  assert.equal(JSON.stringify(pending).includes("/api/tasks"), false);
  assert.equal(JSON.stringify(pending).includes("retry-session-token"), false);

  await api.verifiedRequest("POST", "/api/tasks", body);
  assert.equal(storage.has("campusvoice.pendingWriteIntents.v1"), false);
  await api.verifiedRequest("POST", "/api/tasks", body);

  assert.equal(writeKeys.length, 3);
  assert.equal(writeKeys[0], writeKeys[1]);
  assert.notEqual(writeKeys[1], writeKeys[2]);
  assert.equal(storage.has("campusvoice.pendingWriteIntents.v1"), false);
});

test("corrupt pending-write storage fails closed before any network request", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.corrupt-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const corrupt = [
    {
      fingerprint: "0000000000000000-1",
      key: "wx-1700000000000-corrupt",
      createdAt: Date.now(),
      body: { private: "must-not-survive" },
    },
  ];
  storage.set("campusvoice.pendingWriteIntents.v1", corrupt);

  await assert.rejects(
    () => api.verifiedRequest("POST", "/api/tasks", { title: "fresh" }),
    /无法验证待处理写入记录/,
  );
  assert.equal(requests.length, 0);
  assert.deepEqual(storage.get("campusvoice.pendingWriteIntents.v1"), corrupt);
});

test("pending-write storage read failures fail closed before any network request", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.read-error-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  storageReadError = new Error("storage unavailable");

  await assert.rejects(
    () => api.verifiedRequest("POST", "/api/tasks", { title: "fresh" }),
    /无法验证待处理写入记录/,
  );
  assert.equal(requests.length, 0);
});
test("a definite write rejection clears the pending idempotency key", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.definite-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "确定失败写入", priority: "low", source_type: "manual" };
  const writeKeys = [];
  let writeAttempt = 0;
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "definite-challenge-value-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeKeys.push(options.header["Idempotency-Key"]);
    writeAttempt += 1;
    if (writeAttempt === 1) {
      options.success({
        statusCode: 422,
        data: { error: { code: "invalid_body", message: "invalid" } },
        header: {},
      });
    } else {
      options.success({ statusCode: 201, data: { entity: body }, header: {} });
    }
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /invalid/);
  await api.verifiedRequest("POST", "/api/tasks", body);
  assert.notEqual(writeKeys[0], writeKeys[1]);
});

test("an aged outcome-uncertain write freezes without deleting its evidence", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.aged-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "跨日未确认写入", priority: "medium", source_type: "manual" };
  const writeKeys = [];
  let writeAttempt = 0;
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "aged-challenge-value-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeKeys.push(options.header["Idempotency-Key"]);
    writeAttempt += 1;
    if (writeAttempt === 1) options.fail({ errMsg: "response lost" });
    else options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  storage.get("campusvoice.pendingWriteIntents.v1")[0].createdAt = Date.now() - 2 * 60 * 60 * 1000;
  const beforeRetry = requests.length;
  await assert.rejects(
    () => api.verifiedRequest("POST", "/api/tasks", body),
    /无法验证待处理写入记录/,
  );

  assert.equal(requests.length, beforeRetry);
  assert.equal(writeKeys.length, 1);
  assert.equal(storage.get("campusvoice.pendingWriteIntents.v1")[0].key, writeKeys[0]);
});

test("a persisted outcome-uncertain write is never replayed after runtime restart", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.restart-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "重启后核对写入", priority: "medium", source_type: "manual" };
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "restart-challenge-value-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    options.fail({ errMsg: "response lost" });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  const beforeRestartRetry = requests.length;
  delete require.cache[require.resolve("../utils/request")];
  const restartedApi = require("../utils/request");

  await assert.rejects(
    () => restartedApi.verifiedRequest("POST", "/api/tasks", body),
    /无法验证待处理写入记录/,
  );
  assert.equal(requests.length, beforeRestartRetry);
  assert.deepEqual(storage.get("campusvoice.pendingWriteIntents.v1"), pending);
});

test("pending-write capacity fails closed without evicting an uncertain intent", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.capacity-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const createdAt = Date.now() - 90 * 24 * 60 * 60 * 1000;
  const intents = Array.from({ length: 64 }, (_value, index) => ({
    accountId: ACCOUNT_ID_A,
    apiBase: "https://api.example.edu",
    fingerprint: index.toString(16).padStart(16, "0") + "-1",
    key: "wx-1700000000000-" + index.toString(36).padStart(6, "0"),
    logoutGeneration: 0,
    createdAt: createdAt + index,
  }));
  storage.set("campusvoice.pendingWriteIntents.v1", intents);

  await assert.rejects(
    () => api.verifiedRequest("POST", "/api/tasks", { title: "第六十五个写入" }),
    /安全上限/,
  );
  assert.equal(requests.length, 0);
  assert.deepEqual(storage.get("campusvoice.pendingWriteIntents.v1"), intents);
});

test("a stale success callback cannot clear a newer same-write idempotency key", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.concurrent-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const body = { title: "并发写入", priority: "high", source_type: "manual" };
  const writeAttempts = [];
  const drainUntilWriteCount = async (expected) => {
    for (let attempt = 0; attempt < 10 && writeAttempts.length < expected; attempt += 1) {
      await Promise.resolve();
    }
    assert.equal(writeAttempts.length, expected);
  };
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "concurrent-challenge-value-with-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeAttempts.push(options);
  };

  const first = api.verifiedRequest("POST", "/api/tasks", body);
  const second = api.verifiedRequest("POST", "/api/tasks", body);
  await drainUntilWriteCount(2);
  const oldKey = writeAttempts[0].header["Idempotency-Key"];
  assert.equal(writeAttempts[1].header["Idempotency-Key"], oldKey);

  writeAttempts[0].success({ statusCode: 201, data: { id: "first" }, header: {} });
  await first;
  const third = api.verifiedRequest("POST", "/api/tasks", body);
  await drainUntilWriteCount(3);
  const newKey = writeAttempts[2].header["Idempotency-Key"];
  assert.notEqual(newKey, oldKey);

  writeAttempts[1].success({ statusCode: 201, data: { id: "second" }, header: {} });
  await second;
  writeAttempts[2].fail({ errMsg: "outcome unknown" });
  await assert.rejects(() => third, /网络请求失败/);

  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  assert.equal(pending.length, 1);
  assert.equal(pending[0].key, newKey);
});

test("pending write intents are scoped to one API base and never clear another base", async () => {
  const body = { title: "跨服务写入", priority: "high", source_type: "manual" };
  const writeAttempts = [];
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.api-a-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "A 用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "cross-base-challenge-value-with-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeAttempts.push({
      base: new URL(options.url).origin,
      key: options.header["Idempotency-Key"],
    });
    if (options.url.startsWith("https://api.example.edu")) options.fail({ errMsg: "lost" });
    else options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  config.saveDevelopmentApiBase("https://replacement.example.edu");
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.api-b-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "B 用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://replacement.example.edu",
  });
  await api.verifiedRequest("POST", "/api/tasks", body);

  assert.equal(writeAttempts.length, 2);
  assert.notEqual(writeAttempts[0].key, writeAttempts[1].key);
  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  assert.equal(pending.length, 1);
  assert.equal(pending[0].apiBase, "https://api.example.edu");
  assert.equal(pending[0].key, writeAttempts[0].key);
});

test("same account after explicit logout never reuses or clears the prior uncertain key", async () => {
  const body = { title: "退出边界写入", priority: "high", source_type: "manual" };
  const writeKeys = [];
  let writeAttempt = 0;
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.same-account-generation-zero-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "same-account-generation-challenge-with-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeKeys.push(options.header["Idempotency-Key"]);
    writeAttempt += 1;
    if (writeAttempt === 1) options.fail({ errMsg: "response lost" });
    else options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  const generationZeroIntent = storage.get("campusvoice.pendingWriteIntents.v1")[0];
  storage.set("campusvoice.explicitLogoutGeneration", 1);
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.same-account-generation-one-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 1,
    apiBase: "https://api.example.edu",
  });

  await api.verifiedRequest("POST", "/api/tasks", body);

  assert.notEqual(writeKeys[0], writeKeys[1]);
  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  assert.equal(pending.length, 1);
  assert.equal(pending[0].key, generationZeroIntent.key);
  assert.equal(pending[0].logoutGeneration, 0);
});

test("different accounts never reuse or clear one another's uncertain key", async () => {
  const body = { title: "跨账号相同正文", priority: "medium", source_type: "manual" };
  const writeKeys = [];
  let writeAttempt = 0;
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.account-a-uncertain-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "A 用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "cross-account-challenge-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    writeKeys.push(options.header["Idempotency-Key"]);
    writeAttempt += 1;
    if (writeAttempt === 1) options.fail({ errMsg: "response lost" });
    else options.success({ statusCode: 201, data: { entity: body }, header: {} });
  };

  await assert.rejects(() => api.verifiedRequest("POST", "/api/tasks", body), /网络请求失败/);
  const accountAIntent = storage.get("campusvoice.pendingWriteIntents.v1")[0];
  storage.set("campusvoice.wechatAccountScope", {
    accountId: ACCOUNT_ID_B,
    apiBase: "https://api.example.edu",
  });
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.account-b-success-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "B 用户",
    accountId: ACCOUNT_ID_B,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });

  await api.verifiedRequest("POST", "/api/tasks", body);

  assert.notEqual(writeKeys[0], writeKeys[1]);
  const pending = storage.get("campusvoice.pendingWriteIntents.v1");
  assert.equal(pending.length, 1);
  assert.equal(pending[0].accountId, ACCOUNT_ID_A);
  assert.equal(pending[0].key, accountAIntent.key);
});

test("a final 2xx with an unreadable completion boundary preserves uncertain evidence", async () => {
  const body = { title: "响应边界失败", priority: "medium", source_type: "manual" };
  let finalWrite;
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.completion-boundary-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "微信用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/write-challenges")) {
      options.success({
        statusCode: 200,
        data: {
          challenge: "completion-boundary-challenge-with-more-than-32-characters",
          required_stages: 1,
        },
        header: {},
      });
      return;
    }
    finalWrite = options;
  };

  const operation = api.verifiedRequest("POST", "/api/tasks", body);
  for (let attempt = 0; attempt < 20 && !finalWrite; attempt += 1) await Promise.resolve();
  assert.ok(finalWrite);
  const pendingBefore = storage.get("campusvoice.pendingWriteIntents.v1");
  storageReadError = new Error("storage unavailable at completion");
  finalWrite.success({ statusCode: 201, data: { entity: body }, header: {} });

  await assert.rejects(
    () => operation,
    (error) => error.code === "ACCOUNT_BOUNDARY_UNAVAILABLE" && error.outcomeUncertain === true,
  );
  storageReadError = null;
  assert.deepEqual(storage.get("campusvoice.pendingWriteIntents.v1"), pendingBefore);
});
test("a 401 token stays invalid when local removal fails and the next request reauthenticates", async () => {
  const oldToken = "cvwx1.failed-401-removal-token-value-with-32-characters";
  storage.set("campusvoice.wechatSession", {
    token: oldToken,
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "旧用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  storageRemoveError = new Error("storage is locked");
  requestHandler = (options) => {
    assert.equal(options.header.Authorization, "Bearer " + oldToken);
    options.success({ statusCode: 401, data: { error: {} }, header: {} });
  };

  await assert.rejects(() => api.request("/api/tasks?limit=1"));
  assert.equal(auth.currentSession("https://api.example.edu"), null);
  assert.equal(storage.get("campusvoice.wechatSession"), "");

  storageRemoveError = null;
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/wechat/login")) {
      options.success({
        statusCode: 200,
        data: {
          session_token: "cvwx1.reauthenticated-after-401-token-value-with-32-characters",
          expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          display_name: "新用户",
          account_id: ACCOUNT_ID_A,
        },
        header: {},
      });
      return;
    }
    assert.equal(
      options.header.Authorization,
      "Bearer cvwx1.reauthenticated-after-401-token-value-with-32-characters",
    );
    options.success({ statusCode: 200, data: { items: [], total: 0 }, header: {} });
  };

  await api.request("/api/tasks?limit=1");
  assert.equal(loginCalls, 1);
});
test("a delayed 401 cannot clear a replacement session and anonymous 401 clears none", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.old-request-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "旧用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  const deferredRequests = [];
  requestHandler = (options) => deferredRequests.push(options);

  const authenticated = api.request("/api/tasks?limit=1");
  await Promise.resolve();
  assert.equal(deferredRequests.length, 1);
  const replacement = {
    token: "cvwx1.replacement-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "新用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  };
  storage.set("campusvoice.wechatSession", replacement);
  deferredRequests[0].success({ statusCode: 401, data: { error: {} }, header: {} });
  await assert.rejects(() => authenticated);
  assert.deepEqual(storage.get("campusvoice.wechatSession"), replacement);

  const anonymous = api.request("/api/health", { auth: false });
  await Promise.resolve();
  deferredRequests[1].success({ statusCode: 401, data: { error: {} }, header: {} });
  await assert.rejects(() => anonymous);
  assert.deepEqual(storage.get("campusvoice.wechatSession"), replacement);
});

test("a delayed logout callback cannot clear a newly established session", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.logout-old-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "旧用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  let deferredLogout;
  requestHandler = (options) => {
    deferredLogout = options;
  };

  const logout = auth.revokeSession();
  assert.equal(storage.has("campusvoice.wechatSession"), false);
  const replacement = {
    token: "cvwx1.logout-new-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "新用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  };
  storage.set("campusvoice.wechatSession", replacement);
  deferredLogout.fail({ errMsg: "late network failure" });

  await assert.rejects(() => logout, /撤销会话/);
  assert.deepEqual(storage.get("campusvoice.wechatSession"), replacement);
});

test("a stale 401 cannot cancel a same-base login already in flight", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.stale-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "旧用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://api.example.edu",
  });
  let staleRequest;
  let loginExchange;
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/wechat/login")) loginExchange = options;
    else staleRequest = options;
  };

  const staleOperation = api.request("/api/tasks?limit=1");
  await Promise.resolve();
  assert.ok(staleRequest);
  const loginOperation = auth.ensureSession(true);
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(loginExchange);

  staleRequest.success({ statusCode: 401, data: { error: {} }, header: {} });
  await assert.rejects(() => staleOperation);
  loginExchange.success({
    statusCode: 200,
    data: {
      session_token: "cvwx1.fresh-session-token-value-with-32-characters",
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      display_name: "新用户",
      account_id: ACCOUNT_ID_A,
    },
  });

  const fresh = await loginOperation;
  assert.equal(fresh.token, "cvwx1.fresh-session-token-value-with-32-characters");
  assert.equal(storage.get("campusvoice.wechatSession").token, fresh.token);
});
test("a session token is never sent to a different configured API base", async () => {
  storage.set("campusvoice.wechatSession", {
    token: "cvwx1.old-origin-session-token-value-with-32-characters",
    expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
    displayName: "旧服务用户",
    accountId: ACCOUNT_ID_A,
    logoutGeneration: 0,
    apiBase: "https://old-api.example.edu",
  });
  requestHandler = (options) => {
    if (options.url.endsWith("/api/auth/wechat/login")) {
      assert.equal(options.url, "https://api.example.edu/api/auth/wechat/login");
      options.success({
        statusCode: 200,
        data: {
          session_token: "cvwx1.new-origin-session-token-value-with-32-characters",
          expires_at: new Date(Date.now() + 3_600_000).toISOString(),
          display_name: "新服务用户",
          account_id: ACCOUNT_ID_A,
        },
      });
      return;
    }
    assert.equal(options.url, "https://api.example.edu/api/tasks?limit=1");
    assert.equal(
      options.header.Authorization,
      "Bearer cvwx1.new-origin-session-token-value-with-32-characters",
    );
    assert.equal(options.header.Authorization.includes("old-origin"), false);
    options.success({ statusCode: 200, data: { items: [], total: 0 }, header: {} });
  };

  await api.request("/api/tasks?limit=1");
  assert.equal(loginCalls, 1);
  assert.equal(storage.get("campusvoice.wechatSession").apiBase, "https://api.example.edu");
});

test("a pending login cannot commit after the configured API base changes", async () => {
  let deferredExchange;
  requestHandler = (options) => {
    assert.equal(options.url, "https://api.example.edu/api/auth/wechat/login");
    deferredExchange = options;
  };

  const operation = auth.ensureSession(true);
  await Promise.resolve();
  assert.ok(deferredExchange);
  config.saveDevelopmentApiBase("https://replacement.example.edu");
  deferredExchange.success({
    statusCode: 200,
    data: {
      session_token: "cvwx1.stale-login-session-token-value-with-32-characters",
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      display_name: "旧服务用户",
      account_id: ACCOUNT_ID_A,
    },
  });

  await assert.rejects(() => operation, /服务地址已变化/);
  assert.equal(storage.has("campusvoice.wechatSession"), false);
});
