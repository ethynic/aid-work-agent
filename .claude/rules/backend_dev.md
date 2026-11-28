# 后端开发规范

## 日志规范

**统一使用 `loguru`，禁止标准库 `logging`**（`src/` 内 `import logging` 清零，唯一例外 `src/config/logging.py` 的 InterceptHandler——把标准库 logging 含第三方库统一转发进 loguru）。

```python
from loguru import logger

# 异常捕获必须用 opt(exception=True) 或 logger.exception，堆栈才能进 log_error 表
logger.opt(exception=True).error(f'后端日志：数据库连接失败: {e}')

# ❌ loguru 不识别 exc_info=True：它只被塞进 extra，record['exception'] 恒为 None，
#    log_error 表 traceback 列永远为空
logger.error(f'...: {e}', exc_info=True)

# 临时调试日志（bug 修复后删除）
logger.debug(f'临时调试：请求参数 {params}')
```

### 临时主题日志（tlog）

主日志单日上万条，排查具体主题（如「语音合并」「会话取消」）时用 `tlog` 写独立文件，与主日志完全隔离：

```python
from src.core.temp_logger import tlog

tlog("语音合并", f"合并 {a} + {b} -> {c}")
# 命名占位符（推荐）
tlog("语音合并", "处理 session={sid}..., merged={merged}", sid=session_id[:20], merged=was_merged)
tlog("语音合并", "处理失败: {err}", err=e, level="ERROR")
```

- 文件位于 `log/temp/{topic}.log`，服务器排查直接下载即可；多线程写同主题自动加锁
- **仅用于临时调试**，bug 修复后必须删除调用；主题名指向具体问题，禁止 `debug`/`test`
- 整个 `log/temp/` 可随时清空

## Runtime / CLI 服务端升级兼容

已交付客户的 Runtime＋CLI 不强制跟随服务端升级，服务端升级必须保留其原有功能：

- 保持旧 Device API 参数/响应、能力上报、工具发现与调用、回执/重投及计费语义兼容；旧任务应能跨服务端发布恢复，不能仅因配置指纹或装配方式变化被拒绝
- 新功能经能力上报协商按需启用；旧客户端缺能力时只给明确、可恢复的说明，不隐藏原有工具、不强制升级、不新造全局版本门禁。确实不能兼容的变更先说明影响与迁移方案
- 验证用实际已交付的旧客户端组合，复用有效旧包证据，只补变化影响的验证；模拟版本字段不等于旧二进制验收

## 错误处理规范

所有 API 错误响应必须含 `debug` 字段，且过滤敏感信息（`sanitize_error_info` 脱敏 password/api-key/token 等模式后返回）：

```python
return {
    "success": False,
    "error": "操作失败，请稍后重试",
    "debug": sanitize_error_info(str(e))
}
```

## 假异步（Fake Async）规范

**核心规则**：`async def` 里调用同步阻塞函数（`subprocess.run`、`requests.post`、`time.sleep`、Pandoc/LibreOffice 等子进程调用、CPU 密集计算）**必须** `asyncio.to_thread()` 包裹，否则阻塞 UvicornWorker 事件循环，该 worker 上所有并发请求无法调度。

**修复点在 async 入口层**：底层同步模块保持 `def`（在同步函数里写 `await` 是语法错误），在调用它们的 `async def` 入口里包裹：

```python
# ✅ 底层保持同步
def render_pages(file_path: str, ...) -> Dict[str, Any]:
    result = subprocess.run(cmd, ...)
    return ...

# ✅ async 入口用 to_thread 包裹
async def _handle_render_pages(self, ctx, params) -> Dict:
    return await asyncio.to_thread(render_pages, file_path, pages=params.get("pages"), dpi=params.get("dpi", 150))
```

- `to_thread(func, *args, **kwargs)` 第一参数是函数对象；`to_thread(func())` 会先同步执行、失去异步化意义

| 场景 | `to_thread` |
|------|-------------|
| `async def` 里调 subprocess / requests / time.sleep / 有 IO 的底层同步函数 | ✅ 必须 |
| 纯内存计算（<1ms）、同步 `def` 函数里调 subprocess、守护线程/APScheduler 线程里的 sleep | ❌ 不需要 |

