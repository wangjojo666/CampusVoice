# 微信小程序发布说明

CampusVoice 小程序的规范工程目录是 `apps/wechat-mini`，AppID 为 `wx3648488d39d15ff4`。工程使用原生 WXML、WXSS 和 JavaScript，不使用 `web-view`；录音权限、页面生命周期、请求域名和 WebSocket 均由微信基础库管理。仓库外的 `D:\yind\project.config.json` 只为当前工作机提供快捷入口，不是发布配置的事实来源；两者不一致时必须直接导入 `apps/wechat-mini`。

## 身份与 WebSocket 安全边界

小程序调用 `wx.login` 取得一次性 code，再提交到 `POST /api/auth/wechat/login`。只有 API 服务持有微信 AppSecret，并由服务端通过微信官方 HTTPS `code2session` 接口换取 OpenID。OpenID、UnionID、`session_key` 和 AppSecret 不返回小程序，也不写入日志。

API 返回有期限的随机 `cvwx1.` Bearer 会话；数据库只保存会话哈希。小程序以 `Authorization: Bearer cvwx1.…` 请求 REST；退出时调用 `POST /api/auth/wechat/logout` 撤销服务端会话，即使网络失败也会清除本机 token。迟到的旧请求 401 或旧注销回调只能清除它实际携带的同一 token/API base，不能取消已在进行的新登录或删除替换后的会话。

小程序写操作把幂等记录绑定到规范化后的精确 API base，并把 challenge 与实际写入固定在同一 base；切换服务地址不能复用或删除另一服务的 key。响应结果不确定时，只允许同一小程序运行时在 1 小时内复用原 key。进程重启或超过 1 小时后会保留未决证据并 fail-closed，不自动重放、换 key 或删除记录；此时必须先在对应服务端核对操作结果。该冻结策略不能表述为“跨重启自动恢复”。

认证模式必须按部署拓扑选择：

- `wechat`：仅服务微信小程序；不提供 Web OIDC 登录。
- `oidc_wechat`：同一 API 同时服务 Web 与微信小程序。无 `Authorization` 的请求只使用 Web OIDC `HttpOnly` cookie；带 Bearer 的请求必须是格式正确的 `cvwx1.` 微信会话。错误 Bearer 不会回退并借用浏览器 cookie。
- `oidc`：仅服务 Web OIDC；微信登录端点不可用。

语音连接先通过已认证 REST 换取 30 秒有效、单次消费的 WebSocket ticket。ticket 仅通过 `Sec-WebSocket-Protocol` 发送，不进入 URL。浏览器 OIDC 会话签发的 ticket 绑定精确、已允许的 `Origin`；微信会话签发的 ticket 绑定服务端内部 `wechat-miniprogram://<AppID>` origin sentinel（不同于 `wechat:miniprogram:<AppID>` 会话 issuer），不依赖小程序 WebSocket 是否携带浏览器 Origin。两种 ticket 都绑定当前用户、只存哈希并原子单次消费，不能互换。

## 小程序录音协议

微信 RecorderManager 以 `format: "mp3"`、`frameSize: 4` 产生 MP3 分帧。小程序先发送 `audio_format: "mp3"` 的 `start` 控制消息，再按顺序把所有二进制数据分成最多 1024 字节的 WebSocket 帧（低于服务端允许配置的最小单帧上限）。若在 `onStop` 前收到明确的 `isLastFrame`，客户端先发送该帧再最多发送一次 `stop`；一旦 `onStop` 先到，就忽略其后的迟到 frame callback，并只以 `onStop.tempFilePath` 为唯一完成数据源，逐字节确认已发送帧是完整文件前缀、补发有界尾部后再发送 `stop`。文件读取有 5 秒边界；缺失、超时或前缀不一致均 fail-closed。

服务端在录制期间只做单帧和累计压缩字节数限制，不把不完整 MP3 帧交给 ASR，也不返回实时 partial。`stop` 后立即发送 `finalizing`，随后把完整 MP3 交给固定参数、非 shell 的 FFmpeg 子进程，在输入大小、解码时长、输出 PCM 大小和超时限制内转换为 16 kHz、单声道、s16le PCM，再送入真实 ASR provider 完成最终识别；未结束时每 5 秒发送一次不落库的 `finalizing` 心跳。小程序保持 15 秒无进展 fail-closed 边界，心跳或转写进度只刷新该 idle timer；另有从发送 `stop` 起 180 秒、任何心跳都不可重置的 hard deadline。因此界面只能承诺“停止后完成识别”，不能宣传录制中实时转写。

