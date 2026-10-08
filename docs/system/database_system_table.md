# 系统核心表用途与关系

> 本文档介绍智能体系统核心数据表的用途和相互关系，仅覆盖非 `bs_` 前缀的系统表。
> `bs_` 开头的业务表由各子智能体自行管理，不在此文档范围内。

> 2026-07-17 Browser Run/Executor Phase 2 例外登记：新增业务审计表
> `bs_browser_runs`、`bs_browser_assistance_requests`。两表只保存租户归属、
> 状态枚举和恢复关联，不保存完整 URL、DOM、截图、cookie、header、表单值或
> 用户输入；所有读取和更新均要求 `tenant_id` 条件，不创建长期 device 表。
> 2026-10-02 源码核对发现旧基础表未出现在部署 DDL；本次补入
> `deploy/init-postgres.sql` 与 `deploy/db_update.yaml`，对既有表采用非破坏增列。
>
> 2026-07-22 Phase 3R 已新增 `bs_browser_resume_jobs` 持久 lease 队列，替代
> Redis Stream。字段仅包括任务/租户/assistance/run 标识、pending/processing/
> completed/failed 状态、lease、重试调度、白名单错误码和时间戳；
> `assistance_id` 唯一保证幂等入队。worker 用 `FOR UPDATE SKIP LOCKED` 领取，
> lease 过期可回收。本次基础兼容 DDL 同步于上述两份实际部署文件，
> Python service 为 `src/tools/browser/run_db.py` 的 `BrowserResumeJobDB`。
> 遵循 `database_dev.md` 不加外键约束。

> M4 Browser 原 Runner 绑定基础片：`bs_browser_runs` 新增可空的
> `runner_id/runner_execution_id/runner_tool_call_id`、`owner_worker_id/owner_boot_id`、
> `browser_epoch/owner_endpoint/owner_lease_until/runtime_state/closed_at`。
> 原调用到 run 为部分唯一索引；原 worker boot/epoch 一旦绑定不得用同调用换 runtime。
> legacy 行保持整组 NULL；原 Runner 行必须完整绑定，`starting/live` 有租约且无关闭时间，
> `closed/lost` 清租约并保关闭时间。新 owner 租约为 TIMESTAMPTZ，锁后读数据库时钟；
> 旧审计 TIMESTAMP 保持数据库会话原有墙钟语义，不作为 owner fence。
> `bs_browser_assistance_requests` 新增可空 `runner_id/runner_wait_id/owner_boot_id/browser_epoch`
> 和成对可空的 `completion_ref/completion_fact`，只容纳白名单完成事实，不含页面证据。
> 原生人工操作使用 `extended_at TIMESTAMPTZ NULL` 标记唯一延期；同事务更新原 `expires_at`，旧 NULL 表示尚未延期。Redis 只投影已提交期限，不重新授予延期。
> `owner_endpoint` 是服务器固定配置的内控服务地址，不是网页 URL，也不含凭据。
> 新仓储将 mandatory run/wait 行和原完整执行树 CP 关联同事务提交；基础片尚未装配到
> Browser Runtime，不以此声明人工续跑可用。原 Runner 完成不进入 `bs_browser_resume_jobs`。
> 此 Browser 迁移块以单条原子 DO 执行并核对实际列类型、关键唯一索引和已验证 CHECK 定义；
> 不兼容旧结构整块回滚且不推进原迁移水位，不自动删除或替换历史结构。
> 旧自增主键 int4/int8、审计 timestamp/timestamptz 以及 text/无长度上限 varchar 可兼容；
> 新 fencing 时间严格为 timestamptz，完成事实严格为 jsonb。
> 等待绑定还须核当前受信 worker/boot/epoch，assistance SQL 锁等待后再次校验独立 Browser 租约。
>
> 2026-10-05 M7 旧卡片透明迁移：`bs_browser_assistance_requests` 新增可空
> `continuation_id`，native `bind_wait` 每新 wait 持久写入 audit 中的原随机 bac，
> 与 Runner execution/call/wait 同事务关联，独立于 completion 相位；
> 部分唯一索引 `uq_browser_assistance_continuation`（WHERE continuation_id IS NOT NULL）
> 保证一 bac 一行，存量 legacy 行保持 NULL 不受影响。旧 events 薄读桥按该持久行
> JOIN `agent_runners` 复验当前 owner/tenant；纯只读，不入 `bs_browser_resume_jobs`。

> 2026-07-21 社媒营销智能体 outbound 模块 B0.5 例外登记：新增托管登录态表
> `bs_outbound_account_sessions`。存知乎/小红书等 web 操作型连接器的 Playwright
> `storage_state`（cookies + localStorage）**加密 blob**，跨 run 维持登录态（设计
> §7.3 / §10）。`storage_state_encrypted` 由应用层 `encryption_manager` 加密，
> **绝不存储明文 cookie**；读写按 `(tenant_id, account_id)` 强制过滤，状态机
> `active → expired / revoked`。源码：`src/social_media/outbound/account_session_store.py`。
> 表 DDL 已同步 `deploy/init-postgres.sql` 与 `deploy/db_update.sql`。商机池 3 表
> (`bs_outbound_leads` / `_lead_interactions` / `_outreach_actions`) 由并行智能体开发。

> 2026-09-12 端侧会话任务 C1 系统表族登记：新增 `session_task_*` 11 张系统表
> （`session_tasks`/`session_task_specs`/`session_task_assignments`/
> `session_task_events`/`session_task_messages`/`session_task_batches`/
> `session_task_decisions`/`session_task_execution_links`/`session_task_texts`/
> `session_task_confirmations`/`session_task_cost_reservations`，通用编排协议，
> 不存业务话术）。DDL 三处同步：`deploy/init-postgres.sql`、
> `deploy/db_update.yaml`（2026-09-12 22:30:00 块）、
> `src/session_tasks/init_tables.py`。关键约束：占用唯一部分索引
> `idx_session_tasks_occupancy`（未终结已发布状态一会话一任务）；assignments
> 当前行部分唯一；events 双唯一（assignment+local_seq / event_id）；decisions
> 五元唯一 + opening 跨 spec_revision 部分唯一；texts 为 secret_crypto Fernet
> 受控文本（goal/policy/正文等，无明文副本）；confirmations 一次性发布确认
> （10 分钟有效）；cost_reservations 为预算预留幂等账（C3 接 client_usage_logs
> 结算）。业务配套：`session_tasks_idempotency_keys`（API 幂等，模块自建不进
> db_update.yaml，同 weixin_marketing_idempotency_keys 先例）；业务表
> `bs_weixin_conversation_bindings`（src/weixin_conversation 自建，pending 骨架，
> verified 仅接受受信 Provider 真机证据）。

> 2026-09-09 微信营销自动化 P2 例外登记：新增基础设施表
> `weixin_marketing_idempotency_keys`（非 bs_ 前缀，API 请求幂等去重，同
> `desktop_agent_turn_requests` 先例）。scope=(tenant_id, user_id, route,
> idempotency_key) 唯一，`request_digest` 为「请求路径+请求体规范化 JSON」摘要
> （同 key 异 payload → 409）；仅存响应 JSON 与状态码，不存业务正文；pending
> 占位超 10 分钟 TTL 可被接管（崩溃兜底）。DDL 双轨：`deploy/init-postgres.sql`
> 与 `src/weixin_marketing/api.py` `_IDEMPOTENCY_DDL`（模块幂等自建）；不进
> db_update.yaml；按 tenant_id 物理清理。

> 2026-09-10 微信营销自动化 P4-B 例外登记：新增事件闭环 4 表（合并一条；非 bs_
> 前缀基础设施/示例表，同幂等键表先例：模块幂等自建，DDL 双轨
> `deploy/init-postgres.sql` 与 `src/weixin_marketing/event_sources.py`
> `_TABLES_DDL` / `internal_event_example.py` `_EXAMPLE_DDL`，不进 db_update.yaml）：
> `weixin_marketing_event_source_keys`（webhook 源 HMAC 签名密钥版本行，Fernet
> 密文存储；rotate 时旧 active→retiring+retire_at 并行窗，UNIQUE(source_id,key_id)
> /UNIQUE(source_id,key_version) 仲裁并发；按 tenant_id 物理清理，孤儿行
> source 不存在即无引用可清）、`weixin_marketing_webhook_nonces`（nonce 防重放，
> 消耗与事件接纳同事务，TTL 900s 由 event_match_tick 清理；无 tenant 列，按
> source_id 子查询清理且须先于 event_sources 删除）、
> `weixin_marketing_event_payloads`（受控事件 payload 持久化，tenant+hash 去重
> 复用；底座 events 行只存 payload_ref/hash；保留期清理属 P5/运维，登记未做）、
> `weixin_marketing_example_orders`（内部事件示例业务对象，P5 后真实业务事件源
> 参照；源侧 outbox 复用 desktop_automation_outbox 不另建表；按 tenant_id
> 物理清理）。

---

## 1. 表分类总览

系统表按功能分为以下类别：