```bash
# 检测：逐个检查命中函数是否 async def
grep -rn "subprocess\.run" src/ --include="*.py" | grep -v "to_thread"
```

## 包初始化副作用规范（Python `__init__.py` 反模式）

**核心规则**：包的 `__init__.py` 和模块顶层**禁止**执行重计算或创建单例——任何对包内子模块的 import 都不应触发副作用。曾有一个渠道子模块的 ctypes 封装 import 因此被迫拉起 100+ 个 src 模块、构造 Agent + 27 工具。

**单例改延迟构造**：模块级 `__getattr__` 懒加载（Python 3.7+）：

```python
# ✅ src/core/__init__.py
def __getattr__(name: str) -> Any:
    if name in ("Agent", "AgentMode"):
        from src.core.agent import Agent, AgentMode
        return {"Agent": Agent, "AgentMode": AgentMode}[name]
    if name in ("master_agent", "agent"):
        from src.core.agent import get_master_agent
        return get_master_agent()
    raise AttributeError(f"module 'src.core' has no attribute {name!r}")

# ✅ src/core/agent.py：get_master_agent() 首次调用才构造，
#    模块 __getattr__ 让 `from src.core.agent import master_agent` 仍可用
```

| 代码 | 副作用 |
|------|--------|
| 顶层 `class Foo: ...`、`from .xxx import` 类/函数 | ✅ 安全 |
| 顶层 `foo = Foo()`（重计算/单例）、`db_engine = create_engine(...)` | ❌ 禁止 |
| 顶层 `RedisClient()` 等构造 | ⚠️ 检查是否建立连接 |

```python
# 排查：轻量 import 拉起几十个 src 模块 → 逐层向上找 __init__.py 或顶层单例
before = set(sys.modules.keys()); from src.xxx import yyy
print([m for m in set(sys.modules) - before if m.startswith('src.')])
```

## 全局运行时变更的下游消费方盘点（2026-11-26 教训）

改**全局运行时基础设施**（子进程生命周期、DB 池初始化时机、环境注入、bootstrap/sitecustomize、连接池参数）时，必须盘点所有下游消费方并附进 PR 描述——动机不涉及的模块最易漏。

**事故**：08e40836 惰性化 skill 子进程 DB 池后，非 LLM 技能脚本经服务层间接读 DB 失败且静默降级，`load_api_config` 的「读取失败」与「租户未配置」不可区分，API 类技能新会话全挂。

```bash
# 技能脚本 DB 访问盘点（直接 + 间接经服务层），每个命中点确认有惰性 init 兜底
grep -rln "get_db_connection\|get_pooled_connection\|tenant_api_doc\|SubagentDefinitionDB\|SubagentEnvVarDB\|TenantAgentInstanceDB" \
  src/skills/*/scripts/*.py subagents/*/scripts/*.py
```

- 脚本直接 `get_db_connection` → 脚本内惰性 init 兜底（参照 `sms_cli.py`）；经服务层间接读 → 服务层入口兜底（参照 `tenant_api_doc._ensure_db_pool`），不指望脚本作者知道服务层有 DB 读
- 新增/修改降级路径 → 降级结果必须与合法业务态可区分（如 `reason: db_unavailable` vs `no_doc`），禁止共用文案静默吞掉

## Gunicorn 多 Worker 内存隔离

磁盘/数据库是共享的，**内存是隔离的**：跨请求状态**禁止**用进程内变量（`dict`/`set`/`list`）存储，必须用 Redis 或数据库。共享方案选型：高频读走 Redis、持久化走 DB、低频读可磁盘刷新、开发调试单 worker。

- 判断标准：变量在注释中被描述为「跨请求共享」「全局缓存」「内存缓存」即必须改共享存储（如 `self._pending_clarifications: dict` → `redis_client.hset("pending_clarification:{sid}", ...)`）
- 降级策略：用 `src.core.redis_client.RedisClient`，Redis 不可用时自动降级内存并 `logger.warning`

## 下载接口规范（限速 + 原生下载）

