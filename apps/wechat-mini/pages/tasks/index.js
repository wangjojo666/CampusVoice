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

Page({
  data: {
    configured: false,
    loading: false,
    busy: false,
    tasks: [],
    error: "",
    editorOpen: false,
    priorities: ["low", "medium", "high"],
    priorityLabels: ["低", "中", "高"],
    priorityIndex: 1,
    form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
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
    return request("/api/tasks?limit=100")
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) return;
        const tasks = (data.items || []).map((item) =>
          Object.assign({}, item, {
            dueText: dateTime(item.due_at),
            statusText:
              item.status === "completed"
                ? "已完成"
                : item.status === "in_progress"
                  ? "进行中"
                  : item.status === "cancelled"
                    ? "已取消"
                    : "待处理",
            completed: item.status === "completed",
          }),
        );
        this.setData({ tasks });
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

  selectPriority(event) {
    this.setData({ priorityIndex: Number(event.detail.value) });
  },

  createTask() {
    const title = this.data.form.title.trim();
    if (!title || this.data.busy) {
      if (!title) this.setData({ error: "请输入任务标题" });
      return;
    }
    let dueAt = null;
    if (this.data.form.dueDate) {
      const date = new Date(this.data.form.dueDate + "T" + this.data.form.dueTime + ":00");
      if (Number.isNaN(date.getTime())) {
        this.setData({ error: "截止时间格式无效" });
        return;
      }
      dueAt = date.toISOString();
    }
    const body = {
      title,
      course: this.data.form.course.trim() || null,
      due_at: dueAt,
      priority: this.data.priorities[this.data.priorityIndex],
      source_type: "manual",
    };
    const generation = pageGeneration(this);
    this._writePending = true;
    this.setData({ busy: true, error: "" });
    verifiedRequest("POST", "/api/tasks", body)
      .then(() => {
        if (!isPageCurrent(this, generation)) return;
        this.setData({
          editorOpen: false,
          form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
          priorityIndex: 1,
        });
        wx.showToast({ title: "任务已创建", icon: "success" });
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

  completeTask(event) {
    const task = this.data.tasks.find((item) => item.id === event.currentTarget.dataset.id);
    if (!task || task.completed || this.data.busy) return;
    const path = "/api/tasks/" + encodeURIComponent(task.id);
    const generation = pageGeneration(this);
    this._writePending = true;
    this.setData({ busy: true, error: "" });
    verifiedRequest("PATCH", path, { status: "completed", expected_version: task.version })
      .then(() => {
        if (!isPageCurrent(this, generation)) return;
        wx.showToast({ title: "任务已完成", icon: "success" });
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
