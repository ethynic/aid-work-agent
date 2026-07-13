# 系统缓存使用情况

> 本文档介绍智能体系统中所有缓存的使用情况，包括 Redis 缓存、内存缓存和数据库缓存。
> 了解缓存分布有助于排查数据不一致、内存泄漏和性能问题。

---

## 1. 缓存基础设施

### 1.1 Redis 客户端（带内存降级）

系统使用统一的 `redis_client` 单例（`src/core/redis_client.py`），所有缓存操作都通过它进行。

**核心特性**：
- 自动降级：Redis 不可用时，透明降级到线程安全的内存字典（`_InMemoryFallback`）
- 统一序列化：所有值自动 JSON 序列化/反序列化
- 键前缀隔离：通过 `REDIS_KEY_PREFIX`（默认 `aid-local`）实现多实例共享 Redis 而不冲突

**键构造规则**：
```
{key_prefix}:{cache_prefix}:{identifier}
```

例如：`aid-local:token:abc123`、`aid-local:user:user_456`

**配置**（`configs/config.yaml` / `.env`）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `redis.enabled` | `false` | 是否启用 Redis |
| `redis.host` | `localhost` | Redis 主机 |
| `redis.port` | `6379` | Redis 端口 |
| `redis.db` | `0` | Redis 数据库编号 |
| `REDIS_KEY_PREFIX` | `aid-local` | 键前缀，多实例隔离用 |

### 1.2 缓存工具函数

`src/core/cache_utils.py` 提供统一的缓存操作接口：

| 函数 | 用途 |
|------|------|
| `get_cached(prefix, identifier)` | 获取缓存 |
| `set_cached(prefix, identifier, value, ttl)` | 设置缓存（带 TTL） |
| `delete_cached(prefix, identifier)` | 删除缓存 |
| `delete_cached_pattern(prefix_pattern)` | 正则批量删除 |
| `invalidate_user_cache(user_id)` | 清除用户相关缓存 |
| `invalidate_tenant_cache(tenant_id)` | 清除租户相关缓存 |
| `invalidate_session_cache(session_id)` | 清除会话相关缓存 |
| `@cached(ttl=600)` | 异步函数自动缓存装饰器 |

`CacheKeys` 类定义了所有缓存的命名前缀：`TOKEN`、`USER`、`AGENT_QUOTA`、`USER_AGENTS`、`PROMPT_REGISTRY`、`PROMPT_LABEL`、`PROMPT_CONTENT`。

---

## 2. 缓存分类总览

系统缓存按用途分为以下类别：

| 类别 | 缓存数量 | 存储类型 | TTL 范围 | 说明 |
|------|---------|---------|---------|------|
| 认证与安全 | 3 | Redis + 内存降级 | 60s ~ 7天 | Token、用户信息、限流 |
| Prompt 版本管理 | 3 | Redis + 内存降级 | 300s ~ 600s | Prompt 注册、标签、内容 |
| 权限与配额 | 2 | Redis + 内存降级 | 300s（5分钟） | 配额、用户-智能体访问列表 |
| 技能与配置 | 3 | 纯内存 | 进程生命周期 ~ 300s | 技能定义、Prompt 模板、页面元数据 |
| 会话与队列 | 6 | Redis + 内存 | 5s ~ 3600s | 会话队列锁、短期记忆、实例状态 |
| 去重 | 1 | PostgreSQL | 300s | 渠道消息去重 |

---

## 3. 认证与安全缓存

### 3.1 Token 认证缓存

存储 JWT Token 的验证结果，避免每次请求都解析 Token。

**存储**：Redis + 内存降级
**键模式**：`token:{token}`
**TTL**：`min(Token 剩余有效时间, 604800s)`（最大 7 天）
**失效时机**：用户登出、Token 轮换时删除
**源文件**：`src/api/auth.py`

### 3.2 用户信息缓存

缓存用户档案数据，减少数据库查询。

**存储**：Redis + 内存降级
**键模式**：`user:{user_id}`
**TTL**：600s（10 分钟）
**失效时机**：权限变更、用户登出时删除
**源文件**：`src/api/auth.py`

