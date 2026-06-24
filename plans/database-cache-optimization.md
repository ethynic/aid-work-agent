# 后端数据库查询 Redis 缓存优化方案

> 创建时间：2026-05-18
> 背景：项目已集成 Redis（生产环境 gunicorn 多 worker + redis；开发环境 uvicorn 单 worker + 内存缓存）。经全面代码审查，发现大量高频数据库查询尚未利用缓存加速，存在显著优化空间。

---

## 一、当前缓存架构回顾

| 组件 | 当前状态 | 说明 |
|------|---------|------|
| RedisClient | `src/core/redis_client.py` | 统一封装，支持 JSON 序列化、TTL、连接失败降级到内存 |
| 短期记忆 | `src/memory/short_term.py` | 进程内内存缓存（多 worker 不共享） |
| Skill 缓存 | `src/saas/services/tenant_skill_cache.py` | 进程内内存缓存，TTL 5 分钟（多 worker 不共享） |
| 实例状态 | `src/saas/services/instance_manager.py` | Redis 缓存运行状态 |
| 定时任务锁 | `src/scheduler/manager.py` | Redis 分布式锁 |

**关键问题**：除实例状态和分布式锁外，大量高频读取的业务数据（用户、租户、权限、会话等）**完全没有使用 Redis 缓存**，每次请求都直接查询 PostgreSQL。

---

## 二、可优化项汇总（按优先级排序）

### P0：Token 验证与用户认证（每次请求都执行）

#### 2.1 `verify_token()` —— 最高频查询

**位置**：`src/api/auth.py:173`

**现状**：每个需要认证的 API 请求都会执行：
```python
def verify_token(token: str) -> Optional[str]:
    with get_db_connection() as conn:
        cursor.execute("SELECT user_id, expires_at FROM tokens WHERE token = %s", (token,))
        # ... 验证过期、刷新有效期
```

**问题**：
- 每次 API 请求至少 1 次 DB 查询（token 验证）
- 滑动窗口刷新时额外 UPDATE
- 高并发时 tokens 表成为热点

**优化方案**：
```python
# 1. 缓存 token → user_id 映射（TTL = token 剩余有效期，最长 7 天）
cache_key = f"token:{token}"
cached = redis_client.get(cache_key)
if cached:
    user_id = cached["user_id"]
    expires_at = cached["expires_at"]
    # 检查是否需要刷新（避免每次请求都刷新，降低频率）
    # ...
    return user_id

# 2. DB 查询后写入缓存
redis_client.set(cache_key, {"user_id": user_id, "expires_at": expires_at_str}, ex=remaining_seconds)

# 3. 登出时删除缓存
def delete_token(token: str):
    redis_client.delete(f"token:{token}")
    # ... DB 删除
```

**预期收益**：认证相关 DB 查询减少 **90%** 以上。

---

#### 2.2 `get_current_user()` —— 次高频查询

**位置**：`src/api/auth.py:267`

**现状**：
```python
def get_current_user(request: Request) -> Optional[dict]:
    user_id = verify_token(token)
    if user_id:
        user = UserDB.get_by_id(user_id)  # 每次请求都查 users 表
        return user
```

**问题**：token 验证后紧接着查 users 表，每次请求 2 次 DB 查询。

**优化方案**：
```python
# 缓存用户基本信息（TTL 10 分钟，更新时失效）
cache_key = f"user:{user_id}"
user = redis_client.get(cache_key)
if not user:
    user = UserDB.get_by_id(user_id)
    if user:
        # 过滤敏感字段后缓存
        safe_user = {k: v for k, v in user.items() if k != "password_hash"}
        redis_client.set(cache_key, safe_user, ex=600)
```

**缓存失效时机**：
- `UserDB.update_info()` 后删除缓存
- `update_profile` API 后删除缓存
- 密码重置后删除缓存

**预期收益**：`get_current_user()` DB 查询减少 **95%** 以上。

---

#### 2.3 `UserDB.get_by_id()` / `get_by_phone()` —— 高频单条查询

**位置**：`src/db/models.py:92-109`

**适用场景**：登录、注册、token 刷新、权限检查等。

