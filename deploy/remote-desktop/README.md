# 账号独立的浏览器远程桌面

2026-09-24 已替换共享物理桌面方案，三台统一使用 XFCE + Xvfb 独立会话。原 5090 无显示器配置方案已放弃，相关自动任务已删除，无需重启显示管理器。

## 使用

状态首页登录后点击服务器卡片右上角显示器。输入 Linux 账号密码，PAM 验证通过后直接进入自己的桌面，不再经过 GDM 登录界面。

每台服务器同时只允许一个网页远程连接，占用时显示该 Linux 账号。不同账号对应不同 UID 和 X 显示，相同账号重连恢复原桌面；已有物理桌面不被接管。

- **断开并保留**：立即释放远程占用，保留该账号的桌面和窗口。异常断网保留约 30 秒重连窗口，心跳失联最长约 120 秒回收占用。后台桌面会继续占用内存，不自动关闭未保存应用。
- **结束桌面**：经网页确认后结束当前独立桌面全部应用并释放资源；不会停止从 SSH 启动的进程或原物理桌面。
- **OpenGL**：使用桌面“OpenGL GPU 加速启动”，或终端 `lab-opengl 程序 参数`。该命令通过 VirtualGL EGL 后端使用 NVIDIA GPU；多卡可用 `LAB_OPENGL_DEVICE=egl1` 选择另一张卡。
- **Vulkan / CUDA**：直接启动，不套用 `lab-opengl`。

## 架构

浏览器 noVNC 1.7.0 → 现有 HTTPS/WSS 443 → VPS broker（127.0.0.1:28461）→ GPU 主动 WSS 隧道。没有新增公网端口。

GPU root agent 验证 PAM 账号后启动 `lab-desktop-session@UID.service`。该服务以 UID 对应的普通用户运行，创建 `:10000+UID` 虚拟显示和 XFCE。Xauthority 置于用户专属 `/run/lab-desktop-UID/`（0700），禁用 X11 TCP 监听。x11vnc 只在有人连接时以该用户 UID 启动，通过私有 UNIX socketpair 传输，永不监听 VNC TCP 端口。

固定 1600×900，noVNC 在浏览器缩放；画质流畅/均衡/清晰可选。XFCE 关闭合成器，桌面组件使用软件渲染，OpenGL 应用按需启用 GPU。远程桌面配置独立保存于 `~/.config/lab-remote-desktop`，不覆盖已有 GNOME 配置。

网站登录、Origin、节点、浏览器绑定票据都由 broker 校验。每来源 10 分钟 5 次失败、每账号 10 分钟 10 次失败限流，原子锁防止同时占用。密码只通过加密连接交给 PAM 子进程验证，不存储、不写日志。新会话 PAM account/session 检查由 systemd 执行。独立桌面保留账号原有系统权限；服务按 control-group 结束，只清理该桌面的进程。

## 组件与文件

GPU 依赖：python3-aiohttp、python3-pam、xvfb、x11vnc、XFCE 最小组件、dbus-x11、mesa-utils、vulkan-tools、官方 VirtualGL 3.1.5。没有安装第二个显示管理器。

- `/opt/lab-desktop/agent.py`、`session.py`、使用说明和启动器
- `/etc/lab-desktop/agent.json`（root 600，节点令牌）
- `lab-desktop-agent.service`（代理）、`lab-desktop-session@.service`（按需用户桌面）
- `/etc/pam.d/lab-web-desktop`（密码认证）、`lab-web-desktop-session`（会话账号检查）
- `/usr/local/bin/lab-opengl`、`/opt/VirtualGL/`

VPS：`/opt/lab-desktop/broker.py`，`/etc/lab-desktop/broker.json`（root:lab-desktop 640），`lab-desktop-broker.service`，现有 nginx desktop snippet。前端在 `status/desktop/`。

## 验证

17 项测试覆盖认证失败不启动会话、并发独占、票据绑定、断开保留、仅结束已认证账号等。真实浏览器覆盖三台单次登录直达 XFCE、画面、占用、断开重连与结束。临时普通账号验证了跨账号不同 UID/显示、无法读取其他账号 Xauthority、释放后另一用户可用、原用户恢复同一会话；测试账号已删除。

三台实际部署会话内 `lab-opengl glxinfo -B` 均返回 NVIDIA / OpenGL 4.6；原生 vkcube 窗口测试均使用 NVIDIA 并正常结束。实机结果及原始日志保留在私有部署记录中；公开仓库仅保留可复现的验证程序。没有承诺所有应用兼容或网络端固定帧率。

空桌面实测内存约 160–220 MiB（启动时可能更高），每台空闲代理约 21 MiB；应用自身额外计入。结束桌面后桌面进程释放。

## 运维和回滚

`systemctl status lab-desktop-agent`；`systemctl status lab-desktop-session@UID`；`journalctl -u` 对应单元。不要记录含 ticket 的 WebSocket URL 或密码。

本次变更前备份：`/var/backups/lab-desktop-independent-20260924/`。若需停用，先让用户保存并结束独立桌面，再停止/禁用 lab-desktop-agent 和 broker，移除网页入口及 nginx desktop include。不要批量终止某 UID 或其 SSH 进程。原共享物理桌面版本仅作为历史备份，不自动回退。
