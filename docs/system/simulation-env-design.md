# 仿真环境（Staging）设计方案 -- 生产 243 同机混部

> 2026-09-17 设计（同日评审修订：渠道对接保留、Redis 共用实例、Redis 数据同步、附件共享生产目录）。目标：在生产服务器 129.211.65.243 上搭建一套与生产同构的仿真环境，用于按租户同步生产数据、复现生产 bug，避免直接在生产环境验证。业界对应实践为 staging / pre-production environment，本方案按"正式 staging 环境管理"的思路设计（访问控制、副作用门控、单向同步），非临时脚本拼凑。

---

## 1. 背景与定位

| 项 | 说明 |
|----|------|
| 痛点 | 生产环境（243）客户报错后，无法直接在生产复现调试；测试机（254）数据与生产不一致，难以还原现场 |
| 方案 | 在 243 上部署一套独立仿真环境，**一键同步指定租户**的生产数据 + Redis 状态到仿真库，在同版本代码上复现问题 |
| 定位 | 正式 staging 环境：单向同步、按需启停、主动外呼门控、白名单访问 |
| 明确不做 | 双向同步（仿真 → 生产禁止）；对外服务真实客户；长期常驻运行 |

## 2. 总体布局

生产与仿真**同宿主机、同 Postgres 实例、同 Redis 实例、共享附件目录**，通过独立目录 / 独立容器 / 独立库 / 独立 DB 账号 / Redis prefix / 附件写删门控实现隔离。

```
243 宿主机
├── 生产（常驻）
│   ├── /var/www/agent/                 docker-compose.prod.yml
│   ├── aid-agent-api        → 8000     （Gunicorn 6 workers）
│   ├── aid-agent-background             （定时调度/后台任务）
│   ├── aid-redis                         （REDIS_KEY_PREFIX=aid-agent）
│   ├── aid-postgres → 10864             （库：aid_work_agent / aid_work_logs）
│   ├── /var/www/qb3_upload/agent_uploads / agent_storage（附件，共享给仿真）
│   └── nginx: agent.aidingyi.cn → localhost:8000
│
└── 仿真（按需启停）
    ├── /var/www/agent1/                docker-compose.sim.yml
    ├── aid-agent-api1       → 8010     （Gunicorn 2 workers，mem 限 4g）
    ├── （不起 background 容器，见 §4）
    ├── aid-redis 共用 → REDIS_KEY_PREFIX=aid-agent1（prefix 隔离，见 §2.1）
    ├── aid-postgres 同实例 → 新库：aid_work_agent1 / aid_work_logs1（专用账号 aid_sim_user）
    ├── 附件共享生产目录 qb3_upload/agent_storage / agent_uploads（读写，见 §5.4）
    └── nginx: agent1.aidingyi.cn → localhost:8010（办公 IP 白名单；callback 路径放行渠道回调）
```

| 隔离维度 | 生产 | 仿真 |
|---------|------|------|
| 项目目录 | /var/www/agent | /var/www/agent1（代码 rsync 自生产，configs/.env 独立维护） |
| 容器 | aid-agent-api / aid-agent-background | aid-agent-api1（仅 1 个容器） |
| 宿主机端口 | 8000 | 8010 |
| 数据库 | aid_work_agent / aid_work_logs（账号 aid_user） | aid_work_agent1 / aid_work_logs1（专用账号 **aid_sim_user**，仅授权这两个库） |
| Redis | aid-redis，prefix=aid-agent | **共用 aid-redis**，prefix=aid-agent1 |
| 附件 | qb3_upload/agent_storage（读写） | **共享生产目录**（读写），删除/覆盖门控，见 §5.4 |
| 日志 | /var/www/agent/log | /var/www/agent1/log |
| 域名 | agent.aidingyi.cn | agent1.aidingyi.cn（办公 IP 白名单） |
| 运行状态 | 常驻 | 默认 stopped，复现问题时 start |

### 2.1 Redis 共用实例 + prefix 隔离

