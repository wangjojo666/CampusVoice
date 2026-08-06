const { configuredApiBase } = require("./config");

const SESSION_KEY = "campusvoice.wechatSession";
let pendingLogin = null;
let loginGeneration = 0;

function clearSession(expectedSession) {
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
    wx.removeStorageSync(SESSION_KEY);
    return true;
  }
  loginGeneration += 1;
  pendingLogin = null;
  wx.removeStorageSync(SESSION_KEY);
  return true;
}

function currentSession(expectedBase) {
  const base = expectedBase === undefined ? configuredApiBase() : expectedBase;
  const session = wx.getStorageSync(SESSION_KEY);
  if (
    !session ||
    typeof session.token !== "string" ||
    typeof session.expiresAt !== "string" ||
    typeof session.apiBase !== "string" ||
    !base ||
    session.apiBase !== base
  ) {
    if (session) clearSession();
    return null;
  }
  const expiresAt = Date.parse(session.expiresAt);
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now() + 60000) {
    clearSession();
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
        if (statusCode >= 200 && statusCode < 300 && data && data.session_token) {
          resolve({
            token: data.session_token,
            expiresAt: data.expires_at,
            displayName: data.display_name || "微信用户",
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
  const session = currentSession();
  const base = configuredApiBase();
  if (!session || !base) {
    clearSession();
    return Promise.resolve();
  }
  clearSession();
  return new Promise((resolve, reject) => {
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
    const existing = currentSession(base);
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
      wx.setStorageSync(SESSION_KEY, session);
      return session;
    })
    .finally(() => {
      if (pendingLogin && pendingLogin.promise === operation) pendingLogin = null;
    });
  pendingLogin = { base, promise: operation };
  return operation;
}

module.exports = { clearSession, currentSession, ensureSession, revokeSession };
