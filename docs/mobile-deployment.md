# 手机端部署与 PWA 安全边界

手机试点使用同源 HTTPS：`/` 由 Next.js 提供，`/api/**` 反向代理到 FastAPI，`/ws/**` 反向代理到 ASR WebSocket。手机不能访问 `localhost`；请使用受信任证书的校园域名，并确保 HTTPS 页面只建立 WSS 连接。配置错误会显式失败，不降级为 WS。

校园网环境必须启用完整 OIDC 或 JWT。默认 demo auth 与仅绑定 `127.0.0.1` 的 Compose 配置只供本机开发，不得直接暴露。反向代理必须保留严格的 Cookie 属性、Origin、CORS、CSRF、WebSocket Origin 与登录回跳白名单。不得把密钥、Token 或敏感错误放入 `NEXT_PUBLIC_*`、URL、Manifest 或日志。

本次 PWA 只提供 Manifest、standalone 元数据、安全区适配和生成式 PNG 图标。没有注册 Service Worker，因此不会缓存 `/api/**`、`/ws/**`、OIDC、通知正文或用户数据，也没有 Background Sync、离线写队列或自动重放。后续若增加 Service Worker，必须证明上述路径始终 NetworkOnly，并单独审计版本化静态资源白名单。

安装后登录回跳应保持在同源 scope `/` 内。需在真实 iPhone Safari 与 Android Chrome 上人工验证：刘海/Home Indicator、底部导航、软键盘、锁屏/后台录音中断、重新进入后不自动恢复、OIDC 回跳与 WSS Origin。

## 上线前 HTTPS / OIDC / WSS 检查

以下命令中的主机名均为部署时替换的占位符，不应提交真实凭据：

- `curl -fsSIL https://<campus-host>/` 必须返回成功，并且所有 HTTP 入口重定向到 HTTPS；证书链、主机名和有效期必须由浏览器验证通过。
- `curl -fsS https://<campus-host>/manifest.webmanifest` 必须返回 `application/manifest+json` 或兼容的 manifest JSON 类型；三个 `/pwa/*` 图标必须返回 `image/png`。
- 浏览器 Network 面板中，页面、`/api/**`、OIDC authorize/callback 全部使用 HTTPS，ASR 只使用 `wss://<campus-host>/ws/asr`。在 HTTPS 页面配置 `ws://` 必须启动失败，不能降级。
- OIDC 管理台只登记精确的 `https://<campus-host>/api/auth/callback`；登录完成后的应用回跳只允许同一 origin 且路径位于 `/` scope。拒绝协议、主机、端口不同，或使用 `//evil.example`、编码斜杠和路径穿越的回跳值。
- 登录 Cookie 必须同时具备 `Secure`、`HttpOnly` 和合适的 `SameSite=Lax`/`Strict`；生产响应不得包含长期 bearer token、WebSocket ticket 或 Cookie 值。
- CORS 只允许精确的校园应用 origin，不使用 `*` 与凭据组合；对带 Cookie 的写请求验证 `Origin`/`Referer` 并执行 CSRF token 检查。
- WebSocket upgrade 必须校验精确 `Origin: https://<campus-host>`；拒绝缺失、`null`、HTTP、不同端口和相似后缀 origin。
- WebSocket 短期 ticket 只通过 `Sec-WebSocket-Protocol: campusvoice.ticket.<one-time-ticket>` 传递，不放入 URL、日志、错误上报或持久化存储。

## 真机发布验收表

每个目标平台都必须填写设备型号、系统版本、浏览器版本、执行人、时间和证据链接；“模拟器/Playwright 通过”不能替代真机结论。

| 项目                           | iPhone Safari | Android Chrome | 通过标准                               |
| ------------------------------ | ------------- | -------------- | -------------------------------------- |
| 安装到主屏幕并 standalone 启动 | 未执行        | 未执行         | 无浏览器栏；启动页与导航完整           |
| 刘海与 Home Indicator          | 未执行        | 未执行         | 顶栏、底部导航和主内容不被遮挡         |
| 软键盘与长/嵌套 Modal          | 未执行        | 未执行         | 操作按钮可见；关闭后焦点回到触发器     |
| 锁屏、切后台、BFCache 返回     | 未执行        | 未执行         | 当前录音 fail-closed；返回后不自动恢复 |
| 麦克风允许、拒绝、撤销         | 未执行        | 未执行         | 明确终态；旧 chunk/level 不再发送      |
| Wi-Fi/蜂窝网络切换             | 未执行        | 未执行         | 连接中断且不自动重放写操作             |
| OIDC 登录与回跳                | 未执行        | 未执行         | 仅回到允许的同源 `/` scope             |
| WSS Origin 与短期 ticket       | 未执行        | 未执行         | Origin 被服务端校验；URL 无凭据        |

## 当前验收环境状态（2026-08-03）

当前工作机没有 `adb`，没有监听中的 Chrome 远程调试端口，也没有 iOS 调试工具或真实设备服务，因此本轮未执行真机验收。Docker 与 Lighthouse 也未安装；本轮不得将本地容器、Compose、Lighthouse PWA 或可访问性检查报告为通过。这些项目保持为 Draft PR 转 Ready 前的发布阻塞项，直到在真实校园 HTTPS/OIDC/WSS 环境和至少一台 iPhone、Android 设备上逐项签字。