沿用 254 服务器三套环境（生产/测试/开发）共用 Redis、以 `REDIS_KEY_PREFIX` 隔离键空间并**稳定运行数月**的既有实践，仿真环境与生产共用 `aid-redis` 容器，`REDIS_KEY_PREFIX=aid-agent1`。附加约束：

1. **一切清理操作禁止 `FLUSHALL`/`FLUSHDB`**（共用实例会冲掉生产缓存），只能按 prefix `SCAN + DEL`（清理 `aid-agent1:*`）
2. 关注 maxmemory 水位：生产缓存 + 仿真复现共用内存，仿真大量复现会话时可能触发 eviction 挤掉生产缓存键，必要时调大容器内存上限

**版本一致性**：仿真目录整体 rsync 自 `/var/www/agent/`（排除 `.env`、`configs/`、`log/`、代码目录外的挂载内容），代码天然与生产同版本；同步脚本输出 rsync 时间戳供核对。`configs/` 独立维护（渠道按 §4.3 原则配置、SIMULATION_MODE=1），每次代码同步后 diff 一次生产 configs 确认无新增必配项。

## 3. 数据库隔离与账号

1. 在生产 `aid-postgres`（10864）实例上新建库 `aid_work_agent1` / `aid_work_logs1`，表结构用 `deploy/init-postgres.sql` + `init-postgres-logs.sql` 初始化（db_update.yaml 增量机制启动时自动补齐）。
2. 新建专用账号 `aid_sim_user`，**只 GRANT** `aid_work_agent1` / `aid_work_logs1` 的 CONNECT + CRUD；生产库（`aid_work_agent`）对其不可见。从权限层面杜绝仿真容器误连/误写生产库。
3. 新建生产**只读**账号 `aid_readonly`（仅 GRANT SELECT），供同步脚本从生产导出数据。脚本凭据双账号隔离：导出用只读、写入用 sim 账号。
4. 仿真库定位为**一次性耗材**：可随时 drop 重建，不做备份、不进备份策略。

## 4. 副作用防护（SIMULATION_MODE 门控）-- 本方案最关键部分

仿真环境跑着真实租户数据，任何"对外动作"都可能把消息发给真实客户。门控语义按消息来源二分：

- **被动回复（保留）**：由客户消息触发的链路，包括渠道回复、recap 推送（客户消息触发、归属被动链路）——这是渠道调试的目的，不能关
- **主动外呼（门控）**：无客户消息触发的对外发送，包括群发、定时推送、SMTP 邮件——一律 dry-run

### 4.1 配置层

- 仿真 `.env`：`SIMULATION_MODE=1`
- 仿真 compose **不部署 background 容器**：定时调度（APScheduler）、后台任务 runner 整体不存在，从物理上消除定时类主动外呼风险。第一版仅部署 api 容器；消息触发的被动链路（含 recap 推送）不受影响
- **渠道凭证不同步**：同步脚本对渠道配置表的凭证字段（token / secret / encoding_aes_key 等）置空（见 §5.2）——即使生产回调被误路由到仿真环境，验签失败自动拒答；仿真环境也失去"用生产凭证直接给真实客户发消息"的能力

### 4.2 代码门控（新增）

`settings` 增加 `simulation_mode`（env `SIMULATION_MODE`，默认 `False`；**默认即生产行为，未显式设置不改变任何现有逻辑**）。门控点：

| # | 门控点 | 行为 |
|---|--------|------|
| 1 | 主动外呼出口（群发、定时推送、SMTP 邮件发送） | dry-run：不建立真实连接/不调用服务商 API，日志记录接收方 + 内容摘要后返回成功 |
| 2 | 渠道被动回复（含 recap 推送） | **保留，不门控**（渠道调试目的）；防护靠 §4.1 渠道凭证不同步 + 测试应用隔离 |
| 3 | 附件删除 / 就地覆盖写（共享生产目录） | `simulation_mode=True` 时跳过删除/覆盖，日志记录（实现见 §5.4） |
| 4 | 启动日志 | `simulation_mode=True` 时启动横幅显著标注「⚠️ 仿真环境」，便于一眼区分日志归属 |