### 3.3 限流器（滑动窗口）

基于 Redis Sorted Set 实现滑动窗口限流，用于登录等敏感操作的频率控制。

**存储**：Redis Sorted Set + 内存降级
**键模式**：`rate_limit:{category}:{key}`（如 `rate_limit:login:192.168.1.1`）
**TTL**：等于窗口大小（登录限流为 60s）
**清理策略**：通过 `zremrangebyscore` 自动清理过期分数
**源文件**：`src/api/rate_limit.py`

---

## 4. Prompt 版本管理缓存

Prompt 解析采用三层缓存架构，按优先级依次查找：

```
production 标签缓存 → 标签→版本映射缓存 → Prompt 内容缓存
      (300s)              (300s)              (600s)
```

### 4.1 Prompt 注册缓存

缓存每个 Prompt 的当前标签和版本号。

**存储**：Redis + 内存降级
**键模式**：`prompt_registry:{prompt_id}`
**TTL**：300s（5 分钟）
**失效时机**：草稿提交、标签变更时失效
**源文件**：`src/prompts/prompt_resolver.py`

### 4.2 Prompt 标签→版本映射缓存

缓存标签指向的版本号映射。

**存储**：Redis + 内存降级
**键模式**：`prompt_label:{prompt_id}:{label}`
**TTL**：300s（5 分钟）
**失效时机**：调用 `invalidate_label(prompt_id, label)` 时
**源文件**：`src/prompts/prompt_resolver.py`

### 4.3 Prompt 内容缓存

缓存实际的 Prompt 模板文本。

**存储**：Redis + 内存降级
**键模式**：`prompt_content:{prompt_id}`
**TTL**：600s（10 分钟）
**失效时机**：调用 `invalidate_prompt(prompt_id)` 时（同时清除标签缓存）
**源文件**：`src/prompts/prompt_resolver.py`

---

## 5. 权限与配额缓存

### 5.1 智能体配额缓存

缓存租户对特定智能体的配额限制，避免每次调用都查询数据库。

**存储**：Redis + 内存降级
**键模式**：`agent_quota:{tenant_id}:{agent_id}`
**TTL**：300s（5 分钟）
**失效策略**：隐式 TTL 过期
**源文件**：`src/saas/permissions/checker.py`

### 5.2 用户-智能体访问列表缓存

缓存用户可访问的智能体 ID 列表。

**存储**：Redis + 内存降级
**键模式**：`user_agents:{user_id}:{tenant_id}`
**TTL**：300s（5 分钟）
**隔离设计**：复合键（`user_id + tenant_id`）防止跨租户数据污染
**源文件**：`src/saas/permissions/checker.py`

---

## 6. 技能与配置缓存（纯内存）

以下缓存在进程生命周期内有效，重启后重建。

### 6.1 租户技能缓存

缓存合并后的技能定义（基础技能 + 租户自定义技能）。

**存储**：纯内存字典
**键模式**：`Dict[tenant_id, Tuple[skills_dict, timestamp]]`
**TTL**：300s（5 分钟），可配置
**失效方法**：`invalidate(tenant_id)` / `invalidate_skill(tenant_id, skill_name)`
**源文件**：`src/saas/services/tenant_skill_cache.py`

### 6.2 Prompt 模板缓存（文件）

缓存从 `.md` 文件加载的 Prompt 模板。

**存储**：纯内存字典
**键模式**：`Dict[name, content]`
**TTL**：进程生命周期
**失效方法**：手动调用 `clear_cache()`
**源文件**：`src/prompts/manager.py`

### 6.3 页面元数据缓存

缓存页面配置元数据（部署时加载）。

**存储**：模块级全局字典
**键模式**：模块级 `_metadata_cache`
**TTL**：进程生命周期
**失效方法**：无运行时失效机制，仅在部署时重新加载
**源文件**：`src/api/page_metadata.py`

---

## 7. 会话与队列缓存

### 7.1 会话队列（分布式锁）