**优化方案**：
```python
# UserDB.get_by_id 增加缓存层
@staticmethod
def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    cache_key = f"user:{user_id}"
    cached = redis_client.get(cache_key)
    if cached:
        return cached
    
    # ... DB 查询
    user = dict(row) if row else None
    if user:
        safe = {k: v for k, v in user.items() if k != "password_hash"}
        redis_client.set(cache_key, safe, ex=600)
    return user
```

**注意**：`verify_login()` 等需要 `password_hash` 的场景不走缓存，或单独缓存策略。

---

### P1：租户信息查询（变化极少，命中极高）

#### 2.4 `TenantDB.get_by_id()` / `get_by_code()`

**位置**：`src/saas/db/tenant_db.py:66-93`

**现状**：
- 统一登录时查询租户（`unified_login`）
- Token 刷新时检查租户到期日（`verify_token`）
- 每个 SaaS 模式请求都需要租户上下文

**问题**：租户信息几乎不变，但每次都查 DB。

**优化方案**：
```python
# 租户信息缓存（TTL 30 分钟，更新时主动失效）
cache_key = f"tenant:{tenant_id}"
# 或
cache_key = f"tenant_code:{tenant_code}"

# 缓存内容包含 settings 解析后的字典
# TTL 建议 1800 秒（30 分钟）
```

**缓存失效时机**：
- `TenantDB.update()` 后删除对应缓存
- `TenantDB.delete()` 后删除缓存

**预期收益**：租户相关 DB 查询减少 **99%**。

---

#### 2.5 `TenantDB.get_stats()` —— 统计聚合查询

**位置**：`src/saas/db/tenant_db.py:192-230`

**现状**：每次访问企业概览页都执行 4 条 COUNT 聚合查询。

**优化方案**：
```python
# 统计数据缓存（TTL 60 秒，容忍短暂延迟）
cache_key = f"tenant_stats:{tenant_id}"
```

**预期收益**：概览页加载速度提升明显，DB 聚合查询减少 **95%**。

---

### P2：权限与订阅查询（高频，相对稳定）

#### 2.6 `get_agent_quota()` / `check_agent_access()`

**位置**：`src/saas/permissions/checker.py:28-96`

**现状**：
```python
def get_agent_quota(tenant_id: str, agent_id: str):
    with get_db_connection() as conn:
        cursor.execute("""
            SELECT COUNT(*) > 0 AS has_access, COALESCE(MAX(instance_quota), 0) AS instance_quota
            FROM subscriptions
            WHERE tenant_id = %s AND subagent_type = %s AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (tenant_id, agent_id))
```

**问题**：用户每次与数字员工交互前都执行此查询。

**优化方案**：
```python
# 租户-数字员工权限缓存（TTL 5 分钟）
cache_key = f"agent_quota:{tenant_id}:{agent_id}"
# 缓存 {"has_access": bool, "instance_quota": int}

# 用户级权限缓存
cache_key = f"user_agents:{user_id}"
# 缓存 ["agent_id_1", "agent_id_2", ...]
```

**缓存失效时机**：
- `SubscriptionDB.set_tenant_subscriptions()` 后清除租户级缓存
- `UserAgentPermissionDB.set_permissions()` 后清除用户级缓存

**预期收益**：权限检查 DB 查询减少 **90%**。

---

#### 2.7 `get_allowed_agent_ids_for_user()`

**位置**：`src/saas/permissions/checker.py:99-153`

**现状**：返回用户允许访问的数字员工列表，前端每次加载都调用。

**优化方案**：
```python
# 用户允许的数字员工列表缓存（TTL 5 分钟）
cache_key = f"user_allowed_agents:{user_id}"
```

**预期收益**：数字员工列表加载 DB 查询减少 **95%**。

---

#### 2.8 `SubscriptionDB.count_active_subscriptions()`

**位置**：`src/saas/db/subscription_db.py:156-171`

**现状**：在租户列表、租户详情页中反复调用。

**优化方案**：
```python
# 租户订阅数量缓存（TTL 5 分钟）
cache_key = f"tenant_sub_count:{tenant_id}"
```

---

### P3：会话查询（高频，变化适中）

#### 2.9 `SessionDB.get_by_id()`

