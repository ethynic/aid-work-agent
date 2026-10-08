# AgentRunner 分进程部署检查清单（同一代码制品 overlay）

适用对象：`docker-compose.agent-runner.yml`（叠加到目标环境主 compose 使用）。两服务同镜像
`aid-agent-api:latest`：`runner-api`（`python -m src.services.agent_runner.bootstrap`）、
`runner-worker`（`python -m src.services.agent_runner.worker`）。微信 KF 恢复主 API 原渠道入口，
overlay 不再包含 KF ingress/admission 或 `wecom-kf` profile。

## 已部署旧 KF 方案的切换前置

旧渠道重构已退出新版；新版不包含旧任务兼容消费者或重复消息 receipt 跳过逻辑。切换前在旧制品上完成以下核对，不能直接启动新版并期待它接管旧任务：

1. 停止该环境 KF 新流量，保留旧执行及投递能力收尾；同一客服账号不得同时运行两套消费者。
2. 核对所有已拉取消息，包括尚未 accepted 的消息、人工/员工消息及欢迎语等业务副作用。逐条处理未完成消息；已接受 Runner 须完成或明确取消，副作用不确定时人工核对，不能直接重跑。
3. 逐条核对实际回复送达。失败/未知的投递须记录未送达或人工处理结论；Runner completed 不等于用户已收到回复。
4. 全部已拉取消息均有处理结论后，将旧制品最终拉取 cursor 交给原 `src/channels/wecom_kf/cursor.py` 的 `CursorManager.set_cursor(open_kfid, cursor)`。原 Redis 键为 `wecom_kf_cursor:{open_kfid}`，由项目 RedisClient 添加环境前缀；须在目标环境执行，不能跨环境共用键。不能提前复制最新 cursor 跳过未处理消息，也不能留空导致已处理消息重放。
5. 完成上述核对后冻结新的 Runner 提交，停止旧 KF 消费者及旧共用 `runner-api`、`runner-worker`，核对进程确已退出；旧 Web 任务按既有排空/安全暂停/取消流程处理并保留原状态，不强删 claim。旧版共用服务也读写 gate，不能仅停两个 KF 容器。随后清理下面的旧凭据/peer，发布新制品并切换主 API 原渠道入口。主 API 启动会自动应用新增迁移，必须在全部旧服务退出后移除 gate 列，不能把发布脚本末尾的孤儿清理当作迁移前的停止确认。
6. 发布脚本沿既有 `--remove-orphans` 移除同 compose project 中被删 service 的容器；发布后核对旧 KF 容器不存在，其他 project 的容器不会被这一步移除。前置未满足时不运行发布脚本。

本次只修改代码与部署材料，未核对真实存量、写 Redis、执行迁移或操作容器。旧数据库表及历史记录保留，历史迁移与累积 DDL 仍建表，运行代码不消费；不提供自动 DROP TABLE 或强制解锁脚本。新增块只移除废弃 gate 列，本次未执行。

### agent2 / agent3 旧凭据清理（授权发布窗口内分别核对）

先在旧制品完成上述收尾并停止旧消费者，再编辑各自 `.env` 与当前配置；只核对键名和 service 身份，勿输出 token/hash 值。

- 删除专用 `AGENT_RUNNER_WECOM_KF_SERVICE_TOKEN` 与旧开关 `AGENT_RUNNER_WECOM_KF_ENABLED`。
- 审核提到的四键若为该 token 加 `AGENT_RUNNER_SERVICE_ID`、`AGENT_RUNNER_SERVICE_TOKEN_HASH`、`AGENT_RUNNER_SERVICE_SOURCES`：确认后三项实际配置的是已退出的 KF 专用 peer 后一并删除。后三项是通用覆盖变量，若绑定主 API/Web 或 Browser 身份则保留，不能按名字批量删除。
- 移除旧 KF 专用 peer 及 `agent_runner.wecom_kf` 节；保留主 API peer 对 `chat`、`wecom_kf` 的授权、`AGENT_RUNNER_WEB_SERVICE_TOKEN`、共用 API URL 与 Browser 配置。
- 新进程装配后验证 Web 和 KF 均能提交到共用 Runner，且退役 peer 无法认证；删除 `.env` 行不会更新已经运行的容器，按授权发布流程重建相关新容器。