| 类别 | 表数量 | 说明 |
|------|--------|------|
| 用户与认证 | 3 | 用户、Token、验证码 |
| 对话核心 | 4 | 会话、消息、记录、错误日志 |
| 渠道适配 | 2 | 渠道会话、渠道消息 |
| 知识库 | 5 | 分类、文档、文本块、向量、全文搜索 |
| 数字员工实例 | 3 | 实例、队列、回复风格 |
| SaaS 多租户 | 5 | 租户、订阅、支付、渠道配置、用户授权 |
| 定时任务 | 2 | 任务定义、执行日志 |
| Prompt 版本管理 | 5 | 注册表、版本、标签、草稿、分段 |
| 子智能体配置 | 4 | 环境变量、知识库关联、定义、Prompt 分段 |
| 其他 | 4 | Token 成本、远程凭据、邮箱配置、数据连接器 |

---

## 2. 用户与认证

### 2.1 `users` — 用户表

系统所有用户的基础信息，支持多种登录方式（密码、手机验证码、企业微信 OAuth）。

**关键字段**：
- `user_id` — 全局唯一用户标识
- `role` — 角色（`user` / `tenant_admin` / `platform_admin`）
- `tenant_id` — 所属租户（平台管理员为空）
- `source` — 注册来源
- `gender` — 性别（SMALLINT，0未知/1男/2女，企微 `kf/customer/batchget` 返回值，渠道用户注册/更新时落库）

**约束**：同一租户内手机号唯一（`idx_users_tenant_phone`）。

### 2.2 `tokens` — 会话 Token 表

用于多进程共享登录状态。存储一次性认证 Token，替代传统的 JWT 或 Session。

**关系**：`users.user_id` ← `tokens.user_id`

### 2.3 `sms_codes` — 验证码表

手机登录时的一次性验证码，通过 `expires_at` 和 `used` 字段控制有效期和使用状态。

---

## 3. 对话核心

### 3.1 `chat_sessions` — 会话表

用户与智能体的一次对话会话。一个会话包含多条消息和一条或多条记录。

**关键字段**：
- `session_id` — 会话唯一标识
- `subagent_id` — 关联的子智能体 ID
- `instance_id` — 关联的数字员工实例 ID
- `context_data` — 会话上下文（JSON），用于跨轮次保持状态

**关系**：
- `chat_messages.session_id` → `chat_sessions.session_id`（一对多）
- `chat_records.session_id` → `chat_sessions.session_id`（一对多）
- `agent_instances.instance_id` ← `chat_sessions.instance_id`

### 3.2 `chat_messages` — 消息表

