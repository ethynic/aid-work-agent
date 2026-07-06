# Redis 缓存管理页设计

> 本文档设计平台管理后台的「Redis 缓存」管理页，供平台管理员枚举、查看、搜索、删除 Redis 中的键值，作为排查缓存不一致、内存泄漏、跨租户隔离问题的运维工具。
>
> 与 [cache_usage.md](./cache_usage.md) 的关系：`cache_usage.md` 是缓存**使用规范**（每个缓存的键模式、TTL、失效时机），本文档是缓存**运维工具**的设计文档。两者互补：规范约束写入，工具支撑排查。

---

## 1. 背景与目标

### 1.1 背景

- 服务端缓存统一走 Redis（见 [backend_dev.md 缓存使用规范](../../.claude/rules/backend_dev.md)），键名前缀由 `CacheKeys` 类登记（26 个前缀，含 `token`、`user`、`session`、`tenant`、`agent_quota`、`prompt_*`、`ch_session` 等）。
- 当前排查 Redis 缓存内容缺乏统一入口：PostgreSQL 有 `psql` / Navicat，文件有 `ls` / 文件管理器，Redis 只能 `redis-cli` 手敲命令，且生产 Redis 通常在容器内，运维门槛高。
- 已有 `/api/clear_cache` 端点（[src/main.py:759](../../src/main.py)）支持「清空所有键」，但粒度过粗，无法定位单键、查看值、按前缀清理。最近提交 `1950d12` 整改了 6 处裸键，更需要一个可视化工具持续监控裸键回归。

### 1.2 目标

| 优先级 | 能力 |
|--------|------|
| P0 | 按 `REDIS_KEY_PREFIX` 隔离枚举所有键，列表展示（键名、类型、TTL、大小） |
| P0 | 单键查看（按 Redis 类型分支渲染：string / hash / set / list / zset） |
| P0 | 单键删除 |
| P0 | 平台管理员鉴权，租户管理员/普通用户禁入 |
| P1 | 按前缀过滤（左侧树形导航，按 `CacheKeys` 分组） |
| P1 | 关键字搜索（键名子串匹配） |
| P1 | 裸键识别（不在 `CacheKeys` 登记的前缀，单独分组告警） |
| P1 | 敏感值脱敏（password / api_key / token / secret 字段） |
| P2 | 按前缀批量删除（高危，二次确认 + 审计日志） |
| P2 | 操作审计日志（删除动作写 `log/temp/redis_admin.log`） |

### 1.3 非目标

- **不做**键值编辑（缓存应是业务代码写入，运维只读 + 删除；手动编辑易破坏序列化语义）
- **不做**按租户隔离视图（平台管理员视角应能看所有租户的键，键名含 `tenant_id` 自然区分）
- **不做**实时刷新（运维工具，按需手动刷新即可，避免无意义轮询）
- **不做**Stream 类型支持（项目目前未使用 Stream，先不实现，未来需要时再扩展）

---

## 2. 现状盘点

### 2.1 已有 Redis 客户端能力（`src/core/redis_client.py`）

| 方法 | 用途 | 管理页是否够用 |
|------|------|---------------|
| `get(key)` | 读取 string 值（JSON 反序列化） | ✅ |
| `set(key, value, ex)` | 写入 string 值 | ❌ 管理页不写 |
| `delete(key)` | 删除键 | ✅ |
| `hget(key, field)` / `hgetall(key)` | Hash 操作 | ✅ |
| `keys(pattern)` | 按模式匹配键 | ⚠️ 用 `KEYS` 命令，生产阻塞，需改 `SCAN` |
| `exists(key)` | 检查键是否存在 | ✅ |
| `zcard` / `zrange` | Sorted Set 操作 | ✅ |
| `clear_all()` | 清空所有键（已按 `REDIS_KEY_PREFIX` 隔离） | ✅ |
| `make_key(prefix, identifier)` | 拼接完整键名 | ✅ |