上述四键对应关系需在目标环境核实，本任务未读取 agent2/agent3 的 `.env`，未撤销凭据或操作容器。

> ⚠️ **红线**：本清单是准备与核查材料。执行 `up/down/restart/recreate`、跑迁移、改服务器配置
> 均属部署动作，必须由用户当场明确授权（见 AGENTS.md「真机部署授权」）。本文所有命令均为
> 授权后步骤示例，不在本任务内执行。`configs/config.yaml` 是三环境共享挂载且
> `agent_runner.enabled` 当前为本地验证态 `true`——任何 runner 服务 `up` 即激活 Runner 接线。

---

## 0. 拓扑总览

```
目标环境主 compose（api / background / redis）
        │  共享：业务库+日志库（DATABASE_URL/LOGS_DATABASE_URL）、
        │        uploads/storage 宿主目录、./configs 共享挂载
        ▼
runner-api(8091, /health) ◄── HTTP ── 主 API 的 Web / KF gateway
        ▲
        │ DB lease/事件/控制
        ▼
runner-worker(执行/恢复, 无HTTP)

主 API 的微信 KF 原渠道：回调 → 拉取/合并/语音/人工 → Runner HTTP → 原渲染发送
```

- 两服务只经共享数据库协作；`runner-worker` 不调用 HTTP API。
- 共享产物路径：`AGENT_RUNNER_STORAGE_ROOT=/app/storage`（`src/core/storage.py`
  `configured_storage_root`）与 `AGENT_RUNNER_RESOURCE_DIR=/app/storage/agent_runner`
  （`src/services/agent_runner/worker.py` RuntimeFactory）在两服务显式注入，均落在与主
  API 同一宿主 storage 挂载卷内。
- 宿主路径/网络经变量注入（仅 compose 插值，应用不读取）：

| 变量 | 生产（prod.yml） | 测试（test.yml, agent2） | 在线开发（dev.yml, agent3） |
|------|------------------|--------------------------|------------------------------|
| `AGENT_RUNNER_NETWORK` | `aid-network` | `aid-network2` | `aid-network3` |
| `AGENT_RUNNER_UPLOADS_HOST_DIR` | `/var/www/qb3_upload/agent_uploads` | `/var/www/qb3_upload/agent2_uploads` | `/var/www/qb3_upload/agent2_uploads` |
| `AGENT_RUNNER_STORAGE_HOST_DIR` | `/var/www/qb3_upload/agent_storage` | `/var/www/qb3_upload/agent2_storage` | `/var/www/qb3_upload/agent2_storage` |

  三个变量可在 `.env` 追加（条目格式参考 `deploy/env.sim.example`，形如
  `AGENT_RUNNER_NETWORK=aid-network2`）或命令行 `AGENT_RUNNER_NETWORK=... docker compose ...` 注入。

## 1. 前置检查（不启动任何容器）

### ① 迁移前置：db_update 已含全部 AgentRunner 增量块（assert_schema 只断言不建表）

`runner-api` 与 `runner-worker` 启动仅执行 `RunnerRepository().assert_schema()`
（`src/services/agent_runner/repository.py:19-26`）：断言 `agent_runners`、
`agent_runner_session_claims`、`agent_runner_usage_receipts`、`agent_runner_controls`、
`agent_runner_events` 五表存在，缺表直接 `AGENT_RUNNER_SCHEMA_REQUIRED` 启动失败，**不建表**。
建表/变更唯一入口是迁移脚本。

`deploy/db_update.yaml` 保留全部已提交历史块，`deploy/init-postgres.sql` 保留累积建表内容。仅在两文件末尾追加同一 gate 退役操作：`2026-10-08 15:21:10`，`ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate`。新库沿完整历史建表后移除 gate，已有库按 `last_datetime` 只执行新增块；新旧最终结构一致。旧 KF 表/数据保留，不做 DROP TABLE。通用执行 claim 仍在终态释放，代码不再使用 delivery gate。