页面进入后台、卸载、录音被系统中断、WebSocket 断开或超时时，客户端立即 fail-closed，停止录音并使旧 recorder、socket、timer 和 callback 失效；不会自动恢复旧会话。

## 生产环境

仅小程序 API 可使用：

```dotenv
CAMPUSVOICE_ENV=production
CAMPUSVOICE_AUTH_MODE=wechat
CAMPUSVOICE_WECHAT_APP_ID=wx3648488d39d15ff4
CAMPUSVOICE_WECHAT_APP_SECRET=<secret-manager value>
CAMPUSVOICE_WECHAT_CODE_EXCHANGE_MAX_CONCURRENCY=8
CAMPUSVOICE_WECHAT_CODE_EXCHANGE_RATE_PER_SECOND=2
CAMPUSVOICE_WECHAT_CODE_EXCHANGE_BURST=8
CAMPUSVOICE_CONFIRMATION_SECRET=<at least 32 random characters>
CAMPUSVOICE_DATABASE_AUTO_CREATE=false
CAMPUSVOICE_STORE_RAW_AUDIO=false
CAMPUSVOICE_ASR_PROVIDER=funasr
CAMPUSVOICE_ASR_DEVICE=cpu
```

Web 与小程序共用 API 时把 `CAMPUSVOICE_AUTH_MODE` 改为 `oidc_wechat`，并同时提供完整的 OIDC HTTPS issuer、client ID、回调、登录/登出跳转、scope、算法和可选 client secret 配置；浏览器 CORS origin 必须精确配置，不能使用通配符。

不得把 `CAMPUSVOICE_WECHAT_APP_SECRET` 放入 `config.js`、`project.config.json`、微信开发者工具配置、客户端日志或 Git。AppSecret 与确认密钥必须由生产 secret manager 注入。正式发布前，把 `apps/wechat-mini/config.js` 的 `apiBaseUrl` 设置为已登记的生产 HTTPS API 地址；正式版本会忽略开发机本地覆盖值。

FFmpeg 和真实 ASR 是硬发布条件。官方 API 容器包含 FFmpeg，但仍必须在最终镜像中执行 `ffmpeg -version` 验证；还必须启用 AI 构建、安装所选 `funasr` 或 `whisper` 依赖并使模型可用。默认 `CAMPUSVOICE_DOCKER_INSTALL_AI=false`、`CAMPUSVOICE_ASR_PROVIDER=disabled` 只能验证非语音功能，不得作为小程序语音发布环境。就绪探针不会下载或实际推理模型，发布前还必须完成一次真实 WSS 录音识别。

Compose API 入口会自动执行 Alembic migration。非 Compose 部署必须在启动新版本前、使用同一生产数据库配置执行：

```powershell
Set-Location services/api
python -m alembic upgrade head
```

部署后要求 `GET https://<api-host>/api/health/ready` 返回 200 且数据库、Alembic head 与启用组件检查均为 `ok`；还要单独验证 `wss://<api-host>/ws/asr` 的升级和真实识别。只有 `/health/live` 成功不足以发布。

## 微信公众平台与 HTTPS 配置

在微信公众平台为同一个 AppID 完成：

1. 确认小程序主体、管理员/开发者权限、名称、头像、简介、服务类目和所需主体资质可用于本产品。
2. request 合法域名登记生产 API 的 HTTPS origin；socket 合法域名登记同一反向代理的 WSS origin。域名配置不包含业务路径。
3. 生产反向代理把 `/api/**` 转发到 FastAPI，把 `/ws/asr` 正确进行 WebSocket upgrade；所有公网入口仅使用 HTTPS/WSS。
4. 服务器证书链必须公开受信任且覆盖精确主机名；不能使用 IP、`localhost`、自签名证书或明文 HTTP/WS。
5. 发布并保持准确的《小程序用户隐私保护指引》，声明微信登录/用户标识符、麦克风、停止后语音识别、任务、日程、通知及数据删除用途；平台说明必须与 `app.json` 的 `scope.record` 文案和产品实际行为一致。
6. 后端 AppSecret、确认密钥及其他凭据只通过部署平台 secret manager 注入，并建立轮换与撤销流程。
7. Ingress 必须对 /api/auth/wechat/login 执行按来源 IP 的分布式速率限制并设置合理突发量。应用内 CAMPUSVOICE_WECHAT_CODE_EXCHANGE_RATE_PER_SECOND / CAMPUSVOICE_WECHAT_CODE_EXCHANGE_BURST 是每进程持续 token bucket，CAMPUSVOICE_WECHAT_CODE_EXCHANGE_MAX_CONCURRENCY 限制单进程同时外呼；两者都不能替代多实例共享的入口限流。
8. 应用会在 JSON 解析前把微信登录请求体限制为 4 KiB；Ingress 仍应对同一路径配置不高于 4 KiB 的请求体上限，作为抵御未认证内存消耗的纵深防护。

