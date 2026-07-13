# 开发计划：图片资产全链路承载能力

> **反向关联**：[设计文档](../system/image-asset-pipeline-design.md) · [ideas.md 第 37 条](../ideas.md)
> **创建日期**：2026-07-13
> **状态**：📋 待开发
> **实施流程**：所有非平凡任务严格遵循 [.claude/rules/dev_workflow.md](../../.claude/rules/dev_workflow.md) 的「三智能体开发流程」（开发 → 测试 → CodeReview）。

---

## 0. 计划总览

### 0.1 Phase 切分与目标

| Phase | 目标 | 范围 | 估时 | 依赖 |
|-------|------|------|------|------|
| **Phase 0** | 基建层 | ImageRef 模型 + ImageRegistry 类 + 与 cp 兼容性测试 | 2 天 | 无 |
| **Phase 1** | 旅游顾问行程 Word 嵌图端到端 | 知识库图片资产 + attraction_search 返回 cover_image + image_inliner + md_to_word 集成 + 景点 Excel+zip 导入 + SUBAGENT.md 规范 + 前端景点管理页图片上传 | 5 天 | Phase 0 |
| **Phase 2** | Agent 回复显图能力 | images SSE 事件 + ChatMessage/UnifiedResponse 字段 + 前端 ImageGallery + Markdown image renderer + 渠道图片消息（feishu/dingtalk） | 4 天 | Phase 0 |
| **Phase 3** | 高级能力（按需） | 文档解析器内嵌图提取 + image_parser OCR/VLM + LLM `[[IMAGE:file_id]]` 占位符 + wecom 渠道 + PPT/PDF inliner 接入 | 待评估 | Phase 1+2 |

### 0.2 关键决策摘要（细节见各 Phase 章节）

| 决策点 | 结论 | 理由 |
|--------|------|------|
| ImageRegistry 与 cp 的关系 | **复用 cp 的 Redis 协议**（`uploaded_file:{file_id}` + `hset` + `expire`），不修改 cp_tool | 避免改动 cp_tool 影响面，复用已验证协议 |
| 图片存储路径 | `storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}` | 对齐 file_usage.md 的 `tenants/` 规范，新增 `images/` 子目录 |
| ImageRef 是否继承 DownloadableFileInfo | **不继承，组合**（ImageRef 内部用 file_id/download_url，独立 BaseModel） | 图片语义比文件丰富，单独建模；底层 file_id 协议复用 |
| 知识库图片存储 | `tenants/{id}/knowledge/images/`（与文档解析产物同域） | 与 file_usage.md 现有 `knowledge/` 子目录并列 |
| LLM 引用图片的 scheme | **只支持 `file_id:file_xxx`**（kb:// 等扩展 scheme 移到 Phase 3） | Phase 1 最小集；attraction_search 已返回 ImageRef，LLM 直接拿到 file_id |
| 图片下载超时与缓存 | 单 URL 超时 15s，Redis 记 `image_fetch_url:{url_hash}` → file_id，TTL 7 天 | 防止重复下载 + 控制单次失败影响 |
| Word 嵌图格式 | **方案 B：行程表格不动，后追加"景点图集"章节** | Pandoc pipe_tables 不支持单元格块级图，方案 B 零侵入 |
| SSE 事件命名 | `images`（独立事件，不污染 response 流） | 与现有 `response`/`tool_result` 等并列，前端独立处理 |
| 前端图片画廊 | 网格布局（1 张大图/2-3 张并排/4+ 张瀑布流）+ lightbox | 复用 DownloadFileCard 已有的预览能力作为 lightbox 底座 |
| 渠道发图优先级 | Phase 1 不做渠道；Phase 2 feishu+dingtalk；wecom Phase 3 | Phase 1 直击旅游顾问（Web 端），渠道需要 media.upload 较重 |
| 渠道图文混排能力 | **所有第三方渠道不支持单消息图文混排**，拆分多条发送（文本→图片→文件）+ placement 降级为文本占位符 `[图片：{name}]` | 渠道 API 限制（feishu/dingtalk/wecom 单条消息只能单一类型）；通过占位符补偿位置语义 |
| ImageRef.placement 字段 | 新增 `placement: Literal["after_text","before_text","inline"]`，默认 `after_text` | Web 端决定渲染位置；渠道端降级为占位符策略（见 P2.9.0） |
| 知识库图片是否允许外部 URL | **禁止**，必须落地租户目录 | 链接腐烂 + 无法审计 + 无法租户隔离 |
| `_register_download` 重构方式 | 不动 cp_tool，**新模块 image_asset.py 内独立实现 register**，避免循环依赖 | cp_tool 内部依赖 `UPLOAD_DIR`（来自 src.main），新模块直接走 storage.py 标准函数更干净 |

### 0.3 验收门槛（每 Phase 通过条件）

- **Phase 0**：单元测试通过（ImageRegistry CRUD + Redis 协议兼容 + 与 cp file_id 互通）
- **Phase 1**：端到端 demo（上传 Excel+图片 zip → 搜索景点返回 cover_image → 旅游顾问生成带图集章节 Word）+ 三智能体流程通过
- **Phase 2**：Web 端 Agent 调用返回 ImageRef 的工具时能渲染图片画廊 + feishu/dingtalk 收到图片消息
- **Phase 3**：按需评估，单独立子计划

---

## Phase 0：基建层（ImageRef + ImageRegistry）

### P0.1 新增模块 `src/core/image_asset.py`

**文件**：`src/core/image_asset.py`（新增）

**职责**：
- 定义 `ImageRef` Pydantic 模型
- 提供 `ImageRegistry` 类的 `register` / `resolve_local_path` / `fetch_to_local` / `cleanup_temp` 四个核心方法
- 复用 cp 的 Redis 协议（`uploaded_file:{file_id}` + hset + expire）

**关键设计决策**：

1. **ImageRef 字段**（与设计文档 §2.2 完全一致）：

```python
class ImageRef(BaseModel):
    file_id: str
    download_url: str
    display_name: str
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: str = "image/*"
    size_bytes: int = 0
    source: Literal["knowledge_base", "tool_generated", "user_upload", "web_fetch", "screenshot"]
    source_ref: Optional[str] = None
    usage: Literal["inline", "attachment", "embedded", "thumbnail"] = "inline"
    placement: Literal["after_text", "before_text", "inline"] = "after_text"
    linked_doc_id: Optional[int] = None
    linked_chunk_id: Optional[int] = None
```

2. **ImageRegistry.register() 实现细节**：
   - 文件名规范：`{file_id}{ext}`（file_id 复用 cp 的 `file_{uuid.uuid4().hex[:12]}` 格式）
   - 存储路径：`storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}`
   - 通过 `src.core.storage.ensure_tenant_storage_dir` 创建目录（**不直接拼路径**，遵守 backend_dev.md）
   - `move=True` 时 `shutil.move`，否则 `shutil.copy2`
   - Redis 写入：
     ```
     key = make_key("uploaded_file", file_id)
     hset(key, "file_id", "name", "path", "size", "mime_type", "type"="image",
          "source", "usage", "source_ref", "linked_doc_id", "linked_chunk_id",
          "visible"=True, "width", "height")
     expire(key, ttl_seconds)  # 默认 86400，knowledge_base 来源用 -1（永久）
     ```
   - 返回 `ImageRef`，其中 `download_url = f"/api/files/{file_id}/download"`（复用现有 `/api/files/{id}/download` 路由，无需新增）

3. **图片元信息获取（宽高）**：
   - 使用 `Pillow`（项目已依赖，见 `src/tools/image/`）读取 `Image.open(path).size`
   - 失败时不阻断注册，宽高留空、记 `logger.warning`，不抛异常（图片可能损坏但仍可用作占位）

4. **fetch_to_local() 实现细节**：
   - 用 `httpx.AsyncClient`（项目已用，见 `src/network/`）下载，超时 15s
   - **URL 去重缓存**：Redis key `image_fetch_url:{sha256(url)}` 记录已下载的 file_id，TTL 7 天
     - 命中：直接复用 file_id，不重复下载（节省带宽 + 避免图片重复占用磁盘）
     - 未命中：下载 → register(source="web_fetch", usage="embedded")
   - 文件名提取：从 URL 解析 basename，失败用 `fetched_{file_id}.jpg`
   - 限制单文件 ≤ 10MB，超过拒绝（防止抓取视频误识别为图）