用于渠道会话消息的串行化处理，通过 Redis 键族实现分布式协调。

**存储**：Redis + 内存降级
**键族**：

| 键模式 | 用途 | TTL |
|--------|------|-----|
| `session_lock:{sid}` | 会话处理锁 | 120s |
| `session_cancel:{sid}` | 会话取消标记 | 10s |
| `session_merge:{sid}` | 消息合并标记 | 5s |
| `session_pending:{sid}` | 待处理消息队列 | 30s |
| `session_responding:{sid}` | 正在响应标记 | 10s |
| `recall_pending:{sid}` | 竞态兜底：撤回事件到达时消息还在处理中，落库前查此 SET 命中则打 is_recalled=TRUE | 300s |

**释放策略**：处理完成后释放；TTL 自动过期兜底
**源文件**：`src/core/session_queue.py`；`recall_pending` 由 `src/channels/session.py::mark_recalled_message`（写入）和 `add_messages_batch_transactional`（读取+清理）协作。

### 7.2 短期记忆（Short-Term Memory）

对话上下文的滑动窗口缓存，按 session_id 分隔。

**存储**：内存 deque + 数据库降级
**键模式**：`Dict[session_id, deque]`
**限制**：每会话最多 100 条消息
**TTL**：3600s（1 小时），可配置
**失效时机**：TTL 过期、显式 `clear(session_id)`、Worker 重启时从 `chat_messages` 表重建
**源文件**：`src/memory/short_term.py`

### 7.3 实例状态同步缓存

缓存数字员工实例的运行时状态（运行标志、进程 ID、启动时间）。

**存储**：Redis + 内存降级
**键模式**：`instance_status:{instance_id}`
**TTL**：3600s（1 小时）
**失效时机**：实例停止时清除；TTL 自动过期兜底
**源文件**：`src/saas/services/instance_manager.py`

### 7.4 图片资产元信息与去重缓存

ImageRegistry 管理的图片资产元信息（复用 cp 的 `uploaded_file:{file_id}` 协议）+ Web 抓取 URL 去重缓存。

**存储**：Redis + 内存降级

| 缓存 | 键模式 | TTL | 失效时机 |
|------|--------|-----|---------|
| 图片资产元信息（通用） | `uploaded_file:file_{uuid12}`（Hash） | 86400s | `cleanup_temp` 定时清理 source∈(tool_generated/web_fetch)∧usage∈(inline/embedded) 的过期图 |
| 图片资产元信息（知识库） | `uploaded_file:file_{uuid12}`（Hash） | -1（永久） | 知识库文档删除时级联清理（Phase 3） |
| Web 图片抓取去重 | `image_fetch_url:{sha256(url)}` | 604800s（7 天） | 同 URL 复用 file_id，不重复下载 |

**Hash 字段**：`file_id / name / path / size / mime_type / type="image" / source / usage / source_ref / linked_doc_id / linked_chunk_id / visible / width / height / registered_at`

**关键约束**：
- Redis key 与 `cp_tool._register_download` 完全同命名空间，因此现有 `/api/files/{file_id}/download` 路由可直接下载 ImageRegistry 注册的图片
- `PERMANENT_TTL(-1)` 不调用 `expire`（Redis hash 默认无 TTL）；正数 TTL 才调 `expire`
- URL 去重缓存：下载成功才写入，失败/超时不写入（下次同 URL 仍会重试）

**源文件**：`src/core/image_asset.py`
**关联模块**：[image-asset-pipeline-design.md](image-asset-pipeline-design.md) §3 + §7.2

### 7.5 定时任务调度器启动锁

多 worker 环境下，确保只有单个 worker 启动 APScheduler 调度器，避免重复注册定时任务。

**存储**：Redis + 内存降级
**键模式**：`sched_task_lock:manager`（全局唯一，无 identifier 维度）
**TTL**：300s（5 分钟）
**失效时机**：调度器 `shutdown()` 时主动释放；TTL 自动过期兜底（worker 异常退出时）
**源文件**：`src/scheduler/manager.py`

---

## 8. 去重缓存（PostgreSQL）