**仅存储 web 端**用户与 AI 之间的每一条消息，用于前端展示聊天历史和构建对话上下文。第三方渠道（wecom_kf/wecom/dingtalk/feishu 等）的消息走 [`channel_messages`](#42-channel_messages--渠道消息表)，**不写本表**（详见 [3.5 节](#35-网页会话-vs-渠道会话) 的分离规则）。

**关键字段**：
- `role` — 消息角色（`user` / `assistant` / `system` / `tool`）
- `content` — 消息文本内容
- `metadata` — 附加信息（JSON），如工具调用详情

**与 `chat_records` 的区别**：

| 维度 | `chat_messages` | `chat_records` |
|------|----------------|----------------|
| 粒度 | 单条消息（最小单元） | 完整对话交互（用户输入+助手回复） |
| 用途 | 消息展示、上下文构建 | 用量统计、计费、审计、性能监控 |
| 数据 | 简单的 role/content/metadata | 含 token 统计、执行详情、状态等 |

两个表存在内容冗余但设计合理，服务于不同业务目的。

### 3.3 `chat_records` — 会话记录表

每次完整对话（用户输入 → AI 回复）的处理记录，是计费和审计的核心数据源。

**关键字段**：
- `total_token_count` / `prompt_tokens` / `completion_tokens` / `cached_input_tokens` — Token 消耗明细
- `model` / `provider` — 使用的大模型和提供商
- `agent_iterations` — 智能体循环迭代次数
- `subagent_calls` — 子智能体调用记录（JSON）
- `duration_ms` — 对话耗时
- `status` — 执行状态（`completed` / `failed` / `cancelled`）
- `source_type` — 来源类型（`chat` 网页端 / `wecom` / `dingtalk` / `feishu` / `wecom_kf` 等渠道端）

**关系**：
- `chat_records.user_id` → `users.user_id`
- `chat_records.tenant_id` → `tenants.tenant_id`
- `chat_records.session_id` → `chat_sessions.session_id`（网页端）或 `channel_sessions.session_id`（渠道端），无外键约束

### 3.4 `log_error` — 错误日志表

平台级错误日志，仅平台管理员可见。记录系统运行时的异常，支持状态跟踪和处理流程。

**关键字段**：
- `module` — 错误来源模块（如 `agent.py:process_message:1234`）
- `status` — 处理状态（`unprocessed` / `processed` / `ignored`）
- `processed_by` / `processed_at` — 处理人和时间

### 3.5 网页会话 vs 渠道会话

系统存在两套会话存储体系，服务不同的接入场景。

> **⚠️ 严格分离规则**：`chat_sessions`/`chat_messages` **只给 web 端**用；`channel_sessions`/`channel_messages` 等 `channel_` 前缀表**只给第三方渠道**用。**读写都按来源走对应表，严禁混用**（渠道消息绝不写/读 `chat_messages`，反之亦然）。上下文重建必须按会话来源分流（`is_channel_session` 判定），不能靠 fallback，否则迁移期残留数据会劫持真实对话。`chat_records` 是唯一两端共用的表（靠 `source_type` 区分），但不参与上下文重建。详见 [context-reconstruction-pitfalls.md](../incidents/context-reconstruction-pitfalls.md)。

| 维度 | 网页端 | 渠道端 |
|------|--------|--------|
| 存储表 | `chat_sessions` + `chat_messages` | `channel_sessions` + `channel_messages` |
| 会话管理 | 用户可点击"新会话"按钮创建多个会话 | 无可操作按钮，按 `(tenant_id, channel_type, channel_user_id, subagent_id, channel_chat_id)` 唯一确定。`channel_chat_id` 为空时退化为前四元组（wecom/dingtalk/feishu 等单会话场景）；非空时同一渠道用户可在不同客服账号/群下各自独立会话（如 wecom_kf 不同客服账号 open_kfid） |
| 生命周期 | 用户主动创建/切换/删除 | 首次发消息时自动创建，无用户主动删除入口（代码层面支持删除） |
| 用途 | 前端展示聊天历史 + 构建对话上下文 | 同上，但需额外记录渠道特有字段（渠道类型、渠道用户ID等） |

**两种会话的生命周期差异**：

```
网页端：用户 ──→ 会话A（多轮对话）
             ──→ 点击"新会话" ──→ 会话B（新的多轮对话）
             ──→ 删除会话A ──→ chat_sessions/chat_messages 记录删除

渠道端：渠道用户 ──→ 首次发消息 ──→ 自动创建会话（按五元组确定，channel_chat_id 非空时纳入唯一性）
                  ──→ 持续对话 ──→ 消息追加到该渠道会话
                  ──→ 换客服账号/群（channel_chat_id 变化）──→ 新建独立会话（独立上下文与计费归属）
                  ──→ 无可操作按钮，无用户主动删除入口
```

> **多会话场景说明**：同一租户、同一渠道、同一子智能体下的同一渠道用户，可因 `channel_chat_id`（wecom_kf 为客服账号 `open_kfid`，其它渠道将来为会话/群 id）不同而建立多个会话。会话按 `channel_chat_id` 拆分后，各账号/群拥有独立上下文和独立积分计费归属，避免不同客服账号间的消费互相串扰。

**`chat_records` 与两套会话的关系**：

无论是网页端还是渠道端，每次完整对话（用户输入 → AI 回复）都会在 `chat_records` 表创建一条记录，用于**计费和审计**。`chat_records` 独立于会话的生命周期：

- **删除网页会话时**：仅删除 `chat_sessions` 和 `chat_messages` 中的记录，`chat_records` **不删除**
- **删除渠道会话时**：仅删除 `channel_sessions` 和 `channel_messages` 中的记录，`chat_records` **不删除**
- **原因**：`chat_records` 是计费和审计依据，必须保留完整历史
- **`chat_records` 的 `session_id` 字段**：可以指向 `chat_sessions.session_id`（网页端）或 `channel_sessions.session_id`（渠道端），靠 `source_type` 字段区分来源（`chat` / `wecom` / `dingtalk` / `feishu` / `wecom_kf` 等）

```
chat_sessions / channel_sessions（会话）
    ↓ 1:N（创建记录时，session_id 关联到来源会话）
chat_records（计费记录，独立存储，不随会话删除）
```

---

## 4. 渠道适配

### 4.1 `channel_sessions` — 渠道会话表

企业微信、钉钉、飞书等第三方渠道的会话管理。与网页端会话（`chat_sessions`）的核心差异见 [3.5 节](#35-网页会话-vs-渠道会话)。

**关键字段**：
- `channel_type` — 渠道类型（`wecom` / `wecom_kf` / `dingtalk` / `feishu`）
- `channel_user_id` — 渠道侧的用户标识
- `channel_chat_id` — 渠道侧的聊天标识，**通用字段**：wecom_kf 渠道为客服账号 `open_kfid`，其它渠道为会话/群 id（dingtalk/feishu/wecom 群等）。为空时（单会话场景）不参与会话唯一性；非空时纳入 session_id 生成与会话查找，使同一渠道用户在不同账号/群下各自独立会话

### 4.2 `channel_messages` — 渠道消息表

渠道会话中的消息，支持多种消息类型和附件。

**关键字段**：
- `message_type` — 消息类型（`text` / `image` / `file` 等）
- `attachments` — 附件信息（JSON）

**关系**：
- `channel_messages.session_id` → `channel_sessions.session_id`

---

## 5. 知识库

知识库采用 RAG（检索增强生成）架构，包含解析 → 分块 → 嵌入 → 向量检索 + 全文检索的完整链路。

```
documents（文档元数据）
    ↓ 1:N
chunks（文本块）
    ↓ 1:1                  ↓ 1:1
chunks_vec（向量）      chunks_fts（全文搜索）
```

### 5.1 `knowledge_categories` — 知识库分类表

租户级知识库分类，按 `source_type` 区分不同来源（如 `upload`、`url`、`attraction_resource` 等）。

**关键字段**：
- `parent_id` — 父分类 ID（自引用，NULL=顶级分类，支持任意级树形分类；`UNIQUE(tenant_id, source_type)` 保留，子分类 source_type 租户内仍全局唯一）

**关系**：`documents.source_type` → `knowledge_categories.source_type`（逻辑关联，恒为**顶级**分类代号）；`documents.sub_category` → 子分类 `knowledge_categories.source_type`

### 5.2 `documents` — 文档表

上传文档或知识条目的元数据，包含文件信息、处理状态和摘要。

**关键字段**：
- `source_type` — 来源类型（**恒为顶级分类代号**，使 LLM 提示词注入/跨租户共享/检索过滤按 source_type 精确匹配的逻辑零改动）
- `sub_category` — 文档直接所属子分类代号（顶级分类下的文档为 NULL；检索/共享/LLM 均按 source_type=顶级，不受影响）
- `file_type` / `file_path` / `file_size` — 文件信息
- `total_chunks` — 分块数量
- `embedding_model` — 使用的嵌入模型
- `raw_text` — 提取的原始文本
- `summary` — AI 生成的文档摘要
- `uuid` — 带前缀的业务唯一 ID（如 `doc_abc123def456`）
- `origin` — 内容来源（默认 `manual_upload`；外部源如 `wechat_mp`，2026-09-14 公众号入知识库 WP1 新增）
- `external_id` — 外部源规范身份（如公众号 `appid:article_id:item_key`）；`UNIQUE(tenant_id, origin, external_id) WHERE external_id IS NOT NULL` 部分唯一索引防重
- `status` — 生命周期（默认 `active`；外部源软删除置 `deleted`）
- `expires_at` — 时效截止时间（NULL=永不过期；仅限制检索，过期仍可管理查看）

### 5.3 `chunks` — 文本块表

文档经过分块处理后的文本片段，是检索的基本单元。

**关键字段**：
- `doc_id` — 所属文档 ID
- `chunk_index` — 块在文档中的位置序号
- `text` — 分块后的文本内容
- `tokens` — Token 数量
- `uuid` — 带前缀的业务唯一 ID（如 `chunk_abc123def456`）

### 5.4 `chunks_vec` — 向量表

使用 pgvector 扩展存储文本块的向量嵌入，支持 HNSW 索引和余弦相似度搜索。

**关系**：`chunks_vec.chunk_id` → `chunks.id`（1:1）

### 5.5 `chunks_fts` — 全文搜索表

使用 PostgreSQL tsvector 实现全文检索，与向量搜索配合实现混合检索。

**关系**：`chunks_fts.chunk_id` → `chunks.id`（1:1）

---

## 6. 数字员工实例

### 6.1 `agent_instances` — 智能体实例表

租户创建的数字员工实例（如"外贸小明"），是用户实际交互的对象。

**关键字段**：
- `subagent_type` — 子智能体类型
- `instance_name` — 实例名称（用户可见）
- `status` — 运行状态（`idle` / `busy`）
- `current_session_id` / `current_user_id` — 当前锁定会话和用户
- `locked_at` / `lock_expires_at` — 锁定时间，用于并发控制
- `reply_style_id` — 绑定的回复风格 ID
- `bound_channel_type` — 绑定的渠道类型
- `allowed_skills` — 允许使用的技能列表

**关系**：
- `agent_instances.tenant_id` → `tenants.tenant_id`
- `agent_instances.subscription_id` → `subscriptions.subscription_id`
- `agent_instances.reply_style_id` → `reply_styles.style_id`
- `chat_sessions.instance_id` → `agent_instances.instance_id`

### 6.2 `agent_instance_queue` — 实例等待队列表

当数字员工实例正在服务其他用户时，新请求进入此队列等待。

**关键字段**：
- `position` — 队列中的位置
- `status` — 状态（`waiting` / `ready` / `expired` / `cancelled`）
- `wait_timeout_at` — 等待超时时间

**关系**：`agent_instance_queue.instance_id` → `agent_instances.instance_id`

### 6.3 `reply_styles` — 回复风格表

租户级回复风格配置，可绑定到数字员工实例上，控制 AI 的回复语气和风格。

**关键字段**：
- `style_id` — 风格标识
- `content` — 回复风格的提示词内容
- `version` — 版本号
- `is_active` — 是否启用

---

## 7. SaaS 多租户

### 7.1 `tenants` — 租户表

企业租户的基础信息，是整个 SaaS 体系的核心实体。

**关键字段**：
- `plan` — 套餐类型（`basic` / `standard` / `enterprise`）
- `max_instances` — 允许的最大数字员工实例数
- `max_users` — 允许的最大用户数
- `expire_at` — 到期时间（时分秒为 23:59:59，当天仍可登录，空表示永久有效）
- `tenant_code` — 租户短代码，用于 URL 路径

### 7.2 `subscriptions` — 订阅表

租户订阅数字员工服务的记录，包含配额和计费信息。

**关键字段**：
- `subagent_type` — 订阅的子智能体类型
- `instance_quota` — 实例并发配额
- `token_quota` — Token 配额（-1 表示不限制）
- `tokens_used` — 已使用 Token 数
- `status` — 订阅状态
- `payment_status` — 支付状态
- `starts_at` / `expires_at` — 订阅生效和到期时间

**关系**：
- `subscriptions.tenant_id` → `tenants.tenant_id`
- `agent_instances.subscription_id` → `subscriptions.subscription_id`

### 7.3 `payment_orders` — 支付订单表

租户购买订阅时产生的支付记录。

**关系**：
- `payment_orders.tenant_id` → `tenants.tenant_id`
- `payment_orders.subscription_id` → `subscriptions.subscription_id`

### 7.4 `tenant_channel_configs` — 租户渠道配置表

租户在各渠道（企业微信、钉钉、飞书、微信公众号内容等）上的配置信息。

**关键约束**：
- `uq_tenant_channel_configs_wecom_personal_rpa`：wecom_personal_rpa 同租户单例部分唯一索引
- `uq_tenant_channel_configs_wechat_mp_appid`：wechat_mp 同租户同 appid 部分唯一索引
  （`(tenant_id, config->>'appid') WHERE channel_type='wechat_mp'`，公众号内容入知识库 WP4，设计 §4）

**敏感字段**：wecom_personal_rpa / wechat_mp 类型的 config JSON 中敏感字段 Fernet 加密存储
（公共原语 `src/core/secret_crypto.py`，主密钥 settings.app.secret_key → APP_SECRET_KEY → RPA_SECRET_KEY）。

**关系**：`tenant_channel_configs.tenant_id` → `tenants.tenant_id`

### 7.5 `user_agent_permissions` — 用户级数字员工授权表

控制哪些用户可以使用哪些数字员工。

**关系**：
- `user_agent_permissions.user_id` → `users.user_id`
- `user_agent_permissions.tenant_id` → `tenants.tenant_id`

---

## 8. 定时任务

### 8.1 `scheduled_tasks` — 定时任务表

用户配置的定时任务，支持 cron 表达式和固定间隔两种调度方式。

**关键字段**：
- `schedule_type` — 调度类型（`cron` / `interval`）
- `cron_expression` / `interval_seconds` — 调度规则
- `status` — 任务状态（`active` / `paused` / `completed`）
- `total_runs` / `success_count` / `fail_count` — 执行统计
- `next_run_at` — 下次执行时间

**关系**：`scheduled_tasks.user_id` → `users.user_id`

### 8.2 `scheduled_task_logs` — 定时任务执行日志表

每次定时任务执行的详细记录。

**关键字段**：
- `status` — 执行状态
- `trigger_type` — 触发方式
- `duration_ms` / `token_usage` — 资源消耗
- `result_summary` / `result_detail` — 执行结果

**关系**：`scheduled_task_logs.task_id` → `scheduled_tasks.task_id`

---

## 9. Prompt 版本管理

Prompt 版本管理提供系统提示词的版本控制、标签管理和草稿功能。

```
prompt_registry（Prompt 注册表，每个 Prompt 一条）
    ↓ 1:N
prompt_versions（版本记录，不可变，每次提交创建新版本）
    ↓ 1:N
prompt_labels（标签，如 "production" / "staging"，指向特定版本）

prompt_registry（同上）
    ↓ 1:1
prompt_drafts（草稿，每 Prompt 最多一条，未发布的修改）
```

### 9.1 `prompt_registry` — Prompt 注册表

每个被管理的 Prompt 在此注册一条记录，维护基础信息和最新版本号。

**关键字段**：
- `scope` / `scope_id` — Prompt 所属的作用域和具体实体（如 `agent` + `agent_id`）
- `prompt_type` — Prompt 类型
- `latest_version` — 当前最新版本号

### 9.2 `prompt_versions` — Prompt 版本表

存储每个版本的 Prompt 内容，不可变。

**关键字段**：
- `version` — 版本号（递增整数）
- `content` — Prompt 内容
- `variables` — 模板变量（JSONB）
- `model_config` — 模型配置（JSONB）
- `content_hash` — 内容哈希，用于去重和完整性校验
- `parent_version` — 父版本号，用于追踪变更来源

### 9.3 `prompt_labels` — Prompt 标签表

给特定版本打标签，如 `production`、`staging`、`beta` 等。标签是命名指针，可以快速切换。

**关系**：`prompt_labels.prompt_id` → `prompt_registry.id`

### 9.4 `prompt_drafts` — Prompt 草稿表

每 Prompt 最多一条草稿记录，用于保存未发布的修改。

**关键字段**：
- `base_version` — 基于的版本号

### 9.5 `subagent_prompt_sections` — 子智能体 Prompt 分段表

将子智能体的 System Prompt 拆分为多个可独立管理的段落（Phase 3.7）。

**关键字段**：
- `agent_id` — 子智能体 ID
- `section_key` — 段落标识（如 `role_definition`、`rules`、`examples`）
- `content` — 段落内容

**关系**：`subagent_prompt_sections.agent_id` → `subagent_definitions.agent_id`

---

## 10. 子智能体配置

### 10.1 `subagent_definitions` — 子智能体定义表

存储子智能体的元数据定义（Phase 2），包括能力描述、工具列表、技能列表等。

**关键字段**：
- `agent_id` — 子智能体唯一标识
- `triggers` — 触发条件（文件模式、关键词等，JSONB）
- `tools` — 可用工具列表（JSONB）
- `skills` — 可用技能列表（JSONB）
- `delegatable_to` — 可委托的其他子智能体（JSONB）
- `reply_style` — 回复风格
- `business_pages` — 关联的业务页面（JSONB）
- `knowledge_sources` — 关联的知识库来源（JSONB）

### 10.2 `subagent_env_vars` — 子智能体环境变量表

按租户和子智能体隔离的环境变量配置。

**约束**：`tenant_id + subagent_name + var_name` 联合唯一。

### 10.3 `subagent_knowledge_sources` — 子智能体知识库关联表

租户级子智能体与知识库的关联关系（Phase 3.2）。

**关键字段**：
- `sources` — 知识库来源列表（JSONB）

**约束**：`tenant_id + subagent_name` 联合唯一。

---

## 11. 其他系统表

### 11.1 `token_cost_prices` — Token 成本价表

存储各模型的 Token 单价，用于成本核算。

**关键字段**：
- `model_name` — 模型名称
- `input_price_per_m` — 输入 Token 单价（每百万 Token 价格，元）
- `output_price_per_m` — 输出 Token 单价（每百万 Token 价格，元）
- `is_multimodal` — 是否原生多模态（支持图片输入），2026-08-28 新增；TRUE 的模型（当前 kimi-k3、GLM-5.3-Flash、qwen-vl-max、qwen-vl-plus、qwen3-vl-flash）收到用户上传图片可直接进 content 数组原生理解，FALSE 维持先 OCR

### 11.2 `remote_credentials` — 远程连接凭据表

存储 SMB/FTP 等远程连接的凭据配置，供智能体访问外部文件系统。

**关键字段**：
- `connection_type` — 连接类型（`smb` / `ftp` / `sftp` 等）
- `password` — 加密后的密码
- `status` — 凭据状态

**关系**：`remote_credentials.user_id` → `users.user_id`

### 11.3 `user_email_settings` — 用户邮箱配置表

用户个人邮箱的 SMTP/IMAP 配置，用于邮件收发功能。

**关系**：`user_email_settings.user_id` → `users.user_id`（1:1）

### 11.4 `data_connectors` — 数据连接器表

数据分析智能体使用的外部数据库连接配置。

**关键字段**：
- `db_type` — 数据库类型
- `password_encrypted` — 加密后的密码
- `imported_tables` — 已导入的表列表（JSONB）
- `last_sync_at` — 上次同步时间

**关系**：`data_connectors.tenant_id` → `tenants.tenant_id`

### 11.5 社媒运营表

社媒内容运营智能体与聚合平台使用以下租户业务表，均包含 `tenant_id` 并由 API/服务层强制过滤：

- `social_accounts`：平台账号、授权方式、密文凭证和能力声明。
- `social_content_plans`：周发布计划。
- `social_content_items`：计划下的日历条目。
- `social_content_masters`：平台无关内容母版、事实来源和内容 hash。
- `social_media_assets`：素材来源、授权和统一文件存储引用。
- `social_content_asset_links`：母版与素材关系。
- `social_content_variants`：面向具体平台账号的内容版本。
- `social_review_records`：审核记录，绑定 revision 和 content hash。
- `social_publish_jobs`：不可变发布快照、幂等键和发布状态。
- `social_publish_attempts`：每次平台调用或人工交接的脱敏摘要。
- `social_published_contents`：外部内容标识、链接和确认来源。
- `social_metric_snapshots`：原始指标与标准化指标快照。
- `social_data_import_batches`：视频号等人工数据导入批次。

### 11.6 巡检商机表（bs_outbound_*）

巡检商机模块（社媒营销智能体 §7）使用以下租户业务表，遵循 database_dev.md bs_ 规范。
**原文 PII 加密存储**（`encryption_manager`）、**同 tenant 去重指纹 UNIQUE**、**状态机**（new → contacted → qualified|invalid → converted）由应用层 `src/social_media/outbound/` 强制。

> `bs_outbound_account_sessions`（托管登录态加密存储）由登录态子系统负责，本段不覆盖。

### 11.7 桌面 CLI 无人值守自动任务底座（desktop_automation_*，P1-A 2026-09-08；P1 复审 2026-09-09 增补）

桌面 CLI 自动任务底座（docs/design/desktop-automation/desktop-cli-automation-design.md）使用的
12 张系统表 + 1 张本地通道许可表，统一规范：TIMESTAMPTZ / UUID 主键 / 无外键无触发器
（引用完整性在 Python 校验）/ `tenant_id` 一律 NOT NULL；任务族表 `user_id` NOT NULL，
events/outbox/audit/quota 等系统生成行 `user_id` 可 NULL。DDL 三处同步：
`deploy/init-postgres.sql`、`deploy/db_update.yaml`、`src/desktop_automation/init_tables.py`。

- `desktop_automation_subjects`：中立 subject registry（task/revision 复合引用；
  task 行持 `active_revision_ref` 与 `authorization_epoch` 授权快照，UNIQUE(tenant_id, scenario_key, kind, ref)）。
- `desktop_automation_schedules`：时间/事件订阅（interval 锚点、cron(mon..sun)、迟到宽限、
  一次性 `consumed` 留行对账；UNIQUE(tenant_id, scenario_key, revision_ref, trigger_key)）。
- `desktop_automation_event_sources` / `desktop_automation_events`：事件源 schema 与事件接纳
  （payload 只存 ref/hash；UNIQUE(tenant_id, source_id, external_event_id) 去重；
  eligible_revision_refs 快照固化匹配集合）。
- `desktop_automation_occurrences`：触发接纳账本（R11 规范触发键
  time/event/manual，外部 ID 先哈希；UNIQUE(tenant_id, scenario_key, task_ref, trigger_key)，
  INSERT ON CONFLICT DO NOTHING 幂等）。
- `desktop_automation_runs`：执行账本（短事务 lease/fence；§5.4 聚合终态
  succeeded/failed/cancelled/partial/unknown/expired；UNIQUE(tenant_id, occurrence_id)）。
- `desktop_automation_deliveries`：本轮第几条内容（effect=none/applied/unknown +
  phase=prepared/may_have_started/verified/unknown；UNIQUE(tenant_id, run_id, position)；
  不存场景正文/群名，只有 payload_ref/hash）。
- `desktop_automation_attempts`：一次操作尝试（invocation 唯一绑定、request_id 防重、
  predecessor_attempt_id 人工重试链；UNIQUE(invocation_id)）。
- `desktop_automation_evidence`：写后验证证据登记（P1 复审 R27/R28，2026-09-09）——
  applied+verified 落账前经适配器 `validate_evidence`（存在性与归属）+ 绑定交叉核对
  （invocation.device_id/attempt.request_id/delivery.target_ref/delivery.payload_hash）+
  `INSERT ... ON CONFLICT` 仲裁登记；UNIQUE(tenant_id, evidence_ref) 事务级防复用，
  冲突败者比对绑定（同操作幂等放行/他操作拒绝收敛 unknown）；迟到回执（R28）携带
  applied/verified 证据同样持久追加（原始判定不变）。
- `desktop_automation_audit_events`：底座审计（发布/暂停/许可/效果/接纳；只存受控引用/摘要）。
- `desktop_automation_outbox`：可靠投递（确定性 dedupe_key；UNIQUE(tenant_id, kind, dedupe_key)）。
- `desktop_automation_quota_buckets`：额度（scope_type tenant/task/target/account/resource
  固定顺序逐层 FOR UPDATE + 条件 UPDATE 预留；bucket_start 窗口对齐）。
- `local_tool_operation_permits`：写动作短期一次性许可（绑定 invocation/device/claim/
  request_id/target_version/payload_hash/epoch/resource；token 只存 hash；
  quota_reservation 持 R9 预留凭据）。
- `local_tool_invocations` v2 扩列：`provider_key`（NULL=旧聊天链路任何设备可领；非 NULL
  需设备能力含该 provider）、`business_kind`（'desktop_automation'）、`business_ref`、
  `dedupe_key`（部分唯一索引 (tenant_id, business_kind, dedupe_key) 幂等）、`deadline_at`
  TIMESTAMPTZ、`authorization_epoch`、`write_phase`（许可发放后置 may_have_started）。

- `bs_outbound_leads`：商机主表。来源平台/类型、外部内容 ID/URL、原文加密（`raw_text_encrypted`）、意向分、状态、分配销售、去重指纹（同 tenant 部分唯一索引）、接触要点、风险标记。
- `bs_outbound_lead_interactions`：商机互动/跟进记录。互动类型（note/call/email/dm/comment/visit/wechat/other）、内容、跟进人 `actor_user_id`。
- `bs_outbound_outreach_actions`：我方接触动作审计。动作类型（comment/dm/post）、渠道、内容快照、执行状态（默认 `draft`，需人审后推进）、审核人。

### 11.8 微信公众号内容入知识库表（bs_wechat_mp_*，WP1 2026-09-14）

公众号内容入知识库（docs/system/wechat-mp/wechat-mp-knowledge-ingestion-design.md §8 二审定稿）
使用的 4 张租户业务表，遵循 database_dev.md bs_ 规范（无外键无触发器，引用完整性在
Python 校验）。**队列语义**：`sync_runs` + `sync_items` 即执行队列——受理同事务建 queued
run + pending items，worker 按租户串行领取（事务内 queued→running）；`articles` 只维护文章
当前状态；`events` 是回调收件箱（先落库再返回 success）。DDL 三处同步：
`deploy/init-postgres.sql`、`deploy/db_update.yaml`、`src/wechat_mp/db.py`。

- `bs_wechat_mp_articles`：文章唯一当前态（`UNIQUE(tenant_id, external_id)`；
  original_url/fetch_url/external_id 分离；status 含 active/missing/deleted/alias/unconfirmed，
  alias 行经 `master_article_row_id` 指向主记录；`processing_status` + `next_retry_at` 失败退避；
  `doc_id` 关联 documents.id）。
- `bs_wechat_mp_events`：回调事件收件箱（`UNIQUE(tenant_id, config_id, event_key)` 幂等去重；
  pending/done/failed；`run_id` 关联受理批次）。
- `bs_wechat_mp_sync_runs`：同步运行账本兼执行队列（queued/running/success/partial_failed/
  skipped_no_credit/failed/interrupted；trigger_type=callback/scheduled/manual/agent/retry/recheck；
  **租户级 running 部分唯一索引** `uq_wechat_mp_runs_active WHERE status='running'`，与 Redis 锁
  同粒度；queued 不限条数可排队；owner_token/heartbeat_at 支撑 stale 回收）。
- `bs_wechat_mp_sync_items`：批次内逐篇任务（`UNIQUE(tenant_id, run_id, article_row_id)`；
  action=new/update/delete/restore/check；同批次别名重复项 status='skipped' 且
  `duplicate_of_item_id` 关联主 item；billing_status=pending/charged/failed/unknown/not_required）。

---

## 12. 核心表关系图

```
tenants（租户）
├── users（用户） ────┬── tokens（认证Token）
│                     ├── sms_codes（验证码）
│                     ├── remote_credentials（远程凭据）
│                     └── user_email_settings（邮箱配置）
│
├── subscriptions（订阅） ────┬── payment_orders（支付订单）
│                              └── agent_instances（数字员工实例）
│                                   └── chat_sessions（网页会话，用户可创建多个）
│                                        └── chat_messages（网页消息）
│                                   └── chat_records（计费记录，不随会话删除）  ← 独立存储
│
├── agent_instance_queue（实例队列） ──→ agent_instances
├── reply_styles（回复风格） ──────────→ agent_instances
├── tenant_channel_configs（渠道配置）
├── user_agent_permissions（用户授权）
│
├── channel_sessions（渠道会话，按 channel_chat_id 可多会话） ──→ channel_messages（渠道消息）
│                                                 └── chat_records（计费记录，同上）  ← 独立存储
│
├── knowledge_categories（知识分类） ──→ documents（文档）
│                                            └── chunks（文本块）
│                                                 ├── chunks_vec（向量）
│                                                 └── chunks_fts（全文搜索）
│
├── scheduled_tasks（定时任务） ───────→ scheduled_task_logs（任务日志）
├── data_connectors（数据连接器）
├── social_accounts（社媒平台账号） ──────┬── social_content_variants（平台内容版本）
│                                        ├── social_publish_jobs（发布任务）
│                                        └── social_metric_snapshots（指标快照）
├── social_content_plans（社媒内容计划） ─→ social_content_items（日历条目）
├── social_content_masters（内容母版） ───┬→ social_content_variants（平台内容版本）
│                                        └→ social_content_asset_links → social_media_assets（素材）
├── social_review_records（社媒审核记录）
├── social_publish_attempts（发布尝试）
├── social_published_contents（已发布内容）
├── social_data_import_batches（数据导入批次）
├── subagent_env_vars（环境变量）
├── subagent_knowledge_sources（知识库关联）
│
├── prompt_registry（Prompt注册表）
│    ├── prompt_versions（版本）
│    ├── prompt_labels（标签）
│    └── prompt_drafts（草稿）
│
└── subagent_definitions（子智能体定义）
     └── subagent_prompt_sections（Prompt分段）

全局表（不归属租户）：
├── token_cost_prices（Token单价）
├── log_error（错误日志）
```

### C3 决策调用恢复（2026-09-15）

`session_task_decision_attempts` 以 `(tenant_id, attempt_ref)` 唯一关联调用。`result_text_id` 引用既有 `session_task_texts` 中 purpose=decision 的加密模型结果，与 usage/model/user 和槽位释放同事务写入；崩溃重领不重新调用模型。`credit_cost` 通过 CAS 冻结，账务唯一键及预留结算复用同一金额。`billing_retry_at`、`billing_retry_count` 提供逐 attempt 退避和公平扫描。`reservation_missing` 表示账务已确认但缺少预留，保留重试与人工核对；仅账务及预留均确认才进入 `settled`。

### C4 会话任务站内通知

`session_task_notifications`：任务状态变化的站内通知；`tenant_id/task_id/user_id` 限定归属，`(tenant_id,task_id,control_epoch)` 唯一去重，状态迁移事务中写入。仅 completed/stopped/human_required/blocked 写入，等待状态不刷屏，不外发。只存状态与原因码，不存消息正文；租户+任务外键级联清理。

### B1.2 会话任务通用控制请求表（2026-09-18）

`session_task_control_requests`：human_required **异步控制迁移**队列（BOSS 端侧接入 B1.2，设计 §5.5.5 冻结 schema；DDL 四处同步 `deploy/init-postgres.sql`、`deploy/db_update.yaml` 2026-09-17 20:30:00 块、`src/session_tasks/init_tables.py`、`tests/unit/session_tasks/test_migrations.py`）。`(tenant_id, task_id, expected_control_epoch, expected_block_epoch, reason)` 唯一幂等（CR 三审 P1-1：block epoch 纳入幂等键——同代同原因、不同阻断代的请求各自成行，旧代请求按 epoch 复核自然 stale；唯一索引同时服务 pending 检查的廉价查询）；`expected_control_epoch/expected_block_epoch` 校验不符置 stale，不得覆盖较新阻断；`processing_owner/processing_lease_expires_at` 支撑 `FOR UPDATE SKIP LOCKED` 认领与崩溃重领（5s tick，`src/session_tasks/control_requests.py`）；`retry_count` 限次退避，10 次后 `failed` + 脱敏审计告警。**只负责异步迁移，不是同步发送门禁**；processing/failed 均保持场景 binding 的 `automation_blocked=true`，本表无任何自动解阻路径（人工 owner-only unblock 属 B4）。`source_type` ∈ {permit_denied, rate_settlement}；`source_ref` 仅存受控 delivery/invocation 引用，不落敏感正文。

### B2 BOSS 端侧会话场景表（2026-09-18，bs_ 业务表例外登记）

BOSS 直聘端侧会话（`boss.chat_reply.v1`，设计 §5；DDL 四处同步 `deploy/init-postgres.sql`、`deploy/db_update.yaml` 2026-09-18 23:00:00 块、`src/boss_conversation/init_tables.py`、`tests/unit/session_tasks/test_migrations.py`；均含 tenant_id/user_id/created_at 规范列、无外键/触发器，引用完整性在 Python 层）：

- `bs_boss_conversation_bindings`：候选人绑定。`verification_status` ∈ {pending, verified, invalid, expired}（verified 仅接纳 Provider 真机双锚唯一命中证据）；`login_fingerprint_hash` 只存 HMAC-SHA256 摘要；频控触发计数列（`rate_trigger_date`/`rate_trigger_count`/`last_rate_decision_id`，跨日按 Asia/Shanghai 原子重置并清 last_rate_decision_id）；同步阻断列（`automation_blocked`/`automation_block_reason`/`automation_block_epoch`/`automation_blocked_at`，prepare-send 与 write-authorize 在 `binding FOR UPDATE` 后的硬门禁，仅人工 owner-only unblock（CAS expected_block_epoch）可清除且 epoch 再递增）。部分唯一索引 `uq_boss_conv_bindings_verified` 保证同租户同设备同 candidate_name+job_id 仅一条 verified 有效绑定。
- `bs_boss_reply_script_versions`：不可变话术版本（append-only，仅场景 API 写入）；`(tenant_id, lineage_id, version_no)` 唯一；`content_hash` = 模板规范化字节 sha256；`source_script_id/source_job_id` 为历史来源引用无 FK；spec 冻结副本（script_version_id+content_hash+frozen_template）纵深防御在授权复判与渲染不变量校验。
- `bs_boss_conversation_rate_slots`：频控账本（设计 §5.5.4 冻结 DDL）。正常路径仅在预留成功时创建 reserved 行（write-authorize 复判通过、许可事务内 `reserved_at=NOW()`）；结算 reserved→settled（submitted/verified/unknown）/released（not_started）；全部窗口以 `reserved_at` 为发送发生时刻；`(tenant_id, delivery_id)`、`(tenant_id, decision_id)` 双唯一；CHECK 冻结状态机；部分窗口索引 `idx_boss_rate_windows` 服务 60s/10min/日三窗口查询。
- `bs_boss_rate_settlement_anomalies`：结算异常队列（只存受控错误码 `rate_slot_missing|rate_slot_state_conflict|settlement_failed` 等，不落敏感详情）；`(tenant_id, delivery_id)` 唯一幂等；slot 缺失补建成功仍登记。
- `bs_boss_comm_log_projection_queue`：沟通日志投影队列（发送 verified 或 unknown 人工判定后入队；后台 job 幂等 upsert 到 `bs_recruiting_operator_resume_comm_logs`，按 `binding.resume_id` 关联不按姓名匹配；失败退避重试仅补投影）；`(tenant_id, delivery_id)` 唯一防双写。
- `bs_recruiting_operator_resume_comm_logs` ALTER 补列 `source_delivery_id UUID NULL`、`source_message_id TEXT NULL` + 唯一索引 `uq_boss_comm_logs_source_delivery (tenant_id, source_delivery_id)`（NULL 不判重；目标表由 recruiting 模块自建，ALTER 幂等且表不存在时条件跳过）。


## AgentRunner 独立运行服务（2026-10-01）

M2 系统表：`agent_runners`、`agent_runner_session_claims`、`agent_runner_usage_receipts`。
DDL 同步维护于 `deploy/init-postgres.sql` 与 `deploy/db_update.yaml`
（`2026-10-01 20:45:58`）；服务启动只检查 schema，不自动迁移或启动渠道/调度。
开发计划：[AgentRunner](../plans/plan-agent-runner-service.md)。公开订阅 events 属 M5，
不与计费 receipts 共表。本节记录实际 schema，worker/计费状态能力以计划阶段验收为准。

| 表 | 字段与职责 |
|---|---|
| `agent_runners` | `runner_id` 主键；`queue_order` 持久队列顺序；真实 nullable `tenant_id`、内部 `scope_key`、`session_kind/session_id`、`actor_kind/actor_id/user_id/service_id/source`；`client_request_id/input_digest/input` 保存不可变意图；`profile_id/profile_fingerprint` 分开保存解析配置；`checkpoint/checkpoint_version` 为私有恢复事实；`status` 执行状态与 `settlement_status` 费用状态分开；`attempt/worker_id/lease_until` 执行权；`revision` 恢复点 CAS、`view_revision` 公开变化、`control_revision/cancel_requested` 控制请求；`public_snapshot/result` 可见投影；`event_seq` 永不回退的通知 head、`event_floor_seq` 已清理连续前缀末序（历史默认均为 0）；唯一稳定 `record_id`；`accepted_at/updated_at/finished_at` |
| `agent_runner_events` | M5 公开通知系统表，主键 `(runner_id,seq)`；`tenant_id/scope_key` 与原 root 精确归属；`kind=created/revision_changed/terminal/settlement_changed`、`payload` 仅安全 invalidate 与版本/attempt/公开状态水位、`created_at TIMESTAMPTZ`；不复制累计回复、私有模型/收据/画面；created/terminal 保留期唯一索引，长期一次事实仍由原接单 winner/Finalizer 状态控制 |
| `agent_runner_session_claims` | 主键 `(scope_key,session_kind,session_id)`，`tenant_id` 与 scope 对应；唯一 `owner_runner_id`、`revision`、`gate=execution/delivery`、创建/更新时间；表达会话逻辑位置，不是 worker lease，不按 TTL 释放 paused/waiting/interrupted |
| `agent_runner_usage_receipts` | `receipt_id` 主键，唯一 `(runner_id,call_id)`；`tenant_id/scope_key/execution_id/tool_call_id/authorized_attempt` 归属；`owner/purpose/billing_boundary` 费用责任；真实 `provider/model/provider_request_id`、`phase=started/observed/unknown/no_usage`、原始 `usage/price_snapshot/fact_digest`；`applied/external_owner_id/record_id` 结算水位及外部台账引用；创建/观测/应用时间 |

M5 事件基础块同步于双 DDL `2026-10-02 17:38:36`，单原子 DO 核字段、关键索引、
已验证 CHECK 与零默认值；不回填旧事件。通知只在原 root 锁下最终 commit 尾点与公开
变化同事务提交，私有 checkpoint、heartbeat 或单独 view_revision 变化不产生事件。
仅输出变化按 head 的 DB 时间窗 250ms 合并，重要状态/控制/财务/卡片变化和 terminal
即时通知；查询 snapshot 始终权威。短一致 RR 页面携 head/floor 与实际 paged last_seq，
低于 floor、未来 cursor 或序列缺口返回 reset，不从剩余 events 的 MAX 重建水位。
有界维护入口 `python -m src.services.agent_runner.event_maintenance --keep-last 1000 --roots 16
--delete-limit 1000 --after 0` 每次只删连续前缀并返回 next_cursor（0 表示可环回），不自动
启动 cron；删除全部后 head 仍保留。事件不授派发/人控权，也不是渠道送达证明；SSE
及客户端接线另属后续片。

真实 `tenant_id` 允许 NULL，仅已验证 platform_admin 自己的全局 Web 会话可使用；
渠道必须 tenant-bound。`scope_key` 由服务内部导出：NULL 对应 `global`，有 tenant 对应
`tenant:<tenant_id>`，CHECK 防止二者漂移。它不含 session，不是假 tenant，不接受客户端提交。
接单幂等 UNIQUE `(scope_key,actor_kind,actor_id,source,client_request_id)`，同键换会话仍是
输入冲突；会话另在意图摘要与 claim 中表达。所有 NULL tenant 查询使用
`IS NOT DISTINCT FROM`，不代表跨租户全局查询。

Web 输入接单时只写 runner；结束事务才写 `chat_messages`，稳定 `runner_id:user` 投影
用于刷新去重，当前 runner 不会读到未来 queued 输入。渠道始终读写 `channel_*`；
费用审计共用 `chat_records`。M2a queued cancel 不创造聊天、收费记录或余额变动。
运行中 cancel 仅增加控制/公开版本，不增加 checkpoint revision；worker 仍须实际派发前
核对控制与 attempt/lease。M2 三表无外键、触发器或自动清理业务资源；M4 controls 曾以
SQL 外键关联原 Runner，2026-10-04 起撤除外键级联，引用完整性由应用层同事务检查。

### M4 控制命令（2026-10-02）

`agent_runner_controls` 是私有系统表，记录控制意图，不承担公开事件订阅或计费。

| 表/字段 | 用途和约束 |
|---------|------------|
| `agent_runners.pause_requested` | 用户暂停请求，与已安全停稳的 `status=paused`、内部停机/失租标记分别表示；默认 false |
| `agent_runners.resume_control_id` | 原 claim 上等待专用领取的控制 ID；不是新的 queued Runner，不改变原 queue_order |
| `agent_runner_controls` | `control_id` 主键；`runner_id` 关联原 Runner（无 SQL 外键，存在性与归属由应用层同事务检查）；`tenant_id/scope_key` 同原归属，合法 global 保持 NULL；`action` 为 pause/resume/reply/browser_complete；唯一 `(runner_id,client_request_id)`，`intent_digest` 包含动作、目标 execution/wait、回答/附件/完成引用；`payload` 私有，不在 GET/list 公开；`status=accepted/claimed/consumed/rejected`、稳定 `error_code`、`consumed_attempt`、接受/消费时间 |

先验证当前主体及原 Runner 归属，再查命令幂等事实，然后判断当前状态是否可操作。
同键换动作或参数返回冲突；同键重试可读取原消费结果。暂停保留会话 claim；
恢复必须原 claim、新 attempt/lease 和消费后的完整 checkpoint 在同一事务提交，
已提交的 finalizing 结果不被后续暂停、恢复或取消命令覆盖。公开只投影命令 ID、动作和状态，
回答、附件内容、子执行私有状态和完成事实引用不作为公开命令 payload。


## M6a 微信客服 received 事实基础（2026-10-03）

三表为渠道执行协调系统表，部署 schema 同步于 `deploy/init-postgres.sql` 与
`deploy/db_update.yaml` 的 2026-10-03 12:00:00 单语句原子 DO。所有归属字段必须完整，
可空 SaaS `user_id` 保持原渠道主体语义；引用完整性由同 cursor 的 Python 检查维护，不加 SQL 外键。

| 表 | 身份与关键字段 | 用途 |
|---|---|---|
| `wecom_kf_account_sync` | `account_id` PK；完整 tenant/config/corp/open 唯一；自增 `account_order`；profile/raw selector；config_version；requested/completed_generation；cursor；worker_id/claim_epoch/lease_until；verification_code | callback 已提交拉取意图才 ACK；有界 keyset 领取原账号，网络请求不持 SQL 锁；锁后数据库时钟、epoch、expected cursor 强校验 |
| `channel_session_routes` | `route_id` PK；tenant/source/config/corp/open/actor/chat_kind/chat_id/profile 唯一；raw_profile；session_id/user_id；legacy_shared/config_version | 固定当前配置的完整路由到原渠道 SID，原空 selector 与 canonical main 等价；首次绑定存在两个旧候选时拒绝；不任意新建来掩盖缺失历史 |
| `wecom_kf_inbox` | `(account_id,namespace,message_id)` PK；完整原账号/actor/route；origin/message_type/send_time；payload/payload_digest；私有 capability_ciphertext；config_version；state=received/received_at | 有界白名单消息与生命周期事实，page 全部 inbox/route/session 与 next cursor 同事务；无 Agent、Runner、发送或计费 |

新 lease/audit 时刻为 TIMESTAMPTZ；`config_version` 保持原配置 `updated_at` TIMESTAMP。
版本是本次 IO fence：配置变化后旧 owner 不可提交。仅欢迎文案等非路由修改或空/main
默认 selector 等价变更，下一 fresh owner 可领取同一未完成 generation，保原 cursor；
旧 inbox/route 的版本、raw selector、SID 不改。真实 corp/account/profile 差异或账号不可用
保意图并记录安全 verification_code。内部 `read_received` 复验既有 route/session，不创建绑定。

同步消息保 provider msgid；直接 encrypted callback 的 lifecycle 使用独立 `callback`
namespace，按原安全类型字段、CreateTime 与必要 grant digest 定义 receipt 幂等边界，
不冒充用户 msgid。未知 msgtype 仅存有界 unsupported 类型事实，避免阻断后续整页。
enter_session 的 scene、状态/员工字段保留；Code/welcome_code 仅以既有 `secret_crypto`
Fernet 加密存私有 capability 字段，主密钥缺失明确拒绝，明文不入公共 payload/日志/CP。
截图、鉴权 XML/query、callback Token、corp secret/access_token 均不入这三表。
当前原 SDK sync_msg 未使用 callback Token，其平台合同待后片核定；不据猜测新建凭据平台。

`agent_runner.wecom_kf.enabled` 为服务端 typed default false，环境覆盖
`AGENT_RUNNER_WECOM_KF_ENABLED` 仅接受 true/false。启用时 KF POST 在旧 XML/调试日志前
分流：真实四元素密文签名与 decrypt receiverCorp/current config 验证后短 PG 提交；
不接受 plain XML 为 durable proof。其他渠道及旧关闭分支不改，不声明全部 KF 已迁移。
显式 worker `python -m src.channels.wecom_kf.ingress_worker --max-pages N` 只保存 received。
原 SDK native HTTP 模式在 JSON 解码前限制字节，日志仅安全 code/type；实际 HTTP 与
线程归还后才释放 lease。当前每页短生命周期 SDK owner，页内 token/cache 复用，跨页
不复用，account_list+sync_msg+首次 gettoken 开销留 M7 性能核定，不宣称生产容量。

迁移校验全部实际列类型/NULL、关键 btree 索引键/唯一/有效性、CHECK 的 relation
绑定 deparse 与 validated、默认值和 account_order 的自增来源。不兼容整块回滚，不推进
迁移水位、不清历史/不静默重建错误结构。无自动部署或默认切换。

KF 仓储仅在自身短事务使用 LOCAL statement_timeout=5s / lock_timeout=3s，
page 每条 inbox 写入前后与提交前均独立重核 lease/epoch/cursor。原 pool checkout/connect
策略不改；此为单次 SQL 界限，不承诺所有线程清理或整个页事务在 5 秒内完成。


### M6a text source input facts（2026-10-03）

- `agent_runner_inputs` 是系统表，租户与完整执行 route 保存在服务器核实的 `provenance`，不使用 SQL 外键。`input_ref` 和唯一 `source_key` 对应原平台 receipt，`intent`/`intent_digest` 为不可变规范输入；不是正文去重。
- `ordinal` 为全局稳定接受顺序；`receipt_seq` 是原 account 已提交的接收顺序。`accepted_runner_id` 不变，`current_runner_id`/`deferred_to_runner` 仅在原 root/claim/cutoff 同事务内关联下一 queued Runner。旧 delivery claim 继续阻止下一执行。
- `phase`：`accepted` 未挂入 CP，`attached` 已在 CP followup 且未进 messages，`appended` 已进原 messages，`applied` 已在原 Finalizer 同事务写稳定 `input_ref:user` 历史，`deferred` 原预算/cutoff 转接，`cancelled` 未用输入随原取消闭合。`applied` 不代替 model/usage 物理调用事实。`attached_revision` 对应实际 CP CAS，不能把仅接受升级为已执行。
- 原 `wecom_kf_account_sync.inbox_seq` 从 0 计新 receipt；`wecom_kf_inbox.receive_seq` 可 NULL，旧行不伪造 account 顺序。`receipt_order` 供渠道有限扫描，`accepted_input_ref` 可 NULL，由受信 Runner service 在接受/相同 key 恢复事务内核原 receipt 后写；渠道 consumer 不读 Runner 私有 input 表。
- 首门仅当前明确 service_state=1 的 origin=3 sync text。state 未知、生命周期、语音、员工/人工消息及 parked clarification 仍 received；不同完整 route 可同 SID 排队，但不追加原 Runner。2 秒前置合并、精确人工期/clarification、语音与发送仍是完整迁移前门，不默认启用。
- 同事务锁序为候选 Runner root → 原 claim → 当前配置/来源 receipt → input；网络状态读在 SQL 事务外，实际响应时间及当前配置版本在最终派发 cursor 复核。输入队列上限 128，满时原 receipt 保留而不接新输入。仅完成 cutoff/确实 iteration limit 可以转接未进入原 messages 的后续 input；未知或前置失败保事实等待核对。
- canonical 迁移 `2026-10-03 13:00:00` 与初始化脚本使用相同原子块，核字段类型/NULL/default、独占序列、有效唯一索引与 validated CHECK。来源 receipt 查询不回填旧未知顺序。


### wecom_kf_input_preparations — 已接受 AI 语音的私有准备事实

仅原生客服 AI 客户语音使用。`input_ref + operation_version` 唯一，稳定 `preparation_ref` 与物理调用 ID 来源于该引用；不使用音频正文摘要作为接单键。系统表无 SQL 外键；原 root/claim 与来源绑定由同事务仓储校验。

| 字段 | 类型 | 约束/用途 |
|---|---|---|
| preparation_ref | TEXT | 主键，稳定准备引用 |
| input_ref / operation_version | TEXT / INTEGER | 唯一组合，版本正整数 |
| tenant_id / intent_digest / provenance / media_id | TEXT / TEXT / JSONB / TEXT | 原租户、不可变输入与完整来源证明；不含凭据 |
| phase | TEXT | media_ready / started / known / unknown；started/unknown 不授权再次 POST |
| artifact | JSONB，允许 NULL | 租户有界音频 artifact、格式、大小和 sha256；无 base64 |
| transcript / success / result_kind | TEXT / BOOLEAN / TEXT | ≤32KiB 可信文字；recognition/preflight/provider；未知不伪装失败文字 |
| fee_owner_runner_id / authorized_attempt / authorized_worker_id | TEXT / INTEGER / TEXT，允许 NULL | 付费开始后固定原 owner/Attempt；输入转交不迁移费用 |
| physical_call_id / receipt_id / provider_status | TEXT / TEXT / BIGINT，允许 NULL | 原物理调用/唯一 Usage receipt 与明确 provider 状态 |
| error_code | TEXT，允许 NULL | 有限稳定未知代码，不保存异常正文 |
| io_config_version / dispatch_observed_at | TIMESTAMPTZ，允许 NULL | 原实际 POST 的配置 fence 版本与状态观察时点 |
| created_at / started_at / observed_at | TIMESTAMPTZ | 创建默认 DBclock；物理开始/已知结果时间 |

付费 POST 前准备 `started` 与原 `asr/main/aliyun/aliyun-nls-asr` receipt 同 cursor 提交；价格在事务外冻结。可信文字和 Usage 观察同事务，晚到结果仍归原授权 Attempt；取消或失租不取消已派发的结果保存。识别文字只以局部投影提供模型/历史/record，不改变提交 intent/hash。未开始、已知失败的准备可为零收费；未知保留原 claim 与待核对费用。原页面可信 recognition 可零 ASR，旧 inbox 摘要不回填。媒体下载/固定 SILK 子进程均实际有界排水。人工/员工语音、发送和预合并尚未迁移，默认开关保持关闭。

### 微信客服 Context13：固定分类与普通文本语境

`wecom_kf_receipt_classifications` 以 `(account_id, namespace, message_id)` 唯一保存原 inbox digest、完整 route scope、首次可靠 SDK 状态及原观察时间/配置版本；类别为 `ai/human/ended/employee/unknown/event`。客户暂时网络/格式失败不插入分类，允许后续首次可靠观察；员工 `origin=5` 可凭原可信 receipt/full route 固定 `employee`，SDK 不是员工身份前置。没有真实 SDK 观察时 `observed_state/observed_at/io_config_version` 三字段均为 NULL；首次回合的可选可靠观察可补一次，失败仍落员工历史，已固定重试不重新取状态。已有可靠非 AI 分类不因当前状态变为 1 而改属。`classification_resolved` 只表示可信事件的分类影响已解释，`business_pending` 与欢迎、注册、发送效果分离。已有 `accepted_input_ref` 的 Text/Voice 不伪补接待时期、不改原 intent/provenance/digest。

`wecom_kf_context_consumptions` 同 receipt 保存稳定公共 history ID、精确 recall target 及 disposition。首次可靠分类先独立短事务固定；普通客户/员工文本的 history、context 消费、recap 意图在其后一个事务中原子提交，投影失败不撤销已固定分类；客户保留 `customer_human/customer_ended`，员工保留 `source=servicer` 与 `[人工客服] `，NULL user/原 SID 不改。撤回只匹配原完整 tenant/config/corp/account/actor/chat/profile/user/route 和 sync msgid；已有稳定历史逐字段核合法客户/员工展示 source，实际 UPDATE 命中才确认历史撤回，真正尚无历史仍保留先到撤回事实。未到目标为 `pending_target`；已接受目标仅保撤回证明，不删除 Runtime 消息或模型/工具/费用事实。`pending_history` 表示更早接受输入的当前公开执行状态尚不能证明结束，不从历史行或私有 Runner 表推断。

本域 `source_terminal` 是独立完成观察，不是 context 消费或 SDK 分类。唯一 Runner 服务受信 `GET /v1/source-inputs` 要求 source/account_id/namespace/message_id 恰各一次、无多余 query；在短一致只读事务中返回原 input/current Runner 的小公开状态；首次写回需原 observed_at 距 DBclock 0..10 秒、full route/inbox accepted link/digest 一致。仅不可逆 terminal 且原 input applied/cancelled 的绑定可缓存到 `accepted_input_ref/completion_observation`；不要求财务 settled，不当投递成功。每次只查询最多 4 个未缓存较早接受事实；未查完、状态非终态、查询失败或绑定变化均保留待核对。

`wecom_kf_context_task_intents` 保存原完整 route/receipt/history 与 operation_version=1，原任务名仅 `lead_refresh/external_push_human`，状态仅 `pending_adapter`；客户固定 3/4 或员工可靠 3/4 才生成。未调用旧 void adapter/Redis 队列，不代表 recap 效果完成。

三表采用 TIMESTAMPTZ 事实时间、原配置版本 TIMESTAMP、无 SQL 外键；canonical 双 DDL 原子创建与严格 catalog 校验，主/唯一键分别按各 relation 的键序映射列名比较，不跨表比较原始 attnum。锁序为 cfg→route/session→inbox→本域事实，网络/名字查找/服务观察均锁外；本片不增加员工姓名 SDK 读口。由既有 admission worker 有界组合，KF 新接单开关关闭时仍可排水固定 context，CLI `--max-inputs` 只计 AI 服务接受，不计历史/待执行 recap。人工 Voice、媒体、recap 实际执行、欢迎/归因、2 秒合并及投递仍属后续片。


### wecom_kf_context_voice_preparations — 人工/员工语音的独立识别 owner

每个完整 KF receipt/route/payload digest/operation_version=1 一个稳定 `operation_ref`，与 AI `wecom_kf_input_preparations`、Runner Attempt 和费用 receipts 分开。客户须已固定 human/ended，员工须可信 origin5/employee；`accepted_input_ref` 非空仍由原 AI 链处理。当前配置/既存 NULL-user SID 与完整来源复核用于首次媒体与识别，不新增 AI 执行权限、余额或价格必填拦截。

`phase` 为 media_ready/started/unknown/known；`authorized_epoch` 只在原首次 start 增加一次，started/unknown 不再取得第二次 POST 许可。固定 artifact/hash、冻结 price_snapshot、原 config_version、started/lease 时点及稳定 `record_id` 属于原物理调用，迟到返回按该原授权事实保存，不重新检查新派发权限。`cost=NULL/finance_pending=true` 表示未知费用；unknown 不创建 completed/credit0 的 chat_record。只有 known ASR 返回才同事务存结果、原独立 `wecom_kf_human_asr` record（aliyun/aliyun-nls-asr）与实际差额 debit；成功 calls1，明确失败 calls0。缺价/零价按原 billing 产生已知0费用，区别于 unknown。Recognition/确定零POST本地失败不产生付费 record。

`history_projection` pending/projected/cleared/recalled 跟原稳定 channel_messages ID 独立。`wecom_kf_context_consumptions.disposition=pending_asr` 的可见 `[语音消息]`/`[人工客服] [语音消息]` 带完整 operation/version、pending_asr/asr_phase，不是消费 done；unknown 不授 recap意图。每次历史投影前仍用原 SourceGET ordering 与最终短事务候选复核；排序等待不丢已知费用或重新 ASR。可信 known 可 CAS 原精确 pending 内容/metadata/attachments，明确 fullscope 冲突拒绝；合法 clear 或 recalled 不复造旧历史、不清除撤回标记、不产生新 effect。known 正常历史消费/原真实3或4资格的 recap `pending_adapter` 意图同事务，未执行 recap。

一条唯一键 `(account_id,namespace,message_id,operation_version)` 与独立唯一 record_id 防重复 owner；无 SQL FK、无自动重发/任务平台、无第四方回调凭证存储。当前只语音准备、历史与独立计费，不包含任意媒体下载链接、欢迎/注册/发送或通用 effect 调度。

### KF 完整收发事实（2026-10-03 17:00:00）

原双 DDL 同一原子 DO 增加以下 system 表，无 SQL FK；应用在原来源与会话锁下核完整租户/配置/企业/账号/actor/profile/SID。

| 表 | 有限事实 | 唯一键 |
|---|---|---|
| wecom_kf_input_batches | 固定两秒接收窗与 sealed/accepted 清单 | batch_ref |
| wecom_kf_input_batch_members | 原 receipt、固定成员顺序、不可改 digest | batch_ref/ordinal；account/namespace/message |
| agent_runner_input_batch_members | 单 Runner 模型消息与成员原 intent 的关联 | input_ref；batch_ref/ordinal |
| wecom_kf_deliveries | 捕获公开 presentation、原路由、收尾封口 | delivery_id；input_ref/runner/presentation_digest |
| wecom_kf_wire_operations | 客户物理 POST 的 started/ACK/reject/unknown/零派发事实 | operation_ref；delivery_id/ordinal |
| wecom_kf_business_facts | 原注册、first-touch 与后台薄交接事实 | business_ref；account/namespace/message/kind |

原 context_task_intents 扩展为 pending_adapter/claimed/started/dispatch_returned/unknown/suppressed；claimed 可按本域短 lease 恢复，started/unknown 不重新调用整项后台业务。dispatch_returned 仅为原 void adapter 返回，不证明留资或外推完成。历史人工任务保原 NULL user 财务角色。SDK 真平台响应与合成错误严格分开，未知客户写不重 POST；收尾由服务核原公开终态和领域已封完整表示证明。重新引用旧 ACK 不再次消耗实际五条预算。

2026-10-04 00:03:10：delivery 的 `closed_outcome` 增加 `closed_unknown`。原 wire 操作仍为 unknown、无平台 ACK/成功结果；仅本 owner 的实际 HTTP/adapter 已强排水，持久 `proof.transport_drained`、原 delivery/epoch 同一，且服务执行终态、无未完成派发时，由原服务事务关闭本轮并释放会话 claim，让下一客户消息继续。覆盖同 Runner 所有旧 presentation 的未知操作；无真实排水证明或仍 started 的操作继续保留待核对，租约超时不证明传输结束。关闭未知不授成功 recap，不重发旧正文/资产，不改原费用、输入或历史；迟到真实结果仍可补原操作事实。双 DDL 只允许原精确 CHECK 形状向新枚举升级，并支持完整 updater 回放。

客户回复真实子澄清只核原当前唯一 clarification leaf、可信来源与控制 revision，不要求先持有问题发送 ACK；展示从当前嵌套 child_wait 取原问题，不从累计旧问题列表选择。

AgentRunner 来源输入的 `source_control_id` 为服务内部澄清回复控制的可空唯一锚关联。批的所有成员保留原意图，只有锚关联控制；派发、历史和幂等按原批关系与稳定输入引用确认，不接受客户端 proof。
