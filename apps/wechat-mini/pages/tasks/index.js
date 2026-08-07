const { currentAccountBoundary } = require("../../utils/auth");
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

function accountScopeOptions(page) {
  return {
    expectedApiBase: page._activeApiBase,
    expectedAccountBoundary: {
      accountId: page._activeAccountId || "",
      logoutGeneration: page._activeLogoutGeneration,
    },
  };
}

function handleAccountBoundaryError(page, reason, generation) {
  if (
    !reason ||
    (reason.code !== "ACCOUNT_BOUNDARY_CHANGED" && reason.code !== "ACCOUNT_BOUNDARY_UNAVAILABLE")
  ) {
    return false;
  }
  if (reason.code === "ACCOUNT_BOUNDARY_UNAVAILABLE") {
    page._activeApiBase = "";
    page._activeAccountId = "";
    page._activeLogoutGeneration = -1;
  }
  page._accountBoundaryResetGeneration = generation;
  page.onShow();
  return true;
}

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
    let apiBase;
    let boundary;
    try {
      apiBase = configuredApiBase();
      boundary = apiBase ? currentAccountBoundary(apiBase) : { accountId: "", logoutGeneration: 0 };
    } catch (_error) {
      this._activeApiBase = "";
      this._activeAccountId = "";
      this._activeLogoutGeneration = -1;
      this.setData({
        configured: false,
        loading: false,
        busy: Boolean(this._writePending),
        tasks: [],
        error: "无法安全读取本机会话，请重试",
        editorOpen: false,
        form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
        priorityIndex: 1,
      });
      return;
    }
    const configured = Boolean(apiBase);
    const accountId = boundary.accountId;
    const hasPreviousScope = this._activeApiBase !== undefined;
    const baseChanged = hasPreviousScope && this._activeApiBase !== apiBase;
    const accountChanged =
      hasPreviousScope &&
      this._activeApiBase === apiBase &&
      Boolean(this._activeAccountId) &&
      this._activeAccountId !== accountId;
    const logoutChanged =
      hasPreviousScope && this._activeLogoutGeneration !== boundary.logoutGeneration;
    const scopeChanged = baseChanged || accountChanged || logoutChanged;
    this._activeApiBase = apiBase;
    this._activeAccountId = accountId;
    this._activeLogoutGeneration = boundary.logoutGeneration;
    const busy = Boolean(this._writePending);
    if (!configured) {
      this.setData({
        configured: false,
        loading: false,
        busy,
        tasks: [],
        error: "",
        editorOpen: false,
        form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
        priorityIndex: 1,
      });
      return;
    }
    const nextState = { configured: true, loading: false, busy };
    if (scopeChanged) {
      Object.assign(nextState, {
        editorOpen: false,
        form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
        priorityIndex: 1,
      });
    }
    this.setData(nextState);
    this.load();
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

  retryLoad() {
    this.onShow();
  },

  load() {
    if (!this.data.configured || this.data.loading) return Promise.resolve();
    const generation = pageGeneration(this);
    this.setData({ loading: true, tasks: [], error: "" });
    return request("/api/tasks?limit=100")
      .then(({ accountBoundary, data }) => {
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
        const nextState = { tasks };
        if (accountBoundary) {
          const accountChanged =
            Boolean(this._activeAccountId) && this._activeAccountId !== accountBoundary.accountId;
          const logoutChanged = this._activeLogoutGeneration !== accountBoundary.logoutGeneration;
          if (accountChanged || logoutChanged) {
            Object.assign(nextState, {
              editorOpen: false,
              form: { title: "", course: "", dueDate: "", dueTime: "18:00" },
              priorityIndex: 1,
            });
          }
          this._activeAccountId = accountBoundary.accountId;
          this._activeLogoutGeneration = accountBoundary.logoutGeneration;
        }
        this.setData(nextState);
      })
      .catch((reason) => {
        if (!isPageCurrent(this, generation)) return;
        if (handleAccountBoundaryError(this, reason, generation)) return;
        this.setData({ error: friendlyError(reason) });
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
    if (!this.data.configured) return;
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
    verifiedRequest("POST", "/api/tasks", body, accountScopeOptions(this))
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
        if (!isPageCurrent(this, generation)) return;
        if (handleAccountBoundaryError(this, reason, generation)) return;
        this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        this._writePending = false;
        const activeGeneration = pageGeneration(this);
        if (!isPageCurrent(this, activeGeneration)) return;
        this.setData({ busy: false });
        if (this._accountBoundaryResetGeneration === generation) {
          delete this._accountBoundaryResetGeneration;
          return;
        }
        if (activeGeneration !== generation) {
          if (this.data.loading) this._reloadAfterLoad = true;
          else this.load();
        }
      });
  },

  completeTask(event) {
    const task = this.data.tasks.find((item) => item.id === event.currentTarget.dataset.id);
    if (!this.data.configured || !task || task.completed || this.data.busy) return;
    const path = "/api/tasks/" + encodeURIComponent(task.id);
    const generation = pageGeneration(this);
    this._writePending = true;
    this.setData({ busy: true, error: "" });
    verifiedRequest(
      "PATCH",
      path,
      { status: "completed", expected_version: task.version },
      accountScopeOptions(this),
    )
      .then(() => {
        if (!isPageCurrent(this, generation)) return;
        wx.showToast({ title: "任务已完成", icon: "success" });
        return this.load();
      })
      .catch((reason) => {
        if (!isPageCurrent(this, generation)) return;
        if (handleAccountBoundaryError(this, reason, generation)) return;
        this.setData({ error: friendlyError(reason) });
      })
      .finally(() => {
        this._writePending = false;
        const activeGeneration = pageGeneration(this);
        if (!isPageCurrent(this, activeGeneration)) return;
        this.setData({ busy: false });
        if (this._accountBoundaryResetGeneration === generation) {
          delete this._accountBoundaryResetGeneration;
          return;
        }
        if (activeGeneration !== generation) {
          if (this.data.loading) this._reloadAfterLoad = true;
          else this.load();
        }
      });
  },
});
