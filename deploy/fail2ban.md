# Fail2Ban 配置（生产 243）

> 2026-09-16 上线。本文档是 Fail2Ban 的完整配置档案，服务器上的生效文件以本文为准（仓库同步维护，重建服务器时直接复制）。
>
> 服务器现状总览见 [服务器部署现状.md](服务器部署现状.md)；迁移历史见 [生产环境部署.md](生产环境部署.md)。

## 1. 背景：2026-09-16 安全扫描

对 nginx 访问日志（9/11~9/16，13521 条）做了全量扫描：

- **无入侵成功迹象**。恶意流量全部被正确拦截：`.env` 嗅探（301）、PHP CVE 利用（400/405）、分布式漏扫（200 但返回的是 642B SPA 兜底页，无数据泄露）、矿池/RDP/Gh0st 协议探测（400）
- **SSH 零爆破**（端口 10167 + 仅密钥认证生效）
- 主要恶意来源：
  - `80.94.95.211` — .env 批量嗅探，992 次
  - GCloud 三 IP（`34.83.179.105` / `34.182.110.140` / `136.107.229.103`）— 同构漏扫各 1564 次，伪造 PerplexityBot / ClaudeBot / OAI-SearchBot 等 UA
  - `libredtail-http` UA 多 IP — PHP-CGI 参数注入（CVE-2024-4577）、phpunit eval-stdin 探测
- 正常业务流量（勿封）：`180.169.235.195/196/197`（办公出口，偶发 401 是 token 过期后前端自动重登）、`112.232.229.10`（RPA 本地工具心跳，UA=node）、`117.185.158.93`、`183.195.14.245`、`115.231.78.4`

## 2. 安装与文件位置

```bash
# Ubuntu 26.04，fail2ban 1.1.0
sudo apt-get install -y fail2ban
```

| 服务器文件 | 说明 |
|-----------|------|
| `/etc/fail2ban/jail.local` | 三个 jail 配置（见 §3） |
| `/etc/fail2ban/filter.d/nginx-probe.conf` | 路径嗅探 filter（见 §4） |
| `/etc/fail2ban/filter.d/nginx-api-401.conf` | /api/ 认证失败限频 filter（见 §5） |

修改任一文件后：`sudo systemctl restart fail2ban`。

## 3. jail.local（完整内容）

```ini
# 243 生产环境 fail2ban 配置（2026-09-16 安全扫描后部署）
[DEFAULT]
# 办公/业务出口 IP 白名单（2026-09-16 扫描确认的正常来源）
ignoreip = 127.0.0.1/8 ::1 180.169.235.0/24 117.185.158.93 183.195.14.245 112.232.229.10
bantime  = 24h
findtime = 10m
maxretry = 5
banaction = iptables-multiport

[sshd]
enabled = true
port    = 10167
maxretry = 5

# 路径嗅探（.env / phpunit / passwd / 矿池 / RDP 探测等）
[nginx-probe]
enabled  = true
port     = http,https
filter   = nginx-probe
logpath  = /var/log/nginx/access.log
maxretry = 10
findtime = 10m
bantime  = 24h

# /api/ 认证失败限频（阈值放宽，避免误伤 token 过期自动重登的正常用户）
[nginx-api-401]
enabled  = true
port     = http,https
filter   = nginx-api-401
logpath  = /var/log/nginx/access.log
maxretry = 30
findtime = 1m
bantime  = 2h
```

**白名单依据**：`180.169.235.0/24`（办公出口，含 .195/.196/.197）、`117.185.158.93`、`183.195.14.245`（办公/用户出口）、`112.232.229.10`（RPA 本地工具心跳，每 5s 一次 heartbeat）。办公网出口 IP 变化时需同步更新此行。

## 4. nginx-probe.conf（路径嗅探 filter）

```ini
# 嗅探/漏洞探测路径 filter：无论返回状态码，命中即计数
# 匹配 access.log 请求行中的典型恶意特征
[Definition]
failregex = ^<HOST> -.*"(?:GET|POST|HEAD|PUT|OPTIONS|CONNECT)\s[^"]*(?:\.env|\.git/|phpunit|eval-stdin|/etc/passwd|@fs/|@vite/|mining\.subscribe|mstshash|wp-(?:admin|login)|phpmyadmin|Gh0st|aws/credentials|__aws_leak_probe|allow_url_include|auto_prepend_file|actuator|docker-compose\.yml|secrets\.env|telescope|/cgi-bin/)[^"]*"\s.*$
            ^<HOST> -.*"CONNECT\s.*$

ignoreregex =
```

## 5. nginx-api-401.conf（认证失败限频 filter）

```ini
# /api/ 认证失败限频 filter：401/403 且路径为 /api/
[Definition]
failregex = ^<HOST> -.*"(?:GET|POST|HEAD|PUT)\s/api/[^"]*"\s(?:401|403)\s.*$

ignoreregex =
```

## 6. 验证与日常运维

```bash
# 查看 jail 列表与封禁情况
sudo fail2ban-client status
sudo fail2ban-client status sshd            # 或 nginx-probe / nginx-api-401

# 手动解封 / 封禁
sudo fail2ban-client set nginx-probe unbanip <IP>
sudo fail2ban-client set nginx-probe banip <IP>

# 修改配置后重启
sudo systemctl restart fail2ban

# 用历史 access.log 验证 filter 命中率（上线时 nginx-probe 命中 2473 行 / nginx-api-401 命中 16 行）
sudo fail2ban-regex /var/log/nginx/access.log /etc/fail2ban/filter.d/nginx-probe.conf

# 封禁动作日志
sudo tail -f /var/log/fail2ban.log
```

**部署时踩坑**：`apt-get install` 会立即启动 fail2ban，此时若 jail.local 还没放入，只有 sshd jail 生效——必须 `systemctl restart fail2ban` 让后放入的配置生效。

**日志分析注意事项**（排查是否误封时）：
- 先排除白名单 IP 再判断恶意流量
- `/api/saas/*` 偶发 401 是 token 过期后前端自动重登，属正常行为（nginx-api-401 的阈值 30 次/1m 即为此放宽）
- GCloud 漏扫命中 `/fetch`、`/actuator` 等路径返回 200，是 SPA 兜底页（642B index.html），不代表接口存在

## 7. 遗留加固项（2026-09-16 未实施）

- [ ] 开启 ufw：入站仅放行 10167 / 80 / 443，给 8000（API 直连）/ 10864（Postgres）加第二层防护（当前仅腾讯云安全组单层拦截，已实测公网不可达）
- [ ] postgres 容器端口映射改绑 `127.0.0.1:10864:5432`（当前 `0.0.0.0`，改前确认 254 备份通道与调试连接方式不受影响）