5. **cleanup_temp() 实现细节**：
   - 扫描 Redis 所有 `uploaded_file:*` 键，过滤 `source in ("tool_generated", "web_fetch")` 且 `usage in ("inline", "embedded")` 且注册时间 > TTL 的
   - 删除 Redis key + 删除磁盘文件
   - 由 `src/scheduler/` 定时任务调用（Phase 0 不接调度，只暴露 API，调度接入在 Phase 1 末或 Phase 2 末）

**关键代码骨架**：

```python
# src/core/image_asset.py
from typing import Optional, Literal, Union, List, Dict, Any
from pathlib import Path
from datetime import datetime
import hashlib
import httpx
import shutil
import uuid
from pydantic import BaseModel, Field
from loguru import logger

from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path


class ImageRef(BaseModel):
    # ... 见上
    pass


class ImageRegistry:
    """图片资产注册与寻址（系统级横切能力，所有图片资产必经入口）"""

    DEFAULT_TTL = 86400
    PERMANENT_TTL = -1
    FETCH_CACHE_TTL = 7 * 86400
    MAX_FETCH_SIZE = 10 * 1024 * 1024  # 10MB

    def __init__(self):
        from src.core.redis_client import redis_client
        self._redis = redis_client

    async def register(
        self,
        source_path: Union[str, Path],
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        source: str = "tool_generated",
        usage: str = "inline",
        source_ref: Optional[str] = None,
        linked_doc_id: Optional[int] = None,
        linked_chunk_id: Optional[int] = None,
        move: bool = False,
        ttl_seconds: Optional[int] = None,
    ) -> ImageRef:
        ...

    async def resolve_local_path(self, ref: ImageRef) -> Path:
        """从 ImageRef.file_id 查 Redis 拿本地路径"""
        ...

    async def fetch_to_local(
        self,
        url: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        timeout: int = 15,
    ) -> ImageRef:
        ...

    async def cleanup_temp(self, older_than_hours: int = 24):
        ...


# 模块级单例（惰性）
_registry_instance: Optional[ImageRegistry] = None

def get_image_registry() -> ImageRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ImageRegistry()
    return _registry_instance
```

**验收标准**：
- ImageRef 所有字段类型正确
- register() 写入路径与 cp 完全不同的目录（`tenants/{id}/images/{yyyy-mm}/`，非 `tenants/{id}/{user_id}/`），但 Redis key 命名空间一致（`uploaded_file:{file_id}`）
- 现有 `/api/files/{file_id}/download` 路由能直接下载 ImageRegistry 注册的文件（**兼容性测试**）

**测试文件**：`tests/unit/test_image_asset.py`（新增）

测试用例：
- `test_register_copy_creates_image_ref`：复制注册，验证 file_id/download_url/路径
- `test_register_move_replaces_source`：移动注册，源文件不存在
- `test_register_pillow_failure_does_not_raise`：损坏图片不报错，宽高为 None
- `test_register_knowledge_base_permanent_ttl`：source=knowledge_base 时 Redis expire 为 -1（永久）
- `test_resolve_local_path_hits_redis`：resolve 返回与 register 一致的路径
- `test_fetch_to_local_caches_by_url_hash`：同 URL 二次调用不重新下载
- `test_fetch_to_local_rejects_oversize`：> 10MB 拒绝
- `test_fetch_to_local_timeout`：mock httpx 超时，返回错误
- `test_compat_with_cp_file_download_route`：注册后调用现有 `/api/files/{file_id}/download` 能返回文件
- `test_cleanup_temp_only_removes_inline_embedded_tool`：清理只针对 tool/web_fetch 来源的临时图，知识库永久图不受影响

---

### P0.2 file_usage.md / cache_usage.md 协调更新

**文件**：
- `docs/system/file_usage.md`（在分类表中新增 `images/` 子目录条目）
- `docs/system/cache_usage.md`（在 Redis 缓存表中新增 `uploaded_file:` 图片子类型 + `image_fetch_url:` URL 去重缓存）

**改动内容**：

file_usage.md 新增条目（§2 分类表）：

| 类别 | 目录 | 存储类型 | 租户隔离 | 说明 |
|------|------|---------|---------|------|
| 图片资产（通用） | `tenants/{id}/images/{yyyy-mm}/` | 磁盘+Redis | 是 | ImageRegistry 管理的非知识库图片（工具生成/上传/Web 抓取），TTL 24h |
| 知识库图片资产 | `tenants/{id}/knowledge/images/` | 磁盘+Redis | 是 | 知识库关联图片（景点封面、文档内嵌图等），永久存储 |

cache_usage.md 新增条目：

| 缓存 | 键模式 | TTL | 失效时机 |
|------|-------|-----|---------|
| 图片资产元信息（通用） | `uploaded_file:file_xxx` | 86400s | 文档生成后清理（usage=embedded）/ TTL 过期 |
| 图片资产元信息（知识库） | `uploaded_file:file_xxx` | -1（永久） | 知识库文档删除时级联清理（Phase 3） |
| Web 图片抓取去重 | `image_fetch_url:{sha256(url)}` | 604800s（7 天） | 同 URL 复用 file_id，不重复下载 |

**验收标准**：
- 两个文档登记条目清晰，与 ImageRegistry 实现一致

---

### P0.3 backend_dev.md 协调更新

**文件**：`.claude/rules/backend_dev.md`

**新增章节**：「## 图片资产使用规范」

```markdown
## 图片资产使用规范

**核心规则**：所有图片资产的注册、寻址、传递必须通过 `src/core/image_asset.py` 的 `ImageRegistry` 与 `ImageRef`，**禁止**：
- 业务代码直接拼 `storage/` 路径写图片
- 接口之间传 file_path / url / base64 裸字段
- 知识库图片使用外部 URL（必须落地租户目录）

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
```

### 接口契约

