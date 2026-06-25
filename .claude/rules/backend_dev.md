# 后端开发规范

## 日志规范
**本项目后端统一使用 `loguru` 作为日志库，禁止使用标准库 `logging`。**

```python
from loguru import logger

# 后端日志：复杂业务逻辑长期保留
logger.info('后端日志：开始处理用户请求')

# 后端日志：异常捕获
logger.error(f'后端日志：数据库连接失败: {e}', exc_info=True)

# 临时调试日志（bug 修复后删除）
logger.debug(f'临时调试：请求参数 {params}')
```

### 临时主题日志（tlog）

主日志文件单日可达数千上万条，针对某个具体问题（如「语音合并」「会话取消」）排查时，在主日志中筛选非常低效。**调试某一主题时，使用 `tlog` 把日志单独写到独立文件**，与主日志完全隔离。

```python
from src.core.temp_logger import tlog

# 基本用法：日志写入 log/agent/temp/语音合并.log
tlog("语音合并", f"合并 {a} + {b} -> {c}")

# 支持命名占位符（推荐，避免拼接 f-string 时的字段冗余）
tlog(
    "语音合并",
    "处理 session={sid}..., original_len={o_len}, merged={merged}",
    sid=session_id[:20],
    o_len=len(original),
    merged=was_merged,
)

# 指定日志级别
tlog("语音合并", "处理失败: {err}", err=e, level="ERROR")
```

**特点**：
- 日志文件位于 `log/agent/temp/{topic}.log`，与主日志完全隔离，不在控制台和 `aid-work-agent.log` 中出现
- 文件名即主题名，中英文均可（非法字符自动替换为 `_`）
- 主题名按调试场景命名（如 `语音合并`、`会话取消`、`工具超时`），一个主题对应一个文件
- 多线程写同一主题文件时加锁，避免日志交错

**使用规范**：
- **仅用于临时调试**，bug 修复后必须删除 `tlog` 调用
- 主题名应明确指向具体问题，避免 `debug`、`test` 等模糊命名
- 服务器排查时，直接下载 `log/agent/temp/{topic}.log` 即可，无需在主日志中过滤
- 整个 `log/agent/temp/` 目录可随时清空，不影响主流程

## 错误处理规范
所有 API 错误响应必须包含 `debug` 字段，且必须过滤敏感信息：

```python
import re

SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
]

def sanitize_error_info(error_msg: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg

# 错误响应格式
return {
    "success": False,
    "error": "操作失败，请稍后重试",
    "debug": sanitize_error_info(str(e))
}
```

## 异步/同步开发规范
**避免 `await` 调用同步方法导致的 `TypeError`**：

| 类型 | 定义 | 调用 |
|------|------|------|
| 同步 | `def method()` | `obj.method()` |
| 异步 | `async def method()` | `await obj.method()` |

异步路由调用同步服务时，使用 `asyncio.to_thread()`：
```python
import asyncio

@router.get("/data")
async def get_data():
    service = MyService()
    result = await asyncio.to_thread(service.sync_method)  # ✅ 正确
    return result
```

## Gunicorn 多 Worker 进程内存隔离
**核心问题**：Gunicorn 启动多个 worker 进程时，每个 worker 拥有独立的 Python 内存空间。

| 方案 | 适用场景 |
|------|---------|
| 磁盘刷新 | 低频读操作（如管理后台配置读取） |
| Redis 共享缓存 | 高频读操作 |
| 数据库 | 持久化数据 |
| 单 worker | 开发/调试（`gunicorn --workers 1`） |

> **磁盘/数据库是共享的，内存是隔离的。** 任何依赖内存状态且跨请求的读写操作，都必须考虑多 worker 一致性。

### 禁止依赖内存变量存储跨请求状态

**原则**：所有需要在多个 HTTP 请求之间保持的状态，**禁止**使用进程内变量（如 `dict`、`set`、`list`）存储。

