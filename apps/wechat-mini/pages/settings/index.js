const appConfig = require("../../config");
const { clearSession, currentSession, revokeSession } = require("../../utils/auth");
const { configuredApiBase, envVersion, saveDevelopmentApiBase } = require("../../utils/config");
const { request } = require("../../utils/request");
const { friendlyError } = require("../../utils/format");
const {
  hidePage,
  isPageCurrent,
  pageGeneration,
  showPage,
  unloadPage,
} = require("../../utils/page-lifecycle");

const LOCAL_CONFIG_ERROR = "无法安全读取本地配置，请重试";

Page({
  data: {
    appId: appConfig.appId,
    environment: "",
    release: false,
    apiInput: "",
    configured: false,
    signedIn: false,
    displayName: "",
    checking: false,
    operation: "",
    message: "",
    error: "",
    localStateReadFailed: false,
  },

  onShow() {
    showPage(this);
    this.refreshLocalState();
  },

  refreshLocalState() {
    const recovering = this.data.localStateReadFailed;
    try {
      const environment = envVersion();
      if (environment === "unknown") throw new Error("无法确认小程序运行环境");
      const api = configuredApiBase();
      const session = currentSession();
      const patch = {
        environment,
        release: environment !== "develop",
        apiInput: api,
        configured: Boolean(api),
        signedIn: Boolean(session),
        displayName: session ? session.displayName : "",
        checking: Boolean(this._operationPending),
        operation: this._operationName || "",
        localStateReadFailed: false,
      };
      if (recovering) patch.error = "";
      this.setData(patch);
      return true;
    } catch (_error) {
      this.setData({
        environment: "",
        release: true,
        apiInput: "",
        configured: false,
        signedIn: false,
        displayName: "",
        checking: Boolean(this._operationPending),
        operation: this._operationName || "",
        message: "",
        error: LOCAL_CONFIG_ERROR,
        localStateReadFailed: true,
      });
      return false;
    }
  },

  retryLocalState() {
    this.refreshLocalState();
  },

  onHide() {
    hidePage(this);
  },

  onUnload() {
    unloadPage(this);
  },

  updateApi(event) {
    if (this.data.checking) return;
    this.setData({ apiInput: event.detail.value });
  },

  saveApi() {
    if (this.data.checking) return;
    try {
      const api = saveDevelopmentApiBase(this.data.apiInput);
      if (!clearSession()) throw new Error("无法从本机删除旧登录凭证，请重试");
      this.setData({
        apiInput: api,
        configured: Boolean(api),
        signedIn: false,
        displayName: "",
        message: api ? "开发服务地址已保存，请执行连接检查" : "开发服务地址已清除",
        error: "",
      });
    } catch (reason) {
      this.setData({ error: friendlyError(reason), message: "" });
    }
  },

  checkConnection() {
    if (!this.data.configured || this.data.checking) return;
    const generation = pageGeneration(this);
    this._operationPending = true;
    this._operationName = "check";
    this.setData({ checking: true, operation: "check", error: "", message: "" });
    request("/api/health", { auth: false })
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) return;
        if (!this.refreshLocalState()) return;
        this.setData({
          message: "CampusVoice API 健康检查已通过（" + (data.status || "ok") + "）",
        });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        this._operationPending = false;
        this._operationName = "";
        const activeGeneration = pageGeneration(this);
        if (isPageCurrent(this, activeGeneration)) {
          this.setData({ checking: false, operation: "" });
        }
      });
  },

  signOut() {
    if (this.data.checking) return;
    const generation = pageGeneration(this);
    this._operationPending = true;
    this._operationName = "signOut";
    this.setData({ checking: true, operation: "signOut", message: "", error: "" });
    revokeSession()
      .then(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          signedIn: false,
          displayName: "",
          message: "服务端会话已撤销，本机登录凭证已清除",
        });
      })
      .catch((reason) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          signedIn: false,
          displayName: "",
          error: friendlyError(reason) + "；本机凭证已清除",
        });
      })
      .finally(() => {
        this._operationPending = false;
        this._operationName = "";
        const activeGeneration = pageGeneration(this);
        if (isPageCurrent(this, activeGeneration)) {
          this.setData({ checking: false, operation: "" });
        }
      });
  },

  openPrivacy() {
    wx.navigateTo({ url: "/pages/privacy/index" });
  },
});
