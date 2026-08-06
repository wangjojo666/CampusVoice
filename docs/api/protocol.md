# API 与 WebSocket 协议

## 权限边界

development/test 可显式启用种子身份 `user_demo`；production 禁止 demo。浏览器单独部署选择 `oidc`，微信小程序单独部署可选择 `wechat`，同一 API 同时服务两者选择 `oidc_wechat`；通用非浏览器集成仍可选择独立 `jwt`。

校园浏览器经 `GET /api/auth/login` 启动 OIDC Authorization Code + PKCE，API 在 `GET /api/auth/callback` 原子校验 state、nonce、issuer、audience、JWKS 签名、有效期与必需 claims，并换成哈希存储的 `HttpOnly` 会话。`POST /api/auth/logout` 只接受配置 allowlist 中的浏览器 `Origin`，撤销对应 OIDC issuer 的会话并返回安全的 IdP 登出 URL。

微信小程序把 `wx.login` code 发送到 `POST /api/auth/wechat/login`；API 只向微信官方 HTTPS `code2session` 端点交换身份，返回短期、随机、带 `cvwx1.` 前缀的 Bearer 会话，数据库仅保存哈希。`POST /api/auth/wechat/logout` 只撤销同一微信 issuer 的会话。`code2session` 的 AppSecret、OpenID、UnionID 与 `session_key` 不返回客户端或进入日志。

在 `oidc_wechat` 中，无 `Authorization` 的请求只尝试 OIDC cookie；一旦带 `Authorization`，就必须是格式正确的 `Bearer cvwx1.…`，并只按当前 AppID 的微信 issuer 查询。错误 Bearer 不会回退到 OIDC cookie，两类会话不能互换。任何路径、正文或临时请求头都不能选择用户，跨用户对象统一返回 `404`。

普通任务、日历、设置和热词写入先申请绑定用户、方法、路径、规范化正文哈希、阶段与有效期的一次性 write challenge。可靠 Action 另使用绑定用户、action、payload 指纹、阶段与有效期的签名 challenge。普通迁移 bundle 使用一阶段；初始高风险 mutation（删除、冲突覆盖、高风险批量写入和整组撤销）需要两次分离用户交互，通用前端 API helper 不得自动循环完成两阶段。第一次交互只签发并推进 challenge，不执行业务写入；第二次交互才携带绑定同一正文的最终 challenge。

## 统一错误

```json
{
  "error": {
    "code": "invalid_action_state",
    "message": "Action must receive all required confirmations before execution",
    "details": { "state": "awaiting_confirmation" }
  },
  "request_id": "c7b12a..."
}
```

常用状态码：

- `401`：Bearer 凭据缺失或无效，响应包含 `WWW-Authenticate: Bearer`。
- `403`：账户停用或 Origin 不允许。
- `404`：当前用户范围内记录或文档不存在。
- `409`：重复、时间冲突、状态转换、挑战消费或幂等键冲突。
- `422`：请求不符合 Pydantic Schema。
- `428`：操作需要确认或补充信息。
- `500`：事务或执行后验证失败；响应不得声称成功。
- `503`：模型适配器未配置、未就绪或暂时不可用。

## 列表与已验证写入

任务、事件和热词列表使用：

```json
{ "items": [], "total": 0 }
```

数据库写入使用：

```json
{
  "success": true,
  "action": "create_event",
  "record_id": "event_123",
  "verified_fields": { "title": true, "start_at": true },
  "side_effects": [],
  "message": "日历事件已创建并验证成功",
  "record": {}
}
```

`success=true` 只允许在事务提交后重新查询并验证目标字段、重复记录、冲突和副作用均符合预期时返回。

## 通知版本雷达与整组迁移

`NoticeSeries` 是当前用户范围内显式创建的版本链。`POST /api/notice-radar/series/{series_id}/versions` 从第二版开始必须提交当前 predecessor；服务端不会只凭标题或自由文本版本号静默串联通知。每个版本返回结构化 claims、chunk ID 和 Unicode code-point 证据区间；`GET /api/notice-radar/changes/{change_set_id}` 返回确定性 semantic diff、before/after claim、证据、置信度和审核状态。

