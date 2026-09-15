# 系统核心表用途与关系

> 本文档介绍智能体系统核心数据表的用途和相互关系，仅覆盖非 `bs_` 前缀的系统表。
> `bs_` 开头的业务表由各子智能体自行管理，不在此文档范围内。

> 2026-07-17 Browser Run/Executor Phase 2 例外登记：新增业务审计表
> `bs_browser_runs`、`bs_browser_assistance_requests`。两表只保存租户归属、
> 状态枚举和恢复关联，不保存完整 URL、DOM、截图、cookie、header、表单值或
> 用户输入；所有读取和更新均要求 `tenant_id` 条件。两表 DDL 已同步
> `deploy/init-postgres.sql` 与 `deploy/db_update.sql`，不创建长期 device 表。
>
> 2026-07-22 Phase 3R 已新增 `bs_browser_resume_jobs` 持久 lease 队列，替代
> Redis Stream。字段仅包括任务/租户/assistance/run 标识、pending/processing/
> completed/failed 状态、lease、重试调度、白名单错误码和时间戳；
> `assistance_id` 唯一保证幂等入队。worker 用 `FOR UPDATE SKIP LOCKED` 领取，
> lease 过期可回收。DDL 已同步 `deploy/init-postgres.sql` 与 `deploy/db_update.sql`，
> Python service 为 `src/tools/browser/run_db.py` 的 `BrowserResumeJobDB`。
> 遵循 `database_dev.md` 不加外键约束。

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

租户在各渠道（企业微信、钉钉、飞书）上的配置信息。

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
