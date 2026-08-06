const { request } = require("../../utils/request");
const { configuredApiBase, websocketBase } = require("../../utils/config");
const { friendlyError } = require("../../utils/format");
const { createVoiceSessionController } = require("../../utils/voice-session");

const recorder = wx.getRecorderManager();
const voiceSession = createVoiceSessionController({ wxApi: wx, recorder });

function authorizeRecording() {
  return new Promise((resolve, reject) => {
    const authorize = () =>
      wx.authorize({
        scope: "scope.record",
        success: resolve,
        fail: () => reject(new Error("需要麦克风权限才能录音，请在右上角设置中允许")),
      });
    if (wx.canIUse("requirePrivacyAuthorize")) {
      wx.requirePrivacyAuthorize({
        success: authorize,
        fail: () => reject(new Error("请先阅读并同意隐私保护指引")),
      });
    } else {
      authorize();
    }
  });
}

Page({
  data: {
    phase: "idle",
    statusText: "点击开始，说出你的任务、日程或校园问题",
    transcript: "",
    error: "",
    configured: false,
  },

  onLoad() {
    this.voiceObserver = {
      onState: (state) => {
        this.setData({
          phase: state.phase,
          statusText: state.statusText,
          transcript: state.transcript,
          error: state.error,
        });
      },
    };
    voiceSession.attach(this.voiceObserver);
    this.setData({ configured: Boolean(configuredApiBase()) });
  },

  onShow() {
    this.setData({ configured: Boolean(configuredApiBase()) });
  },

  onHide() {
    const phase = voiceSession.snapshot().phase;
    if (phase !== "idle" && phase !== "done") {
      voiceSession.failClosed("页面进入后台，录音已安全停止");
    }
  },

  onUnload() {
    voiceSession.detach(this.voiceObserver);
    this.voiceObserver = null;
  },

  start() {
    if (!this.data.configured) return;
    const generation = voiceSession.begin();
    if (generation === null) return;
    authorizeRecording()
      .then(() => request("/api/auth/ws-ticket", { method: "POST" }))
      .then(({ data }) => {
        if (!voiceSession.isCurrent(generation)) return;
        if (!data || typeof data.ticket !== "string" || !data.ticket) {
          throw new Error("服务端未返回有效的语音连接凭证");
        }
        voiceSession.connect(websocketBase() + "/ws/asr", data.ticket, generation);
      })
      .catch((reason) => voiceSession.cancel(generation, friendlyError(reason)));
  },

  stop() {
    voiceSession.stop();
  },

  reset() {
    voiceSession.reset();
  },
});