**缺失能力**（需扩展）：
- `scan(cursor, match, count)` —— 游标式迭代，替代 `KEYS`
- `type(key)` —— 获取键的 Redis 类型（string/hash/list/set/zset）
- `ttl(key)` —— 获取剩余 TTL（秒，-1=永不过期，-2=键不存在）
- `hkeys(key)` / `llen(key)` / `lrange(key, start, end)` / `smembers(key)` —— 按类型枚举成员
- `memory_usage(key)` —— 单键内存占用（Redis 4.0+ `MEMORY USAGE` 命令），用于「大小」列
- `dbsize()` —— 当前 db 键总数，用于页面顶部概览

### 2.2 已有端点

- `/api/clear_cache`（[src/main.py:759](../../src/main.py)）：清空所有 Redis 缓存，平台管理员鉴权。
- `/api/admin/*`：平台管理后台路由约定前缀（参考 `src/api/admin_reports.py`、`src/api/admin_subagent.py`），用 `is_platform_admin(user)` 鉴权。

### 2.3 `CacheKeys` 已登记前缀

`src/core/cache_utils.py:22` 共 26 个前缀。前端按此前缀清单做左侧树形分组；未匹配到任何前缀的键进入「裸键」分组。

---

## 3. 后端设计

### 3.1 路由设计