当前迁移工具按 `_db_update_applied.last_datetime` 执行，`file_hash` 仅为兼容旧结构保留的字段；不以文件 hash 变化触发全量重跑。

**核查方法（只读）**：核对当前迁移条目及五张通用表存在；Browser Web 兼容仍要求 `bs_browser_assistance_requests.continuation_id`，controls 不使用 SQL 外键级联。启动断言不等于完成迁移，不能据此跳过数据库准备。

### ② 旧主 API 容器补注入 AGENT_RUNNER_API_URL（需 recreate，必检）

主 API（`aid-agent-api` / `aid-agent-api2` / `aid-agent-api3`）的 Runner header 转接用
`web_client`（`config.api_url` 为 base_url）。config.yaml 现值 `http://127.0.0.1:8091` 在主 API
容器内指向自身回环，分进程后必失败。必须在主 API 容器注入：

```bash
AGENT_RUNNER_API_URL=http://runner-api:8091
```

（注意是 compose 服务名 `runner-api`，且主 API 与 overlay 必须在同一 `AGENT_RUNNER_NETWORK`。）

- 环境变量变更对已存在容器**不生效**，必须 `up -d --force-recreate` 重建主 API 容器——这是
  重启主服务的动作，**不放进 overlay 自动生效**，须用户授权并安排窗口。
- KF 原回调直接处理渠道业务，对话经独立 Runner HTTP API 执行，提交 `source=wecom_kf` 及客户/客服账号路由。旧 KF 开关、专用服务 token 和 peer 应从目标环境配置移除；共用 Runner 连接与凭据仍须保留。
- 可选字节上限覆盖（与 settings.py 命名对齐，不设则用 config.yaml `agent_runner.limits`）：
  `AGENT_RUNNER_LIMITS_REQUEST_BYTES` / `AGENT_RUNNER_LIMITS_CHECKPOINT_BYTES` /
  `AGENT_RUNNER_LIMITS_SNAPSHOT_BYTES`。

### ③ 共库串环境检查（agent2/agent3）

agent2（test）与 agent3（dev）共用业务库 `aid_work_agent2`（`deploy/服务器部署现状.md` §4；
`docker-compose.dev.yml` 头部同类警告先例）。`agent_runners` 的 lease/认领是库级共享池：
**同一数据库同一时刻只允许一个环境常驻 runner-worker**，
否则两环境 worker 会在同一 lease 池上跨环境分摊任务。启动前也核查旧 KF 容器已完成收尾。

### ④ 令牌与 peer 检查（凭据不入文档/日志）

`runner-worker` 启动门槛：`agent_runner.enabled=true` 且全部 peer 都有 `token_hash`
（`src/services/agent_runner/worker.py` `AGENT_RUNNER_SERVICE_AUTH_REQUIRED`）；`runner-api`
同样校验（`bootstrap.py`）。主 API peer hash 位于 config.yaml `agent_runner.peers`，sources 包含 `chat` 与 `wecom_kf`；明文 token
只在 `.env` 的 `AGENT_RUNNER_WEB_SERVICE_TOKEN`（勿写进本清单、日志或提交），Web/KF 共用该可信网关身份。
KF 原平台凭据仍由租户渠道配置提供，worker 只为原工具重建上下文。新增 peer 用 `AGENT_RUNNER_SERVICE_ID` +
`AGENT_RUNNER_SERVICE_TOKEN_HASH`（+可选 `AGENT_RUNNER_SERVICE_SOURCES`）注入。

### ⑤ 纯语法校验（无部署动作，可随时执行）

```bash
# 本机/服务器均可，config 只做解析与插值，不创建容器：
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml config --quiet
```

注意：若 `.env` 缺主 compose 要求的必填变量（如 prod 的 `REDIS_PASSWORD`），校验需临时
`REDIS_PASSWORD=dummy` 前缀（仅命令行临时值，**不得写入任何文件**）。

## 2. 授权后启动顺序