| ❌ 禁止（内存变量） | ✅ 正确（共享存储） |
|-------------------|-------------------|
| `self._pending_clarifications: dict = {}` | `redis_client.hset("pending_clarification:{sid}", ...)` |
| `self.cancelled_sessions: set = set()` | `redis_client.sadd("cancelled_session:{sid}", ...)` |
| `uploaded_files: dict = {}` | `redis_client.hset("uploaded_file:{fid}", ...)` |
| `self._plans: dict = {}` | `redis_client.set("execution_plan:{sid}", ...)` |
| `self._task_records: dict = {}` | `redis_client.hset("task_record:{eid}", ...)` |

**判断标准**：如果该变量在代码注释中被描述为"跨请求共享"、"全局缓存"或"内存缓存"，则必须使用 Redis 或数据库替代。

**降级策略**：使用 `src.core.redis_client.RedisClient`，在 Redis 不可用时自动降级到内存，并记录 `logger.warning`。

## API 接口命名规范
接口名称应与 Python 方法名保持一致，使用具体、有明确指向性的命名：

```python
# ✅ 正确
@router.post("/search_documents")
async def search_documents(request: SearchRequest):

# ❌ 错误
@router.post("/search")
async def search_documents(request: SearchRequest):
```

## 枚举值定义规范
**涉及到字段枚举值的判断代码，必须以 `src/saas/models/enums.py` 为准。**

所有 SaaS 相关表字段的枚举值（如租户状态、订阅状态、支付状态等）统一在 `src/saas/models/enums.py` 中定义。

```python
# ✅ 正确：使用枚举类
from src.saas.models import TenantStatus

if tenant.status == TenantStatus.ACTIVE:
    ...

# ❌ 错误：硬编码数字或字符串
if tenant.status == 1:
    ...
if tenant.status == "active":
    ...
```

### 枚举值设计原则

1. **数据库字段类型尽量使用 TEXT**：这样管理员在检查数据时无需查找枚举定义即可理解字段含义。

2. **前后端枚举值尽量与数据库存储值一致**：减少 mapping 翻译，提高代码可读性。

```python
# ✅ 推荐：枚举值 = 数据库存储值
class TenantStatus(str, Enum):
    ACTIVE = "active"       # 数据库存 "active"
    SUSPENDED = "suspended" # 数据库存 "suspended"

# ❌ 不推荐：枚举值需额外映射
class TenantStatus(IntEnum):
    ACTIVE = 1       # 数据库存 1，需翻译为 "active"
    SUSPENDED = 0    # 数据库存 0，需翻译为 "suspended"
```

3. **前端显示值不受此限制**：显示值通常为中文，通过映射表实现（如 `TenantStatusMap`）。

**如需修改字段枚举值，注意前后端协调修改**：同时更新 `src/saas/models/enums.py`（后端）和 `frontend/src/api/enums.ts`（前端）。

## 业务数据表（bs_）CRUD 规范

所有业务数据表（`bs_` 开头）的增删改查接口必须遵循以下规范：

### 创建数据

在能确定创建用户时，必须附带 `user_id` 字段的值：

```python
# ✅ 正确：附带 user_id
cursor.execute(
    "INSERT INTO bs_example (tenant_id, user_id, name, created_at) VALUES (%s, %s, %s, NOW())",
    (tenant_id, user_id, name)
)

# ❌ 错误：遗漏 user_id
cursor.execute(
    "INSERT INTO bs_example (tenant_id, name) VALUES (%s, %s)",
    (tenant_id, name)
)
```

`created_at` 字段有数据库默认值 `DEFAULT CURRENT_TIMESTAMP`，后端代码可以不显式指定，数据库会自动填充。

### 列表查询

列表页接口默认按 `created_at DESC` 排序，确保用户一眼看到最新数据：

```python
# ✅ 正确：按创建时间倒序
cursor.execute(
    "SELECT * FROM bs_example WHERE tenant_id = %s ORDER BY created_at DESC",
    (tenant_id,)
)

# ❌ 错误：无排序或默认升序
cursor.execute(
    "SELECT * FROM bs_example WHERE tenant_id = %s",
    (tenant_id,)
)
```

### 表结构要求

详见 [database_dev.md](./database_dev.md)「业务数据表必需字段」章节。

## SaaS 租户隔离规范

### 核心规则

所有需要租户隔离的 API 必须遵循以下规则：