任何在工具结果、SSE 事件、API 响应中传递图片的字段，**必须**使用 `ImageRef`（list[ImageRef] 或 Optional[ImageRef]），**禁止**用裸 file_path / url / base64。
```

**验收标准**：
- 规范文档清晰、可直接执行

---

Phase 0 完成验收
- [x] `src/core/image_asset.py` 实现，单元测试 15/15 通过（含 CodeReview 阶段补的 Content-Length 预检回归用例）
- [x] file_usage.md / cache_usage.md / backend_dev.md 协调更新完成
- [x] 三智能体流程通过（开发 + 测试 + CodeReview，CodeReview 修复 1 个 P1 Content-Length 预检 bug）

> **完成日期**：2026-07-13
> **测试结果**：`tests/unit/test_image_asset.py` 15 passed；`tests/unit/test_redis_client.py` 58 passed（无回归）
> **启动安全**：import 仅拉起 3 个 src 模块（image_asset / storage / redis_client），不拉起 master_agent
> **关键决策落地**：PERMANENT_TTL(-1) 不调 expire（避开内存降级版 seconds≤0 立即删键的陷阱）；Redis key 与 cp_tool 同命名空间（`make_key("uploaded_file", file_id)`）；存储路径走 `ensure_tenant_storage_dir(tenant_id, "images/{yyyy-mm}")` 标准函数

---

## Phase 1：旅游顾问行程 Word 嵌图端到端

### P1.1 知识库图片资产数据契约

**文件**：无新增 SQL，仅约定 metadata 结构

**关键决策**：
- **不修改 documents/chunks 表结构**（设计文档 §5.1 已确认），全部走 `metadata` JSON 字段
- documents.metadata 新增 `images` 字段（JSON 对象），结构：
  ```json
  {
    "images": {
      "cover": "file_id_of_cover_image",
      "gallery": ["file_id_1", "file_id_2"],
      "inline": {"chunk_0_top": "file_id_x"}
    }
  }
  ```
- documents 表的 `thumbnail_path` / `width` / `height` / `mime_type` 字段（已存在但未使用）：**Phase 1 暂不写入**（冗余但便于 SQL 查询），Phase 3 知识库图片列表优化时再考虑；ImageRef 路径作为权威源

**验收标准**：
- 决策记录在设计文档与本计划中保持一致
- 后续 import_attraction 改造时使用 documents.metadata.images.cover 作为单一权威

---

### P1.2 景点 Excel + 图片 zip 导入

**改造目标**：景点 Excel 导入接口 `/api/v1/travel-quote/import/attraction-excel-kb` 支持 zip 包（Excel + 景点图片目录）。

#### P1.2.1 改造 `import_attraction()` 支持 ImageRef

**文件**：`src/skills/travel-quote/scripts/attraction_retriever.py:213`

**改造点**：
- 函数签名扩展，新增可选参数：
  ```python
  def import_attraction(
      self, tenant_id, attraction_name, region, info_text, ticket_table_text,
      project_table_text="", metadata=None, source_file="", user_id=None,
      cover_image_path: Optional[str] = None,    # 新增：封面图本地路径
      gallery_image_paths: Optional[List[str]] = None,  # 新增：图集本地路径列表
  ) -> int:
  ```
- 内部逻辑：
  1. 如果 `cover_image_path` 提供：调用 `ImageRegistry.register(source="knowledge_base", usage="thumbnail", source_ref=source_file)` 得到 `cover_ref`
  2. 如果 `gallery_image_paths` 提供：循环 register 得到 `[gallery_ref_1, gallery_ref_2, ...]`
  3. metadata 合并 `images.cover = cover_ref.file_id`、`images.gallery = [ref.file_id for ref in gallery_refs]`
  4. 写入 documents.metadata 时 JSON 序列化上述结构

**关键决策**：
- **register 调用方式**：异步 `ImageRegistry.register`，但 `import_attraction` 是同步函数 → 用 `asyncio.run()` 包装单次调用，或改造 `import_attraction` 为 async
  - **决策**：**改造为 async**（API 路由本来就是 async，调用方一致），向下兼容同步调用通过提供 sync wrapper：
    ```python
    def import_attraction_sync(**kwargs):
        """同步兼容包装"""
        import asyncio
        return asyncio.run(self.import_attraction(**kwargs))
    ```
  - 现有调用点（`src/api/travel_quote.py:1420` 和 `:1753`）改为 `await retriever.import_attraction(...)`

#### P1.2.2 改造 Excel 导入接口支持 zip

**文件**：`src/api/travel_quote.py:1317` `import_attraction_excel_to_kb`

**改造点**：
1. 文件类型支持：`.xlsx` 和 `.zip` 两种
2. zip 包结构约定：
   ```
   attraction_data.zip
   ├── attractions.xlsx       # 必须存在，根目录
   └── images/                # 可选，景点图片目录
       ├── 黄果树瀑布.jpg
       ├── 小七孔.jpg
       └── ...
   ```
3. Excel Sheet 内表格列：在原有 `景点名称 / 区域 / 景点信息 / 门票价格表 / 项目价格表` 之后新增可选列 `封面图` / `图集`：
   - `封面图` 值为文件名（相对于 images/ 目录），如 `黄果树瀑布.jpg`
   - `图集` 值为分号分隔的文件名列表，如 `图1.jpg;图2.jpg;图3.jpg`
4. 解析流程：
   - 解压 zip 到临时目录（`tempfile.mkdtemp`），处理完后 `shutil.rmtree`
   - 解析 Excel 同现状
   - 对每个景点：从 `封面图` / `图集` 列读出文件名 → 拼接临时目录路径 → 校验文件存在 → 调用 `import_attraction(cover_image_path=..., gallery_image_paths=[...])`
   - 单个景点图片缺失：记 warning，跳过该图，不阻断整体导入

**关键决策**：
- **图片与景点匹配方式**：通过 Excel 列中的文件名匹配，不通过图片文件名自动推断景点（避免同名歧义）
- **图片格式限制**：仅接受 `.jpg/.jpeg/.png/.webp`，其他格式记 warning 跳过
- **重复导入处理**：景点查重逻辑（`search_by_name`）不变；已存在的景点不重新导入，但日志提示用户先删除再导入

#### P1.2.3 AttractionExcelParser 解析新列

**文件**：`src/skills/travel-quote/scripts/attraction_excel_parser.py`

**改造点**：
- `parse_sheet_by_name()` 返回的 dict 新增字段 `cover_image_filename` / `gallery_image_filenames`（可选，仅当列存在）
- 表头匹配规则：识别 `封面图` / `图集` 列名（含同义词如 `封面` / `图片` / `图集`）

**验收标准**：
- 上传纯 Excel 文件（无图片）：行为与现状完全一致，向后兼容
- 上传 zip（Excel + images/）：解析每个景点的封面图/图集文件名，调用 import_attraction 携带图片路径

---

### P1.3 attraction_search 返回 cover_image

**文件**：`src/tools/knowledge/attraction_search_tool.py:141`

**改造点**：
- 在 `formatted.append({...})` 中新增字段：
  ```python
  images_meta = meta.get("images") or {}
  cover_file_id = images_meta.get("cover") if isinstance(images_meta, dict) else None
  
  cover_image: Optional[ImageRef] = None
  if cover_file_id:
      try:
          cover_image = await get_image_registry().get_ref_by_file_id(cover_file_id)
      except Exception as e:
          logger.warning(f"[AttractionSearch] doc_id={row['doc_id']} cover_file_id={cover_file_id} resolve failed: {e}")
  
  formatted.append({
      # ... 现有字段
      "cover_image": cover_image.model_dump() if cover_image else None,
  })
  ```

**新增辅助方法**：`ImageRegistry.get_ref_by_file_id(file_id) -> Optional[ImageRef]`
- 从 Redis hgetall 拿元信息，组装成 ImageRef
- Redis 不存在或字段不全时返回 None（不抛异常，让上游优雅降级）

**关键决策**：
- 工具返回的是 `dict`（Pydantic `.model_dump()`），不是 ImageRef 对象（工具结果最终要序列化为 JSON 给 LLM 看）
- 字段命名用 `cover_image`（与设计文档 §5.2.1 一致），不复用 `thumbnail`（避免混淆）
- **不返回图集**（gallery）字段，避免 context 膨胀；LLM 需要时通过额外工具查询（Phase 3）

**验收标准**：
- 景点知识库无图片时，`cover_image` 为 `null`，与现状一致
- 有图片时返回完整 ImageRef dict，含 `file_id` / `download_url` / `display_name` / `source="knowledge_base"` / `usage="thumbnail"` / `linked_doc_id`
- 单测覆盖：mock metadata.images.cover 存在/不存在两种情况

---

### P1.4 通用 image_inliner 模块

**新增文件**：`src/tools/_image_inliner.py`

**职责**：扫描 Markdown（或 HTML）文本中的图片引用，下载/解析为本地路径，返回处理后的文本 + ImageRef 列表。

**Phase 1 仅支持 `file_id:` scheme**（kb:// 等 Phase 3 再加）：

```python
# src/tools/_image_inliner.py

import re
from typing import Tuple, List, Optional
from pathlib import Path

from src.core.image_asset import ImageRef, get_image_registry


_FILE_ID_PATTERN = re.compile(r'!\[([^\]]*)\]\(file_id:([a-z0-9_]+)\)')
_REMOTE_URL_PATTERN = re.compile(r'!\[([^\]]*)\]\((https?://[^\s)]+)\)')


async def inline_images(
    text: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    syntax: str = "markdown",
    fetch_remote: bool = True,
) -> Tuple[str, List[ImageRef]]:
    """扫描文本中的图片引用，下载/解析为本地路径。

    Phase 1 支持的引用形式（markdown syntax）：
      ![alt](file_id:file_xxx)   → ImageRef 直接引用（零拷贝，最快）
      ![alt](https://...)        → fetch_to_local 下载并注册

    Args:
        fetch_remote: 是否处理远程 URL（默认 True；False 时仅替换 file_id:，远程 URL 保持原样）

    Returns:
        (处理后的文本（所有 src 替换为本地绝对路径）, ImageRef 列表)
    """
    refs: List[ImageRef] = []
    registry = get_image_registry()

    # 1. 替换 file_id: scheme
    def _replace_file_id(m):
        alt, file_id = m.group(1), m.group(2)
        try:
            ref = await registry.get_ref_by_file_id(file_id)
            if ref is None:
                logger.warning(f"[image_inliner] file_id={file_id} not found in registry, skip")
                return m.group(0)  # 保留原文
            local_path = await registry.resolve_local_path(ref)
            refs.append(ref)
            return f"![{alt}]({local_path})"
        except Exception as e:
            logger.warning(f"[image_inliner] resolve file_id={file_id} failed: {e}")
            return m.group(0)

    text = await _are_sub(_FILE_ID_PATTERN, _replace_file_id, text)

    # 2. 替换远程 URL（可选）
    if fetch_remote:
        async def _replace_remote(m):
            alt, url = m.group(1), m.group(2)
            try:
                ref = await registry.fetch_to_local(url, tenant_id=tenant_id, user_id=user_id)
                local_path = await registry.resolve_local_path(ref)
                refs.append(ref)
                return f"![{alt}]({local_path})"
            except Exception as e:
                logger.warning(f"[image_inliner] fetch url={url} failed: {e}")
                return m.group(0)

        text = await _are_sub(_REMOTE_URL_PATTERN, _replace_remote, text)

    return text, refs
