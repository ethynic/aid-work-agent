# Nginx 反向代理（宿主机）

> 两台服务器 nginx 均跑在宿主机（非容器）。conf 仓库源文件在本目录，修改后需同步到服务器 `/etc/nginx/conf.d/` 并 `nginx -t && systemctl reload nginx`。

## 1. 生产服务器（129.211.65.243）

配置位于 `/etc/nginx/conf.d/`，证书在 `/etc/nginx/conf.d/SSL/`（商业泛域名 `*.aidingyi.cn`，从旧服务器迁移）：

| 域名 | 配置文件 | 后端端口 |
|------|---------|---------|
| `agent.aidingyi.cn`（生产主域名） | `agent.aidingyi.cn.conf`（仓库源文件 [agent.aidingyi.cn.conf](agent.aidingyi.cn.conf)） | `localhost:8000` |

> `agent1.aidingyi.cn` 为迁移验证期临时域名，2026-09-15 已删除其 nginx 配置（备份于新机 `/tmp/agent1.aidingyi.cn.conf.bak_20260915`）；DNS A 记录需在腾讯云控制台另行删除，删除前该域名请求会落到 nginx 默认 server（即生产站点）。

关键配置要点（同旧机生产 conf）：

- **限流**：单 IP 并发连接与请求频率双限制；`limit_conn_zone` 全局 zone 只允许一份定义（多份 conf 重复定义会报 `already bound` 启动失败）。
- **下载接口单独限速**：对话附件/知识库文档等大文件下载点单连接 `limit_rate 128k`、前 1MB 全速、单 IP 并发 2（新增下载点须同步三份 conf 的正则，见 `.claude/rules/backend_dev.md`）。
- **知识库上传限速**：`/api/knowledge/upload` 单文件 `256KB/s`，保证 5Mbps 总带宽不被独占。
- **SSE 流式输出**：`/api/` 路径 `proxy_buffering off` + `proxy_cache off`，长连接超时 600s。
- **静态资源缓存**：JS/CSS/图片 30 天 `immutable`，HTML 不缓存。
- **HTTP → HTTPS**：80 端口 `return 301 https://$host$request_uri`。

## 2. 旧服务器（124.222.3.254）

| 域名 | 配置文件 | 后端端口 | 状态 |
|------|---------|---------|------|
| `agent2.aidingyi.cn`（测试） | [agent2.aidingyi.cn.conf](agent2.aidingyi.cn.conf) | `localhost:8001` | 正常服务 |
| `agent3.aidingyi.cn`（在线开发） | [agent3.aidingyi.cn.conf](agent3.aidingyi.cn.conf) | `localhost:8004` | 正常服务 |
| `agent.aidingyi.cn` | [agent.aidingyi.cn.conf](agent.aidingyi.cn.conf) | `localhost:8000` | **已失效**（DNS 指向新机；conf 保留供回滚） |

## 3. 访问日志与安全分析

- 日志路径：`/var/log/nginx/access.log`（按天轮转），错误日志 `error.log`
- Fail2Ban 基于 access.log 工作（jail/filter 配置见 [fail2ban.md](fail2ban.md)）
- 分析日志注意事项（勿误判）：先排除 Fail2Ban 白名单办公 IP；`/api/saas/*` 偶发 401 是 token 过期自动重登；漏扫命中任意路径返回 200/642B 是 SPA 兜底页，不代表接口存在