公网出带宽仅 5Mbps，大文件下载必须遵守本节。

**1. nginx 限速**：`deploy/agent{,2,3}.aidingyi.cn.conf` 三份配置各有「文件下载接口（单独限速）」正则 location（单连接 1Mbps、前 1MB 全速、单 IP 并发 2）。**新增下载点必须把路由加入三份 conf 正则**。已纳入：对话附件 `/api/files/{file_id}/download`、知识库文档、外部客户附件、配置文件、travel-quote 导出/模板。例外：`/api/files/{file_id}`（inline 预览）不纳入——聊天图片并发批量加载，`limit_conn` 会导致第 3 个请求 503。

**2. 前端下载策略**（统一走 `frontend/web/utils/download.ts`，禁止绕过策略函数直接拼 `<a>` 或 `window.location.href`）：

- `resolveDownloadStrategy(sizeBytes)` 分发：
  - `wechat_guide`：微信/企微内置浏览器（UA 含 MicroMessenger）——无论大小一律引导「··· 在浏览器打开后下载」（WKWebView 无下载管道，2026-10-27 事故：服务端 200 完整传输但 iOS 微信静默丢弃）
  - `blob`：非微信移动端且 <10MB（大小未知也按 blob）——`downloadViaBlob(url, filename, headers?)`
  - `native`：PC 端、移动端 ≥10MB——`triggerNativeDownload(url, filename)`
- 需认证的下载点用 `downloadViaTicket(downloadPath, filename)`：先 POST `{downloadPath}_ticket` 换短期 HMAC 票据（5 分钟、绑定路径+租户+用户+角色、无状态不落库，密钥 `DOWNLOAD_TICKET_SECRET` 缺省由 `DATABASE_URL` 派生），再原生下载 `?ticket=xxx`。`TenantContextMiddleware` 的 `_TICKET_DOWNLOAD_PATHS` 白名单与 `require_admin` 的 `_require_admin_or_ticket` 兜底需同步新增下载点
- 已接入入口：`DownloadFileCard.vue`、`AttachmentPreviewPanel.vue`；前端实现参考 `api/knowledge.ts` / `api/saasPermissions.ts` / `api/travelQuote.ts`

**3. 后端返回**统一 FileResponse / StreamingResponse（禁止 `open(...).read()` 一次性读入内存）。**Content-Disposition 必须带 ASCII filename fallback**：Starlette 只发 `filename*=utf-8''...`，部分国产移动内核解析不出会放弃下载；自构 disposition 头同时携带 `filename="ascii兜底"` + `filename*=utf-8''...`，参考 `src/api/message_share.py` 的 `content_disposition_header()`。

## API 接口命名规范

接口名与 Python 方法名一致、具体明确：`@router.post("/search_documents")` + `async def search_documents(...)`，禁止 `/search` 这类泛化路由名。

## 枚举值定义规范

字段枚举值判断代码必须以 `src/saas/models/enums.py`（后端）/ `frontend/web/api/enums.ts`（前端）为准，禁止硬编码；修改枚举值时**前后端协调同步**两处。

设计原则：数据库字段用 TEXT（管理员直读无需查定义）；**枚举值 = 数据库存储值**（`class TenantStatus(str, Enum): ACTIVE = "active"`，不用 IntEnum 免一层映射）；前端显示值用映射表（如 `TenantStatusMap`），不受此限。

## 业务数据表（bs_）CRUD 规范

- **创建**：能确定创建用户时必须带 `user_id`；`created_at` 有 DB 默认值，无需显式指定
- **列表查询**：默认 `ORDER BY created_at DESC`（最新在前）
- 表结构必需字段见 [database_dev.md](./database_dev.md)「业务数据表必需字段」

## SaaS 租户隔离规范

1. 租户 ID 由 `TenantContextMiddleware` 统一解析到 `request.state.tenant_id` 与 ContextVar `current_tenant_id`
2. 优先级：`X-Tenant-Id` header（平台管理员代租户操作，管理员本身无租户属性）> 用户 token 中的 tenant_id；租户用户禁止越权访问其他租户
3. **数据库查询必须带租户过滤**：`WHERE tenant_id = %s`，禁止仅按 id 查（跨租户泄露）