```

**关键决策**：
- **替换失败时不抛异常**：单张图失败不阻断整篇文档生成，原图保留为 broken link（Pandoc 会渲染为 alt 文字）
- **同步正则 + 异步替换**：用 `asyncio.gather` 批量并发处理多张图，加速文档生成
- **Phase 1 默认 `fetch_remote=True`**：旅游顾问偶尔会粘贴外部景点 URL（如官网图），允许 inliner 下载
- **HTML syntax（`<img src>`）**：Phase 1 不支持（旅游顾问走 Markdown），Phase 3 接入 pdf_process 时再加

**辅助函数**：`_are_sub` 是异步正则替换辅助，逐 match 调用 async 替换函数（标准 Python `re.sub` 不支持 async replace，需自己写）

**测试文件**：`tests/unit/tools/test_image_inliner.py`

测试用例：
- `test_inline_file_id_scheme_resolves_local_path`
- `test_inline_unknown_file_id_keeps_original`
- `test_inline_remote_url_fetches_and_replaces`
- `test_inline_remote_url_fetch_failure_keeps_original`
- `test_inline_no_images_returns_text_unchanged`
- `test_inline_multiple_images_concurrent`

---

### P1.5 md_to_word 集成 inliner

**文件**：`src/tools/word/md_to_word.py:55` `convert()`

**改造点**：

```python
async def convert_async(md_text: str, template: Optional[str] = None,
                       title: str = "", author: str = "",
                       tenant_id: Optional[str] = None,
                       user_id: Optional[str] = None) -> Document:
    """异步版本：先 inline 图片，再走 Pandoc"""
    from src.tools._image_inliner import inline_images

    if tenant_id:
        md_text, _refs = await inline_images(md_text, tenant_id=tenant_id, user_id=user_id)

    return _convert_sync(md_text, template=template, title=title, author=author)


def convert(md_text: str, template: Optional[str] = None,
            title: str = "", author: str = "",
            tenant_id: Optional[str] = None,
            user_id: Optional[str] = None) -> Document:
    """同步包装：tenant_id 提供时走 async，否则走原同步路径（向后兼容）"""
    if tenant_id:
        import asyncio
        return asyncio.run(convert_async(md_text, template, title, author, tenant_id, user_id))
    return _convert_sync(md_text, template, title, author)
```

**关键决策**：
- **保留同步 convert() 入口**：现有所有调用方（如 `word_process_tool.py`）无需立即改造，仅在 tenant_id 提供时启用 inliner
- **改造 `word_process_tool`**：调用 `convert` 时传入 tenant_id（从 ContextVar 或注入的 self._tenant_id 拿），启用图片 inline
- **不修改 `_convert_sync` 内部**：把现在的 `convert` 主体抽成 `_convert_sync`，逻辑零改动（避免回归）

**改造 word_process_tool**：

**文件**：`src/tools/word/word_process_tool.py`（参考 cp_tool 的 tenant_id 注入方式）

- 在 execute 入口获取 `tenant_id`：优先 `self._tenant_id`（注入）→ `get_current_tenant_id()`（ContextVar）
- 调用 `convert(md_text, ..., tenant_id=tenant_id, user_id=user_id)`

**验收标准**：
- 无 tenant_id 时（旧调用方式）：行为完全不变
- 有 tenant_id 时：Markdown 中 `![alt](file_id:file_xxx)` 被替换为本地路径，Pandoc 嵌入 Word
- Word 文档打开后图片可见

---

### P1.6 SUBAGENT.md 行程输出规范改造

**文件**：`subagents/travel-consultant/SUBAGENT.md`

**改造点**：在详细行程输出流程中新增"景点图集"章节规范。

具体追加内容（位置：现有详细行程格式规范之后）：

```markdown
## 行程文档图片规范

生成详细行程 Word 时，**必须在 5 列行程表格之后追加「景点图集」章节**：

### 章节格式

```markdown
## 景点图集

### {景点1名称}
![{景点1名称}]({file_id})
*一句话描述（如：卧龙潭、翠谷瀑布、水上森林）*

### {景点2名称}
![{景点2名称}]({file_id})
*一句话描述*
```

### 图片引用规则

- 从 `attraction_search` 返回结果的 `cover_image.file_id` 取得图片引用
- **必须使用 `file_id:file_xxx` 格式**（不要使用 http URL，避免文档生成时跨网下载）
- 行程中出现的所有景点（不含酒店、餐厅）都应在图集中展示
- 同一景点多次出现只展示一次

### 处理顺序

1. 调用 `attraction_search` 时识别每个景点的 `cover_image`
2. 生成详细行程时，5 列行程表格之后追加「## 景点图集」二级标题
3. 按行程中景点出现的先后顺序，每个景点一个 `### {景点名}` 三级标题 + 一张图 + 一行斜体描述
4. 调用 `word_process` 生成 Word

### 容错

- 某景点 `cover_image` 为 null：跳过该景点，不阻断图集生成
- 图集无图可用（所有景点都无图）：不生成图集章节，仅输出表格行程（与现状一致）
```

**关键决策**：
- **不强制要求图集**：知识库无图时图集章节不生成，向后兼容
- **格式约定明确**：三级标题 `###`、图片用 `![]()`、描述用斜体（与 Pandoc 兼容）
- **示例放在 SUBAGENT.md**：让 LLM 能在 system prompt 中看到具体格式

**验收标准**：
- SUBAGENT.md 新增章节清晰，含示例
- 不破坏现有行程输出规范（5 列表格等保持不变）

---

### P1.7 前端景点管理页图片上传

**文件**：`frontend/src/components/travel/AttractionManager.vue`（确认存在，参考 list-page-convention.md）

**改造点**：
1. 上传弹窗支持 zip 文件类型：`accept=".xlsx,.zip"`
2. 列表新增「封面」缩略图列（基于 `documents.metadata.images.cover` file_id 拼接 `/api/files/{file_id}/download`）
3. 详情弹窗新增「图集」面板（显示 cover + gallery）

**关键决策**：
- **列表缩略图**：仅当 cover 存在时显示，否则显示占位 SVG（不破坏布局）
- **图集面板**：复用现有 `DownloadFileCard` 的图片预览能力（无需新组件，Phase 2 再做 ImageGallery）
- **API 改造**：
  - 列表接口（如有）的返回结构需要带 `cover_file_id`
  - 景点搜索接口 `/api/v1/travel-quote/attractions/search` 返回结构带 cover_image

**改造景点搜索 API**（如有）：让 API 返回 cover_file_id（从 documents.metadata.images.cover 取），前端拼接 URL

**验收标准**：
- 上传纯 Excel：行为不变
- 上传 zip：成功导入景点+图片，前端列表显示封面缩略图
- 详情弹窗显示图集

---

### P1.8 端到端集成测试

**测试场景**：
1. 准备测试 zip：`tests/fixtures/travel/attractions_with_images.zip`（含 `attractions.xlsx` + `images/黄果树瀑布.jpg` + `images/小七孔.jpg`）
2. 测试用例：
   - `test_import_attraction_zip_registers_images`：调用导入接口，验证 documents.metadata.images.cover/gallery 写入
   - `test_attraction_search_returns_cover_image`：搜索景点，验证返回 cover_image 含完整 ImageRef
   - `test_inline_file_id_to_word`：构造含 `![](file_id:file_xxx)` 的 Markdown，调用 md_to_word.convert，验证 Word 中图片可见
   - `test_e2e_attraction_search_to_word`：导入 → 搜索 → 拼接 Markdown → 生成 Word，断言 Word 中图片数量

