# AgentRunner 分进程部署检查清单（同一代码制品 overlay）

适用对象：`docker-compose.agent-runner.yml`（叠加到目标环境主 compose 使用）。四服务同镜像
`aid-agent-api:latest`：`runner-api`（`python -m src.services.agent_runner.bootstrap`）、
`runner-worker`（`python -m src.services.agent_runner.worker`）、`kf-ingress`
（`python -m src.channels.wecom_kf.ingress_worker`）、`kf-admission`
（`python -m src.channels.wecom_kf.admission_worker`）。KF 两服务挂 `profiles: [wecom-kf]`，
不启用 profile 时不会随任何 `up` 启动。

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
runner-api(8091, /health) ◄── HTTP ── kf-admission(SourceClient→api_url)
        ▲                                 │
        │ DB lease/事件/控制              │ DB 固定上下文扫描
        ▼                                 ▼
runner-worker(执行/恢复, 无HTTP) ◄── DB ── kf-ingress(账号租约逐页拉取)
```

- 四服务只经共享数据库 + `runner-api` HTTP 协作；`runner-worker` 不调用 HTTP API。
- 共享产物路径：`AGENT_RUNNER_STORAGE_ROOT=/app/storage`（`src/core/storage.py`
  `configured_storage_root`）与 `AGENT_RUNNER_RESOURCE_DIR=/app/storage/agent_runner`
  （`src/services/agent_runner/worker.py` RuntimeFactory）在四服务显式注入，均落在与主
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

fk-migration 与 legacy-web-compat 落地后的最终 `deploy/db_update.yaml` 形态（两新增量块都在，
`db_update.yaml` 尾部依次）：

1. `2026-10-01 20:45:58` 起 AgentRunner 表族（agent_runners / session_claims / usage_receipts
   → 1077 行起）、`2026-10-02` controls/events 系列、M6a KF 系列至 `2026-10-04 00:03:10`；
2. **fk-migration 块** `2026-10-04 02:00:00`（db_update.yaml:3410）：
   `agent_runner_controls` 撤除 `runner_id` 的 SQL 外键级联，系统表引用完整性按库规则改由
   应用层检查——增量库与 fresh DDL（`deploy/init-postgres.sql` 的 controls 建表无
   `REFERENCES agent_runners`）对齐，重复执行为 no-op；
3. **legacy-web-compat 块** `2026-10-05 01:00:00`（db_update.yaml:3430）：
   `bs_browser_assistance_requests` 持久化原随机 `continuation_id`（bac→Runner 稳定映射），
   存量 NULL 行共存、部分唯一索引 `uq_browser_assistance_continuation`。

**核查方法（只读）**：确认迁移工具已执行至 `2026-10-05 01:00:00` 条目，或直接查库：

```sql
SELECT to_regclass('agent_runners'), to_regclass('agent_runner_session_claims'),
       to_regclass('agent_runner_usage_receipts'), to_regclass('agent_runner_controls'),
       to_regclass('agent_runner_events');           -- 五项均非 NULL
SELECT 1 FROM pg_attribute WHERE attrelid='bs_browser_assistance_requests'::regclass
  AND attname='continuation_id' AND NOT attisdropped; -- fk/legacy 块已落地
SELECT count(*) FROM pg_constraint                    -- controls 无 SQL FK（=0）
  WHERE conrelid='agent_runner_controls'::regclass AND contype='f';