1. **租户 ID 解析**：由 `TenantContextMiddleware` 统一解析 `tenant_id` 并设置到：
   - `request.state.tenant_id`
   - ContextVar `current_tenant_id`

2. **优先级规则**：
   ```
   X-Tenant-Id Header（平台管理员代租户操作） > 用户 token 中的 tenant_id
   ```
   - 平台管理员（role=platform_admin）本身没有租户属性，通过 `X-Tenant-Id` header 指定目标租户
   - 租户管理员和普通用户使用账号本身的 `tenant_id`，禁止越权访问其他租户

3. **数据库查询必须加租户过滤**：
   ```python
   # ✅ 正确：必须带 tenant_id 过滤
   cursor.execute("SELECT * FROM documents WHERE tenant_id = %s", (tenant_id,))

   # ❌ 错误：不带租户过滤会导致跨租户数据泄露
   cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
   ```

### X-Tenant-Id Header 处理逻辑

| 路由前缀 | 解析方法 | 是否处理 X-Tenant-Id |
|---------|----------|----------------------|
| `/api/saas/*` | `_resolve_admin_tenant` | ✅ 是 |
| `/api/sessions/*` | `_resolve_session_tenant` | ✅ 是 |
| `/api/chat/*` | `_resolve_user_tenant` | ✅ 是 |
| 其他 `/api/*` | `_resolve_user_tenant` | ✅ 是 |

**所有 `/api/*` 路径都支持 `X-Tenant-Id`，无需自己手动解析。**

### 注意事项

- 业务数据表（`bs_` 开头）必须包含 `tenant_id` 字段，详见 [database_dev.md](./database_dev.md)
- 平台管理员访问租户前台 (`/t/{tenant_id}`) 时，前端必须在所有 API 请求中添加 `X-Tenant-Id` header
- 租户管理员只能访问自己租户的数据，`require_admin` 会验证 `X-Tenant-Id` 与用户自身 `tenant_id` 是否一致

## 租户附件存储规范

**核心规则**：所有租户产生的附件（上传文件、生成文件、导出文件等），**必须**统一存放到 `storage/tenants/{tenant_id}/` 目录下，并按业务场景建立子目录分类存放。

### 目录结构

```
storage/
└── tenants/
    ├── {tenant_id_a}/
    │   ├── conversation/         # 对话中产生的附件
    │   ├── knowledge/            # 知识库附件
    │   ├── export/               # 业务导出文件
    │   ├── report/               # 报表/统计文件
    │   └── ...                   # 其他业务场景
    └── {tenant_id_b}/
        └── ...
```

### 常见业务场景子目录

| 子目录 | 用途 |
|--------|------|
| `conversation/` | 对话过程中用户上传/Agent 生成的附件 |
| `knowledge/` | 知识库文档、向量化文件 |
| `export/` | 业务数据导出（Excel、CSV 等） |
| `report/` | 统计报表、运营报告 |
| `avatar/` | 用户/企业头像、Logo |
| `temp/` | 临时文件（必须有清理机制） |

### 实现要点

1. **统一路径工具**：禁止在业务代码中直接拼路径字符串，必须通过统一的工具函数或配置项获取：

   ```python
   # ✅ 正确：使用工具函数
   from src.core.storage import get_tenant_storage_path

   file_path = get_tenant_storage_path(
       tenant_id=tenant_id,
       scene="conversation",
       filename=filename
   )
   # 返回：storage/tenants/{tenant_id}/conversation/{filename}

   # ❌ 错误：硬编码或随意拼路径
   file_path = f"storage/uploads/tenant_{tenant_id}/files/{filename}"
   ```

2. **tenant_id 来源**：必须从 `request.state.tenant_id` 或 `TenantContext` 获取，禁止从用户输入或 URL 参数中直接拼接。

3. **目录自动创建**：写入文件前必须确保目标目录存在：

   ```python
   import os
   os.makedirs(os.path.dirname(file_path), exist_ok=True)
   ```

4. **历史目录迁移**：旧的 `storage/uploads/tenant_xxx/` 目录应逐步迁移到新结构，禁止新老结构并存产生歧义。

