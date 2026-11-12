# AgentRunner 分进程部署检查清单（同一代码制品 overlay）

适用对象：`docker-compose.agent-runner.yml`（叠加到目标环境主 compose 使用）。三服务同镜像
`aid-agent-api:latest`：`runner-api`（`python -m src.services.agent_runner.bootstrap`）、
`runner-worker-light` / `runner-worker-general`（轻重负载分离双池，每副本由 `python -m src.services.agent_runner.supervisor` 管理一个单并发 Worker；light 池领取过滤 `load_class='light'`，general 池无过滤，见 [runner-capacity 设计](../docs/system/runner-capacity-and-load-separation-design.md)）、
`runner-monitor`（`python -m src.services.agent_runner.monitor` 每分钟扫描运行告警）。微信 KF 恢复主 API 原渠道入口，
overlay 不再包含 KF ingress/admission 或 `wecom-kf` profile。

Agent/AgentRunner 架构重构已完成，本清单继续作为各环境发布与核查说明，不是“重构未完成”事项。KF agent2 切换及真机验收已于 2026-10-08 完成，agent3 旧 KF 开关/凭据清理见[恢复计划 Phase 5](../docs/plans/plan-wecom-kf-channel-restore.md)；其他环境不能据此视为已验收。后续渠道保留原业务，只接入共用 Runner，不新增渠道专属容器。

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

上述四键对应关系需按目标环境核实；此清单准备阶段未读取服务器 `.env` 或操作容器，后续 agent2/agent3 清理记录以恢复计划为准，不用准备阶段的限制覆盖已完成发布事实。

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
runner-worker-light / runner-worker-general(执行/恢复, 无HTTP; 双池按 load_class 过滤)
runner-monitor(只读 Runner 状态、写告警/通知/扫描状态, 无HTTP)

