# 后端开发规范

## 日志规范
**本项目后端统一使用 `loguru` 作为日志库，禁止使用标准库 `logging`。**

```python
from loguru import logger

# 后端日志：复杂业务逻辑长期保留
logger.info('后端日志：开始处理用户请求')

# 后端日志：异常捕获（必须用 opt(exception=True) 或 logger.exception，才能把堆栈写入错误日志库）
logger.opt(exception=True).error(f'后端日志：数据库连接失败: {e}')

# 注意：loguru 不识别标准库 logging 的 exc_info=True 参数！
# ❌ logger.error(f'...: {e}', exc_info=True) 不会捕获堆栈，
#    exc_info 只是普通 kwarg 被塞进 extra，record['exception'] 恒为 None，
#    log_error 表的 traceback 列永远为空。正确写法见上。

# 临时调试日志（bug 修复后删除）
logger.debug(f'临时调试：请求参数 {params}')
```

### 临时主题日志（tlog）

主日志文件单日可达数千上万条，针对某个具体问题（如「语音合并」「会话取消」）排查时，在主日志中筛选非常低效。**调试某一主题时，使用 `tlog` 把日志单独写到独立文件**，与主日志完全隔离。

```python
from src.core.temp_logger import tlog

# 基本用法：日志写入 log/temp/语音合并.log
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
- 日志文件位于 `log/temp/{topic}.log`，与主日志完全隔离，不在控制台和 `aid-work-agent.log` 中出现
- 文件名即主题名，中英文均可（非法字符自动替换为 `_`）
- 主题名按调试场景命名（如 `语音合并`、`会话取消`、`工具超时`），一个主题对应一个文件
- 多线程写同一主题文件时加锁，避免日志交错

**使用规范**：
- **仅用于临时调试**，bug 修复后必须删除 `tlog` 调用
- 主题名应明确指向具体问题，避免 `debug`、`test` 等模糊命名
- 服务器排查时，直接下载 `log/temp/{topic}.log` 即可，无需在主日志中过滤
- 整个 `log/temp/` 目录可随时清空，不影响主流程

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

### 假异步（Fake Async）规范

**核心规则**：在 `async def` 函数中调用同步阻塞函数（`subprocess.run`、`requests.post`、`time.sleep`、Pandoc/LibreOffice/ripgrep 等子进程调用、CPU 密集计算）**必须**通过 `asyncio.to_thread()` 包裹，否则会阻塞 UvicornWorker 的 asyncio 事件循环，使该 worker 上的所有其他并发请求在阻塞期间无法调度。

**关键定位原则**：修复点在 **async 入口层**，不是底层同步模块。

- 底层同步模块（如 `pdf_renderer._render_with_poppler`、`md_to_word._pandoc_convert`、`ppt.renderer.NodePptRenderer.render`）保持 `def` 同步，**不要**在同步函数里写 `await asyncio.to_thread(...)`（语法错误）
- 在调用它们的 `async def` 入口（如 `*_process_tool.py` 的 `_handle_*` handler、`convert_async`、`BaseTool.execute`）里用 `await asyncio.to_thread(sync_func, *args, **kwargs)` 包裹

**正确示例**：

```python
# ✅ 底层保持同步
def render_pages(file_path: str, ...) -> Dict[str, Any]:
    result = subprocess.run(cmd, ...)  # 同步函数里直接调，没问题
    return ...

# ✅ async 入口用 to_thread 包裹
async def _handle_render_pages(self, ctx, params) -> Dict:
    file_path = self._resolve_file(ctx.file_paths[0])
    return await asyncio.to_thread(
        render_pages, file_path,
        pages=params.get("pages"),
        dpi=params.get("dpi", 150),
    )
```

**错误示例**：

```python
# ❌ async 函数里直接调同步阻塞函数（假异步）
async def _handle_render_pages(self, ctx, params) -> Dict:
    file_path = self._resolve_file(ctx.file_paths[0])
    return render_pages(file_path, ...)  # 阻塞事件循环

# ❌ 在同步函数里写 await（语法错误）
def _render_with_poppler(path, ...) -> Dict:
    result = await asyncio.to_thread(subprocess.run, cmd, ...)  # SyntaxError
