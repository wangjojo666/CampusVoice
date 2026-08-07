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

const LOCAL_CONFIG_ERROR = "无法安全读取本地配置，请重试";

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
    this.retryLoad();
  },

  retryLoad() {
    let configured = false;
    try {
      configured = Boolean(configuredApiBase());
    } catch (_error) {
      this.setData({ configured: false, loading: false, cards: [], error: LOCAL_CONFIG_ERROR });
      return Promise.resolve();
    }
    if (!configured) {
      this.setData({ configured: false, loading: false, cards: [], error: "" });
      return Promise.resolve();
    }
    this.setData({ configured: true, loading: false });
    return this.load();
  },

  onHide() {
    hidePage(this);
  },

  onUnload() {
    unloadPage(this);
  },

  onPullDownRefresh() {
    this.retryLoad().finally(() => wx.stopPullDownRefresh());
  },

  load() {
    if (!this.data.configured || this.data.loading) return Promise.resolve();
    const generation = pageGeneration(this);
    this.setData({ loading: true, cards: [], error: "" });
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
        this.setData({ cards, loading: false });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) {
          this.setData({ error: friendlyError(reason), loading: false });
        }
      });
  },
});
