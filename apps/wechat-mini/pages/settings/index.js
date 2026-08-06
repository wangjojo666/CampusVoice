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
    message: "",
    error: "",
  },

  onShow() {
    showPage(this);
    const environment = envVersion();
    const api = configuredApiBase();
    const session = currentSession();
    this.setData({
      environment,
      release: environment !== "develop",
      apiInput: api,
      configured: Boolean(api),
      signedIn: Boolean(session),
      displayName: session ? session.displayName : "",
      checking: Boolean(this._operationPending),
    });
  },

  onHide() {
    hidePage(this);
  },

  onUnload() {
    unloadPage(this);
  },

  updateApi(event) {
    this.setData({ apiInput: event.detail.value });
  },

  saveApi() {
    try {
      const api = saveDevelopmentApiBase(this.data.apiInput);
      clearSession();
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
    this.setData({ checking: true, error: "", message: "" });
    request("/api/health", { auth: false })
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) return;
        const session = currentSession();
        this.setData({
          signedIn: Boolean(session),
          displayName: session ? session.displayName : "",
          message: "CampusVoice API 健康检查已通过（" + (data.status || "ok") + "）",
        });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        this._operationPending = false;
        const activeGeneration = pageGeneration(this);
        if (isPageCurrent(this, activeGeneration)) this.setData({ checking: false });
      });
  },

  signOut() {
    if (this.data.checking) return;
    const generation = pageGeneration(this);
    this._operationPending = true;
    this.setData({ checking: true, message: "", error: "" });
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
        const activeGeneration = pageGeneration(this);
        if (isPageCurrent(this, activeGeneration)) this.setData({ checking: false });
      });
  },

  openPrivacy() {
    wx.navigateTo({ url: "/pages/privacy/index" });
  },
});
