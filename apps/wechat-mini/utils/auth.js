const { configuredApiBase } = require("./config");

const SESSION_KEY = "campusvoice.wechatSession";
const ACCOUNT_SCOPE_KEY = "campusvoice.wechatAccountScope";
const LOGOUT_GENERATION_KEY = "campusvoice.explicitLogoutGeneration";
const ACCOUNT_ID_PATTERN = /^usr_[0-9a-f]{48}$/;
const SESSION_TOKEN_PATTERN = /^cvwx1\.[A-Za-z0-9_-]{32,256}$/;
let loginGeneration = 0;
let pendingLogin = null;
const invalidatedSessionTokens = new Set();

function readLogoutGeneration() {
  const value = wx.getStorageSync(LOGOUT_GENERATION_KEY);
  if (value === undefined || value === "") return 0;
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("本机退出边界记录无效");
  }
  return value;
}

function markExplicitLogout() {
  const current = readLogoutGeneration();
  if (current >= Number.MAX_SAFE_INTEGER) throw new Error("本机退出边界记录已超出范围");
  const next = current + 1;
  wx.setStorageSync(LOGOUT_GENERATION_KEY, next);
  return next;
}

function clearAccountScope(expectedScope) {
  try {
    const stored = wx.getStorageSync(ACCOUNT_SCOPE_KEY);
    if (
      expectedScope &&
      stored &&
      (stored.apiBase !== expectedScope.apiBase || stored.accountId !== expectedScope.accountId)
    ) {
      return false;
    }
    wx.removeStorageSync(ACCOUNT_SCOPE_KEY);
    return true;
  } catch (_error) {
    return false;
  }
}

function storeAccountScope(session) {
  if (
    !session ||
    !ACCOUNT_ID_PATTERN.test(session.accountId || "") ||
    typeof session.apiBase !== "string"
  ) {
    throw new Error("微信账号边界无效");
  }
  wx.setStorageSync(ACCOUNT_SCOPE_KEY, {
    accountId: session.accountId,
    apiBase: session.apiBase,
  });
}

function currentAccountBoundary(expectedBase) {
  const base = expectedBase === undefined ? configuredApiBase() : expectedBase;
  const logoutGeneration = readLogoutGeneration();
  const scope = wx.getStorageSync(ACCOUNT_SCOPE_KEY);
  if (
    !scope ||
    !base ||
    typeof scope.apiBase !== "string" ||
    scope.apiBase !== base ||
    !ACCOUNT_ID_PATTERN.test(scope.accountId || "")
  ) {
    if (scope) clearAccountScope();
    return { accountId: "", logoutGeneration };
  }
  return { accountId: scope.accountId, logoutGeneration };
}

function scrubStoredSession() {
  try {
    wx.setStorageSync(SESSION_KEY, "");
    return true;
  } catch (_error) {
    return false;
  }
}

function clearSession(expectedSession, options) {
  const preserveAccountScope = Boolean(options && options.preserveAccountScope);
  if (expectedSession && typeof expectedSession.token === "string") {
    invalidatedSessionTokens.add(expectedSession.token);
  }
  if (expectedSession) {
    let stored;
    try {
      stored = wx.getStorageSync(SESSION_KEY);
    } catch (_error) {
      return false;
    }
    if (
      !stored ||
      stored.token !== expectedSession.token ||
      stored.apiBase !== expectedSession.apiBase
    ) {
      return false;
    }
    let removed = true;
    try {
      wx.removeStorageSync(SESSION_KEY);
    } catch (_error) {
      removed = scrubStoredSession();
    }
    const scopeCleared = preserveAccountScope ? true : clearAccountScope(expectedSession);
    return removed && scopeCleared;
  }
  loginGeneration += 1;
  pendingLogin = null;
  let storedSession = null;
  try {
    storedSession = wx.getStorageSync(SESSION_KEY);
  } catch (_error) {
    storedSession = null;
  }
  if (storedSession && typeof storedSession.token === "string") {
    invalidatedSessionTokens.add(storedSession.token);
  }
  let removed = true;
  try {
    wx.removeStorageSync(SESSION_KEY);
  } catch (_error) {
    removed = scrubStoredSession();
  }
  const scopeCleared = preserveAccountScope ? true : clearAccountScope();
  return removed && scopeCleared;
}

function currentSession(expectedBase) {
  const base = expectedBase === undefined ? configuredApiBase() : expectedBase;
  const session = wx.getStorageSync(SESSION_KEY);
  if (!session) return null;
  if (
    !SESSION_TOKEN_PATTERN.test(session.token || "") ||
    invalidatedSessionTokens.has(session.token) ||
    typeof session.expiresAt !== "string" ||
    typeof session.apiBase !== "string" ||
    !ACCOUNT_ID_PATTERN.test(session.accountId || "") ||
    !Number.isSafeInteger(session.logoutGeneration) ||
    session.logoutGeneration < 0 ||
    !base ||
    session.apiBase !== base
  ) {
    clearSession();
    return null;
  }
  const boundary = currentAccountBoundary(base);
  if (
    (boundary.accountId && boundary.accountId !== session.accountId) ||
    boundary.logoutGeneration !== session.logoutGeneration
  ) {
    clearSession();
    return null;
  }
  if (!boundary.accountId) storeAccountScope(session);
  const expiresAt = Date.parse(session.expiresAt);
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now() + 60000) {
    clearSession(session, { preserveAccountScope: true });
    return null;
  }
  return session;
}
function wechatLoginCode() {
  return new Promise((resolve, reject) => {
    wx.login({
      timeout: 10000,
      success: ({ code }) => (code ? resolve(code) : reject(new Error("微信登录未返回临时凭证"))),
      fail: () => reject(new Error("无法连接微信登录服务")),
    });
  });
}