主 API 的微信 KF 原渠道：回调 → 拉取/合并/语音/人工 → Runner HTTP → 原渲染发送
```

- 三类服务经共享数据库协作；`runner-worker-light` / `runner-worker-general` 和 `runner-monitor` 不调用 HTTP API。
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

`runner-api` 与 runner-worker 双池启动仅执行 `RunnerRepository().assert_schema()`
（`src/services/agent_runner/repository.py:19-26`）：断言 `agent_runners`、
`agent_runner_session_claims`、`agent_runner_usage_receipts`、`agent_runner_controls`、
`agent_runner_events` 五表存在，缺表直接 `AGENT_RUNNER_SCHEMA_REQUIRED` 启动失败，**不建表**。
建表/变更唯一入口是迁移脚本。

`deploy/db_update.yaml` 保留全部已提交历史块，`deploy/init-postgres.sql` 保留累积建表内容。历史 gate 退役操作登记于 `2026-10-08 15:21:10`：`ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate`。新库沿完整历史建表后移除 gate，已有库按 `last_datetime` 只执行新增块；新旧最终结构一致。旧 KF 表/数据保留，不做 DROP TABLE。通用执行 claim 仍在终态释放，代码不再使用 delivery gate。

Phase 3 的 `execution_started_at`、告警/阈值/扫描状态表须在启动 `runner-monitor` 前完成迁移；全新诺度库使用独立的 `deploy/nuodu/init-postgres.sql`，已同步同一结构。平台飞书通知需配置 `PUBLIC_BASE_URL` 生成登录后 Portal 详情链接；缺失时告警与通知事件仍保存，但飞书投递记录为 `render_failure`，不会发送无链接消息。发布前在目标环境核对该地址，不输出任何 webhook 凭据。

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

### ③ 数据库隔离检查（agent2/agent3）

`agent_runners` 的 lease/认领是库级共享池。当前 agent2 与 agent3 已拆库；发布前仍须核对
实际 `DATABASE_URL` 指向不同业务库，并确认旧 KF 容器已完成收尾。若两个环境重新指向同一库，
**同一数据库同一时刻只允许一个环境常驻 runner-worker 双池**，否则会跨环境分摊任务。

> **2026-10-15 起 agent3 环境曾停用**（共库 + lease 池跨环境抢任务，见 `服务器部署现状.md` §1.2）。
> **2026-10-29 已拆库恢复启用**：agent3 改用独立库 `aid_work_agent3`/`aid_work_logs3`（自
> agent2 库全量复制），`.env` 已切库，本条共库风险解除；两环境 runner 容器名已参数化隔离
> （agent3 侧 `AGENT_RUNNER_*_CONTAINER` 加 `3` 后缀，见 overlay 头部说明）。测试数据经
> `deploy/test2dev.sh` 单向同步（test → dev），**不得**反向同步；若未来任何环境重新指回
> 对方库，必须先回到本条核查。Phase 2 的 worker 改为 Compose 自动命名副本，
> agent3 只需保留 runner-api 的独立容器名；用 `docker compose ps runner-worker-light runner-worker-general` 查看副本。

### ④ 令牌与 peer 检查（凭据不入文档/日志）

`runner-worker-light` / `runner-worker-general` 启动门槛：`agent_runner.enabled=true` 且 `web_service_token` 明文存在
（`src/services/agent_runner/worker.py` `AGENT_RUNNER_SERVICE_AUTH_REQUIRED`）；`runner-api`
同样校验（`bootstrap.py`）。服务间凭据统一为 `.env` 明文 `AGENT_RUNNER_WEB_SERVICE_TOKEN`，
主 API 与 runner-api 共用同一 `.env` 注入（恒等比较即验证，2026-10-04 起不再用 bcrypt hash，
config.yaml 的 `peers` 仅保留 service 身份与 sources 白名单；勿把明文写进本清单、日志或提交）。
4 套环境 `.env` 各自独立，token 对必须同批生成、两侧同值——一侧重建 `.env` 时另一侧必须同步，
否则前端 `/runners` 查询报 401 `SERVICE_UNAUTHORIZED`。
KF 原平台凭据仍由租户渠道配置提供，worker 只为原工具重建上下文。新增 peer 用 `AGENT_RUNNER_SERVICE_ID` +
`AGENT_RUNNER_SERVICE_SOURCES`（来源白名单）注入；令牌仍共用同一明文。

### ⑤ 纯语法校验（无部署动作，可随时执行）

```bash
# 本机/服务器均可，config 只做解析与插值，不创建容器：
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml config --quiet
```

注意：若 `.env` 缺主 compose 要求的必填变量（如 prod 的 `REDIS_PASSWORD`），校验需临时
`REDIS_PASSWORD=dummy` 前缀（仅命令行临时值，**不得写入任何文件**）。

### ⑥ Phase 4 双槽与旧客户端验收准备（只读）

- 默认 `AGENT_RUNNER_WORKER_SLOTS=1` 不能验证同步阻塞时另一槽继续工作。准备 agent2
  双槽配置解析、每副本 CPU/内存额度和当前整机余量；`runner-monitor` 保持单实例。
  `deploy.replicas` 由 Compose 配置解析确认，实际两个容器及各自 `worker_id` 须授权后核对。
- 读取目标库 `max_connections` 与现有连接占用；预算纳入每槽 Worker 业务池上限、监督进程
  1–2 连接、monitor 1–2 连接及 healthcheck 短连接、runner-api、主 API/background 和余量。
  特别核对诺度的 40 连接上限；本地配置解析不能替代目标环境读数。
- 正式服不沿用 agent3 的双槽数值：按[容量初评](../docs/plans/plan-runner-execution-slot-isolation-and-alerts.md#phase-4-正式服容量初评2026-11-06只读尚未部署)先验证 4 槽，再按排队与资源实测决定是否升到 6 槽；扩槽前同时核定 Worker 的 `DB_POOL_MAX`、每槽内存上限及同机余量。默认每槽 6 GiB 内存上限和业务池 15 连接不能直接乘以目标槽数使用。
- 登记已交付 Runtime＋CLI 的实际版本/包及对应旧任务样本，用于“只升级服务端”的原工具、
  恢复、回执/重投、结果上报和计费验收。旧包不可得时如实列为待验，不用模拟版本字段代替。
  具体场景与采样指标见[Phase 4 方案](../docs/plans/plan-runner-execution-slot-isolation-and-alerts.md#phase-4-本地准备与真机验收方案2026-11-06)。

## 2. 授权后启动顺序

按序执行，每步观察后再进行下一步（命令为生产形态，其他环境替换主 compose 文件与
`AGENT_RUNNER_NETWORK` 等变量）：

```bash
# 0. 迁移（前置①已核，先备份：见 deploy/backup.md）
# 1. 独立 API（等 healthcheck 转 healthy；日志确认 assert_schema 通过、无 AGENT_RUNNER_SCHEMA_REQUIRED）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml up -d runner-api
# 2. 执行 worker 双池（AGENT_RUNNER_LIGHT_SLOTS / AGENT_RUNNER_GENERAL_SLOTS 控制副本数；
#    每副本 1 个执行进程；AGENT_RUNNER_ROLE 由服务固定注入，勿在 .env 覆盖）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml up -d runner-worker-light runner-worker-general
# 3. 运行告警扫描器（确认迁移；平台飞书目标可后配，但旧事件不追发）
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml up -d runner-monitor
# 4. 主 API 容器 recreate 补 env（前置②；窗口期动作）
docker compose -f docker-compose.prod.yml up -d --force-recreate aid-agent-api
# 5. 观察
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml ps
docker logs aid-runner-api --tail 100
docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml logs --tail 100 runner-worker-light runner-worker-general runner-monitor
curl http://localhost:8000/health   # 主 API
```

授权后的验收还需记录实际 Worker 副本数/各自 `worker_id`、每槽资源与数据库连接峰值、
同步阻塞期间另一槽的领取和完成、旧 Runtime＋CLI 原能力、monitor 扫描时间与 Portal 告警。
仅 `ps`、`health` 或默认单槽正常不能宣布容量与兼容验收通过。

overlay 已给 runner-api 声明 `depends_on: aid-agent-api: condition: service_healthy`
（2026-11-02 agent1 事故固化：db_update 迁移在主 API 启动过程应用，runner 启动即
assert_schema，compose 并发重建时先断言会撞上未迁移的表/列秒退，实撞
`agent_runners.wake_at` 列）。经 compose 启动时主 API 先转 healthy，迁移即已应用；
仅服务器重启按 restart policy 自启不经 compose 编排，此时无待应用迁移，不受影响。

## 3. 回退步骤（授权后）

- 停新增服务：`docker compose -f docker-compose.prod.yml -f docker-compose.agent-runner.yml stop runner-monitor runner-worker-light runner-worker-general runner-api`。
- 已被接受的 runner 任务**不得**交旧 Agent 从头重跑：由 AgentRunner 排空、暂停或显式取消
  （计划红线，`docs/plans/plan-agent-runner-service.md` M7 节）。
- 新表与已接单任务保留，不做破坏性降级；主 API 若已注入 env，回退同样需 recreate。
- 先保留 Runner 告警、通知和扫描状态用于核对；关闭 monitor 不改变已接单任务或旧客户端
  回执，不靠重放原外部工具补救未知结果。已投递/未投递的飞书状态按原记录处理。
- gate 退役迁移后不能直接启动旧 KF native 制品；其依赖的列已移除。若确需恢复旧方案，另行准备恢复 gate 及原约束的新增兼容迁移或核对过的备份恢复方案，再启动旧服务并重新核对存量，不重写历史块，不重跑已完成任务。
- 回退只影响新提交的路由，入口开关不能让同一请求同时进入两条路径。

## 4. healthcheck 局限（如实登记）

- `runner-api`：`curl /health` 仅报告进程与 `enabled` 状态，不代表 DB/lease 健康。
- `runner-worker-light` / `runner-worker-general`：每副本的监督进程作为容器主进程，异常退出由 restart policy
  重启整个槽容器并换新 `worker_id`。无 HTTP 健康端点，容器状态只证明
  监督进程存活，不证明数据库租约或业务执行健康。
  业务健康验证留给 M7 综合验收（真实消息闭环、跨副本并发、慢/断线场景）。
- `runner-monitor`：healthcheck 核对数据库中最近成功扫描时间，超过 3 分钟报告
  `unhealthy`；该状态本身不会触发 Docker 自动重启。进程退出由 restart policy 拉起。

## 5. Browser owner 拓扑说明

- **owner 不是独立进程**：Browser owner 是执行 worker 进程内的应用侧 owner
  （`src/services/agent_runner/browser_owner.py`），随 runner-worker 双池生命周期存在；
  `runner-api` 不参与 Browser。
- **view listener 是同 worker 进程内的 sidecar**（`src/services/agent_runner/browser_sidecar.py`），
  由 `browser_owner.view_enabled` 装配（`worker.py` RuntimeFactory.open）；主 API 的
  `browser_view_gateway.py` 才是跨容器观察请求的网关。sidecar 不另起容器。
- `bind_host/bind_port` 默认 `127.0.0.1:8092`；各 worker 副本位于独立容器网络命名空间，
  同用该 loopback 端口不会互相冲突，不要求按副本改 `bind_port`。同一容器内端口被占用时，
  view listener 会在认领前启动失败。
- 当前默认 `view_enabled: false`，本 overlay 不发布 8092 端口、不注入 Browser env；
  扩 worker 副本无需为关闭的 view listener 配置端口。若未来启用 Browser view，主 API 的
  `browser_view_gateway` 会按数据库记录的 `owner_endpoint` 跨容器连接 owner，并以
  `allowed_endpoints` 校验；`127.0.0.1` 仅指向网关自身，届时须为各 owner 配置可达且可精确
  区分的地址与允许列表，并核对 listener 的 `bind_host/bind_port`，不能仅靠 Compose 服务名
  负载均衡来定位持有该 run 的副本。该功能另行验收，不作为默认扩槽门槛。

## 6. 相关文件

- overlay：`docker-compose.agent-runner.yml`（根目录）
- 部署总文档：`deploy/DEPLOYMENT.md`「AgentRunner 分进程部署」一节
- 迁移：`deploy/db_update.yaml`（增量）、`deploy/init-postgres.sql`（fresh DDL）
- 计划与边界：`docs/plans/plan-agent-runner-service.md`（M7 分进程部署配置节）
