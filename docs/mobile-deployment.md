# 手机端部署与 PWA 安全边界

手机试点使用同源 HTTPS：`/` 由 Next.js 提供，`/api/**` 反向代理到 FastAPI，`/ws/**` 反向代理到 ASR WebSocket。手机不能访问 `localhost`；请使用受信任证书的校园域名，并确保 HTTPS 页面只建立 WSS 连接。配置错误会显式失败，不降级为 WS。

校园网环境必须启用完整 OIDC 或 JWT。默认 demo auth 与仅绑定 `127.0.0.1` 的 Compose 配置只供本机开发，不得直接暴露。反向代理必须保留严格的 Cookie 属性、Origin、CORS、CSRF、WebSocket Origin 与登录回跳白名单。不得把密钥、Token 或敏感错误放入 `NEXT_PUBLIC_*`、URL、Manifest 或日志。

本次 PWA 只提供 Manifest、standalone 元数据、安全区适配和生成式 PNG 图标。没有注册 Service Worker，因此不会缓存 `/api/**`、`/ws/**`、OIDC、通知正文或用户数据，也没有 Background Sync、离线写队列或自动重放。后续若增加 Service Worker，必须证明上述路径始终 NetworkOnly，并单独审计版本化静态资源白名单。

安装后登录回跳应保持在同源 scope `/` 内。需在真实 iPhone Safari 与 Android Chrome 上人工验证：刘海/Home Indicator、底部导航、软键盘、锁屏/后台录音中断、重新进入后不自动恢复、OIDC 回跳与 WSS Origin。