function exchangeCode(base, code) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: base + "/api/auth/wechat/login",
      method: "POST",
      data: { code },
      timeout: 15000,
      header: { "content-type": "application/json" },
      success: ({ statusCode, data }) => {
        const expiresAt =
          data && typeof data.expires_at === "string" ? Date.parse(data.expires_at) : NaN;
        const displayName =
          data && typeof data.display_name === "string" ? data.display_name.trim() : "";
        if (
          statusCode >= 200 &&
          statusCode < 300 &&
          data &&
          SESSION_TOKEN_PATTERN.test(data.session_token || "") &&
          ACCOUNT_ID_PATTERN.test(data.account_id || "") &&
          Number.isFinite(expiresAt) &&
          expiresAt > Date.now() + 60000 &&
          typeof data.display_name === "string" &&
          [...data.display_name].length <= 120 &&
          displayName.length > 0
        ) {
          resolve({
            token: data.session_token,
            expiresAt: data.expires_at,
            displayName,
            accountId: data.account_id,
            apiBase: base,
          });
          return;
        }
        reject(new Error((data && data.error && data.error.message) || "微信身份验证失败"));
      },
      fail: () => reject(new Error("无法连接 CampusVoice 服务")),
    });
  });
}

function revokeSession() {
  let session;
  let base;
  let localError = null;
  try {
    session = currentSession();
    base = configuredApiBase();
  } catch (_error) {
    localError = new Error("无法安全读取本机会话");
  }
  try {
    markExplicitLogout();
  } catch (_error) {
    localError = localError || new Error("无法安全记录本机退出状态");
  }
  if (session) invalidatedSessionTokens.add(session.token);
  if (!clearSession()) {
    localError = localError || new Error("无法从本机删除登录凭证");
  }

  let remoteOperation = Promise.resolve();
  if (session && base) {
    remoteOperation = new Promise((resolve, reject) => {
      wx.request({
        url: base + "/api/auth/wechat/logout",
        method: "POST",
        timeout: 15000,
        header: {
          "content-type": "application/json",
          Authorization: "Bearer " + session.token,
        },
        success: ({ statusCode }) => {
          if (statusCode >= 200 && statusCode < 300) resolve();
          else reject(new Error("服务端会话撤销失败"));
        },
        fail: () => reject(new Error("无法连接服务端撤销会话")),
      });
    });
  }
  return remoteOperation.then(
    () => {
      if (localError) throw localError;
    },
    (remoteError) => {
      if (localError) {
        throw new Error(localError.message + "；" + remoteError.message);
      }
      throw remoteError;
    },
  );
}
function ensureSession(force, expectedBase) {
  let configuredBase;
  try {
    configuredBase = configuredApiBase();
  } catch (_error) {
    return Promise.reject(new Error("无法安全读取 CampusVoice 服务地址"));
  }
  const base = expectedBase === undefined ? configuredBase : expectedBase;
  if (!base) return Promise.reject(new Error("尚未配置 CampusVoice HTTPS 服务地址"));
  if (configuredBase !== base) return Promise.reject(new Error("服务地址已变化，请重试"));
  if (pendingLogin && pendingLogin.base === base) return pendingLogin.promise;
  if (!force) {
    let existing;
    try {
      existing = currentSession(base);
    } catch (_error) {
      return Promise.reject(new Error("无法安全读取本机会话"));
    }
    if (existing) return Promise.resolve(existing);
  }
  const generation = ++loginGeneration;
  let operation;
  operation = wechatLoginCode()
    .then((code) => exchangeCode(base, code))
    .then((session) => {
      if (generation !== loginGeneration || configuredApiBase() !== base) {
        throw new Error("登录期间服务地址已变化，请重试");
      }
      storeAccountScope(session);
      const boundary = currentAccountBoundary(base);
      const boundedSession = Object.assign({}, session, {
        logoutGeneration: boundary.logoutGeneration,
      });
      invalidatedSessionTokens.delete(boundedSession.token);
      wx.setStorageSync(SESSION_KEY, boundedSession);
      return boundedSession;
    })
    .finally(() => {
      if (pendingLogin && pendingLogin.promise === operation) pendingLogin = null;
    });
  pendingLogin = { base, promise: operation };
  return operation;
}

module.exports = {
  clearSession,
  currentAccountBoundary,
  currentSession,
  ensureSession,
  revokeSession,
};