**测试文件**：`tests/integration/test_travel_image_pipeline.py`

**验收标准**：4 个集成测试全绿

---

Phase 1 完成验收
- [ ] P1.1 ~ P1.8 全部完成
- [ ] 端到端 demo：上传景点 Excel+图片 zip → 旅游顾问搜索景点 → 生成带「景点图集」章节的 Word
- [ ] 三智能体流程通过
- [ ] SUBAGENT.md / file_usage.md / cache_usage.md / backend_dev.md 协调更新完成

---

## Phase 2：Agent 回复显图能力

### P2.1 SSE images 事件

**文件**：`src/core/agent_events.py`

**改造点**：新增辅助函数 `make_image_event`

```python
def make_image_event(images: List[Dict[str, Any]], placement: str = "after_text") -> Dict[str, Any]:
    """构造 images SSE 事件。

    Args:
        images: ImageRef 字典列表（model_dump()）
        placement: "after_text"（默认）/ "inline" / "before_text"
    """
    return make_event("images", images=images, placement=placement)
```

**关键决策**：
- **placement 字段**：Phase 2 仅实现 `after_text`（最简单），`inline`/`before_text` 留 Phase 3
- **images 字段名**：复数（`images`，不是 `image`），与未来批量推送兼容
- 事件结构示例：
  ```json
  {"type": "images", "timestamp": 1234567890, "images": [{...ImageRef}], "placement": "after_text"}
  ```

**验收标准**：单元测试覆盖 make_image_event 的三种 placement

---

### P2.2 ChatMessage / UnifiedResponse 字段扩展

**文件**：`src/models/message.py`

**改造点**：
1. `UnifiedResponse` 新增 `images: List[ImageRef] = Field(default_factory=list)`
2. 持久化层：消息保存时 `images` 序列化到 `chat_messages.metadata.images`（**不需要 DDL 变更**）
3. 消息加载时：从 `metadata.images` 反序列化为 `List[ImageRef]`

**关键决策**：
- **不引入 Attachment 字段冲突**：现有 `attachments` 是用户上传附件，新增 `images` 是 Agent 主动推送的图片，语义不同
- **与 downloadable_files 的关系**：downloadable_files 是"可下载的文件列表"（Word/Excel/PDF），images 是"在消息流中展示的图片"，前者生成下载卡片，后者生成画廊
- **向后兼容**：images 默认空 list，历史消息无此字段时加载返回空列表

**验收标准**：
- 单元测试：序列化/反序列化 round-trip
- 历史消息加载不报错

---

### P2.3 Agent 主循环识别 ImageRef 并推送事件

**文件**：`src/core/agent.py`

**改造点**：
1. 在 `process_message` 工具结果处理阶段（约 `agent.py:2482` 之后），扫描工具返回结果中的 `ImageRef` 字段
2. 累积所有 ImageRef，在工具调用结束后（进入最终回复生成前）`yield make_image_event(refs)`
3. 同时把 ImageRef 写入最终 `UnifiedResponse.images`

**识别规则**（约定工具返回的 dict 中如何携带 ImageRef）：
- 工具 execute 返回的 dict 中，**键名 `images`** 值为 `List[ImageRef]` 或 `List[dict]`（dict 字段对齐 ImageRef）
- **键名 `cover_image`** 值为单个 `ImageRef` 或 `dict`（用于 attraction_search 这类返回封面图的工具）
- Agent 主循环识别这两种键，提取 ImageRef 列表

**placement 默认值与传递规则**：
- 工具返回的 ImageRef 如果没指定 `placement`，Agent 主循环统一设为 `"after_text"`（默认）
- 工具可以显式指定 `placement`（如景点封面图配 `before_text` 让用户先看图再看介绍）
- **Phase 2 不实现 inline placement 的智能定位**：`placement="inline"` 在 Phase 2 仍按 `after_text` 渲染（Web 端）；渠道端按占位符规则处理。真正的 inline 行内渲染（在 LLM 文本流中精确位置插入）留 Phase 3 的 `[[IMAGE:file_id]]` 占位符协议

**关键决策**：
- **不修改 BaseTool**：不强制工具实现新接口，约定优于类型约束
- **工具返回 dict 中的 ImageRef 是 `model_dump()` 后的 dict**（工具返回值最终要 JSON 序列化）
- **图片推送时机**：所有工具调用结束后、最终回复生成前（不在每个工具后单独推，避免事件流太碎）

**改造 attraction_search_tool 同步对齐**：
- 工具返回时 `cover_image` 已经是 dict（Phase 1 已实现），placement 默认 `after_text`
- 如果一次搜索返回多个景点，每个景点都有自己的 cover_image → Agent 提取所有 cover_image 推送

**验收标准**：
- 单元测试：mock 一个工具返回 `{"images": [ImageRef, ...]}`，验证 Agent yield 了 images 事件
- 集成测试：attraction_search 返回带 cover_image 的景点，验证前端能收到 images 事件

---

### P2.4 前端类型扩展

**文件**：`frontend/src/types/index.ts`

**改造点**：新增 `ImageRef` 类型，扩展 `ChatMessage`

```typescript
export interface ImageRef {
  file_id: string
  download_url: string
  display_name: string
  width?: number
  height?: number
  mime_type: string
  size_bytes: number
  source: 'knowledge_base' | 'tool_generated' | 'user_upload' | 'web_fetch' | 'screenshot'
  usage: 'inline' | 'attachment' | 'embedded' | 'thumbnail'
  placement: 'after_text' | 'before_text' | 'inline'
  linked_doc_id?: number
  linked_chunk_id?: number
}

export interface ChatMessage {
  // ... 现有字段
  images?: ImageRef[]
}
```

**验收标准**：类型定义与后端 ImageRef 字段一致

---

### P2.5 前端 useAgent 监听 images 事件

**文件**：`frontend/src/composables/useAgent.ts`

**改造点**：
- 在 SSE 事件 handler 中新增 `images` 分支
- 收到 images 事件时，把 images 数组合并到当前消息的 `images` 字段
- 不污染 `response` 文本流

**关键决策**：
- **合并策略**：一次回复可能多次推 images 事件（如多轮工具调用），按 file_id 去重合并
- **事件顺序**：images 事件通常在 tool_result 之后、response 之前；前端不阻塞 response 流渲染

**验收标准**：
- 收到 images 事件后，message.images 字段更新
- 渲染层（MessageItem）响应式更新

---

### P2.6 ImageGallery 组件

**新增文件**：`frontend/src/components/ui/ImageGallery.vue`

**职责**：展示一组图片，支持网格布局、懒加载、点击放大。

**关键设计决策**：
- **布局规则**：
  - 1 张：单图大图（max-width 600px，居中）
  - 2-3 张：横向并排，等宽
  - 4+ 张：CSS Grid 2-3 列瀑布流
- **懒加载**：原生 `<img loading="lazy">`
- **点击放大（lightbox）**：复用 `DownloadFileCard` 的预览模态（已有的图片预览能力）
- **source 标识**：右下角小图标（如"知识库"/"AI 生成"/"上传"），鼠标悬停 tooltip
- **下载**：长按或右键菜单（浏览器原生行为，不强加 UI）

**关键代码骨架**：

```vue
<template>
  <div class="image-gallery" :class="`layout-${layoutMode}`">
    <div
      v-for="(img, idx) in images"
      :key="img.file_id"
      class="gallery-item"
      @click="openLightbox(idx)"
    >
      <img
        :src="img.download_url"
        :alt="img.display_name"
        loading="lazy"
        class="gallery-img"
      />
      <div class="source-badge">{{ sourceLabel(img.source) }}</div>
    </div>

    <!-- Lightbox -->
    <BaseModal v-if="lightboxIndex >= 0" v-model="lightboxOpen" size="xl" :title="images[lightboxIndex]?.display_name">
      <img :src="images[lightboxIndex]?.download_url" class="lightbox-img" />
      <template #footer>
        <BaseButton intent="secondary" @click="lightboxOpen = false">关闭</BaseButton>
        <BaseButton @click="downloadCurrent">下载</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>
```