```

**`to_thread` 参数传递**：`asyncio.to_thread(func, *args, **kwargs)`，第一个参数是函数对象（不要加括号调用），后续参数传给该函数。`to_thread(func())` 会在主线程同步执行后把返回值传给 `to_thread`，失去异步化意义。

**判断标准**：

| 场景 | 是否需要 `to_thread` |
|------|---------------------|
| `async def` 里调 `subprocess.run` / `requests.post` / `time.sleep` | ✅ 必须 |
| `async def` 里调底层同步函数（底层内部有 subprocess/网络/磁盘 IO） | ✅ 必须 |
| `async def` 里调纯内存计算（<1ms） | ❌ 不需要 |
| 同步 `def` 函数里调 `subprocess.run` | ❌ 不需要（同步函数本就跑在调用方的线程里） |
| 守护线程 / APScheduler worker 线程里的 `time.sleep` | ❌ 不需要（不在事件循环里） |

**已修复的假异步案例**（2026-07）：

| 文件 | 修复点 |
|------|--------|
| `src/tools/file/grep_tool.py` | `execute` 里 `subprocess.run(rg)` 用 `to_thread` 包裹 |
| `src/tools/pdf/pdf_process_tool.py` | `_handle_render_pages` / `_handle_validate` 用 `to_thread` 包裹 `render_pages` / `validate_pdf` |
| `src/tools/word/md_to_word.py` | `convert_async` 内部 `_convert_sync`（Pandoc 转换）用 `to_thread` 包裹 |
| `src/tools/ppt/ppt_process_tool.py` | `_handle_html` / `_handle_spec` / `_generate_ppt` / `execute` 中调 `_render_node_spec` / `_apply_quality_validation` / `_check_node_renderer_ready` 用 `to_thread` 包裹 |

**检测方法**：

```bash
# 查找 async def 函数里直接调 subprocess.run 但未 to_thread 包裹的位置
grep -rn "subprocess\.run" src/ --include="*.py" | grep -v "to_thread"
# 逐个检查对应函数是否是 async def，若是则需要 to_thread 包裹
```

## 包初始化副作用规范（Python `__init__.py` 反模式）

**核心规则**：包的 `__init__.py` 和模块顶层**禁止**执行重计算或创建单例对象。包初始化应该是惰性的——任何对包内任意子模块的 import 都不应触发副作用。

### 反模式（禁止）

```python
# ❌ src/core/__init__.py
from .agent import master_agent   # 顶层 import 立即触发 Agent 构造
                                    # → 注册 27 个工具、加载 skills/subagents
                                    # → 任何对 src.core.* 的间接 import 都被迫拉起整套环境

# ❌ src/core/agent.py（模块底部）
master_agent = Agent(is_master=True)   # 模块级实例化，import 该模块即执行

# ❌ src/config/__init__.py
from .logging import setup_logging     # logging 内部 import src.core.log_retention
                                        # → 触发 src.core/__init__.py → 全套 Agent 启动
```

**真实事故**：曾经 `from src.channels.wecom_personal_rpa.archive import wecom_finance_sdk`（一个纯 ctypes 封装）被迫拉起 100+ 个 src 模块、构造 Agent + 27 工具 + 8 子智能体，根因就是 `src.core/__init__.py` 在顶层 import `master_agent`。

### 正确做法：模块级 `__getattr__` 懒加载

Python 3.7+ 支持模块级 `__getattr__`，属性访问时才执行：

```python
# ✅ src/core/__init__.py
from typing import Any


def __getattr__(name: str) -> Any:
    """按需导出，避免顶层 import 触发 Agent 构造"""
    if name in ("Agent", "AgentMode"):
        from src.core.agent import Agent, AgentMode
        return {"Agent": Agent, "AgentMode": AgentMode}[name]
    if name in ("master_agent", "agent"):
        from src.core.agent import get_master_agent
        return get_master_agent()
    raise AttributeError(f"module 'src.core' has no attribute {name!r}")


__all__ = ["Agent", "master_agent"]
```

```python
# ✅ src/core/agent.py（单例改为延迟构造函数）
_master_agent_instance: Optional["Agent"] = None