### 8.1 渠道消息去重

跨 Worker 的消息去重记录，防止多 Worker 重复处理同一渠道消息。

**存储**：PostgreSQL（`channel_message_dedup` 表）
**键模式**：基于渠道消息唯一标识
**TTL**：300s（5 分钟），通过 `cleanup_expired()` 定期清理
**清理策略**：定时任务调用 `cleanup_expired()` 删除过期记录
**源文件**：`src/channels/idempotency.py`

> **注意**：这是唯一不使用 Redis/内存的去重机制，因为 PostgreSQL 在多 Worker 环境下提供最强的原子性保证。

---

## 9. 缓存架构要点

### 9.1 无条件的 Redis 使用

由于所有操作都通过 `redis_client`（自动选择 Redis 或内存降级），各组件代码中**从不检查 `if redis_enabled:`**。这意味着无论 Redis 是否运行，应用行为完全一致。

### 9.2 TTL 标准

| 场景 | 标准 TTL | 原因 |
|------|---------|------|
| 业务数据（权限、配额、Prompt） | 300s（5 分钟） | 平衡一致性与性能 |
| Token 验证 | 匹配 JWT 过期时间（最大 7 天） | 与认证周期对齐 |
| 会话队列协调 | 5s ~ 120s | 实时协调，短 TTL 快速恢复 |
| 短期记忆 | 3600s（1 小时） | 对话上下文需长期保持 |
| 实例状态 | 3600s（1 小时） | 状态心跳，长 TTL 减少写入 |

### 9.3 失效策略总结

| 缓存类型 | 失效方式 |
|---------|---------|
| Token/用户缓存 | 主动删除（登出、权限变更） |
| Prompt 缓存 | 级联失效（内容失效 → 同时清除标签缓存） |
| 权限/配额缓存 | TTL 过期（隐式） |
| 技能/配置缓存 | 进程重启 / 手动 `reload` |
| 会话队列 | TTL 自动过期 + 完成时释放 |
| 消息去重 | 定时任务清理 |

### 9.4 缓存与数据库的关系

```
Redis / 内存缓存（热数据，快速读取）
    ↕ TTL 过期 / 主动失效
PostgreSQL（持久化，权威数据源）
```

- **缓存不是权威数据源**：所有缓存数据都可以从数据库重建
- **缓存不一致的排查方向**：检查对应缓存的 TTL 和失效时机是否正确
- **多 Worker 一致性**：依赖 Redis（非内存降级）时保证一致；内存降级模式下各 Worker 独立缓存

---

## 10. 缓存键命名总览

```
{REDIS_KEY_PREFIX}:
├── token:{token}                    # Token 认证
├── user:{user_id}                   # 用户信息
├── rate_limit:{category}:{key}      # 限流器
├── prompt_registry:{prompt_id}      # Prompt 注册
├── prompt_label:{prompt_id}:{label} # Prompt 标签
├── prompt_content:{prompt_id}       # Prompt 内容
├── agent_quota:{tenant_id}:{agent_id}  # 智能体配额
├── user_agents:{user_id}:{tenant_id}   # 用户-智能体访问
├── session_lock:{sid}               # 会话锁
├── session_cancel:{sid}             # 会话取消
├── session_merge:{sid}              # 会话合并
├── session_pending:{sid}            # 会话待处理
├── session_responding:{sid}         # 会话响应中
├── instance_status:{instance_id}    # 实例状态
├── uploaded_file:file_{uuid12}      # 图片/文件资产元信息（Hash，与 cp 共用）
├── image_fetch_url:{sha256(url)}    # Web 图片抓取去重
└── sched_task_lock:manager          # 定时任务调度器启动锁

内存缓存（无 Redis 键前缀）：
├── Dict[tenant_id → skills]         # 租户技能
├── Dict[name → template]            # Prompt 模板文件
├── Dict[session_id → deque]         # 短期记忆
└── _metadata_cache                  # 页面元数据

PostgreSQL（去重表）：
└── channel_message_dedup            # 渠道消息去重
```