**source 标签映射**：
```typescript
const SOURCE_LABELS = {
  knowledge_base: '知识库',
  tool_generated: 'AI 生成',
  user_upload: '上传',
  web_fetch: '网络',
  screenshot: '截图',
}
```

**测试文件**：`frontend/src/__tests__/components/ImageGallery.test.ts`
- 渲染不同数量图片的布局
- 点击图片打开 lightbox
- source 标签显示

**验收标准**：
- 0/1/2/3/4+ 张图片布局正常
- lightbox 可打开关闭
- 单测覆盖主要交互

---

### P2.7 MessageItem 渲染分支

**文件**：`frontend/src/components/MessageItem.vue`

**改造点**：
- 根据 images 的 placement 分三处渲染：
  ```vue
  <!-- before_text 图片：文本上方 -->
  <ImageGallery
    v-if="beforeTextImages.length"
    :images="beforeTextImages"
  />
  <!-- 文本内容 -->
  <div class="message-content" v-html="renderedMarkdown" />
  <!-- after_text 图片：文本下方（默认） -->
  <ImageGallery
    v-if="afterTextImages.length"
    :images="afterTextImages"
  />
  <!-- inline 图片：Phase 2 暂合并到 after_text（不实现精确行内定位） -->
  <ImageGallery
    v-if="inlineImages.length"
    :images="inlineImages"
  />
  <!-- 可下载文件 -->
  <DownloadFileCard v-if="message.downloadableFiles?.length" :files="message.downloadableFiles" />
  ```

- 计算属性按 placement 分组：
  ```typescript
  const beforeTextImages = computed(() =>
    (message.images || []).filter(img => img.placement === 'before_text'))
  const afterTextImages = computed(() =>
    (message.images || []).filter(img => img.placement === 'after_text'))
  const inlineImages = computed(() =>
    (message.images || []).filter(img => img.placement === 'inline'))
  ```

**关键决策**：
- **Web 端尊重 placement**：before_text 在文本上方、after_text 在文本下方、inline 暂合并到 after_text（Phase 2 简化，Phase 3 实现精确行内）
- **历史消息兼容**：`message.images` 可选；旧消息无 placement 字段时按 `after_text` 处理（默认值）
- **渲染顺序**：before_text 图 → 文本 → after_text 图 → inline 图 → 可下载文件

**验收标准**：
- 含图片的消息按 placement 分区渲染
- 不含图片的消息无变化
- 历史消息（无 placement 字段）按 after_text 渲染

---

### P2.8 Markdown image renderer

**文件**：`frontend/src/utils/markdown.ts`

**改造点**：自定义 marked 的 image renderer

```typescript
const renderer = new marked.Renderer()
renderer.image = (href: string, title: string, text: string) => {
  return `<img src="${href}" alt="${text}" loading="lazy" class="md-inline-image" />`
}
```

**关键决策**：
- **样式**：`.md-inline-image { max-width: 100%; border-radius: 8px; cursor: zoom-in; }`
- **点击放大**：在 MessageItem 渲染后，给 `.md-inline-image` 绑定 click → 复用 ImageGallery 的 lightbox（可选，Phase 2 简化版只做样式，点击放大留 Phase 3）
- **XSS 防护**：href/alt 经过 DOMPurify（marked 默认 sanitize 行为）

**验收标准**：
- Markdown 中 `![](url)` 渲染为带样式的 img
- 不破坏现有 Markdown 渲染逻辑

---

### P2.9 渠道图片消息（feishu / dingtalk / wecom_kf）

**改造目标**：`UnifiedResponse.images` 在渠道端被拆分为「文本消息 + 多条图片消息」顺序发送，placement 降级为文本占位符。

#### P2.9.0 渠道拆分发送通用策略（所有渠道共用）

**核心约束**：所有第三方渠道（feishu/dingtalk/wecom/wecom_kf）的**单条消息只能是单一类型**（text/image/file/link 互斥），无法图文混排。必须拆分多条发送。

**统一发送顺序**：

```
1. [文本消息]  response.text（含 placement 占位符，见下）
2. [图片消息]  response.images[0]   ← 逐张发送
3. [图片消息]  response.images[1]
   ...
4. [文件消息]  response.downloadable_files[0]   ← 逐个发送（现有逻辑）
   ...
```

**placement 降级为文本占位符**（仅渠道端，Web 端不需要）：

| placement | Web 端渲染 | 渠道端文本处理 |
|-----------|----------|------------|
| `after_text`（默认） | 文本下方画廊 | 文本不加占位符，图在文本之后发送 |
| `before_text` | 图片在文本上方 | 文本开头插入 `[图片：{display_name}]\n` |
| `inline` | 文本流中行内渲染 | 文本中 Markdown `![alt](file_id:xxx)` 位置替换为 `[图片：{alt}]\n` |

**新增辅助函数**：`src/channels/_image_text_renderer.py`（共享模块）

```python
# src/channels/_image_text_renderer.py
"""渠道端文本+图片占位符渲染（所有渠道共用）"""

from typing import List
import re
from src.models.message import ImageRef


_INLINE_FILE_ID_PATTERN = re.compile(r'!\[([^\]]*)\]\(file_id:[a-z0-9_]+\)')


def render_text_with_image_placeholders(
    text: str,
    images: List[ImageRef],
) -> str:
    """根据 images 的 placement 在文本中插入占位符。

    所有渠道共用此函数，确保占位符行为一致。

    Args:
        text: 原始 Markdown 文本
        images: ImageRef 列表

    Returns:
        处理后的纯文本（已转 plain text + 占位符）
    """
    if not text or not images:
        return text

    # 1. before_text 图片：开头插入占位符
    before_imgs = [img for img in images if img.placement == "before_text"]
    if before_imgs:
        prefix = "".join(f"[图片：{img.display_name}]\n" for img in before_imgs)
        text = prefix + text

    # 2. inline 图片：替换 Markdown ![](file_id:xxx) 为占位符
    def _replace_inline(m):
        alt = m.group(1)
        return f"[图片：{alt}]"

    text = _INLINE_FILE_ID_PATTERN.sub(_replace_inline, text)

    # 3. after_text 图片：不加占位符（图自然在文本之后）
    # 注：after_text 不插占位符，避免文本冗余

    return text
```

**关键决策**：
- **占位符格式**：`[图片：{display_name}]`（含图片名，帮用户识别）而非简单 `[图]`
- **inline 占位符**：用 Markdown 中的 alt 文本（`![alt](file_id:xxx)` 的 alt）作为图片名，找不到 alt 时用 `display_name`
- **before_text 占位符在文本开头**：让用户先看到「后面有图」的提示
- **after_text 不插占位符**：图自然在文本之后发送，不需要提示
- **单次回复图片上限**：默认 5 张，超过的部分转为 `downloadable_files`（避免消息条数爆炸）；上限可配置

#### P2.9.1 feishu 适配

**文件**：`src/channels/feishu/adapter.py:474` `send_message`

**现状**：已有 `media.upload_image(local_path)` + `send_image`，且 send_message 已按「文本→downloadable_files」顺序发送。

**改造点**：
- 在文本发送前，调用 `render_text_with_image_placeholders(text, message.images)` 处理占位符
- 在文本发送后、downloadable_files 之前，新增 images 循环：
  ```python
  # 2. 发送图片（新增）
  for ref in message.images:
      try:
          local_path = await get_image_registry().resolve_local_path(ref)
          image_key = await self.media.upload_image(str(local_path))
          if image_key:
              success = await self.send_image(image_key, message.reply_to)
              if not success:
                  all_success = False
          else:
              all_success = False
      except Exception as e:
          logger.warning(f"[feishu] send image {ref.file_id} failed: {e}")
          all_success = False
  ```
- 错误处理：单图失败不阻断，记 warning

#### P2.9.2 dingtalk 适配

**文件**：`src/channels/dingtalk/adapter.py`

**现状**：已有 `upload_from_url` + `sampleImageMsg`。

