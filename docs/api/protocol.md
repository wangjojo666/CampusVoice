# API 与 WebSocket 协议

## 权限边界

development/test 可显式启用种子身份 `user_demo`；production 禁止 demo。校园浏览器经 `GET /api/auth/login` 启动 OIDC Authorization Code + PKCE，API 在 `GET /api/auth/callback` 原子校验 state、nonce、issuer、audience、JWKS 签名、有效期与必需 claims，并换成哈希存储的 `HttpOnly` 会话。`POST /api/auth/logout` 只接受配置 allowlist 中的浏览器 `Origin`，撤销本地会话并返回安全的 IdP 登出 URL。Bearer JWT 适配器继续服务非浏览器客户端。任何路径、正文或临时请求头都不能选择用户，跨用户对象统一返回 `404`。

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

客户端先通过认证 REST 调用 `POST /api/auth/ws-ticket`，并携带允许的 `Origin`。原始 ticket 只返回一次，数据库仅保存哈希；连接时以 `campusvoice.ticket.<ticket>` WebSocket 子协议提交，不放入 URL。服务端在接受连接前原子消费并再次校验 Origin。

认证完成后客户端先发送 JSON 控制消息，再发送二进制 PCM 帧：

```json
{
  "type": "start",
  "sample_rate_hz": 16000,
  "channels": 1,
  "sample_width_bytes": 2,
  "language": "zh",
  "hotwords": ["机器学习"]
}
```

控制消息：`start`、`flush`、`stop`、`ping`。暂停和继续录音由浏览器在本地停止/恢复发送音频帧；会话 ID 由服务端在 `ready` 事件中生成。服务端事件：

- `ready`：会话已建立且适配器可用。
- `speech_start` / `speech_end`：VAD 边界。
- `interim`：临时转写，可被后续结果覆盖。
- `final`：稳定句子，包含 `text`、`confidence`、`latency_ms`。
- `error`：包含稳定错误码、用户可读消息和 `recoverable`。

浏览器 `SpeechRecognition` 不属于此协议，也不能作为生产识别引擎。

服务端还强制限制单帧/控制消息字节数、空闲超时、最大会话时长、累计音频时长和单用户并发连接数。超限返回稳定协议错误并关闭连接；最外层清理负责释放 ASR adapter、持久化会话和并发配额。
