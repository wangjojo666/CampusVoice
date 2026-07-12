# API 与 WebSocket 协议

## 权限边界

MVP 是单用户本地应用。服务端只使用种子用户 `user_demo`，不接受调用方任意指定用户 ID，也不接入真实教务系统。读取为低风险；任何写入均须通过同一完整性、风险、事务和执行后验证服务。删除、覆盖和批量写入必须经过两次确认。

## 统一错误

```json
{
  "detail": {
    "code": "ACTION_NOT_READY",
    "message": "操作尚未完成所需确认",
    "retryable": false,
    "context": {}
  }
}
```

常用状态码：

- `400`：请求语义无效或结构化模型修复失败。
- `404`：记录或文档不存在。
- `409`：重复、时间冲突、状态转换冲突或幂等键冲突。
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

确认请求包含唯一 `confirmation_token`，重复令牌不能算作第二次确认。执行接口具备幂等性；已执行操作再次执行返回同一验证结果，不重复写数据库。

## `/ws/asr`

客户端先发送 JSON 控制消息，再发送二进制 PCM 帧：

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