def get_master_agent() -> "Agent":
    """返回 master_agent 单例，第一次调用时构造"""
    global _master_agent_instance
    if _master_agent_instance is None:
        _master_agent_instance = Agent(is_master=True)
    return _master_agent_instance


def __getattr__(name: str):
    """让 `from src.core.agent import master_agent` 仍能工作，但延迟到首次访问"""
    if name in ("master_agent", "agent"):
        return get_master_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### 判断标准

| 代码 | 是否有副作用 |
|------|------------|
| 模块顶层 `class Foo: ...` | ✅ 安全（类定义不实例化） |
| 模块顶层 `foo = Foo()` | ⚠️ **重计算/单例创建**则禁止 |
| `__init__.py` 顶层 `from .xxx import yyy`（yyy 是类/函数） | ✅ 安全 |
| `__init__.py` 顶层 `from .xxx import yyy`（yyy 是单例对象） | ❌ 禁止，改用 `__getattr__` |
| 模块顶层 `db_engine = create_engine(...)` | ❌ 禁止（建立连接池） |
| 模块顶层 `redis_client = RedisClient()` | ⚠️ 检查构造是否建立连接 |

### 排查方法

```python
import sys
before = set(sys.modules.keys())
from src.xxx import yyy        # 被怀疑的 import
after = set(sys.modules.keys())
new = sorted([m for m in (after - before) if m.startswith('src.')])
print(f'拉起 {len(new)} 个 src 模块')
print('是否拉起 agent:', any('src.core.agent' in m for m in new))
```

如果一个看似轻量的 import 拉起几十个模块，必然存在包初始化副作用。逐层向上找 `__init__.py` 或模块顶层单例即可定位元凶。

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

## 下载接口规范（限速 + 原生下载）

服务器公网出带宽仅 5Mbps（约 625KB/s），大文件下载会长时间占满带宽并拖垮其他用户。所有文件下载接口必须遵守本节规范。

### 1. nginx 下载限速（新增下载点必做）

`deploy/agent.aidingyi.cn.conf`、`agent2.aidingyi.cn.conf`、`agent3.aidingyi.cn.conf` 三份配置中有一个「文件下载接口（单独限速）」的正则 location（单连接 `limit_rate 128k`（1Mbps）、前 1MB 全速 `limit_rate_after 1m`、单 IP 并发 `limit_conn 2`），**新增下载点必须把路由加入三份 conf 的正则**，否则不受限速保护。

当前已纳入限速的下载点：

| 路由 | 用途 |
|------|------|
| `/api/files/{file_id}/download` | 对话附件/图片下载 |
| `/api/knowledge/documents/{doc_id}/download` | 知识库文档下载 |
| `/api/saas/external-customers/attachments/download` | 外部客户附件下载 |
| `/api/saas/tenant/config-file/{subagent_name}` | 子智能体配置文件下载 |
| `/api/v1/travel-quote/{vehicles,meals,guides,fees,seasons,kb/attractions,kb/hotels}/export` | 旅游报价 Excel 导出 |
| `/api/v1/travel-quote/import/template` | 导入模板下载 |
| `/api/v1/channels/wecom-personal-rpa/files/{file_id}` | RPA 签名文件下载 |

**例外**：`/api/files/{file_id}`（inline 预览）不纳入限速 location，因为聊天图片会并发批量加载，`limit_conn` 并发限制会导致第 3 个请求直接 503、图片挂掉。小文件也基本不受 `limit_rate_after 1m` 影响。

### 2. 前端触发下载必须走「原生下载」，需要认证的下载点走「下载票据」，禁止 fetch + blob

`fetch + blob` 会把整个文件下载到内存后才弹保存框，期间用户零反馈（50MB 文件浏览器转圈超过 1 分钟）。前端统一使用 `frontend/web/utils/download.ts`：

- `triggerNativeDownload(url, filename)`：直链原生下载（免认证下载点，如 `/api/files/{file_id}/download`）
- `downloadViaTicket(downloadPath, filename)`：先 `POST {downloadPath}_ticket`（带认证 header，毫秒级）换取短期 HMAC 票据，再原生下载 `{downloadPath}?ticket=xxx`

