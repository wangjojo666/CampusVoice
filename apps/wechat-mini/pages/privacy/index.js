const appConfig = require("../../config");
const { configuredApiBase } = require("../../utils/config");
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

let activeDeletionOperation = null;
let deletionOperationSequence = 0;
let pendingDeletionResult = null;
let visiblePrivacyPage = null;

Page({
  data: {
    privacyVersion: appConfig.privacyVersion,
    configured: false,
    deleting: false,
    message: "",
    error: "",
    localStateError: "",
  },

  onShow() {
    showPage(this);
    visiblePrivacyPage = this;
    const result = pendingDeletionResult || {};
    pendingDeletionResult = null;
    let configured = false;
    let localStateError = "";
    try {
      configured = Boolean(configuredApiBase());
    } catch (_error) {
      localStateError = LOCAL_CONFIG_ERROR;
    }
    this.setData(
      Object.assign(
        {
          configured,
          deleting: activeDeletionOperation !== null,
          message: "",
          error: "",
          localStateError,
        },
        result,
      ),
    );
  },

  retryLocalState() {
    try {
      this.setData({
        configured: Boolean(configuredApiBase()),
        localStateError: "",
      });
    } catch (_error) {
      this.setData({ configured: false, localStateError: LOCAL_CONFIG_ERROR });
    }
  },

  onHide() {
    if (visiblePrivacyPage === this) visiblePrivacyPage = null;
    hidePage(this);
  },

  onUnload() {
    if (visiblePrivacyPage === this) visiblePrivacyPage = null;
    unloadPage(this);
  },

  requestDeletion() {
    if (!this.data.configured || this.data.deleting) return;
    const generation = pageGeneration(this);
    wx.showModal({
      title: "删除业务数据",
      content:
        "这会永久删除你的任务、日程、通知、语音转写及操作记录，且无法恢复。微信账号绑定本身不会被删除。",
      confirmText: "继续",
      confirmColor: "#b63d36",
      success: ({ confirm }) => {
        if (!isPageCurrent(this, generation)) return;
        if (confirm) this.issueDeletionChallenge();
      },
    });
  },

  issueDeletionChallenge() {
    if (activeDeletionOperation !== null) return;
    const generation = pageGeneration(this);
    const operation = ++deletionOperationSequence;
    activeDeletionOperation = operation;
    this._deletionOperation = operation;
    this._deletionPending = true;
    this.setData({ deleting: true, message: "", error: "" });
    request("/api/privacy/deletion-challenges", { method: "POST" })
      .then(({ data }) => {
        if (!isPageCurrent(this, generation)) {
          this.finishDeletionOperation(operation, {
            error: "页面状态已变化，请重新开始删除操作",
          });
          return;
        }
        wx.showModal({
          title: "最终确认",
          content: "服务端已生成一次性删除挑战。确认后将立即永久删除业务数据。",
          confirmText: "永久删除",
          confirmColor: "#b63d36",
          success: ({ confirm }) => {
            if (!isPageCurrent(this, generation)) {
              this.finishDeletionOperation(operation, {
                error: "页面状态已变化，请重新开始删除操作",
              });
              return;
            }
            if (!confirm) {
              this.finishDeletionOperation(operation);
              return;
            }
            this.confirmDeletion(data, operation);
          },
          fail: () => this.finishDeletionOperation(operation),
        });
      })
      .catch((reason) => {
        this.finishDeletionOperation(operation, { error: friendlyError(reason) });
      });
  },

  finishDeletionOperation(operation, result) {
    if (activeDeletionOperation !== operation) return;
    activeDeletionOperation = null;
    this._deletionOperation = null;
    this._deletionPending = false;
    const patch = Object.assign({ deleting: false }, result || {});
    const target = visiblePrivacyPage;
    if (target && isPageCurrent(target, pageGeneration(target))) target.setData(patch);
    else pendingDeletionResult = patch;
  },

  confirmDeletion(challenge, operation) {
    request("/api/privacy/deletion-challenges/" + encodeURIComponent(challenge.id) + "/confirm", {
      method: "POST",
      data: {
        challenge: challenge.challenge,
        scope: "business_data",
        confirmation: "DELETE_MY_DATA",
      },
    })
      .then(({ data }) => {
        if (!data.verified) throw new Error("服务端未返回删除核验结果");
        this.finishDeletionOperation(operation, {
          message: "业务数据已删除并由服务端核验",
          error: "",
        });
      })
      .catch((reason) => {
        this.finishDeletionOperation(operation, { error: friendlyError(reason) });
      });
  },
});