`POST /api/notice-radar/changes/{change_set_id}/impacts/detect` 只传播到仍精确依赖 before claim，或当前业务字段的规范化值仍等于 before claim 的 Task/Event。仅共享旧 document ID 不构成依赖。v1 适用而 v2 不再适用时仍返回 impact，并用 `recommended_action=keep|cancel|manual_review` 与 `requires_manual_review` 表达安全建议；需要人工处理的空 patch 不可自动执行。

`POST /api/notice-radar/changes/{change_set_id}/migration-preview` 创建递增 `generation` 的 plan，或在输入和业务状态完全相同时复用同一 ready plan。审核拒绝会使 ready plan 进入 `invalidated`、使 impacts 进入 `dismissed`；重新批准会清除旧 plan 关联并要求新 generation。计划冻结实体版本、before/after、来源和稳定排序的日历冲突，但不写 Task/Event。

`POST /api/notice-radar/migrations/{plan_id}/execute` 在同一数据库事务和 plan claim 内重新验证 change 审核、impact 可执行性、用户适用性、实体版本、旧 claim 依赖和当前日历冲突。preview 后冲突集合变化返回稳定错误 `calendar_conflicts_changed`，整组零写入，plan 回到或保持 `ready`，失败请求不永久占用执行幂等键。两个独立会话并发 claim 同一 plan 只能有一个成功；相同幂等键重试只恢复已提交写入的数据库核验。

执行和整组撤销分别使用 `execute_receipt` / `undo_receipt`，迁移项分别使用 `execute_verification` / `undo_verification`，任何操作都不得覆盖另一操作的证据。回执包含 operation、时间、expected snapshot 和全新数据库会话读取的 database snapshot。恢复前客户端必须先 `GET` 最新 plan：只有 `applied|verification_failed` 或 `undo_applied|undo_verification_failed` 才可用原 plan version、相同幂等键重试对应 POST；此时专用 recovery helper 可以补齐 challenge，因为服务端只继续已提交操作的核验，不重复业务写入。`verified|undone` 只读取已有回执，`ready` 绝不能进入 recovery，必须重新完成所需用户确认。`GET /api/notice-radar/migrations/{plan_id}/receipt?operation=execute|undo` 只读取已生成回执；只有全部数据库快照匹配时才返回 verified/undone。

## 可靠操作状态机

`POST /api/actions/prepare` 创建不可变操作快照。状态包括：

```text
needs_input
awaiting_confirmation
awaiting_second_confirmation
ready
executing
executed
cancelled
failed
undone
expired
```

客户端先调用 `POST /api/actions/{id}/challenge` 取得服务端签发的短时 challenge，再向 `POST /api/actions/{id}/confirm` 发送 `{"confirmed": true, "challenge": "..."}`。数据库只保存 nonce 哈希并原子限制每个 action/stage 一次消费；重放、并发重复、过期、跨用户或 payload 改变均失败。执行接口具备幂等性；已执行操作再次执行返回同一验证结果，不重复写数据库。

## `/ws/asr`

### 一次性 ticket

客户端先通过已认证 REST 调用 `POST /api/auth/ws-ticket`。原始 ticket 只返回一次，数据库仅保存哈希；连接时以 `campusvoice.ticket.<ticket>` WebSocket 子协议提交，不放入 URL。

ticket 的连接上下文由已经认证的主体决定：

- OIDC 浏览器会话必须在签发请求中携带精确命中 CORS allowlist 的 `Origin`，ticket 绑定该 Origin；WebSocket upgrade 时再次校验。
- 微信 `cvwx1.` 会话的 ticket 绑定服务端内部 `wechat-miniprogram://<AppID>` origin sentinel；它不同于 `wechat:miniprogram:<AppID>` 会话 issuer。小程序 WebSocket 不依赖浏览器 Origin，实际报文中的缺失或非浏览器 Origin 不能改变 ticket 的 AppID 绑定。
- `oidc_wechat` 同时接受以上两类绑定，但不会把浏览器 ticket 当作微信 ticket，也不会让微信 ticket 借用浏览器 cookie。ticket 还绑定当前用户、短期有效并原子单次消费；重放或跨上下文使用返回 `invalid_or_replayed_ticket`。

