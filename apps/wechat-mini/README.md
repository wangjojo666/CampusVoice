# CampusVoice 微信小程序

此目录是可由微信开发者工具直接编译的小程序工程。完整生产部署、隐私和上传要求见 ../../docs/wechat-miniprogram.md。

发布前必须在 config.js 中设置已登记为微信合法域名的生产 HTTPS API。开发版可以在“设置”页临时填写本地或 HTTPS 地址；正式版会忽略本机覆盖值。

本目录不允许保存 AppSecret、OpenID、session_key、会话 token、真实学生数据或审核账号密码。