**改造点**：
- 同 feishu 流程：文本占位符 → 文本发送 → images 循环 → downloadable_files
- 对每个 ImageRef：
  1. `download_url = build_public_url(ref.download_url)`（复用现有 `build_public_url` 函数，dingtalk 服务器要公网访问）
  2. `image_code = await dingtalk.upload_from_url(download_url)`
  3. 调用 `dingtalk.send_image_msg(image_code)`

**关键决策**：
- **download_url 公网可达**：复用 wecom_kf 已有的 `build_public_url` 函数（位于 `src/channels/_utils.py` 或类似位置）
- **回退方案**：dingtalk 渠道中先下载到本地，转 base64 上传（备用，仅在 upload_from_url 失败时）

#### P2.9.3 wecom_kf 适配

**文件**：`src/channels/wecom_kf/adapter.py:206` `send_message`

**现状**：已有 `send_msg(msgtype="image")` 能力，且 send_message 已按 markdown 分段发送（text/table/link 块）。

**改造点**：
- 在文本分段发送前，调用 `render_text_with_image_placeholders(text, message.images)` 处理占位符
- 在文本分段发送后、downloadable_files 之前，新增 images 循环：
  ```python
  # 发送图片（新增）
  for ref in message.images:
      try:
          local_path = await get_image_registry().resolve_local_path(ref)
          # wecom_kf 需要先上传到微信临时素材，拿到 media_id
          media_id = await self.api_client.upload_media("image", str(local_path))
          if media_id:
              result = await self.api_client.send_msg(
                  touser=message.reply_to,
                  open_kfid=self.current_open_kfid,
                  msgtype="image",
                  content={"media_id": media_id},
              )
              if result.get("errcode", 0) != 0:
                  all_success = False
          else:
              all_success = False
      except Exception as e:
          logger.warning(f"[wecom_kf] send image {ref.file_id} failed: {e}")
          all_success = False
  ```

**关键决策**：
- **wecom_kf 必须走 media.upload**：微信客服消息要求图片先上传到微信临时素材，拿到 media_id 后才能发送
- **media_id 有效期 3 天**：临时素材，不缓存复用，每次发送都重新上传
- **图片大小限制**：微信要求 ≤ 2MB，超过的图跳过并记 warning（不阻断）

#### P2.9.4 渠道单测

**测试文件**：`tests/unit/channels/test_image_send.py`

测试用例：
- `test_render_text_with_before_text_placeholder`：placement=before_text，文本开头有占位符
- `test_render_text_with_inline_placeholder`：placement=inline，Markdown `![](file_id:xxx)` 被替换为占位符
- `test_render_text_with_after_text_no_placeholder`：placement=after_text，文本不变
- `test_render_text_no_images_unchanged`：无图片时文本不变
- `test_feishu_send_message_with_images`：mock upload_image + send_image，验证调用顺序（文本→图片→文件）
- `test_dingtalk_send_message_with_images`：同上
- `test_wecom_kf_send_message_with_images`：同上
- `test_channel_single_image_failure_does_not_block`：单图失败不阻断后续

**验收标准**：
- feishu/dingtalk/wecom_kf 收到含 images 的 UnifiedResponse 时，按「文本（含占位符）→ 图片 → 文件」顺序发送
- 单图失败不阻断
- 占位符正确插入（before_text 开头、inline 替换、after_text 不插）

---

### P2.10 前端页面 frontend_dev.md 协调更新

**文件**：`.claude/rules/frontend_dev.md`

**新增章节**：「## 图片渲染规范」

```markdown
## 图片渲染规范

**核心规则**：所有图片展示必须使用 `ImageGallery` 组件或自定义 Markdown image renderer，**禁止**裸 `<img>` 标签。

### 强制要求

1. **懒加载**：所有图片必须 `loading="lazy"`
2. **点击放大**：必须支持 lightbox（复用 ImageGallery）
3. **来源标识**：图片右下角显示 source 标签（知识库 / AI 生成 / 上传 / 网络 / 截图）
4. **错误降级**：图片加载失败显示占位 SVG，不报错阻断
```

---

Phase 2 完成验收
- [ ] P2.1 ~ P2.10 全部完成
- [ ] Web 端：调用 attraction_search 返回带 cover 的景点时，前端渲染图片画廊
- [ ] Web 端：图片按 placement 分区渲染（before_text 在文本上方、after_text 在下方）
- [ ] feishu/dingtalk/wecom_kf 渠道：Agent 推送 images 事件，渠道按「文本（含占位符）→ 图片 → 文件」顺序拆分发送多条消息
- [ ] 渠道端单图失败不阻断后续发送
- [ ] 三智能体流程通过
- [ ] 历史消息回放正常（images 字段向后兼容）

---

## Phase 3：高级能力（按需，单独立子计划）

> Phase 3 是开放能力扩展，**估时和细节决策在 Phase 1+2 完成后再立子计划**，本计划仅记录候选方向。

### 候选方向

| 方向 | 描述 | 触发条件 |
|------|------|---------|
| 文档解析器内嵌图提取 | Word/Excel/PPT/PDF 解析时提取内嵌图，注册为 ImageRef | 知识库内容图片检索需求 |
| image_parser OCR + VLM | 实现 `src/tools/ocr/image_parser.py`，OCR + 多模态描述 → 可检索 chunk | 知识库图片内容检索需求 |
| LLM `[[IMAGE:file_id]]` 占位符 | Agent 后处理将 LLM 文本中的占位符替换为 images 事件 | LLM 主动引用图片需要 |
| wecom 渠道图片消息 | wecom adapter 实现 media.upload + image msg | wecom 渠道客户需要图片回复 |
| PPT/PDF inliner 接入 | ppt_process / pdf_process 集成 image_inliner | 非 Word 文档嵌图需求 |
| `kb://` scheme 支持 | inliner 支持 `![](kb://doc/42)` 等语义化引用 | LLM 频繁引用知识库图 |
| HTML img tag 支持 | inliner 支持 `<img src>` HTML syntax | pdf_process / weasyprint 嵌图 |

### Phase 3 优先级评估

待 Phase 1+2 上线后，根据实际使用反馈确定优先级。预计最有可能优先做：
- **wecom 渠道图片消息**（Phase 2 留的缺口，客户大概率会要）
- **PPT/PDF inliner 接入**（与 Word 嵌图能力对齐）

---

## 附录 A：完整任务清单（Checklist）

### Phase 0
- [x] P0.1 新增 `src/core/image_asset.py`（ImageRef + ImageRegistry）
- [x] P0.1 单元测试 `tests/unit/test_image_asset.py`（15 用例，含 CodeReview 补的 Content-Length 预检回归用例）
- [x] P0.2 更新 `docs/system/file_usage.md`
- [x] P0.2 更新 `docs/system/cache_usage.md`
- [x] P0.3 更新 `.claude/rules/backend_dev.md`

### Phase 1
- [ ] P1.1 知识库 metadata.images 契约文档化（设计文档已含，无需新文档）
- [ ] P1.2.1 `attraction_retriever.import_attraction` 改 async + 支持图片参数
- [ ] P1.2.2 `src/api/travel_quote.py` `import_attraction_excel_to_kb` 支持 zip
- [ ] P1.2.3 `attraction_excel_parser` 解析新列
- [ ] P1.3 `attraction_search_tool` 返回 cover_image
- [ ] P1.3 `ImageRegistry.get_ref_by_file_id` 辅助方法
- [ ] P1.4 新增 `src/tools/_image_inliner.py`
- [ ] P1.4 单元测试 `tests/unit/tools/test_image_inliner.py`
- [ ] P1.5 `md_to_word.py` 拆分 sync/async convert + 集成 inliner
- [ ] P1.5 `word_process_tool.py` 传递 tenant_id
- [ ] P1.6 `subagents/travel-consultant/SUBAGENT.md` 新增图集章节规范
- [ ] P1.7 前端 AttractionManager.vue 支持图片上传 + 缩略图列
- [ ] P1.7 景点搜索 API 返回 cover_file_id
- [ ] P1.8 端到端集成测试 `tests/integration/test_travel_image_pipeline.py`

