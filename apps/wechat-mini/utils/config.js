const baseConfig = require("../config");

const DEVELOPMENT_API_KEY = "campusvoice.developmentApiBase";

function envVersion() {
  try {
    const version = wx.getAccountInfoSync().miniProgram.envVersion;
    return ["develop", "trial", "release"].includes(version) ? version : "unknown";
  } catch (_error) {
    return "unknown";
  }
}

function normalizeApiBase(value, allowLocal) {
  const raw = String(value || "")
    .trim()
    .replace(/\/+$/, "");
  if (!raw) return "";
  if (/[?#@]/.test(raw)) throw new Error("服务地址不能包含凭据、查询参数或片段");
  const secure = /^https:\/\/[A-Za-z0-9.-]+(?::\d+)?(?:\/.*)?$/i.test(raw);
  const local = /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?(?:\/.*)?$/i.test(raw);
  if (!secure && !(allowLocal && local)) {
    throw new Error("服务地址必须使用 HTTPS；开发版仅允许 localhost HTTP");
  }
  return raw;
}

function configuredApiBase() {
  const version = envVersion();
  if (version === "develop") {
    const override = wx.getStorageSync(DEVELOPMENT_API_KEY);
    if (override) return normalizeApiBase(override, true);
  }
  return normalizeApiBase(baseConfig.apiBaseUrl, false);
}

function saveDevelopmentApiBase(value) {
  if (envVersion() !== "develop") throw new Error("仅开发版允许修改服务地址");
  const normalized = normalizeApiBase(value, true);
  if (normalized) wx.setStorageSync(DEVELOPMENT_API_KEY, normalized);
  else wx.removeStorageSync(DEVELOPMENT_API_KEY);
  return normalized;
}

function websocketBase() {
  const base = configuredApiBase();
  if (!base) return "";
  return base.replace(/^https:/i, "wss:").replace(/^http:/i, "ws:");
}

module.exports = {
  configuredApiBase,
  envVersion,
  normalizeApiBase,
  saveDevelopmentApiBase,
  websocketBase,
};
