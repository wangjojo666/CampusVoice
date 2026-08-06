const { ensureSession, revokeSession } = require("./utils/auth");
const { configuredApiBase } = require("./utils/config");

App({
  globalData: {
    session: null,
    apiConfigured: false,
  },

  onLaunch() {
    this.globalData.apiConfigured = Boolean(configuredApiBase());
    this.installUpdateHandler();
  },

  ensureSession(force) {
    return ensureSession(Boolean(force)).then((session) => {
      this.globalData.session = session;
      return session;
    });
  },

  signOut() {
    return revokeSession().finally(() => {
      this.globalData.session = null;
    });
  },

  installUpdateHandler() {
    if (!wx.canIUse("getUpdateManager")) return;
    const manager = wx.getUpdateManager();
    manager.onUpdateReady(() => {
      wx.showModal({
        title: "新版本已就绪",
        content: "重新启动后使用新版本。",
        confirmText: "立即更新",
        success: ({ confirm }) => {
          if (confirm) manager.applyUpdate();
        },
      });
    });
    manager.onUpdateFailed(() => {
      wx.showToast({ title: "更新下载失败，请稍后重试", icon: "none" });
    });
  },
});