**位置**：`src/db/models.py:315-327`

**现状**：几乎每个会话相关 API 都先调用此函数验证会话归属：
- `GET /api/sessions/{id}`
- `PATCH /api/sessions/{id}`
- `DELETE /api/sessions/{id}`
- `GET /api/sessions/{id}/messages`
- `GET /api/sessions/{id}/context`

**优化方案**：
```python
# 会话基本信息缓存（TTL 5 分钟，更新时失效）
cache_key = f"session:{session_id}"
# 缓存内容：session 字典（不含 context_data 大字段，或选择性缓存）
```

**缓存失效时机**：
- `SessionDB.update_title()` / `update_context()` / `update_subagent_id()` / `touch()` 后删除缓存
- `SessionDB.delete()` 后删除缓存

**预期收益**：会话详情类 API 的 DB 查询减少 **80%**。

---

#### 2.10 `SessionDB.list_by_user()`

**位置**：`src/db/models.py:330-379`

**现状**：前端每次打开会话列表都调用，`GET /api/sessions` 和 `GET /api/sessions/latest`。

**优化方案**：
```python
# 用户会话列表缓存（TTL 30 秒，适合列表类数据）
cache_key = f"user_sessions:{user_id}:{tenant_id}:{page}:{page_size}"
# TTL 30 秒，因为会话列表变化较频繁
```

**预期收益**：会话列表页 DB 查询减少 **90%**（用户频繁刷新列表的情况）。

---

#### 2.11 `MessageDB.list_by_session()`

**位置**：`src/db/models.py:508-550`

**现状**：每次聊天请求都需要加载历史消息构建上下文。

**优化方案**：
```python
# 会话消息缓存（TTL 60 秒，增量更新困难，建议简单 TTL）
cache_key = f"session_messages:{session_id}:{limit}:{roles_hash}"
# 由于消息是追加模式，可以考虑只缓存最近 N 条
```

**注意**：消息缓存需谨慎，因为聊天是追加写入。可考虑：
- 短 TTL（30-60 秒）
- 或只缓存最近 20 条，新消息只追加到缓存

**预期收益**：聊天接口消息查询减少 **70%**（同一 session 连续对话时）。

---

### P4：报表与统计查询（计算密集，适合缓存）

#### 2.12 `ChatRecordDB.get_platform_token_usage()`

**位置**：`src/db/models.py:827-913`

**现状**：平台级 Token 消耗报表，按月统计，涉及大量聚合计算和 JOIN。

**优化方案**：
```python
# 月度报表缓存（TTL 1 小时，报表数据变化慢）
cache_key = f"platform_token_usage:{month_str}"
```

**预期收益**：报表页加载时间从秒级降至毫秒级。

---

#### 2.13 `ChatRecordDB.get_tenant_token_details()`

**位置**：`src/db/models.py:916-1011`

**现状**：租户 Token 消耗明细，分页查询，含汇总统计。

**优化方案**：
```python
# 租户月度明细缓存（TTL 10 分钟，分页参数作为 key 的一部分）
cache_key = f"tenant_token_details:{tenant_id}:{month_str}:{page}:{page_size}"
# 汇总数据单独缓存
cache_key = f"tenant_token_summary:{tenant_id}:{month_str}"
```

---

#### 2.14 `ChatRecordDB.get_token_usage_by_tenant()`

**位置**：`src/db/models.py:736-790`

**现状**：按天/模型/用户分组的统计查询。

**优化方案**：
```python
# 分组统计缓存（TTL 10 分钟）
cache_key = f"token_usage:{tenant_id}:{start_date}:{end_date}:{group_by}"
```

---

### P5：渠道会话查询（高频，相对简单）

#### 2.15 `ChannelSessionManager.get_or_create_session()`

**位置**：`src/channels/session.py:107-194`

**现状**：每次渠道消息都先查询 `channel_sessions` 表。

**优化方案**：
```python
# 渠道会话缓存（TTL 10 分钟，更新 last_message_at 时延长 TTL）
cache_key = f"channel_session:{channel_type}:{channel_user_id}"
```