新建 `src/api/admin_redis.py`，挂载到 `app.include_router(admin_redis.router)`，路由前缀 `/api/admin/redis`。

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/admin/redis/overview` | 概览：键总数、已登记前缀数、裸键数、内存降级状态 |
| GET | `/api/admin/redis/keys` | 键列表（支持 `prefix`、`search`、`cursor`、`limit` 参数） |
| GET | `/api/admin/redis/keys/{key:path}` | 单键详情（类型、TTL、值、大小） |
| DELETE | `/api/admin/redis/keys/{key:path}` | 删除单键 |
| DELETE | `/api/admin/redis/keys-by-prefix` | 按前缀批量删除（请求体传 `prefix`，需二次确认 token） |

**鉴权**：所有端点统一 `is_platform_admin(user)` 校验，失败返回 403。沿用 `/api/admin/*` 既有约定。

**路径参数 `{key:path}`**：键名常含 `:` 分隔符，需用 `:path` 转换器接收完整路径段。键名 URL decode 后传给 `redis_client`。

### 3.2 `redis_client` 扩展

在 `src/core/redis_client.py` 新增方法：

```python
def scan(self, cursor: int, match: str, count: int = 100) -> tuple[int, list[str]]:
    """游标式扫描键（生产安全，替代 KEYS）"""

def type(self, key: str) -> str:
    """获取键的 Redis 类型：string/hash/list/set/zset/none"""

def ttl(self, key: str) -> int:
    """获取 TTL（秒）：-1=永不过期，-2=键不存在"""

def hkeys(self, key: str) -> list[str]:
    """获取 Hash 的所有 field 名"""

def llen(self, key: str) -> int:
    """获取 List 长度"""

def lrange(self, key: str, start: int, end: int) -> list:
    """获取 List 范围元素"""

def smembers(self, key: str) -> list:
    """获取 Set 所有成员"""

def memory_usage(self, key: str) -> int | None:
    """获取单键内存占用（字节），Redis 4.0+"""

def dbsize(self) -> int:
    """当前 db 键总数"""
```

**内存降级兼容**：`_InMemoryFallback` 同步补齐 `scan` / `type` / `ttl` / `hkeys` / `llen` / `lrange` / `smembers` / `memory_usage` / `dbsize` 的等价实现（基于 `_data` 字典和类型标记），保证 Redis 不可用时管理页仍能展示降级数据。降级数据无 `memory_usage` 真实值，返回 `None`。

### 3.3 键枚举：SCAN 而非 KEYS

**核心约束**：禁用 `KEYS *`，生产 Redis 键多时会阻塞主线程。

```python
@router.get("/keys")
async def list_keys(
    request: Request,
    prefix: str | None = Query(None, description="前缀过滤，如 'token'、'user'"),
    search: str | None = Query(None, description="键名子串搜索"),
    cursor: int = Query(0, description="SCAN 游标，首次传 0"),
    limit: int = Query(200, ge=1, le=1000, description="单次返回上限"),
):
    user = get_current_user(request)
    if not is_platform_admin(user):
        raise HTTPException(403, "仅平台管理员可访问")

    # 构造 MATCH 模式：{key_prefix}:{cache_prefix}*{search}*
    # key_prefix 来自 REDIS_KEY_PREFIX（如 aid-local），由 redis_client.make_key 注入
    match = redis_client.make_key(prefix or "", "")  # 拼出 aid-local:token: 等
    if not prefix:
        match = redis_client.make_key("", "")  # aid-local:
    match = match.rstrip(":") + "*"  # aid-local:token:*
    if search:
        match = match.replace("*", f"*{search}*")  # aid-local:token:*abc*

    # 累积 SCAN 直到达到 limit 或游标归零
    accumulated = []
    next_cursor = cursor
    while len(accumulated) < limit:
        next_cursor, batch = redis_client.scan(next_cursor, match, count=100)
        accumulated.extend(batch)
        if next_cursor == 0:
            break
    # 截断到 limit
    accumulated = accumulated[:limit]

    # 批量查询每个键的 type 和 ttl（pipeline 优化）
    items = []
    for key in accumulated:
        items.append({
            "key": key,
            "type": redis_client.type(key),
            "ttl": redis_client.ttl(key),
            "size": redis_client.memory_usage(key),
        })

    return {
        "success": True,
        "items": items,
        "next_cursor": next_cursor if len(accumulated) == limit else 0,
        "has_more": next_cursor != 0 and len(accumulated) == limit,
    }
```

**性能保护**：
- `limit` 上限 1000，前端默认请求 200
- 单次 SCAN `COUNT=100`，循环累积至 `limit` 或游标归零
- 前端展示「已加载 N / 共 M 键」，支持「加载更多」按钮

### 3.4 单键详情：按类型分支

```python
@router.get("/keys/{key:path}")
async def get_key_detail(request: Request, key: str):
    # 鉴权略
    key_type = redis_client.type(key)
    ttl = redis_client.ttl(key)
    size = redis_client.memory_usage(key)

    value = None
    members = None
    if key_type == "string":
        value = redis_client.get(key)
    elif key_type == "hash":
        members = redis_client.hgetall(key)  # dict
    elif key_type == "list":
        length = redis_client.llen(key)
        members = redis_client.lrange(key, 0, min(length - 1, 199))  # 最多 200 个
    elif key_type == "set":
        members = redis_client.smembers(key)
    elif key_type == "zset":
        length = redis_client.zcard(key)
        members = redis_client.zrange(key, 0, min(length - 1, 199))
    elif key_type == "none":
        return {"success": False, "message": "键不存在"}

    return {
        "success": True,
        "key": key,
        "type": key_type,
        "ttl": ttl,
        "size": size,
        "value": sanitize_value(key, value) if key_type == "string" else None,
        "members": sanitize_members(key, members) if key_type != "string" else None,
        "truncated": (key_type == "list" and length > 200) or (key_type == "zset" and length > 200),
    }
```

### 3.5 敏感值脱敏

复用 `backend_dev.md` 的 `SENSITIVE_PATTERNS` 思路：

```python
SENSITIVE_FIELD_PATTERNS = [
    re.compile(r"(password|passwd|pwd)", re.IGNORECASE),
    re.compile(r"(api[_-]?key|app[_-]?key|secret)", re.IGNORECASE),
    re.compile(r"(token|jwt|bearer)", re.IGNORECASE),
    re.compile(r"(phone|mobile|id[_-]?card)", re.IGNORECASE),
]

def sanitize_value(key: str, value: Any) -> Any:
    """根据键名判断是否敏感，敏感值返回脱敏后的占位"""
    for pattern in SENSITIVE_FIELD_PATTERNS:
        if pattern.search(key):
            return "***[SENSITIVE REDACTED]***"
    # 大值截断
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + f"...[truncated {len(value) - 200} chars]"
    return value

def sanitize_members(key: str, members: dict | list) -> dict | list:
    """Hash/Set/List/Zset 成员脱敏：对 Hash 的 field 名做敏感判断"""
    if isinstance(members, dict):
        return {
            field: "***[SENSITIVE]***" if any(p.search(field) for p in SENSITIVE_FIELD_PATTERNS) else truncate(v)
            for field, v in members.items()
        }
    return [truncate(m) for m in members]
```

**脱敏策略权衡**：
- 键名含 `token` / `password` / `api_key` 等关键字 → 整个值打码
- Hash 的 field 名命中关键字 → 该 field 的值打码
- 普通值超 200 字符 → 截断显示前 200 字符 + `[truncated N chars]` 提示
- **管理员需要原文排查怎么办**：Phase 1 先全脱敏；Phase 2 视实际需求增加「查看原文」按钮，要求二次输入密码 + 写审计日志（参考 [backend_dev.md 错误处理规范](../../.claude/rules/backend_dev.md)）

### 3.6 删除操作

**单键删除**：

```python
@router.delete("/keys/{key:path}")
async def delete_key(request: Request, key: str):
    user = get_current_user(request)
    if not is_platform_admin(user):
        raise HTTPException(403)

    existed = redis_client.exists(key)
    if not existed:
        raise HTTPException(404, "键不存在")

    redis_client.delete(key)

    tlog("redis_admin", "DELETE key={key} by user={uid} tenant={tid}",
         key=key, uid=user.get("user_id"), tid=user.get("tenant_id"))

    return {"success": True, "deleted_key": key}
```

**按前缀批量删除**（高危）：

```python
class DeleteByPrefixRequest(BaseModel):
    prefix: str = Field(..., description="前缀，必须精确匹配 CacheKeys 中登记的值")
    confirm_token: str = Field(..., description="前端先调 /confirm-token 获取，强制二次确认")

@router.delete("/keys-by-prefix")
async def delete_by_prefix(request: Request, body: DeleteByPrefixRequest):
    # 鉴权略
    # 1. 校验 prefix 在 CacheKeys 登记范围内（拒绝裸键批量删除）
    valid_prefixes = {v for k, v in vars(CacheKeys).items() if not k.startswith("_") and isinstance(v, str)}
    if body.prefix not in valid_prefixes:
        raise HTTPException(400, "批量删除仅支持 CacheKeys 登记的前缀")

    # 2. 校验 confirm_token（前端 GET /confirm-token 时返回的随机串，存 Redis 60s）
    cached_token = redis_client.get("redis_admin_confirm", body.confirm_token)
    if not cached_token or cached_token != body.prefix:
        raise HTTPException(400, "确认 token 无效或已过期，请重新确认")

    # 3. SCAN + DELETE 分批执行（避免一次性 keys 拉全量）
    deleted = 0
    cursor = 0
    while True:
        cursor, batch = redis_client.scan(cursor, f"{redis_client.make_key(body.prefix, '')}*", count=100)
        for k in batch:
            redis_client.delete(k)
            deleted += 1
        if cursor == 0:
            break

    tlog("redis_admin", "DELETE BY PREFIX prefix={pfx} count={n} by user={uid}",
         pfx=body.prefix, n=deleted, uid=user.get("user_id"))

    return {"success": True, "deleted": deleted, "prefix": body.prefix}
```

**安全约束**：
- 批量删除仅限 `CacheKeys` 登记前缀，**禁止裸键批量删除**（裸键本身可能是误用，应先排查登记而非一删了之）
- 强制二次确认：前端先调 `GET /api/admin/redis/confirm-token?prefix=xxx` 拿到随机 token（60s TTL），DELETE 请求带上 token
- 不使用 `redis_client.delete_cached_pattern`（其内部用 `keys()`，生产阻塞），改用 SCAN + 逐个 delete
- 全程 `tlog("redis_admin", ...)` 写操作日志到 `log/temp/redis_admin.log`

### 3.7 概览端点

```python
@router.get("/overview")
async def overview(request: Request):
    # 鉴权略
    total = redis_client.dbsize()
    # 裸键统计：SCAN 全量键，统计未匹配 CacheKeys 的数量
    # 注意：全量 SCAN 代价高，结果缓存 60s
    cached = redis_client.get("redis_admin_overview", "stats")
    if cached:
        return cached

    registered_count = 0
    bare_count = 0
    cursor = 0
    while True:
        cursor, batch = redis_client.scan(cursor, redis_client.make_key("", "*"), count=500)
        for k in batch:
            # 去掉 REDIS_KEY_PREFIX 后判断前缀是否在 CacheKeys
            local = k.split(":", 2)[-1] if ":" in k else k  # aid-local:token:xxx → token:xxx
            first_seg = local.split(":", 1)[0]
            if first_seg in {v for v in vars(CacheKeys).values() if isinstance(v, str)}:
                registered_count += 1
            else:
                bare_count += 1
        if cursor == 0:
            break

    result = {
        "success": True,
        "total_keys": total,
        "registered_keys": registered_count,
        "bare_keys": bare_count,
        "redis_connected": redis_client._connected,
        "fallback_active": not redis_client._connected,
    }
    redis_client.set("redis_admin_overview", "stats", value=result, ttl=60)
    return result
```

**缓存策略**：overview 端点结果缓存 60s，避免频繁全量 SCAN。前端「刷新」按钮强制清缓存重算。

---

## 4. 前端设计

### 4.1 路由与菜单

- 路由：`/admin/redis-cache`（注册到 `frontend/src/main.ts`，参考其他 `/admin/*` 路由模式）
- 菜单：在平台管理后台侧边栏「系统运维」分组下新增「Redis 缓存」入口
- 权限：路由守卫校验 `role === 'platform_admin'`，非平台管理员重定向到登录页

### 4.2 页面布局

参考 [list-page-convention.md](../../.claude/rules/list-page-convention.md) 模式二（PortalLayout 子页面），但本页属平台管理后台，使用管理后台布局：

```
┌────────────────────────────────────────────────────────────────┐
│ AppHeader: 标题「Redis 缓存管理」  [刷新]                        │
├──────────┬─────────────────────────────────────────────────────┤
│          │ ┌─ 概览栏 ──────────────────────────────────────┐  │
│ 左侧树   │ │ 总键数 12345 ｜ 已登记 12000 ｜ 裸键 345 ｜    │  │
│ (前缀    │ │ Redis 已连接 ✅                                │  │
│  分组)   │ └────────────────────────────────────────────────┘  │
│          │                                                     │
│ ▼ 全部   │ 搜索区：[搜索框 键名/子串] [搜索] [加载更多]         │
│ ▼ 认证   │ ┌─ 键列表表格 ──────────────────────────────────┐  │
│   token  │ │ 键名         类型   TTL    大小    操作        │  │
│   user   │ │ aid-local:t:.. str   3600   128B   [查看][删]  │  │
│ ▼ 会话   │ │ aid-local:u:.. hash  -1     2KB    [查看][删]  │  │
│   sessio │ │ ...                                            │  │
│   ch_se..│ └────────────────────────────────────────────────┘  │
│ ▼ 租户   │                                                     │
│ ▼ Prompt │ [加载更多] （游标分页）                              │
│ ▼ 配额   │                                                     │
│ ⚠ 裸键   │                                                     │
│   (345)  │                                                     │
└──────────┴─────────────────────────────────────────────────────┘
```

### 4.3 左侧前缀树

按 `CacheKeys` 类的 26 个前缀分组，按业务域归类：

| 业务域 | 包含前缀 |
|--------|---------|
| 认证与安全 | `token`、`user`、`user_sessions` |
| 会话与队列 | `session`、`session_msgs`、`ch_session`、`recall_pending`、`agent_inst`、`tenant_inst` |
| 租户管理 | `tenant`、`tenant_code`、`tenant_stats`、`tenant_sub_cnt` |
| 权限与配额 | `agent_quota`、`user_agents` |
| 用量统计 | `platform_usage`、`tenant_usage`、`tenant_usage_sum`、`token_usage` |
| 知识库 | `docs_list`、`docs_count` |
| Prompt | `prompt_content`、`prompt_label`、`prompt_reg`、`prompt_sections` |
| 渠道限流 | `ch_rate_limit` |
| 上下文压缩 | `comp_metrics` |
| ⚠ 裸键 | 未匹配上述前缀的键 |

点击前缀节点 → 列表按该前缀过滤（`?prefix=token`）。点击「全部」→ 不带前缀过滤（首次加载需警告，键多时慢）。

### 4.4 表格列规范

| 列 | 宽度 | 说明 |
|----|------|------|
| 键名 | 自适应（带 `truncate` + tooltip 显示完整） | 完整键名 `aid-local:token:abc123` |
| 类型 | 80px | `string` / `hash` / `list` / `set` / `zset`，用 `BaseBadge` 着色 |
| TTL | 100px | `3600s` / `永不过期`（-1 标红）/ `已过期`（-2） |
| 大小 | 80px | `128B` / `2KB`（字节自动转可读单位） |
| 操作 | 120px | `[查看]` `ghost sm` + `[删除]` `danger-ghost sm` |

使用 `BaseTable` 组件，遵循 [list-page-convention.md §4 表格规范](../../.claude/rules/list-page-convention.md)。

### 4.5 单键详情弹框

点击「查看」打开 `BaseModal`（`size="xl"`，因为内容可能较宽）：

```
┌─ 键详情 ────────────────────────────────────────────────┐
│ 键名：aid-local:token:abc123                             │
│ 类型：string        TTL：3600s        大小：128B          │
│ ┌─ 值 ─────────────────────────────────────────────┐    │
│ │ {"user_id": "u_123", "role": "platform_admin"}   │    │
│ │                                                   │    │
│ │ （JSON 自动格式化展示，超长时滚动条）              │    │
│ └───────────────────────────────────────────────────┘    │
│                                                          │
│              [关闭]  [删除此键]                          │
└──────────────────────────────────────────────────────────┘
```

- string 类型：JSON 自动美化（`JSON.stringify(value, null, 2)`），非 JSON 字符串原样显示
- hash 类型：表格展示 `field` / `value` 两列
- list / set 类型：列表展示，每行一个元素
- zset 类型：表格展示 `score` / `member` 两列
- 敏感值显示 `***[SENSITIVE REDACTED]***`
- 截断值末尾显示 `...[truncated N chars]` 提示

### 4.6 删除确认

**单键删除**：浏览器原生 `confirm('确定删除键 aid-local:token:abc123？')`，确认后调 DELETE。

**按前缀批量删除**：
- 入口：左侧前缀树节点右键菜单「批量删除此前缀下所有键」
- 二次确认弹框：
  ```
  ┌─ 危险操作 ──────────────────────────────┐
  │ 即将删除前缀 [token] 下的所有键（约 234 个） │
  │                                          │
  │ 请输入 "token" 确认：                     │
  │ [_____________________]                  │
  │                                          │
  │         [取消]  [确认删除]                │
  └──────────────────────────────────────────┘
  ```
- 前端先调 `GET /confirm-token?prefix=token` 拿 token，DELETE 请求带上 token + 用户输入的 prefix 校验
- 删除成功后刷新列表 + 清 overview 缓存

### 4.7 裸键告警

左侧「⚠ 裸键」节点显示数量徽章，点击进入裸键列表。裸键列表页头部黄色提示条：

> ⚠ 这些键未在 `CacheKeys` 中登记前缀，可能是误用裸键。请排查后到 `src/core/cache_utils.py` 补登记，或确认后删除。

---

## 5. 安全与权限

### 5.1 鉴权

- 所有 `/api/admin/redis/*` 端点统一 `is_platform_admin(user)` 校验
- 租户管理员、普通用户访问返回 403
- 前端路由守卫 + 后端鉴权双重保险

### 5.2 操作审计

所有删除操作写 `tlog("redis_admin", ...)`，日志位于 `log/temp/redis_admin.log`：

```
2026-07-06 10:23:45 DELETE key=aid-local:token:abc123 by user=u_123 tenant=t_001
2026-07-06 10:24:12 DELETE BY PREFIX prefix=token count=234 by user=u_123 tenant=t_001
```

参考 [backend_dev.md 临时主题日志](../../.claude/rules/backend_dev.md)，`tlog` 与主日志隔离，便于追溯运维操作。

### 5.3 敏感值保护

- Phase 1：所有命中 `SENSITIVE_FIELD_PATTERNS` 的值脱敏展示
- Phase 2（可选）：增加「查看原文」按钮，要求二次密码 + 审计日志

### 5.4 危险操作约束

| 操作 | 约束 |
|------|------|
| 单键删除 | confirm 一次 |
| 按前缀批量删除 | 二次输入 prefix + confirm-token（60s TTL） |
| 清空所有键 | 沿用现有 `/api/clear_cache`，**不在本页暴露入口**（保留端点供特殊场景） |
| 裸键批量删除 | **禁止**（裸键本身可能误用，应先排查） |

---

## 6. 性能与可靠性

### 6.1 SCAN 性能保护

- 单次请求 `limit` 上限 1000，前端默认 200
- 单次 SCAN `COUNT=100`，循环累积
- 前端「加载更多」按钮代替自动加载，避免无意义遍历
- overview 端点结果缓存 60s，避免频繁全量 SCAN

### 6.2 内存降级兼容

Redis 不可用时，`_InMemoryFallback` 提供等价方法，管理页仍可展示降级数据。页面顶部显示「⚠ 当前为内存降级模式，仅展示当前 worker 进程的缓存」警告。

### 6.3 大 key 保护

- `memory_usage` 单键查询单独发起，列表只展示大小数字
- 单键详情对 list / zset 成员截断到 200 个，提示 `truncated`
- string 值超 200 字符截断显示

### 6.4 多 Worker 一致性

`redis_client` 是进程级单例，但实际数据存 Redis 共享存储，无多 Worker 一致性问题。`_InMemoryFallback` 模式下数据是 worker 隔离的，管理页应明确提示「内存降级模式仅展示当前 worker」。

---

## 7. 实施计划

### Phase 1：MVP（P0 能力）

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | `redis_client` 扩展 `scan` / `type` / `ttl` / `hkeys` / `llen` / `lrange` / `smembers` / `memory_usage` / `dbsize`，同步补齐 `_InMemoryFallback` | 4h |
| 2 | 新建 `src/api/admin_redis.py`，实现 overview / list / detail / delete 四个端点 | 4h |
| 3 | 前端新建 `frontend/src/components/admin/RedisCacheManager.vue`，含左侧树、列表、单键详情弹框 | 6h |
| 4 | 单测：`tests/unit/test_redis_client_scan.py`、`tests/integration/test_admin_redis.py` | 3h |
| 5 | 联调 + CodeReview | 2h |

**Phase 1 验收**：
- 平台管理员能登录管理后台，进入「Redis 缓存」页
- 能看到所有键的列表（键名、类型、TTL、大小）
- 能按 `CacheKeys` 前缀过滤
- 能搜索键名子串
- 能查看单键详情（按类型分支渲染）
- 能删除单键（含确认 + 审计日志）
- 内存降级模式下页面可用

### Phase 2：增强（P1 + P2 能力）

| 步骤 | 内容 | 工作量 |
|------|------|--------|
| 1 | 裸键识别与告警（左侧树「⚠ 裸键」节点） | 2h |
| 2 | 按前缀批量删除（confirm-token 机制） | 3h |
| 3 | overview 端点缓存 + 强制刷新 | 1h |
| 4 | 「查看原文」二次密码确认（敏感值脱敏后的逃生通道） | 3h |

### Phase 3：可选增强

- 键导出 CSV（线下分析）
- 键 TTL 修改（运维场景，非必要不做）
- 实时刷新开关（默认关）

---

## 8. 风险与权衡

### 8.1 SCAN vs KEYS

- **SCAN**：游标式，不阻塞 Redis 主线程，但可能返回重复键（前端需去重）
- **KEYS**：一次性返回，但生产阻塞，**禁用**

**决策**：用 SCAN，前端按键名去重。`delete_cached_pattern` 工具函数仍用 `keys()`，但仅在内部运维场景调用，管理页不复用。

### 8.2 实时扫描 vs 维护键清单

- **实时扫描**：准确，但每次遍历 Redis
- **维护清单**：快，但需业务代码每次写入时登记（侵入性强，易遗漏）

**决策**：实时扫描，限制 `limit` ≤ 1000 + overview 缓存 60s，体验可接受。

### 8.3 跨租户可见性

平台管理员视角应能看所有租户的键，键名含 `tenant_id` 自然区分。**不提供**「按租户筛选」下拉（平台管理员不需要按租户维度查缓存，搜索框已够用）。

### 8.4 裸键批量删除

裸键本身可能是误用，**禁止批量删除**，强制人工逐个排查。这避免误删未知业务产生的裸键，倒逼开发者补登记 `CacheKeys`。

### 8.5 敏感值脱敏 vs 排查便利

Phase 1 全脱敏，管理员排查 token 问题时可能需要看原文。Phase 2 提供「查看原文」二次密码确认通道，平衡安全与便利。

---

## 9. 验收清单

### Phase 1 验收

- [ ] `redis_client` 新增 9 个方法 + 单测通过
- [ ] `/api/admin/redis/overview` 返回正确统计
- [ ] `/api/admin/redis/keys` 支持 `prefix` / `search` / `cursor` / `limit` 参数
- [ ] `/api/admin/redis/keys/{key}` 按类型分支返回 value / members
- [ ] `/api/admin/redis/keys/{key}` DELETE 删除单键 + 写审计日志
- [ ] 非平台管理员访问返回 403
- [ ] 前端页面在平台管理后台侧边栏可见
- [ ] 左侧前缀树按 `CacheKeys` 分组
- [ ] 单键详情弹框按类型渲染
- [ ] 删除操作有确认弹框
- [ ] 内存降级模式下页面可用 + 顶部警告
- [ ] `./scripts/dev_test.sh tests/unit/test_redis_client_scan.py tests/integration/test_admin_redis.py` 全绿
- [ ] `cd frontend && npm run build` 0 错误

---

## 10. 相关文档

- [cache_usage.md](./cache_usage.md) — 缓存使用规范（每个缓存的键模式、TTL、失效时机）
- [backend_dev.md 缓存使用规范](../../.claude/rules/backend_dev.md) — 缓存开发规范
- [backend_dev.md 临时主题日志](../../.claude/rules/backend_dev.md) — `tlog` 用法
- [list-page-convention.md](../../.claude/rules/list-page-convention.md) — 列表页规范
- [detail-page-convention.md](../../.claude/rules/detail-page-convention.md) — 详情页规范
- [dev_workflow.md](../../.claude/rules/dev_workflow.md) — 三智能体开发流程
