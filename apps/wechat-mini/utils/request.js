const { ensureSession, clearSession } = require("./auth");
const { configuredApiBase, normalizeApiBase } = require("./config");

const PENDING_WRITE_STORAGE_KEY = "campusvoice.pendingWriteIntents.v1";
const MAX_PENDING_WRITE_INTENTS = 64;
const MAX_RUNTIME_RETRY_AGE_MS = 60 * 60 * 1000;
const runtimeRetryableKeys = new Set();

function idempotencyKey() {
  const suffix = (Math.random().toString(36).slice(2) + "0000000000").slice(0, 10);
  return "wx-" + Date.now() + "-" + suffix;
}

function request(path, options) {
  const settings = options || {};
  const method = String(settings.method || "GET").toUpperCase();
  if (!/^\/api\/[A-Za-z0-9/_?=&.%-]*$/.test(path)) {
    return Promise.reject(new Error("拒绝无效 API 路径"));
  }
  let configuredBase;
  try {
    configuredBase = configuredApiBase();
  } catch (_error) {
    return Promise.reject(new Error("无法安全读取 CampusVoice 服务地址"));
  }
  const base = settings.expectedApiBase || configuredBase;
  if (!base) return Promise.reject(new Error("尚未配置 CampusVoice HTTPS 服务地址"));
  if (configuredBase !== base) return Promise.reject(new Error("服务地址已变化，请重试"));

  const authenticate = settings.auth !== false ? ensureSession(false, base) : Promise.resolve(null);
  return authenticate.then((session) => {
    let currentBase;
    try {
      currentBase = configuredApiBase();
    } catch (_error) {
      const error = new Error("无法安全读取 CampusVoice 服务地址");
      error.outcomeUncertain = false;
      throw error;
    }
    if (currentBase !== base || (session && session.apiBase !== base)) {
      const error = new Error("服务地址已变化，请重试");
      error.outcomeUncertain = false;
      throw error;
    }
    return new Promise((resolve, reject) => {
      const headers = Object.assign({ "content-type": "application/json" }, settings.header || {});
      if (session) headers.Authorization = "Bearer " + session.token;
      wx.request({
        url: base + path,
        method,
        data: settings.data,
        header: headers,
        timeout: settings.timeout || 15000,
        success: ({ statusCode, data, header }) => {
          if (statusCode >= 200 && statusCode < 300) {
            resolve({ data, header, statusCode });
            return;
          }
          if (statusCode === 401 && session) clearSession(session);
          const message = data && data.error && data.error.message;
          const error = new Error(message || "请求失败（" + statusCode + "）");
          error.statusCode = statusCode;
          error.code = data && data.error && data.error.code;
          error.outcomeUncertain = statusCode === 408 || statusCode >= 500;
          reject(error);
        },
        fail: () => {
          const error = new Error("网络请求失败，请检查连接后重试");
          error.outcomeUncertain = true;
          reject(error);
        },
      });
    });
  });
}

function canonicalValue(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "[" + value.map(canonicalValue).join(",") + "]";
  if (typeof value === "object") {
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map((key) => JSON.stringify(key) + ":" + canonicalValue(value[key]))
        .join(",") +
      "}"
    );
  }
  const encoded = JSON.stringify(value);
  return encoded === undefined ? "undefined" : encoded;
}

function shortFingerprint(value) {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x85ebca6b);
  }
  return (
    (first >>> 0).toString(16).padStart(8, "0") +
    (second >>> 0).toString(16).padStart(8, "0") +
    "-" +
    value.length
  );
}

function writeFingerprint(apiBase, method, path, data) {
  return shortFingerprint(
    apiBase + "\n" + String(method).toUpperCase() + "\n" + path + "\n" + canonicalValue(data),
  );
}

function pendingStorageError() {
  const error = new Error(
    "无法验证待处理写入记录；为避免重复写入，本次请求已停止，请先核对上次操作结果再处理小程序存储",
  );
  error.outcomeUncertain = true;
  return error;
}

function validStoredIntent(value, now) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (Object.keys(value).sort().join(",") !== "apiBase,createdAt,fingerprint,key") return false;
  try {
    if (normalizeApiBase(value.apiBase, true) !== value.apiBase) return false;
  } catch (_error) {
    return false;
  }
  if (typeof value.fingerprint !== "string" || !/^[0-9a-f]{16}-\d{1,12}$/.test(value.fingerprint)) {
    return false;
  }
  if (typeof value.key !== "string" || !/^wx-\d{10,16}-[a-z0-9]{6,20}$/.test(value.key)) {
    return false;
  }
  return (
    typeof value.createdAt === "number" &&
    Number.isSafeInteger(value.createdAt) &&
    value.createdAt > 0 &&
    value.createdAt <= now + 60000
  );
}