### 优点

- **备份友好**：备份整个 `storage/tenants/` 即可备份所有租户文件；按租户打包也只需打包对应子目录
- **统计友好**：按租户维度统计附件数量、占用空间时，遍历一个目录即可
- **隔离清晰**：删除某个租户时，只需删除其子目录，不会误删其他租户数据
- **权限清晰**：可针对整个租户目录设置文件系统级别的访问权限

### 注意事项

- 禁止将附件存放到 `storage/` 根目录或 `storage/uploads/` 根目录
- 禁止在租户目录下跳过场景子目录直接存放文件（如 `storage/tenants/{id}/xxx.pdf`）
- 文件名必须保证唯一性，建议使用 `{prefix}_{uuid}.{ext}` 格式（如 `file_af08155fe5d9.docx`）

## 缓存使用规范

**核心文档**：系统缓存使用全景见 [cache_usage.md](./cache_usage.md)，包含所有缓存的类型、存储方式、键模式、TTL 和失效策略。

### 新增缓存时必须做的事

1. **更新 `cache_usage.md`**：在对应分类下记录缓存名称、存储类型、键模式、TTL、失效时机和源文件
2. **在 `cache_utils.py` 中注册前缀**：如需新键前缀，在 `CacheKeys` 类中添加
3. **定义明确的失效策略**：不能只有 TTL 过期，必须有主动失效入口（如权限变更时清除相关缓存）
4. **键命名遵循规范**：`{cache_prefix}:{identifier}`，使用 `cache_utils.py` 中的 `CacheKeys` 常量，禁止硬编码字符串

### 禁止项

- **禁止绕过 `redis_client`**：所有缓存操作必须通过 `src/core/redis_client.py`，禁止直接使用 `redis-py`
- **禁止硬编码键前缀**：必须使用 `CacheKeys` 类定义的常量
- **禁止无 TTL 的缓存**：所有缓存必须设置合理的 TTL，防止内存无限增长
- **禁止跨租户缓存污染**：涉及租户的缓存键必须包含 `tenant_id`，使用复合键隔离

## 文件存储使用规范

**核心文档**：系统文件存储全景见 [file_usage.md](./file_usage.md)，包含目录结构、新旧双轨路径、文件命名规范、清理策略等。

### 租户附件存储

所有由租户产生的附件（上传文件、生成文件、导出文件等），**必须**使用 `src/core/storage.py` 中的工具函数存入 `storage/tenants/{tenant_id}/` 的子目录下：

```python
from src.core.storage import get_tenant_storage_path, ensure_tenant_storage_dir

# ✅ 正确：使用工具函数
file_path = get_tenant_storage_path(
    tenant_id=tenant_id,
    scene="conversation",  # conversation / knowledge / export / report / avatar / temp
    filename=filename
)

# ❌ 错误：自行拼路径
file_path = f"storage/uploads/tenant_{tenant_id}/files/{filename}"
```

### 新增文件存储时必须做的事

1. **更新 `file_usage.md`**：在对应分类下记录存储位置、文件类型、命名规范和调用方
2. **使用标准工具函数**：租户附件必须使用 `src/core/storage.py` 中的 `get_tenant_storage_path` 等函数
3. **遵循文件命名规范**：使用 `{prefix}_{uuid12}.{ext}` 格式，确保文件名唯一性

### 禁止项

- **禁止写入 `uploads/` 旧路径**：新代码必须写入 `tenants/` 新路径，禁止继续使用 `storage/uploads/{tenant_id}/` 模式
- **禁止自行拼路径**：必须使用 `src/core/storage.py` 中的工具函数，禁止直接拼接路径字符串
- **禁止跳过场景子目录**：禁止在租户目录下直接存放文件（如 `storage/tenants/{id}/xxx.pdf`）
- **禁止写入 `storage/` 根目录或 `storage/uploads/` 根目录**

### 迁移说明

现有 `uploads/` 中的文件暂不迁移，后续择机统一迁移到 `tenants/` 新目录结构。新代码无需考虑旧文件兼容，但读取时可参考 `file_usage.md` 第 5.2 节的兼容桥接模式。
