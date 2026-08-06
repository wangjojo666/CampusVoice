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

const typeLabels = {
  new_notice: "新通知",
  version_change: "版本变化",
  upcoming_deadline: "临近截止",
  needs_review: "需要核对",
};

Page({
  data: {
    configured: false,
    loading: false,
    cards: [],
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

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  load() {
    if (!this.data.configured || this.data.loading) return Promise.resolve();
    const generation = pageGeneration(this);
    this.setData({ loading: true, error: "" });
    return request("/api/notice-radar?limit=30")
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) return;
        const cards = (data.items || []).map((item) =>
          Object.assign({}, item, {
            typeLabel: typeLabels[item.card_type] || "校园通知",
            createdText: dateTime(item.created_at),
            deadlineText: item.deadline_at ? dateTime(item.deadline_at) : "",
            changeSummary: item.change_count ? item.change_count + " 项变化" : "",
            impactSummary:
              item.affected_tasks || item.affected_events
                ? "影响 " + item.affected_tasks + " 个任务、" + item.affected_events + " 个日程"
                : "",
          }),
        );
        this.setData({ cards });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        if (isPageCurrent(this, generation)) this.setData({ loading: false });
      });
  },
});