所有 `/api/*` 路径都支持 `X-Tenant-Id`（`/api/saas/*`、`/api/sessions/*`、`/api/chat/*`、其他分别经 `_resolve_admin_tenant` / `_resolve_session_tenant` / `_resolve_user_tenant`），无需自己手动解析。`require_admin` 会校验 `X-Tenant-Id` 与用户自身 tenant_id 一致；平台管理员访问租户前台时前端必须携带 `X-Tenant-Id`。

## 租户附件存储规范

**核心规则**：租户产生的所有附件（上传/生成/导出文件等）必须经 `src/core/storage.py` 工具函数存入 `storage/tenants/{tenant_id}/{scene}/`，**禁止**自行拼路径、写入 `storage/` 或 `storage/uploads/` 根目录、跳过场景子目录直接存放（如 `storage/tenants/{id}/xxx.pdf`）。

```python
from src.core.storage import get_tenant_storage_path
file_path = get_tenant_storage_path(tenant_id=tenant_id, scene="conversation", filename=filename)
# storage/tenants/{tenant_id}/conversation/{filename}；写入前 os.makedirs(dirname, exist_ok=True)
```

场景子目录：`conversation`（对话附件）/ `knowledge` / `export` / `report` / `memory`（长期记忆）/ `avatar` / `temp`（必须有清理机制）。

- `tenant_id` 从 `request.state.tenant_id` 或 `TenantContext` 获取，禁止从用户输入或 URL 参数拼接
- 文件名唯一：`{prefix}_{uuid12}.{ext}`（如 `file_af08155fe5d9.docx`）
- 旧 `storage/uploads/tenant_xxx/` 暂不迁移、禁止新代码写入；读取兼容桥接见 [file_usage.md](../../docs/system/file_usage.md) 第 5.2 节
- **新增文件存储时更新** [file_usage.md](../../docs/system/file_usage.md)（存储位置、类型、命名、调用方）

## 缓存使用规范

系统缓存全景见 [cache_usage.md](../../docs/system/cache_usage.md)。新增缓存必做：更新 `cache_usage.md`、键前缀注册进 `cache_utils.py` 的 `CacheKeys`、定义主动失效入口（不能只有 TTL）、键格式 `{cache_prefix}:{identifier}`。

禁止项：绕过 `src/core/redis_client.py` 直接用 redis-py；硬编码键前缀；无 TTL 缓存；跨租户键不含 `tenant_id`。

## 图片资产使用规范

所有图片资产的注册、寻址、传递必须通过 `src/core/image_asset.py` 的 `ImageRegistry` 与 `ImageRef`（设计见 [image-asset-pipeline-design.md](../../docs/system/image-asset-pipeline-design.md)）。

任何在工具结果、SSE 事件、API 响应中传递图片的字段**必须**用 `ImageRef`，**禁止**：直接拼 `storage/` 路径、传 `file_path`/`url`/`base64` 裸字段、知识库图片用外部 URL（必须落地租户目录）。

```python
from src.core.image_asset import get_image_registry, ImageRef

ref: ImageRef = await get_image_registry().register(
    source_path="/tmp/generated_image.png", tenant_id=tenant_id, user_id=user_id,
    display_name="生成的图表.png",
    source="tool_generated",  # knowledge_base / tool_generated / user_upload / web_fetch / screenshot
    usage="inline",           # inline / attachment / embedded / thumbnail
)
# ref.file_id 与 cp 同命名空间，ref.download_url = /api/files/{file_id}/download
```

- TTL：`knowledge_base` 永久（文档删除级联清理）；其余 source 24h（`cleanup_temp` 仅清 `usage ∈ (inline, embedded)`）。`PERMANENT_TTL(-1)` 不调 expire；只有正数 TTL 才调
- `fetch_to_local(url, ...)`：URL 去重缓存 TTL 7 天、单文件 >10MB 拒绝、超时 15s
- Redis key 与 `cp_tool._register_download` 同命名空间（`uploaded_file:{file_id}`）
- ImageRegistry 是惰性单例，import 该模块无副作用、不会拉起 master_agent