**注意**：`last_message_at` 每次消息都更新，如果每次都失效缓存，缓存价值降低。建议：
- 缓存会话基本信息（session_id, user_id, channel_chat_id）
- `last_message_at` 不缓存，或异步更新

**预期收益**：渠道消息处理 DB 查询减少 **80%**。

---

### P6：定时任务查询（中频）

#### 2.16 `ScheduledTaskDB.get_by_id()` / `list_by_user()`

**位置**：`src/scheduler/db.py`

**优化方案**：
```python
# 定时任务缓存（TTL 5 分钟）
cache_key = f"scheduled_task:{task_id}"
cache_key = f"user_scheduled_tasks:{user_id}"
```

---

### P7：数字员工实例查询（中频）

#### 2.17 `AgentInstanceDB.get_by_id()` / `list_by_tenant()`

**位置**：`src/saas/db/agent_instance_db.py`

**优化方案**：
```python
# 实例信息缓存（TTL 10 分钟，更新时失效）
cache_key = f"agent_instance:{instance_id}"
cache_key = f"tenant_instances:{tenant_id}"
```

---

### P8：知识库文档查询（中频）

#### 2.18 `KnowledgeBaseService.list_documents()` / `count_documents()`

**位置**：`src/knowledge/service.py:297-375`

**优化方案**：
```python
# 文档列表缓存（TTL 60 秒）
cache_key = f"docs_list:{tenant_id}:{user_id}:{limit}:{offset}"
cache_key = f"docs_count:{tenant_id}:{user_id}"
```

**注意**：文档上传/删除时需要清除对应缓存。

---

## 三、通用缓存封装建议

### 3.1 统一缓存装饰器

建议实现一个通用的缓存装饰器，简化各处的缓存逻辑：

```python
# src/core/cache_utils.py
from functools import wraps
from src.core.redis_client import redis_client

def cached(key_prefix: str, ttl: int, key_func=None):
    """通用缓存装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存 key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # 默认 key: prefix:arg1:arg2:...
                key_parts = [key_prefix]
                key_parts.extend([str(a) for a in args[1:] if isinstance(a, (str, int, float))])
                cache_key = ":".join(key_parts)
            
            # 尝试读缓存
            cached = redis_client.get(cache_key)
            if cached is not None:
                return cached
            
            # 执行原函数
            result = func(*args, **kwargs)
            
            # 写入缓存
            if result is not None:
                redis_client.set(cache_key, result, ex=ttl)
            
            return result
        return wrapper
    return decorator

# 使用示例
class UserDB:
    @staticmethod
    @cached("user", ttl=600)
    def get_by_id(user_id: str):
        # ... DB 查询
        pass
```

### 3.2 缓存失效工具函数

```python
def invalidate_user_cache(user_id: str):
    """清除用户相关所有缓存"""
    redis_client.delete(f"user:{user_id}")
    redis_client.delete(f"user_allowed_agents:{user_id}")
    # 批量删除用户会话列表缓存
    for key in redis_client.keys(f"user_sessions:{user_id}:*"):
        redis_client.delete(key)

def invalidate_tenant_cache(tenant_id: str):
    """清除租户相关所有缓存"""
    redis_client.delete(f"tenant:{tenant_id}")
    redis_client.delete(f"tenant_stats:{tenant_id}")
    redis_client.delete(f"tenant_sub_count:{tenant_id}")
    # ... 其他租户级缓存
```

---

## 四、实施优先级与工作量评估

| 优先级 | 优化项 | 预估工作量 | 预期 DB 查询减少 | 实施建议 |
|--------|--------|-----------|-----------------|----------|
| P0 | Token 验证缓存 | 0.5 天 | 90% | 立即实施 |
| P0 | `get_current_user()` 缓存 | 0.5 天 | 95% | 立即实施 |
| P1 | 租户信息缓存 | 0.5 天 | 99% | 立即实施 |
| P1 | 租户统计缓存 | 0.5 天 | 95% | 立即实施 |
| P2 | 权限/订阅缓存 | 1 天 | 90% | 高优先级 |
| P2 | 用户允许数字员工列表缓存 | 0.5 天 | 95% | 高优先级 |
| P3 | 会话详情缓存 | 0.5 天 | 80% | 中优先级 |
| P3 | 会话列表缓存 | 0.5 天 | 90% | 中优先级 |
| P3 | 消息列表缓存 | 1 天 | 70% | 中优先级（需谨慎） |
| P4 | 报表统计缓存 | 1 天 | 95% | 中优先级 |
| P5 | 渠道会话缓存 | 0.5 天 | 80% | 中优先级 |
| P6-P8 | 其他查询缓存 | 1 天 | 60-80% | 低优先级 |