票据机制（`src/core/download_ticket.py`）：5 分钟有效、绑定下载路径 + tenant_id + user_id + role、无状态不落库（密钥取 `DOWNLOAD_TICKET_SECRET` 环境变量，缺省由 `DATABASE_URL` 派生）。`TenantContextMiddleware` 对带 `ticket` 的下载请求在认证 header 缺失时验签还原租户上下文（`_TICKET_DOWNLOAD_PATHS` 白名单，新增下载点需同步）；`require_admin` 类端点用 `_require_admin_or_ticket` 做票据兜底（参考 `src/api/tenant_config_file.py`）。

各下载点的前端实现参考：知识库 `api/knowledge.ts` 的 `downloadDocument`、配置文件 `api/saasPermissions.ts` 的 `downloadConfigFile`、travel-quote 导出 `api/travelQuote.ts` 的 `downloadExport`。

### 3. 后端返回文件统一用 FileResponse / StreamingResponse

流式返回，不要 `open(...).read()` 一次性读入内存再返回。

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

**如需修改字段枚举值，注意前后端协调修改**：同时更新 `src/saas/models/enums.py`（后端）和 `frontend/web/api/enums.ts`（前端）。

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
    │   ├── memory/               # 用户长期记忆（LongTermMemory）
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
| `report/` | 统计报表、运营报告（含数据分析工具的图表/导出表） |
| `memory/` | 用户长期记忆文件（LongTermMemory） |
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

## 图片资产使用规范

**核心规则**：所有图片资产的注册、寻址、传递必须通过 `src/core/image_asset.py` 的 `ImageRegistry` 与 `ImageRef`。

**关联文档**：架构设计见 [image-asset-pipeline-design.md](../../docs/system/image-asset-pipeline-design.md)，开发计划见 [plan-image-asset-pipeline.md](../../docs/plans/plan-image-asset-pipeline.md)。

### 强制要求

任何在工具结果、SSE 事件、API 响应中传递图片的字段，**必须**使用 `ImageRef`（`List[ImageRef]` 或 `Optional[ImageRef]`），**禁止**：
- 业务代码直接拼 `storage/` 路径写图片
- 接口之间传 `file_path` / `url` / `base64` 裸字段
- 知识库图片使用外部 URL（必须落地租户目录，防止链接腐烂 + 审计 + 租户隔离）

### 注册入口

```python
from src.core.image_asset import get_image_registry, ImageRef

registry = get_image_registry()
ref: ImageRef = await registry.register(
    source_path="/tmp/generated_image.png",
    tenant_id=tenant_id,
    user_id=user_id,
    display_name="生成的图表.png",
    source="tool_generated",  # knowledge_base / tool_generated / user_upload / web_fetch / screenshot
    usage="inline",           # inline / attachment / embedded / thumbnail
)
# ref.file_id 与 cp 同命名空间，ref.download_url = /api/files/{file_id}/download
```

### TTL 与清理

| source | 默认 TTL | 清理策略 |
|--------|---------|---------|
| `knowledge_base` | 永久（不调 expire） | 文档删除时级联清理 |
| `tool_generated` / `web_fetch` / `user_upload` / `screenshot` | 86400s（24h） | `cleanup_temp` 仅清理 `usage ∈ (inline, embedded)` 的过期图 |

### Web 图片抓取

`ImageRegistry.fetch_to_local(url, tenant_id, ...)` 自动通过 `image_fetch_url:{sha256(url)}` 做 URL 去重缓存（TTL 7 天），同 URL 不重复下载；单文件超过 10MB 拒绝下载；超时 15s。

### 注意事项

- `PERMANENT_TTL(-1)` 不调用 `redis_client.expire`（内存降级版 seconds≤0 立即删键）；只有正数 TTL 才调 expire
- Redis key 与 `cp_tool._register_download` 完全同命名空间（`uploaded_file:{file_id}`），现有 `/api/files/{file_id}/download` 路由可直接下载
- ImageRegistry 是惰性单例（`get_image_registry()`），模块顶层**无**实例化副作用，import 该模块不会拉起 master_agent
