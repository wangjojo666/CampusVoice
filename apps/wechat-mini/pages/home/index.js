const { configuredApiBase } = require("../../utils/config");
const { request } = require("../../utils/request");
const { dateTime, friendlyError } = require("../../utils/format");
const {
  hidePage,
  isPageCurrent,
  pageGeneration,
  showPage,
  unloadPage,
} = require("../../utils/page-lifecycle");

Page({
  data: {
    configured: false,
    loading: false,
    greeting: "你好",
    pendingTasks: [],
    upcomingEvents: [],
    error: "",
  },

  onShow() {
    showPage(this);
    const configured = Boolean(configuredApiBase());
    this.setData({ configured, loading: false });
    if (configured) this.load();
  },

  onHide() {
    hidePage(this);
  },

  onUnload() {
    unloadPage(this);
  },

  load() {
    if (this.data.loading) return Promise.resolve();
    const generation = pageGeneration(this);
    this.setData({ loading: true, error: "" });
    return Promise.all([
      request("/api/auth/session"),
      request("/api/tasks?status=pending&limit=5"),
      request(
        "/api/events?starts_after=" + encodeURIComponent(new Date().toISOString()) + "&limit=5",
      ),
    ])
      .then(([session, tasks, events]) => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          greeting: "你好，" + (session.data.display_name || "同学"),
          pendingTasks: (tasks.data.items || []).map((item) =>
            Object.assign({}, item, {
              dueText: dateTime(item.due_at),
            }),
          ),
          upcomingEvents: (events.data.items || []).map((item) =>
            Object.assign({}, item, {
              startText: dateTime(item.start_at),
            }),
          ),
        });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        if (isPageCurrent(this, generation)) this.setData({ loading: false });
      });
  },

  openSettings() {
    wx.navigateTo({ url: "/pages/settings/index" });
  },

  openPrivacy() {
    wx.navigateTo({ url: "/pages/privacy/index" });
  },
});