**总计预估工作量**：约 **7-8 人天**。

---

## 五、风险与注意事项

### 5.1 缓存一致性问题

- **生产环境**：Redis 是多 worker 共享的，缓存一致性由 Redis 保证
- **开发环境**：内存降级存储是进程级的，多进程开发模式下可能出现不一致（uvicorn 单 worker 不影响）
- **建议**：缓存 TTL 不宜过长，敏感数据（权限、用户状态）建议 TTL 5-10 分钟

### 5.2 敏感信息

- 用户缓存中**必须排除** `password_hash` 字段
- Token 缓存中**必须排除** 原始 token 值（key 中使用 hash）

### 5.3 缓存击穿/雪崩

- 建议对热点 key（如租户信息）使用较长的 TTL
- 报表类查询可加短暂本地缓存（1-2 秒）作为二级缓存

### 5.4 内存降级场景

- 开发环境 Redis 未启用时，降级到内存存储
- 内存存储无 TTL 过期机制（当前 `_InMemoryFallback.set` 忽略 `ex` 参数）
- **建议**：修复内存降级的 TTL 支持，或确保开发环境也启用 Redis

---

## 六、快速检查清单

实施完成后，验证以下场景缓存生效：

- [ ] 登录后多次请求 `/api/sessions`，`verify_token` 不再查 DB
- [ ] 刷新会话列表时，`list_by_user` 优先返回缓存
- [ ] 切换数字员工时，`check_agent_access` 优先返回缓存
- [ ] 查看企业概览时，`get_stats` 优先返回缓存
- [ ] 查看报表时，月度统计优先返回缓存
- [ ] 更新用户信息后，相关缓存被清除
- [ ] 更新租户信息后，租户缓存被清除
- [ ] 登出后，token 缓存被清除

---

## 七、附录：当前无缓存的高频查询统计

| 模块 | 函数/方法 | 调用频率 | 是否可缓存 | 建议 TTL |
|------|----------|---------|-----------|---------|
| auth | `verify_token()` | 每次请求 | 是 | token 剩余有效期 |
| auth | `get_current_user()` | 每次请求 | 是 | 10 分钟 |
| db/models | `UserDB.get_by_id()` | 高频 | 是 | 10 分钟 |
| db/models | `SessionDB.get_by_id()` | 高频 | 是 | 5 分钟 |
| db/models | `SessionDB.list_by_user()` | 中高频 | 是 | 30 秒 |
| db/models | `MessageDB.list_by_session()` | 高频 | 是 | 60 秒 |
| db/models | `ChatRecordDB.get_platform_token_usage()` | 中频 | 是 | 1 小时 |
| saas/tenant_db | `TenantDB.get_by_id()` | 高频 | 是 | 30 分钟 |
| saas/tenant_db | `TenantDB.get_by_code()` | 中频 | 是 | 30 分钟 |
| saas/tenant_db | `TenantDB.get_stats()` | 中频 | 是 | 60 秒 |
| saas/permissions | `get_agent_quota()` | 高频 | 是 | 5 分钟 |
| saas/permissions | `check_agent_access()` | 高频 | 是 | 5 分钟 |
| saas/permissions | `get_allowed_agent_ids_for_user()` | 中高频 | 是 | 5 分钟 |
| saas/subscription | `count_active_subscriptions()` | 中频 | 是 | 5 分钟 |
| saas/instance_db | `AgentInstanceDB.get_by_id()` | 中频 | 是 | 10 分钟 |
| channels/session | `get_or_create_session()` | 高频（渠道） | 是 | 10 分钟 |
| scheduler/db | `ScheduledTaskDB.get_by_id()` | 中频 | 是 | 5 分钟 |
| knowledge | `list_documents()` | 中频 | 是 | 60 秒 |