function persistPendingIntents(entries, required) {
  try {
    if (entries.length) wx.setStorageSync(PENDING_WRITE_STORAGE_KEY, entries);
    else wx.removeStorageSync(PENDING_WRITE_STORAGE_KEY);
    return true;
  } catch (_error) {
    if (!required) return false;
    const error = new Error("无法安全保存写入标识，请清理小程序存储后重试");
    error.outcomeUncertain = false;
    throw error;
  }
}

function loadPendingIntents(now) {
  let stored;
  try {
    stored = wx.getStorageSync(PENDING_WRITE_STORAGE_KEY);
  } catch (_error) {
    throw pendingStorageError();
  }
  if (stored == null || stored === "") return [];
  if (!Array.isArray(stored) || stored.length > MAX_PENDING_WRITE_INTENTS) {
    throw pendingStorageError();
  }
  if (stored.some((entry) => !validStoredIntent(entry, now))) {
    throw pendingStorageError();
  }

  const unique = new Map();
  for (const entry of stored) {
    if (unique.has(entry.fingerprint)) throw pendingStorageError();
    unique.set(entry.fingerprint, entry);
  }
  return [...unique.values()].sort((left, right) => left.createdAt - right.createdAt);
}
function writeIntent(apiBase, method, path, data) {
  const fingerprint = writeFingerprint(apiBase, method, path, data);
  const now = Date.now();
  const entries = loadPendingIntents(now);
  const existing = entries.find((entry) => entry.fingerprint === fingerprint);
  if (existing) {
    if (
      !runtimeRetryableKeys.has(existing.key) ||
      now - existing.createdAt > MAX_RUNTIME_RETRY_AGE_MS
    ) {
      throw pendingStorageError();
    }
    return { fingerprint, key: existing.key };
  }

  if (entries.length >= MAX_PENDING_WRITE_INTENTS) {
    const error = new Error(
      "待核对写入已达到安全上限；为避免丢失幂等标识或重复写入，本次请求已停止，请先核对已有操作结果",
    );
    error.outcomeUncertain = true;
    throw error;
  }

  const intent = { apiBase, fingerprint, key: idempotencyKey(), createdAt: now };
  const next = [...entries, intent];
  persistPendingIntents(next, true);
  runtimeRetryableKeys.add(intent.key);
  return { fingerprint, key: intent.key };
}

function clearWriteIntent(fingerprint, key) {
  let entries;
  try {
    entries = loadPendingIntents(Date.now());
  } catch (_error) {
    return false;
  }
  const next = entries.filter((entry) => entry.fingerprint !== fingerprint || entry.key !== key);
  if (next.length !== entries.length) {
    runtimeRetryableKeys.delete(key);
    return persistPendingIntents(next, false);
  }
  return true;
}

function verifiedRequest(method, path, data) {
  const normalizedMethod = String(method).toUpperCase();
  let base;
  try {
    base = configuredApiBase();
  } catch (_error) {
    return Promise.reject(pendingStorageError());
  }
  if (!base) return Promise.reject(new Error("尚未配置 CampusVoice HTTPS 服务地址"));
  let intent;
  try {
    intent = writeIntent(base, normalizedMethod, path, data);
  } catch (error) {
    return Promise.reject(error);
  }
  const operation = request("/api/auth/write-challenges", {
    expectedApiBase: base,
    method: "POST",
    data: { method: normalizedMethod, path, body: data == null ? null : data },
  }).then(({ data: issued }) => {
    if (issued.required_stages !== 1) {
      const error = new Error("此操作需要额外人工确认，请在网页版完成");
      error.outcomeUncertain = false;
      throw error;
    }
    return request(path, {
      expectedApiBase: base,
      method: normalizedMethod,
      data,
      header: {
        "X-Write-Challenge": issued.challenge,
        "Idempotency-Key": intent.key,
      },
    });
  });
  return operation.then(
    (response) => {
      clearWriteIntent(intent.fingerprint, intent.key);
      return response;
    },
    (error) => {
      if (!error || error.outcomeUncertain !== true) {
        clearWriteIntent(intent.fingerprint, intent.key);
      }
      throw error;
    },
  );
}

module.exports = { idempotencyKey, request, verifiedRequest };