按序执行，每步观察后再进行下一步（命令为生产形态，其他环境替换主 compose 文件与
`AGENT_RUNNER_NETWORK` 等变量）：

```bash
# 0. 迁移（前置①已核，先备份：见 deploy/backup.md）
# 1. 独立 API（等 healthcheck 转 healthy；日志确认 assert_schema 通过、无 AGENT_RUNNER_SCHEMA_REQUIRED）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml up -d runner-api
# 2. 执行 worker（pgrep 判活；观察认领日志）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml up -d runner-worker
# 3. 主 API 容器 recreate 补 env（前置②；窗口期动作）
docker compose -f docker-compose.prod.yml up -d --force-recreate aid-agent-api
# 4. 观察
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml ps
docker logs aid-runner-api --tail 100; docker logs aid-runner-worker --tail 100
curl http://localhost:8000/health   # 主 API
```

## 3. 回退步骤（授权后）

- 停新增服务：`docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml stop runner-worker runner-api`。
- 已被接受的 runner 任务**不得**交旧 Agent 从头重跑：由 AgentRunner 排空、暂停或显式取消
  （计划红线，`docs/plans/plan-agent-runner-service.md` M7 节）。
- 新表与已接单任务保留，不做破坏性降级；主 API 若已注入 env，回退同样需 recreate。
- gate 退役迁移后不能直接启动旧 KF native 制品；其依赖的列已移除。若确需恢复旧方案，另行准备恢复 gate 及原约束的新增兼容迁移或核对过的备份恢复方案，再启动旧服务并重新核对存量，不重写历史块，不重跑已完成任务。
- 回退只影响新提交的路由，入口开关不能让同一请求同时进入两条路径。

## 4. healthcheck 局限（如实登记）

- `runner-api`：`curl /health` 仅报告进程与 `enabled` 状态，不代表 DB/lease 健康。
- `runner-worker`：**无 HTTP、无心跳文件端点**
  （background_runner 的 `.bg_runner_alive` 机制不在这些进程），healthcheck 只能
  `pgrep -f` 进程判活（镜像已装 procps）——只证明进程存活，不证明 lease/执行健康。
  业务健康验证留给 M7 综合验收（真实消息闭环、跨副本并发、慢/断线场景）。

## 5. Browser owner 拓扑说明

- **owner 不是独立进程**：Browser owner 是执行 worker 进程内的应用侧 owner
  （`src/services/agent_runner/browser_owner.py`），随 `runner-worker` 生命周期存在；
  `runner-api` 不参与 Browser。
- **view 网关是同 worker 进程内的只读 sidecar**（`src/services/agent_runner/browser_sidecar.py`），
  由 `browser_owner.view_enabled` 装配（`worker.py` RuntimeFactory.open）；观察请求经 owner
  进程内转发，不落独立容器。
- **每 worker 副本独占 8092 listener**：`bind_host/bind_port` 默认 `127.0.0.1:8092`
  （`configs/config.yaml` `agent_runner.browser_owner` 注释），端口冲突会在认领前失败。
  多 worker 副本必须各自配置不同 `bind_port` 与 `endpoint`/`allowed_endpoints`
  （env：`AGENT_RUNNER_BROWSER_BIND_HOST/PORT`、`AGENT_RUNNER_BROWSER_OWNER_ENDPOINT`、
  `AGENT_RUNNER_BROWSER_ALLOWED_ENDPOINTS`）。
- 当前默认 `view_enabled: false`、单副本 worker：本 overlay 不发布 8092 端口、不注入
  Browser env。开启 view 网关或扩 worker 副本属于后续授权变更，不在本清单默认路径内。

## 6. 相关文件

- overlay：`docker-compose.agent-runner.yml`（根目录）
- 部署总文档：`deploy/DEPLOYMENT.md`「AgentRunner 分进程部署」一节
- 迁移：`deploy/db_update.yaml`（增量）、`deploy/init-postgres.sql`（fresh DDL）
- 计划与边界：`docs/plans/plan-agent-runner-service.md`（M7 分进程部署配置节）