后续新增任何"对外发送"能力（外部 webhook、RPA 下发等）时，按 §4.2 的二分语义归类：客户消息触发 → 被动，保留；否则 → 主动外呼，接入门控。写入开发 checklist。

### 4.3 渠道对接（渠道是 bug 高发区，仿真环境必须能收发渠道消息）

| 场景 | 做法 |
|------|------|
| 日常渠道功能调试（收发/解析/回复/验签逻辑） | 使用**独立测试渠道应用**（企微测试自建应用、测试客服账号等），回调 URL 配 `agent1.aidingyi.cn`，凭证与生产无关，怎么调试都不影响真实客户。nginx 对 callback 路径单独 location 放行渠道回调来源（见 §6） |
| 复现特定租户的渠道配置 bug | 同步来的租户渠道配置**凭证字段为空**，无法直接使用。需要时在渠道服务商后台**临时**把该租户回调 URL 切到仿真环境（凭证同步前先行人工核对该配置在仿真环境的可用性），复现完立即切回。切换期间该租户消息落仿真库，属已知短暂影响，提前与客户沟通 |
| 生产回调误路由兜底 | 同步置空凭证 → 仿真环境验签失败自动拒答（不进入处理流程），双保险 |

### 4.4 不门控的部分（有意保留）

- LLM / Embedding 调用照常执行（复现 bug 往往正需要真实模型行为），费用走同一批 API key 真实计费，**计费落仿真库 `chat_records`**，不影响生产租户额度统计。
- 知识库检索、数据库读写均落仿真库；附件读共享生产目录（§5.4），与生产数据无写冲突。

## 5. 一键同步脚本设计 `deploy/sim.sh`

### 5.1 用法

```bash
# 同步指定租户（数据 + Redis 状态；附件共享不复制）
./sim.sh --tenant <tenant_id>

# 同步前先清空仿真库中该租户旧数据（推荐，避免多轮同步后数据混杂）
./sim.sh --tenant <tenant_id> --wipe

# 同时镜像 Redis 状态（生产 prefix -> 仿真 prefix 改写复制，见 §5.3）
./sim.sh --tenant <tenant_id> --redis

# 只看将执行的操作，不实际执行
./sim.sh --tenant <tenant_id> --dry-run
```

### 5.2 同步流程

1. **安全断言（fail-fast）**
   - 校验写入目标连接串库名为 `aid_work_agent1`（防脚本被误改指向生产库）
   - 校验读取源账号为只读账号 `aid_readonly`（防误用可写账号）
   - 生产 api 容器无需停止（只读导出，不影响线上）
2. **数据同步**
   - 通过 `information_schema.columns` 动态枚举生产库所有含 `tenant_id` 列的表，逐表 `COPY (SELECT * WHERE tenant_id = %s) TO STDOUT` → 管道 → 仿真库 `COPY ... FROM STDIN`
   - 无 `tenant_id` 列但业务上归属租户的表（如渠道配置表、`subagent_definitions` 等），维护**显式白名单**并指明关联字段，随表结构演进人工维护
   - **渠道配置表凭证字段置空**（token / secret / aes_key 等列，列清单显式维护）：防误路由验签通过 + 防仿真环境持生产凭证外呼
   - `--wipe`：先按同口径删除仿真库中该租户数据（删除口径 = 导入口径，对称）
3. **Redis 同步（--redis）**
   - `SCAN aid-agent:*` → 逐键 `SET aid-agent1:{rest} value` 并**保留原 TTL**（共用实例下即 prefix 改写镜像）
   - 按 key 模式白名单复制（`uploaded_file:*`、会话上下文/取消标记/待澄清等与复现相关的键），避免生产全局状态原样带入
   - 清理仿真侧旧键只按 `aid-agent1:*` prefix `SCAN + DEL`，**禁止 FLUSHALL**
4. **附件：不复制，共享生产目录**（设计见 §5.4）
5. **输出报告**：各表同步行数、Redis 键数、耗时、代码 rsync 时间戳

### 5.3 约束

