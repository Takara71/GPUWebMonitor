# FRP SSH ingress guard

Protects TCP 51022–51024 on the VPS with a dedicated `inet frp_ssh_guard` nftables table. It does not flush the host ruleset. Existing established connections and other service ports are preserved. IPv4 and IPv6 are covered. Rules are implemented in `guard.py`; systemd owns application at startup and daemon recovery.

## Policy

All three forwarded SSH ports share per-source meters and connection counts:

| Tier | New connections | Concurrent TCP connections | Timed bans for repeated incidents within 24h |
|---|---|---|---|
| Hefei | 18/minute, burst 6 | 54 | 10m, 1h, 24h |
| Other/unknown | 1/minute, burst 1 | 6 | 1h, 24h, 7d |

These are token-bucket rates, not fixed calendar-minute counters. A source with 6 violations within 60 seconds (Hefei), or 3 within 120 seconds (other), receives a ban. Each new connection is counted once using reserved conntrack mark bits `0x10000000` (seen) and `0x20000000` (rejected); SYN retransmissions do not consume another token. Avoid reusing these mark bits in other firewall software.

Three distinct UTC calendar days with incidents within 30 days result in 7-day bans for Hefei, or permanent forwarding bans for other sources. Bans affect only new connections. Known accounts, geography and successful existing sessions do not grant immunity to new-connection limits.

The aggregate normal budget is 36/minute with burst 18. Five observed aggregate-limit rejections within 60 seconds activate strict mode for 15 minutes: Hefei shares 18/minute (burst 6), other sources share 3/minute (burst 1). An aggregate rejection does not blacklist an arbitrary individual source. Logs are bounded to limit resource consumption; under extreme floods some penalty events may not be recorded, but kernel connection limits still apply.

VPS port 22 separately consumes its own `ssh.service` authentication logs. Five `Failed password` events within 10 minutes yield bans of 1h, 24h, then 7d. No inference of GPU password failures is made from TCP connection counts. GPU logs identify FRP connections as loopback; never blacklist loopback as an attacker address.

On each GPU, `00-frp-login-limits.conf` sets LoginGraceTime=30, MaxAuthTries=3, MaxStartups=18. Eighteen is a per-host **unauthenticated** connection cap, not an active-user cap. Successfully logged-in sessions are not disconnected by these limits.

## Geography

`update_geo.py` retrieves the official ip2region public city datasets over HTTPS, validates both complete files and extracts China / Anhui / Hefei ranges. Geo data includes source URLs, hashes and retrieval time. Upstream data is community-maintained and updated irregularly; geography does not prove identity. Unknown IPs use the stricter tier. A weekly timer refreshes data, and a daily timer removes Hefei exemptions after 45 days without successful refresh. Only geo sets are changed during refresh; bans and meters remain intact.

## Installation locations

- Code: `/opt/frp-ssh-guard/`
- Persistent state and audit log: `/var/lib/frp-ssh-guard/{state.json,events.jsonl,geo.json}`
- Units: `frp-ssh-guard.service`, `frp-ssh-guard-geo.timer`, `frp-ssh-guard-maintenance.timer`
- Log rotation: `/etc/logrotate.d/frp-ssh-guard` (30 rotated logs, daily/10MB)
- Kernel logger: `/etc/modules-load.d/frp-ssh-guard.conf`
- GPU SSH file: `/etc/ssh/sshd_config.d/00-frp-login-limits.conf`

Do not enable a second service that flushes nftables at boot. Existing cloud security groups remain relevant. This installation does not protect separate public ingress on another VPS or the school's own SSH gateway.

## Operations (VPS root)

```sh
systemctl status frp-ssh-guard
python3 /opt/frp-ssh-guard/guard.py status
journalctl -u frp-ssh-guard --since today
nft list table inet frp_ssh_guard
python3 /opt/frp-ssh-guard/guard.py unban --ip ADDRESS
```

An unban removes both forwarding/admin bans and incident history for that address, then signals the daemon to reload the persisted state. It does not bypass the rate limit. Avoid restarting the daemon just to unblock one address, as a restart rebuilds meters. Ban expiry and history persist across daemon/system restarts.

Emergency rollback on the VPS:

```sh
/opt/frp-ssh-guard/rollback.sh
```

This removes only this guard's table and disables its units/timers. GPU SSH rollback is separate: `/var/backups/frp-ssh-guard-20260923/rollback.sh`. Backups were made before deployment. No passwords are stored in this implementation.

## Verification

- `python3 test_guard.py`: expiry, escalation, regional tolerance, source-log trust, IPv6/admin bans and strict-mode activation.
- `unshare -n python3 test_network.py`: real kernel rules in an isolated network; rate buckets, cross-port sharing, concurrency caps 6/54, IPv6 tiers, aggregate budget, timeout and established-session preservation.
- `test_live_ingress.py`: controlled temporary test network on the VPS; real kernel events into the production daemon and five intentionally invalid test-account logins into the separate port-22 ban. Cleans its own test IP/interface afterward. Only run explicitly during maintenance.
- Deployment also verified a fresh external SSH login to each GPU, website response and ban restoration after guard restart.

Rate limiting reduces guessing speed and connection exhaustion. It does not prevent one correct guessed password, detect every distributed low-rate attack, or establish that a host is uncompromised.

## 2026-09-24：密码失败实际封禁

`guard.py` 的同一主循环每 15 秒通过 `auth_policy.py` 只读查询高级采集数据库；防火墙仍由 guard 单一进程更新，避免多个写入者覆盖封禁状态。原连接限速与递进策略保留。

| 触发条件（任意一项） | 普通公网来源 | 合肥来源 |
|---|---:|---:|
| 10 分钟密码失败 | 5 | 12 |
| 1 小时密码失败 | 10 | 30 |
| 24 小时密码失败 | 20 | 60 |
| 1 小时内密码失败涉及的不同无效用户名 | 3 | 8 |

仅统计 `exact_cookie_pair` 的密码失败、公网地址和至少 30 秒前的事件；无效用户名提示不重复计数。三台服务器按来源合并累计。公钥失败、未知认证方式、未关联事件、内网/回环地址不触发本模块。相同指纹的多 IP 群组仍仅观察，不连带封禁。

普通来源初次封禁 1 小时，同日再次触发递进至 1 天、7 天；跨三天反复触发沿用原永久封禁规则。合肥初次 10 分钟，后续 1 小时、1 天，跨三天反复触发 7 天。封禁只影响三台公网 SSH 新连接；已建立连接保留。成功登录不清除其他失败证据。共享出口达到阈值会共同受限，合肥宽阈值降低该风险。

一次封禁到期后，只使用到期之后的新失败证据，不反复使用旧记录续封。采集/数据库错误记录 `authentication_scan_error`，原连接限速继续运行。漏采或未关联的攻击仍不能据此自动封禁。

部署备份 `/var/backups/frp-auth-enforcement-20260924/`。回退：恢复该目录的 `guard.py` 至 `/opt/frp-ssh-guard/guard.py` 并重启 `frp-ssh-guard`；已有封禁保持至到期，如需解除使用原 `unban --ip`。恢复 `dashboard.py`、`status.js` 后重启接收服务可恢复界面说明。

实测：18 项防护单元测试、26 项特征/界面数据测试通过。上线评估只命中 `198.51.100.20`，以 39 次当日精确关联密码失败、近小时 18 次失败及 12 个无效用户名自动封禁；正常测试来源只有一次失败，未命中。核对 nft 实际集合、三台 SSH 转发 banner、首页 200 和匿名安全接口 401 均正常。