### 启动与音频格式

认证完成后客户端先发送 JSON `start` 控制消息。浏览器 PCM 示例：

```json
{
  "type": "start",
  "audio_format": "pcm_s16le",
  "sample_rate_hz": 16000,
  "channels": 1,
  "sample_width_bytes": 2,
  "language": "zh",
  "hotwords": ["机器学习"]
}
```

微信 RecorderManager 示例：

```json
{
  "type": "start",
  "audio_format": "mp3",
  "sample_rate_hz": 16000,
  "channels": 1,
  "sample_width_bytes": 2,
  "language": "zh",
  "hotwords": []
}
```

`pcm_s16le` 二进制帧必须是 16 kHz、单声道、每样本 2 字节，并在接收时直接进入 VAD/provider，因此浏览器可以在录制中收到 interim/final。暂停和继续由浏览器本地停止/恢复发送帧；会话 ID 由服务端在 `ready` 事件中生成。

`mp3` 二进制帧按到达顺序有界聚合。录制期间服务端不把不完整 MP3 交给 provider，`flush` 返回 `flush_unsupported_for_mp3`，也不会产生实时 partial。收到 `stop` 后，服务端先发送 `finalizing`，随后启动固定参数、非 shell 的 FFmpeg 子进程，将完整 MP3 有界解码为 16 kHz、单声道、s16le PCM，再送入 provider 并完成最终识别；尚未完成时每 5 秒发送一次不落库的 `finalizing` 进度心跳。客户端保持 15 秒“无进展”fail-closed 边界，进度只刷新 idle timer；从发送 `stop` 起另有 180 秒不可重置的 hard deadline。FFmpeg 不可用、输入无效、解码超时、输出无效或超过时长分别返回稳定错误；FFmpeg 与真实 ASR provider/模型是小程序语音生产发布的硬条件。

控制消息为 `start`、`flush`、`stop`、`ping`。小程序把所有音频拆成最多 1024 字节的有序二进制帧。`onStop` 一旦到达，后续迟到 frame callback 不再参与发送；客户端只以 RecorderManager 的 `onStop.tempFilePath` 作为唯一完成数据源，逐字节验证此前帧是完整文件的前缀，只补发尚未送达的有界尾部，再且仅发送一次 `stop`。前缀不一致、文件读取失败或读取超时均 fail-closed。成功终态是在最终结果处理和资源清理完成后由服务端以 WebSocket 1000 正常关闭。服务端事件包括：

- `ready`：会话已建立且适配器可用。
- `finalizing`：MP3 `stop` 已接收且服务端仍在有界解码/识别；立即发送并每 5 秒心跳一次，不写入转写持久化。
- `speech_start` / `speech_end`：VAD 边界；MP3 路径只可能在停止后的解码/识别阶段出现。
- `interim`：可被后续结果覆盖；MP3 录制进行中不会发送。
- `final`：稳定句子，包含 `text`、`confidence`、`latency_ms`。
- `pong`：响应 `ping`。
- `error`：包含稳定错误码、用户可读消息和 `recoverable`。

浏览器 `SpeechRecognition` 不属于此协议，也不能作为生产识别引擎。

服务端强制限制 ticket TTL/单次消费、单帧与控制消息字节数、空闲超时、最大会话时长、累计音频时长、MP3 累计压缩字节、FFmpeg 解码超时与 PCM 输出大小，以及单用户并发连接数。超限返回稳定协议错误并关闭连接；最外层清理保证 FFmpeg/ASR 子进程、adapter、持久化会话和并发配额最多释放一次。小程序页面隐藏、卸载、录音中断或连接失败时必须废弃当前 generation，不自动恢复，旧 recorder、socket、timer、listener 和 callback 不能影响新会话。