- **严格单向**：只从生产读、只向仿真写，脚本内无任何反向路径
- 大表 COPY 产生 IO 峰值，与生产同实例，建议**低峰时段执行**（脚本 --dry-run 先看行数评估）
- 敏感数据：同步的是客户真实数据（聊天记录、客户资料），仿真环境访问纪律按生产级管理（见 §6）

### 5.4 附件共享生产目录（不复制，省时省空间）

仿真容器直接**读写挂载**生产 `qb3_upload/agent_storage` 与 `agent_uploads`，不做附件复制。安全性论证与配套门控：

| 场景 | 分析 | 对策 |
|------|------|------|
| 新增附件 | 文件名为 `{prefix}_{uuid12}.{ext}` 随机命名，冲突概率忽略；生产目录多一个"垃圾文件" | 无害，忽略；仿真新增的文件在仿真库有 file 记录，生产库无记录故生产不可见 |
| 删除附件 | 会真删生产文件（仿真库删记录 + 磁盘删文件） | 门控点 3：`simulation_mode=True` 时跳过所有附件删除出口，只删仿真库记录 |
| **就地覆盖写**（最隐蔽） | 头像类固定路径覆盖、模板原文件修改、文档重新解析等 in-place 更新会**直接改写生产文件内容** | 门控点 3 一并覆盖：写入出口若目标文件已存在且非随机新名 → 跳过并日志记录；实现前先**审计所有附件 unlink / 覆盖出口，收口到统一位置**（`src/core/storage.py` / `image_asset.py`），避免散落逐个 if |
| 读 | 完全真实（复现附件类 bug 更准确） | 知情项：仿真环境读权限 = **全部租户**附件（不再是"仅同步租户"），环境纪律按此标准执行 |

> 可选折中方案（不采用，除非审计发现大量 in-place 写路径）：`cp -al` 硬链接镜像附件目录，秒级完成、零空间占用，删除/新增天然隔离（生产持有另一链接）；但 in-place 修改仍共享 inode，覆盖写门控无论如何都需要。

## 6. Nginx 与访问控制

1. 重建 `agent1.aidingyi.cn.conf`（迁移时删除，备份在新机 `/tmp/agent1.aidingyi.cn.conf.bak_20260915`），反代 `localhost:8010`，SSL 证书与生产同族。
2. **IP 白名单**：默认 `allow` 办公出口 IP（与 fail2ban 白名单同一批）+ `deny all`——客户无法误入仿真环境。
   - ⚠️ 白名单文件必须放 `/etc/nginx/snippets/sim_whitelist.conf`（2026-09-17 上线踩坑：放 `conf.d/` 会被 `include conf.d/*.conf` 收进 http 层，裸 `deny all` 被所有 server 继承，生产无白名单 location 一度全部 403，即时修复）。
3. **callback 路径单独 location 放行**：渠道回调来自服务商服务器 IP（非办公 IP），白名单会挡掉。对 `/t/*/callback/*`、`/api/wechat-mp/callback/*` 单独 location 不做 IP 限制（渠道回调本身有签名验证，配合 §4.1 凭证置空双保险）。日志独立，便于核对回调命中情况。
4. DNS：`agent1.aidingyi.cn` A 记录仍指向 243（迁移后未删），保留使用。
5. 日志独立：`/var/log/nginx/` 下 agent1 单独 access/error log，便于排查时区分环境。

## 7. 资源限额与按需启停

| 项 | 配置 |
|----|------|
| api 容器 | `WORKERS=2`，mem-limit 4g，cpus 限 1.5 |
| Redis | 共用 aid-redis（内存水位见 §2.1） |
| background | 不部署 |
| 运行模式 | 默认 `docker compose -f docker-compose.sim.yml stop`，用时 `start`，用完即停 |
| DB / 附件 IO | 与生产共享实例与磁盘，同步与复现尽量避开业务高峰 |

## 8. 复现 bug 标准流程（SOP）

