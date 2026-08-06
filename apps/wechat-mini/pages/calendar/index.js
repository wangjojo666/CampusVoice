const { configuredApiBase } = require("../../utils/config");
const { request, verifiedRequest } = require("../../utils/request");
const { dateTime, friendlyError } = require("../../utils/format");
const {
  hidePage,
  isPageCurrent,
  pageGeneration,
  showPage,
  unloadPage,
} = require("../../utils/page-lifecycle");

function today() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
}

Page({
  data: {
    configured: false,
    loading: false,
    busy: false,
    events: [],
    error: "",
    editorOpen: false,
    form: { title: "", course: "", location: "", date: "", time: "09:00" },
  },

  onLoad() {
    this.setData({ "form.date": today() });
  },

  onShow() {
    showPage(this);
    const configured = Boolean(configuredApiBase());
    this.setData({ configured, loading: false, busy: Boolean(this._writePending) });
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
    const after = encodeURIComponent(new Date().toISOString());
    return request("/api/events?starts_after=" + after + "&limit=100")
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) return;
        const events = (data.items || []).map((item) =>
          Object.assign({}, item, {
            startText: dateTime(item.start_at),
            endText: dateTime(item.end_at),
          }),
        );
        this.setData({ events });
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({ loading: false });
        if (this._reloadAfterLoad) {
          this._reloadAfterLoad = false;
          Promise.resolve().then(() => this.load());
        }
      });
  },

  toggleEditor() {
    this.setData({ editorOpen: !this.data.editorOpen, error: "" });
  },

  updateField(event) {
    this.setData({ ["form." + event.currentTarget.dataset.field]: event.detail.value });
  },

  createEvent() {
    const title = this.data.form.title.trim();
    if (!title || this.data.busy) {
      if (!title) this.setData({ error: "请输入日程标题" });
      return;
    }
    const start = new Date(this.data.form.date + "T" + this.data.form.time + ":00");
    if (Number.isNaN(start.getTime())) {
      this.setData({ error: "开始时间格式无效" });
      return;
    }
    const end = new Date(start.getTime() + 60 * 60 * 1000);
    const body = {
      title,
      course: this.data.form.course.trim() || null,
      location: this.data.form.location.trim() || null,
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      reminder_minutes: 30,
      source_type: "manual",
    };
    const generation = pageGeneration(this);
    this._writePending = true;
    this.setData({ busy: true, error: "" });
    request("/api/events/check-conflict", {
      method: "POST",
      data: { start_at: body.start_at, end_at: body.end_at },
    })
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) {
          const error = new Error("页面状态已变化，已停止后续日程写入");
          error.outcomeUncertain = false;
          throw error;
        }
        if (data.has_conflict) throw new Error("该时段与已有日程冲突，请先调整时间");
        return verifiedRequest("POST", "/api/events", body);
      })
      .then(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          editorOpen: false,
          form: { title: "", course: "", location: "", date: today(), time: "09:00" },
        });
        wx.showToast({ title: "日程已创建", icon: "success" });
        return this.load();
      })
      .catch((reason) => {
        if (isPageCurrent(this, generation)) this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        this._writePending = false;
        const activeGeneration = pageGeneration(this);
        if (!isPageCurrent(this, activeGeneration)) return;
        this.setData({ busy: false });
        if (activeGeneration !== generation) {
          if (this.data.loading) this._reloadAfterLoad = true;
          else this.load();
        }
      });
  },
});
