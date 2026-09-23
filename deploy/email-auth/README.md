# 学校邮箱验证码登录（2026-09-24）

网站只显示“账号 / 验证码”，不公开邮箱后缀及格式说明。后台严格接受 `y[0-9]{1,63}`（ASCII、小写 y、保留前导零，邮箱本地部分最长64字符），拼接 `@students.example.edu`。不使用事先授权名单：任何能完成该邮箱验证的人都可登录网站；远程桌面仍独立验证 Linux 凭据。

## 状态和防护

- 阿里云 SingleSendMail，发信地址 `login@mail.example.com`，杭州 API。RAM 配置 `/etc/lab-status/aliyun-mail.json`，root:www-data 0640，不在前端或日志中出现。
- `/auth/request-code` 固定返回 `{ok:true,retry_after:60}`，合法、非法、错误学号、冷却中返回一致；发送为后台队列，不等待服务商响应，避免由响应时间暴露地址状态。
- 所有格式的账号输入都消耗同 IP 60 秒冷却；SQLite 事务保证并发原子性，重启后保留。来源由 Nginx 覆盖 `X-Real-IP`，服务只监听回环。
- 另外同账号60秒一次、每小时5次、每天20次；全站每小时120次、每天500次，防止多 IP 刷邮件。学校共享出口也受同 IP 60 秒限制。
- 随机8位字母数字，避开 0/O/1/I。首次生成5分钟有效，期间重发同一码且不延长到期；到期或验证成功后才生成新码。有效期内需要重发的原码使用 Fernet 加密保存，验证摘要使用 HMAC-SHA256。密钥与网站会话密钥分用途派生。
- 挑战绑定 HttpOnly/Secure/SameSite=Strict 浏览器 Cookie；验证码成功后使该账号所有浏览器中的同一码立即失效。连续5次错误触发 IP/账号锁定15分钟，挑战本身最多5次验证。
- 状态库 `/var/lib/lab-email-auth/auth.sqlite`：students 自动名单（pending/accepted/verified/invalid），accepted只代表服务商接收请求，verified才代表完成邮箱所有权验证。
- 每5分钟调用 QueryInvalidAddress 同步阿里云明确无效地址，只更新已请求的账号；网络超时、额度、权限、发信地址配置错误不标成错误学号。已验证身份不被历史无效名单直接覆盖。无效地址信息可能延迟，无法在首次请求时保证立即判定邮箱存在。
- 读名单需 `dm:QueryInvalidAddress`，发信需 `dm:SingleSendMail`。仅测试配置时使用 QueryMailAddressByParam。没有开启额外收费的地址预校验。

## 部署

`lab-session-auth.service` 运行 `/opt/lab-status/email_auth.py`，使用 `mail_otp.py` 和 `session_auth.py` 的 Cookie/签名公共函数。旧密码服务不再运行，/login 不处理密码登录。已删除 `/etc/nginx/.htpasswd-lab-status` 与 `.htpasswd-lab-monitor`，轮换网站 session-secret，使所有旧 Cookie 返回401。重启桌面 broker 断开旧浏览器连接，未结束 Linux 桌面或训练进程。

Nginx 新增精确 `/auth/request-code` 转发，其余 auth_request 沿用。前端版本 `20260924-email-login-3`。配置及前端备份 `/var/backups/lab-email-login-20260924/`，不备份旧密码或旧会话密钥；回退不应重新开放已撤销的密码登录。

测试：13 项 OTP 存储测试（并发、重启、冷却、格式、到期、同码重发、一次性、跨浏览器、临时故障/无效地址），4 项新 HTTP 测试（统一响应、CSRF、旧密码/会话拒绝、真实 Cookie 流程）。另运行4项已有公共会话工具回归测试。桌面及390px手机布局无溢出、无脚本错误；真实学校邮箱已收到测试邮件。

最终真实验证：用户提供最新邮件验证码后，浏览器成功进入 `/monitor/`，受保护安全接口200；同码重放401，测试浏览器已退出。五次开发验证邮件的本地发送记录转入 `test_sends_archive`，恢复测试账号发送额度，保留审计信息。前端已移除学校、学号、后缀和示例提示，只有账号、验证码及统一倒计时。

## 公开仓库配置

复制 `aliyun-mail.example.json` 到服务器 `/etc/lab-status/aliyun-mail.json` 并填写真实发件人、收件域名和 RAM 凭据。收件域名从配置读取，不写入公共源代码。升级已有部署时需先补充 `recipient_domain`。依赖 Python 3.10–3.12、cryptography（Ubuntu 可安装 python3-cryptography）；示例备案信息应在私有部署中替换。本文中的公网 IP 为文档示例，不代表真实攻击来源。