1. `sim.sh --tenant <tid> --wipe --redis --dry-run` 预览 → 正式同步
2. 核对仿真代码与生产同版本（rsync 时间戳；必要时先 rsync 代码）
3. `docker compose -f docker-compose.sim.yml start`，确认启动横幅为「仿真环境」
4. 办公网内访问 `https://agent1.aidingyi.cn`，用该租户用户账号登录（users 表已同步，密码 hash 一致可正常登录）
5. 渠道类问题：按 §4.3 选择测试应用调试或临时切换租户回调
6. 复现问题 → 通过 `/var/www/agent1/log/`、tlog 主题日志、仿真库只读查询定位
7. 结论落 bug 报告；修复在 254 开发环境开发 → 测试 → 243 生产发布；修复验证可在仿真环境重放
8. `docker compose -f docker-compose.sim.yml stop`；仿真库视为耗材，下次同步前 `--wipe`
9. **禁止在仿真环境操作**：删除文档/附件、头像修改、模板类 in-place 写操作（门控会跳过，但也不要尝试）

## 9. 实施步骤

| 阶段 | 内容 | 产出 |
|------|------|------|
| P0-1 | 建库建账号：`aid_work_agent1` / `aid_work_logs1`、`aid_sim_user`、`aid_readonly` | SQL 执行记录 |
| P0-2 | 仿真目录初始化：rsync 代码、独立 `.env` / `configs/`、`docker-compose.sim.yml`（附件挂载指向生产目录、Redis 共用 + prefix=aid-agent1） | compose 文件 |
| P0-3 | SIMULATION_MODE 代码门控：`simulation_mode` 配置项 + 主动外呼 dry-run + 附件删除/覆盖写出口收口门控 + 启动横幅 | 代码 + 单测 |
| P0-4 | nginx conf 重建 + IP 白名单 + callback 放行 location | conf 文件 |
| P0-5 | 同步脚本 v1（断言 + 动态枚举 + 渠道凭证置空 + --redis prefix 镜像 + 报告） | `deploy/sim.sh` |
| P1 | 验收：同步一个测试租户 → 起环境 → ①测试渠道应用收发消息正常 ②主动外呼 dry-run 生效 ③生产回调误路由被验签拒答 ④附件新增正常、删除/覆盖被门控跳过 ⑤Redis prefix 隔离互不污染 | 验收记录 |
| P2（可选增强） | 同步脱敏选项（客户姓名/手机号等字段 masking）；background 容器按需门控方案；附件硬链接镜像（若审计发现大量 in-place 写路径） | — |

## 10. 风险与边界

| 风险 | 应对 |
|------|------|
| 仿真环境主动外呼真实客户（最严重） | 主动外呼出口统一 dry-run 门控 + 渠道凭证不同步 + background 容器不部署 |
| 生产回调误路由到仿真 | 凭证置空 → 验签失败自动拒答；nginx 白名单仅放行办公 IP 与 callback 路径 |
| 渠道被动回复触达真实客户 | 只发生于"临时切换租户回调"场景，属已知短暂影响，提前沟通；日常调试用测试应用 |
| 附件共享生产目录的删除/覆盖污染 | 删除/覆盖写出口收口 + simulation_mode 门控；SOP 明令禁止删除类操作 |
| 误写生产库 | 专用 sim 账号仅授权仿真库；同步脚本库名断言；生产侧只读账号 |
| Redis 互串 | prefix 隔离（254 实践背书）；禁 FLUSHALL，只按 prefix 清理 |
| 资源争抢拖累生产 | 低配限额 + 按需启停 + 低峰同步 |
| 真实敏感数据落地仿真环境 | 访问按生产级纪律；白名单；后续可选脱敏（P2） |
| LLM 调用真实计费 | 有意保留（复现需要），落仿真库不影响生产额度 |

---

## 关联文档

- [服务器部署现状](../../deploy/服务器部署现状.md) -- 243/254 现有布局（实施后补充「仿真环境」章节）
- [postgres.md](../../deploy/postgres.md) -- 数据库实例与账号管理
- [fail2ban.md](../../deploy/fail2ban.md) -- 办公 IP 白名单依据
- [file_usage.md](file_usage.md) -- 附件新旧双轨路径与兼容口径
- [cache_usage.md](cache_usage.md) -- Redis 键模式与 prefix 机制