### Phase 2
- [ ] P2.1 `src/core/agent_events.py` 新增 make_image_event
- [ ] P2.2 `src/models/message.py` UnifiedResponse 新增 images 字段（含 placement）
- [ ] P2.3 `src/core/agent.py` 主循环识别 ImageRef 并推送事件（含 placement 传递）
- [ ] P2.4 前端 `types/index.ts` 新增 ImageRef 类型（含 placement）
- [ ] P2.5 前端 `useAgent.ts` 监听 images SSE 事件
- [ ] P2.6 新增 `frontend/src/components/ui/ImageGallery.vue`
- [ ] P2.6 前端单测 `ImageGallery.test.ts`
- [ ] P2.7 `MessageItem.vue` 按 placement 分区渲染（before_text/after_text/inline）
- [ ] P2.8 `markdown.ts` 自定义 image renderer
- [ ] P2.9.0 新增 `src/channels/_image_text_renderer.py` 共享占位符渲染
- [ ] P2.9.0 单测 `tests/unit/channels/test_image_send.py`（占位符 + 渠道发送顺序）
- [ ] P2.9.1 feishu adapter send_message 支持 images（拆分发送 + 占位符）
- [ ] P2.9.2 dingtalk adapter send_message 支持 images（拆分发送 + 占位符）
- [ ] P2.9.3 wecom_kf adapter send_message 支持 images（拆分发送 + 占位符 + media.upload）
- [ ] P2.10 `.claude/rules/frontend_dev.md` 新增图片渲染规范

---

## 附录 B：相关代码改动清单（按 Phase）

### 后端

**Phase 0**：
- 新增 `src/core/image_asset.py`
- 改造 `docs/system/file_usage.md` / `cache_usage.md`
- 改造 `.claude/rules/backend_dev.md`

**Phase 1**：
- 改造 `src/skills/travel-quote/scripts/attraction_retriever.py:213`（import_attraction async + 图片参数）
- 改造 `src/api/travel_quote.py:1317`（zip 支持）
- 改造 `src/skills/travel-quote/scripts/attraction_excel_parser.py`（新列解析）
- 改造 `src/tools/knowledge/attraction_search_tool.py:141`（cover_image 返回）
- 改造 `src/core/image_asset.py`（新增 `get_ref_by_file_id`）
- 新增 `src/tools/_image_inliner.py`
- 改造 `src/tools/word/md_to_word.py:55`（async convert + inliner）
- 改造 `src/tools/word/word_process_tool.py`（传 tenant_id）
- 改造 `subagents/travel-consultant/SUBAGENT.md`（图集章节规范）

**Phase 2**：
- 改造 `src/core/agent_events.py`（make_image_event）
- 改造 `src/models/message.py`（UnifiedResponse.images + ImageRef.placement）
- 改造 `src/core/agent.py`（主循环推送，含 placement 传递）
- 新增 `src/channels/_image_text_renderer.py`（渠道共享占位符渲染）
- 改造 `src/channels/feishu/adapter.py`（send_message images，拆分发送）
- 改造 `src/channels/dingtalk/adapter.py`（send_message images，拆分发送）
- 改造 `src/channels/wecom_kf/adapter.py`（send_message images，拆分发送 + media.upload）

### 前端

**Phase 1**：
- 改造 `frontend/src/components/travel/AttractionManager.vue`（图片上传 + 缩略图列）

**Phase 2**：
- 新增 `frontend/src/components/ui/ImageGallery.vue`
- 改造 `frontend/src/types/index.ts`（ImageRef 类型）
- 改造 `frontend/src/composables/useAgent.ts`（监听 images 事件）
- 改造 `frontend/src/components/MessageItem.vue`（渲染分支）
- 改造 `frontend/src/utils/markdown.ts`（image renderer）

### 测试

**Phase 0**：`tests/unit/test_image_asset.py`
**Phase 1**：
- `tests/unit/tools/test_image_inliner.py`
- `tests/integration/test_travel_image_pipeline.py`
**Phase 2**：
- `frontend/src/__tests__/components/ImageGallery.test.ts`
- `tests/unit/test_agent_events.py`（make_image_event）
- `tests/unit/channels/test_image_send.py`（占位符渲染 + 渠道发送顺序 + 单图失败隔离）

### 数据 / 规范

- `docs/system/file_usage.md`：新增图片资产目录条目（Phase 0）
- `docs/system/cache_usage.md`：新增 uploaded_file: 图片子类型 + image_fetch_url（Phase 0）
- `.claude/rules/backend_dev.md`：新增图片资产使用规范（Phase 0）
- `.claude/rules/frontend_dev.md`：新增图片渲染规范（Phase 2）
- `subagents/travel-consultant/SUBAGENT.md`：新增图集章节规范（Phase 1）
- **无 DDL 变更**（documents/chunks metadata JSON 扩展，无需 db_update.sql）

---

## 附录 C：风险与回滚策略

| 风险 | Phase | 影响 | 缓解 / 回滚 |
|------|-------|------|------------|
| ImageRegistry 与 cp Redis 协议冲突 | P0 | 中 | 单测验证 file_id 命名空间隔离，回滚只需删除新模块 |
| 知识库图片磁盘膨胀 | P1 | 中 | Phase 1 只允许封面图（1 图/景点），上限可控；后续加 cleanup 调度 |
| LLM 不稳定使用 `file_id:` scheme | P1 | 中 | SUBAGENT.md 给明确示例；inliner 容错（找不到保留原文） |
| Pandoc 远程图片 fetch 慢 | P1 | 中 | fetch_to_local 走 URL hash 缓存（7 天）+ 超时 15s |
| 渠道图文拆分发送消息条数过多 | P2 | 中 | 单次回复图片上限 5 张，超过转 downloadable_files；placement=after_text 不加占位符减少消息条数 |
| 渠道端 placement 占位符丢失语义 | P2 | 低 | 占位符含 display_name 帮助识别；after_text 不插占位符 |
| wecom_kf 图片需 media.upload + 2MB 限制 | P2 | 中 | 超过 2MB 的图跳过并 warning；media_id 不缓存（3 天有效期，每次重新上传） |
| dingtalk download_url 公网可达性问题 | P2 | 中 | 复用 `build_public_url`；回退方案：服务端代理下载 + base64 上传 |
| wecom 渠道客户要图片消息 | P2 | 低 | Phase 3 跟进；当前 wecom 渠道用户可降级为文字描述 |
| 历史消息（无 images 字段）回放 | P2 | 低 | images 字段可选，向后兼容；placement 缺失按 after_text 默认 |

---

## 附录 D：开发顺序与并行机会

**Phase 0**（顺序）：
1. P0.1（核心模块）→ 2. P0.1 单测 → 3. P0.2 文档协调 → 4. P0.3 规范协调

**Phase 1**（部分可并行）：
- **串行主线**：P1.2.1（import_attraction async）→ P1.2.2（API zip）→ P1.2.3（parser）→ P1.8（E2E 测试）
- **可并行**：P1.3（attraction_search 返回 cover）+ P1.4（inliner 模块）+ P1.5（md_to_word 集成）+ P1.6（SUBAGENT.md）
- **依赖前置**：P1.3 依赖 P0.1 的 `get_ref_by_file_id`；P1.5 依赖 P1.4

**Phase 2**（部分可并行）：
- **串行主线**：P2.1（事件）→ P2.3（Agent 推送）→ P2.5（前端监听）→ P2.6（组件）→ P2.7（渲染）
- **可并行**：P2.2（模型字段）+ P2.4（前端类型）+ P2.8（Markdown renderer）+ P2.9（渠道适配）
- **依赖前置**：P2.7 依赖 P2.6；P2.9 依赖 P2.2

---

## 附录 E：验收 Checklist（每 Phase 提交前）

每个 Phase 完成时，主控者必须确认：

- [ ] 所有该 Phase 任务清单条目完成
- [ ] 单元测试 + 集成测试全绿
- [ ] 三智能体流程通过（开发 → 测试 → CodeReview）
- [ ] 设计文档与本计划保持一致（如有变更同步更新）
- [ ] file_usage.md / cache_usage.md / backend_dev.md / frontend_dev.md 已协调更新
- [ ] ideas.md 第 37 条状态更新（🔧 部分完成 → ✅ 已完成 → 移到 ideas_finished.md）
- [ ] import / build 终检通过（`python -c "from src.core.image_asset import ImageRegistry"` + 前端 `npm run build`）

---

**本计划结束**。开始实施时从 Phase 0 起步，按 Phase 顺序推进，每 Phase 完成后再启动下一 Phase。