```

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
- KF 走 native 时主 API 另需 `AGENT_RUNNER_WECOM_KF_ENABLED=true`（`src/saas/api/channel_routes.py:1317`
  据此把 `/t/{tenant}/wecom_kf/callback` 分流到 `accept_callback`），同样需 recreate。
- 可选字节上限覆盖（与 settings.py 命名对齐，不设则用 config.yaml `agent_runner.limits`）：
  `AGENT_RUNNER_LIMITS_REQUEST_BYTES` / `AGENT_RUNNER_LIMITS_CHECKPOINT_BYTES` /
  `AGENT_RUNNER_LIMITS_SNAPSHOT_BYTES`。

### ③ 共库串环境检查（agent2/agent3）

agent2（test）与 agent3（dev）共用业务库 `aid_work_agent2`（`deploy/服务器部署现状.md` §4；
`docker-compose.dev.yml` 头部同类警告先例）。`agent_runners` 的 lease/认领是库级共享池：
**同一数据库同一时刻只允许一个环境常驻 runner-worker / kf-ingress / kf-admission**，
否则两环境 worker 会在同一 lease 池上跨环境分摊任务。启动前核查另一环境无 runner/KF 容器在跑。

### ④ 令牌与 peer 检查（凭据不入文档/日志）

`runner-worker` 启动门槛：`agent_runner.enabled=true` 且全部 peer 都有 `token_hash`
（`src/services/agent_runner/worker.py` `AGENT_RUNNER_SERVICE_AUTH_REQUIRED`）；`runner-api`
同样校验（`bootstrap.py`）。web peer hash 已在 config.yaml `agent_runner.peers`；明文 token
只在 `.env` 的 `AGENT_RUNNER_WEB_SERVICE_TOKEN`（勿写进本清单、日志或提交）。KF 凭据来自库表
`wecom_kf_account_sync`，不走 env。新增 peer 用 `AGENT_RUNNER_SERVICE_ID` +
`AGENT_RUNNER_SERVICE_TOKEN_HASH`（+可选 `AGENT_RUNNER_SERVICE_SOURCES`）注入。

### ⑤ 纯语法校验（无部署动作，可随时执行）

```bash
# 本机/服务器均可，config 只做解析与插值，不创建容器：
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml config --quiet
# KF 形态一并校验：
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml --profile wecom-kf config --quiet
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
# 3. KF（仅在 KF native 启用时；profile 门控+env 注入见 overlay 注释）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml --profile wecom-kf up -d kf-ingress kf-admission
# 4. 主 API 容器 recreate 补 env（前置②；窗口期动作）
docker compose -f docker-compose.prod.yml up -d --force-recreate aid-agent-api
# 5. 观察
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml ps
docker logs aid-runner-api --tail 100; docker logs aid-runner-worker --tail 100
curl http://localhost:8000/health   # 主 API
```

## 3. 回退步骤（授权后）

- 停新增服务：`docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml down runner-worker kf-ingress kf-admission runner-api`
  （KF 服务需带 `--profile wecom-kf`）。
- 已被接受的 runner 任务**不得**交旧 Agent 从头重跑：由 AgentRunner 排空、暂停或显式取消
  （计划红线，`docs/plans/plan-agent-runner-service.md` M7 节）。
- 新表与已接单任务保留，不做破坏性降级；主 API 若已注入 env，回退同样需 recreate。
- 回退只影响新提交的路由，入口开关不能让同一请求同时进入两条路径。

## 4. healthcheck 局限（如实登记）

- `runner-api`：`curl /health` 仅报告进程与 `enabled` 状态，不代表 DB/lease 健康。
- `runner-worker` / `kf-ingress` / `kf-admission`：**无 HTTP、无心跳文件端点**
  （background_runner 的 `.bg_runner_alive` 机制不在这些进程），healthcheck 只能
  `pgrep -f` 进程判活（镜像已装 procps）——只证明进程存活，不证明 lease/执行健康。
  业务健康验证留给 M7 综合验收（真实消息闭环、跨副本并发、慢/断线场景）。

## 5. Browser owner 拓扑说明

- **owner 不是独立进程**：Browser owner 是执行 worker 进程内的应用侧 owner
  （`src/services/agent_runner/browser_owner.py`），随 `runner-worker` 生命周期存在；
  `runner-api` 与 KF 两服务不参与 Browser。
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
- 迁移：`deploy/db_update.yaml`（增量）、`deploy/init-postgres.sql`（fresh DDL，2973 行起）
- 计划与边界：`docs/plans/plan-agent-runner-service.md`（M7 分进程部署配置节）