## 开发者工具与版本流程

发布工程应直接导入：

```text
D:\yind\CampusVoice-mobile-final\apps\wechat-mini
```

本机也可通过 `D:\yind\project.config.json` 的 `miniprogramRoot` 快捷打开，但该仓库外文件只是便利配置，不得替代规范工程、进入发布包或作为其他机器的前置条件。若开发者工具仍显示根配置解析旧错误，应关闭项目后直接重新导入规范目录。

常用命令应指向规范目录：

```powershell
D:\微信web开发者工具\cli.bat open --project D:\yind\CampusVoice-mobile-final\apps\wechat-mini --lang zh
D:\微信web开发者工具\cli.bat preview --project D:\yind\CampusVoice-mobile-final\apps\wechat-mini --lang zh
D:\微信web开发者工具\cli.bat upload --project D:\yind\CampusVoice-mobile-final\apps\wechat-mini --version <semver> --desc <description> --lang zh
```

上传前必须固定唯一版本号和发布说明，运行小程序 Node 测试、后端 pytest、Ruff、mypy、全量前后端门禁及开发者工具代码质量检查，并用体验版走完审核路径。上传只生成开发版本；提交审核、处理驳回和正式发布仍需运营者在微信公众平台人工确认，不得用自动化越过这些步骤。

## 审核材料与真机验收

审核说明应明确：

- 核心功能是把用户主动输入的语音、任务和日程整理成个人校园助理数据。
- 麦克风只在用户点击开始并授权后使用；MP3 在停止后才解码和最终识别，录制中没有实时 partial。
- 离开页面、进入后台、权限中断或网络断开时录音立即停止，不自动恢复旧会话。
- 原始音频默认不持久化。
- 设置页可撤销登录，隐私页可双重确认删除业务数据。
- 审核版本连接真实生产 API，不使用 demo auth、`localhost`、开发地址覆盖或仅界面预览模式。
- 审核人员能够从首页按清晰路径进入登录、录音、任务、日历、通知、退出和数据删除功能。

至少在一台真实 iPhone 微信和一台真实 Android 微信上记录型号、系统版本、微信版本及结果，验证：`wx.login`、会话过期/退出、麦克风首次授权与拒绝后恢复、短录音/最长允许录音、主动停止、来电或系统中断、切后台、WSS 断线、旧回调隔离、任务/日历写入、数据删除和重新登录。开发者工具模拟器不能替代真机通过。

## 发布阻塞条件

以下任一项未满足时不得上传审核：

- `config.js` 的 `apiBaseUrl` 为空、是占位地址或未使用登记的生产 HTTPS origin。
- 生产 API/WSS、公开受信任证书、反向代理 upgrade 或微信 request/socket 合法域名未验证。
- 所选 `wechat` / `oidc_wechat` 认证配置不完整，或 AppSecret/确认密钥未通过 secret manager 安全注入。
- Ingress 未对微信登录实施分布式来源限流，或 code2session 单进程并发上限未经压测确认。
- Alembic 未到 head，或 `/api/health/ready` 不是 200/全项 `ok`。
- 最终镜像缺少可用 FFmpeg、真实 ASR provider/AI 依赖/模型，或未完成真实 WSS 识别。
- 微信主体、服务类目、所需资质或隐私保护指引未完成。
- 版本号、上传说明、审核测试路径和审核材料未固定。
- 未完成并记录 iPhone 与 Android 真机微信全链路验收。
