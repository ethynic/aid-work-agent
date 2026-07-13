# 图片资产全链路架构设计

> **文档类型**：架构设计（系统级横切关注点）
> **状态**：📋 待开发
> **创建日期**：2026-07-10
> **触发场景**：旅游顾问行程 Word 缺少景点图片 → 抽象为系统级「图片资产承载能力」
> **关联规范**：[file_usage.md](file_usage.md) / [cache_usage.md](cache_usage.md) / [backend_dev.md](../../.claude/rules/backend_dev.md)
> **关联想法登记**：[ideas.md](../ideas.md) 系统功能 #37
> **关联开发计划**：[plan-image-asset-pipeline.md](../plans/plan-image-asset-pipeline.md)

---

## 0. 文档定位

本文档解决的不是「旅游顾问加图片」这一个问题，而是**为整个系统定义「图片资产」如何被承载、传递、寻址、渲染、嵌入的统一规范**。一次性把架构搭对，后续任何场景（CRM 客户头像、社媒内容配图、电商商品图、数据分析图表、知识库图片预览等）都按此规范执行，避免每个需求各做一套。

### 设计原则

1. **统一抽象**：图片不是文件、不是 URL、不是 base64——是「图片资产（Image Asset）」，有一致的寻址与生命周期。
2. **复用现有基建**：`cp` 工具的 file_id + Redis + download_url 链路是已验证的稳定设施，新方案**复用而非另起**。
3. **来源无关**：图片来自知识库、工具生成、用户上传、外部 URL 都按同一接口暴露。
4. **渐进落地**：每个层次独立可验证，先打通主路径，能力维度逐步扩展。

---

## 1. 现状分析与核心架构空白

### 1.1 当前系统对图片的处理现状

| 场景 | 当前能力 | 缺口 |
|------|---------|------|
| **Agent 文本回复** | 纯文本/Markdown 字符串 | 无图片事件类型，无行内图片字段 |
| **工具生成图片**（x_to_image） | 返回 local path，依赖 cp 注册下载 | 无「图片卡片」与「行内图片」区分 |
| **渠道发图** | dingtalk/feishu 已支持 send_image，wecom 仅 textcard | wecom 图片消息能力未接入 send_response 流程 |
| **知识库图片资产** | documents 表已有 thumbnail_path/width/height/mime_type 字段但 service.py 未写入 | 解析器丢弃内嵌图、attraction_search 不返回图片字段 |
| **Word/PDF 嵌图** | Pandoc 支持 `![](local_path)` 但**不下载远程**，PPT python-pptx 已有 add_picture | 缺「URL→本地路径」通用 inliner |
| **前端图片渲染** | marked 默认渲染 `<img>`，DownloadFileCard 支持图片预览 | Markdown 行内图片无样式与点击放大，无图片画廊组件 |

### 1.2 六大架构空白点

```
┌──────────────────────────────────────────────────────────────┐
│ 1. 图片资产抽象：没有统一的「图片资产」数据模型                │
│ 2. SSE 图片事件：response 只能携带文本 chunk                   │
│ 3. ChatMessage 图片字段：消息只有 downloadableFiles（文件概念）│
│ 4. 知识库图片元数据：documents/chunks 字段未约定图片资产契约   │
│ 5. 图片 inliner：Markdown → 文档生成时远程图片无人下载         │
│ 6. 前端行内图片：marked 默认 <img> 无样式、无懒加载、无大图预览│
└──────────────────────────────────────────────────────────────┘
```

### 1.3 可复用的现有基建（不要重造）

| 基建 | 文件 | 复用方式 |
|------|------|---------|
| **cp 工具的下载注册** | `src/tools/file/cp_tool.py:237` `_register_download` | 直接作为图片资产的注册入口 |
| **file_id + Redis + /api/files/{id}/download** | `cp_tool.py:246-278` | 作为图片寻址的统一协议 |
| **DownloadableFileInfo 模型** | `src/models/message.py:110` | 图片资产的 Pydantic 基类（可继承扩展） |
| **租户存储工具函数** | `src/core/storage.py:62` `get_tenant_storage_path` | 所有图片资产路径必须走此函数 |
| **PPT add_picture 模式** | `src/tools/ppt/layouts/content.py:229` | 本地路径 → 文档插入的成熟范式 |
| **marked Markdown 渲染器** | `frontend/src/utils/markdown.ts` | 自定义 image renderer 的扩展点 |

---

## 2. 总体架构

### 2.1 四层架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 4: 消费场景层（Consumption）                              │
│  ┌──────────────┬────────────────┬────────────────┬───────────┐│
│  │ Agent 回复图片 │ 文档生成嵌图   │ 知识库图片展示 │ 业务页面  ││
│  │ (Layer A)    │ (Layer B)      │ (Layer C)      │ (Layer D) ││
│  └──────┬───────┴────────┬───────┴────────┬───────┴─────┬─────┘│
├─────────┼────────────────┼────────────────┼─────────────┼──────┤
│         │   Layer 3: 寻址层（Addressing）  │             │      │
│         ▼                ▼                ▼             ▼      │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  ImageRef：统一图片资产引用卡（file_id/download_url/path）│ │
│  └────────────────────┬───────────────────────────────────┘ │
├────────────────────────┼─────────────────────────────────────┤
│                        │ Layer 2: 注册层（Registry）          │
│                        ▼                                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  ImageRegistry：注册到 Redis + 磁盘租户目录            │ │
│  │  - register_image() 统一入口                            │ │
│  │  - 持久化 vs 临时 / 内联 vs 附件 的元信息                │ │
│  └────────────────────┬───────────────────────────────────┘ │
├────────────────────────┼─────────────────────────────────────┤
│                        │ Layer 1: 来源层（Source）             │
│                        ▼                                     │
│  ┌─────────────┬──────────────┬──────────────┬─────────────┐ │
│  │ 知识库资产  │ 工具生成图片 │ 用户上传     │ 外部 URL    │ │
│  │ (existing)  │ (x_to_image) │ (chat attach)│ (web fetch) │ │
│  └─────────────┴──────────────┴──────────────┴─────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心数据契约：ImageRef

**所有层之间传递图片都使用 `ImageRef` 结构**（不允许传 file_path / url / base64 这种裸字段）：

```python
# src/core/image_asset.py（新增模块）

class ImageRef(BaseModel):
    """统一图片资产引用卡 - 系统内传递图片的唯一契约"""
    file_id: str                    # 复用 cp 的 file_id 体系（file_xxxxxxxxxxxx）
    download_url: str               # = /api/files/{file_id}/download
    display_name: str               # 业务显示名（如"黄果树瀑布.jpg"）
    width: Optional[int] = None     # 像素宽（可缺省）
    height: Optional[int] = None    # 像素高（可缺省）
    mime_type: str = "image/*"      # MIME 类型
    size_bytes: int = 0             # 字节大小

    # 来源溯源（用于审计、清理策略、UI 提示）
    source: Literal[
        "knowledge_base",   # 来自知识库资产
        "tool_generated",   # 工具生成（x_to_image 等）
        "user_upload",      # 用户对话中上传
        "web_fetch",        # 从外部 URL 下载
        "screenshot",       # 浏览器/RPA 截图
    ]
    source_ref: Optional[str] = None  # 来源具体引用（doc_id / tool_call_id / url / session_id）

    # 用途语义（决定是否在 UI 行内显示 / 是否清理 / 是否计费）
    usage: Literal[
        "inline",           # 行内图片（Agent 回复中的图）
        "attachment",       # 附件形式
        "embedded",         # 嵌入文档内部
        "thumbnail",        # 缩略图（用于列表预览）
    ] = "inline"

    # 位置语义（决定 Web 端渲染位置 + 渠道端占位符策略，见 §4.4.2）
    placement: Literal[
        "after_text",       # 文本之后（默认，最简单）
        "before_text",      # 文本之前
        "inline",           # 文本流中行内（需配合 Markdown ![]() 占位）
    ] = "after_text"

    # 业务关联（可选，用于知识库资产等场景）
    linked_doc_id: Optional[int] = None
    linked_chunk_id: Optional[int] = None
```

**为什么要有 ImageRef 而不是直接复用 DownloadableFileInfo**：
- DownloadableFileInfo 是面向"下载交付物"的（word、excel、pdf、图片混合），没有图片语义。
- ImageRef 携带 `source` 和 `usage`，可以指导前端是渲染成行内图还是下载卡片、后端是否做清理、审计如何归类。
- 但 **ImageRef 内部仍使用 file_id/download_url，与 cp 完全兼容**——继承而非替换。

---

## 3. Layer 1-2：图片资产管理与注册

### 3.1 ImageRegistry 模块

新增 `src/core/image_asset.py`，提供注册、寻址、清理三件套。

```python
# src/core/image_asset.py 关键接口

class ImageRegistry:
    """图片资产注册与寻址"""

    async def register(
        self,
        source_path: Union[str, Path],     # 源文件路径
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        source: str = "tool_generated",
        usage: str = "inline",
        source_ref: Optional[str] = None,
        move: bool = False,                 # True=移动，False=复制
    ) -> ImageRef:
        """
        注册图片到租户目录，返回 ImageRef。
        存储路径：storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}
        走 cp_tool._register_download 同样的 Redis 协议。
        """

    async def resolve_local_path(self, ref: ImageRef) -> Path:
        """从 ImageRef 解析出本地路径（供文档生成 inliner 使用）"""

    async def fetch_to_local(
        self,
        url: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        timeout: int = 15,
    ) -> ImageRef:
        """从外部 URL 下载图片并注册（source=web_fetch）"""

    async def cleanup_temp(self, older_than_hours: int = 24):
        """清理临时图片资产（usage=inline/embedded 且超过 TTL）"""
```

**关键约束**：
- 所有图片资产路径必须走 `get_tenant_storage_path(tenant_id, "images", filename)`，对齐 [file_usage.md](file_usage.md) 的 `storage/tenants/{tenant_id}/{scene}/` 规范
- 在 file_usage.md 的目录表中**新增 `images/` 子目录**（见 §7）
- Redis key 复用 `uploaded_file:{file_id}` 命名空间（与 cp 一致），TTL 默认 86400s，知识库资产可设永久（-1）

### 3.2 来源接入约定

| 来源 | 接入点 | 改造方式 |
|------|-------|---------|
| 知识库资产 | `KnowledgeBaseService` 上传时 | 上传图片文件 → ImageRegistry.register(source="knowledge_base", usage="thumbnail") |
| 工具生成 | `x_to_image_tool.py` / 未来图表工具 | 工具 execute 末尾 → ImageRegistry.register(source="tool_generated", usage="inline") |
| 用户上传 | 聊天附件上传 API | 已有逻辑保留，附件类型为 image/* 时额外生成 ImageRef |
| 外部 URL | 文档生成 inliner | ImageRegistry.fetch_to_local(url, source="web_fetch", usage="embedded") |

---

## 4. Layer 4-A：Agent 回复中显示图片

### 4.1 SSE 事件扩展

**新增 `image` 事件**（不复用 response 拼接，避免污染文本流）：

```python
# src/core/agent_events.py 扩展
def make_image_event(refs: List[ImageRef], placement: str = "after_text") -> Dict:
    """
    placement:
      - "inline": 在文本流当前位置插入（需要配合 markdown ![]() 占位）
      - "after_text": 文本结束后追加图片画廊（默认，最简单）
      - "before_text": 图片在前，文本在后
    """
    return make_event(
        "images",
        images=[ref.model_dump() for ref in refs],
        placement=placement,
    )
```

**Agent 如何触发图片事件**：
- Agent 在 `process_message` 中识别工具结果中的 ImageRef（如 attraction_search 返回带图片），通过 `yield make_image_event(refs, placement="after_text")` 推送。
- LLM 也可以在文本中显式标记 `[[IMAGE:file_id]]` 占位符，Agent 后处理时替换为图片事件（高级用法，Phase 3）。

### 4.2 ChatMessage 字段扩展

前端消息类型新增 `images` 字段：

```typescript
// frontend/src/types/index.ts
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
}

export interface ChatMessage {
  // ... 现有字段
  images?: ImageRef[]          // 新增：本条消息携带的图片
  attachments?: Attachment[]   // 现有
  downloadableFiles?: DownloadableFile[]  // 现有
}
```

**持久化路径**：与 `downloadableFiles` 同样存入 `chat_messages.metadata`（JSON）。**不需要改表结构**。

### 4.3 前端渲染

**MessageItem.vue 新增 images 渲染分支**：

```vue
<!-- 三种 placement 的渲染位置 -->
<div v-if="message.images?.length" class="message-images" :class="`placement-${placement}`">
  <ImageGallery :images="message.images" />
</div>
```

**ImageGallery.vue 新组件**：
- 网格布局（1 张大图 / 2-3 张并排 / 4+ 张瀑布流）
- 懒加载 + 占位骨架
- 点击放大（lightbox，复用现有 DownloadFileCard 的预览能力）
- 长按/右键下载
- source 标识（来源图标，如"知识库"/"AI 生成"）

**Markdown 行内图片支持**：
- `frontend/src/utils/markdown.ts` 新增 image renderer：`![alt](download_url)` 渲染为带 lightbox 的 `<img loading="lazy" class="md-inline-image">`
- 关键：**LLM 在生成 Markdown 时能拿到 download_url**（通过工具返回的 ImageRef，见 §6）

### 4.4 渠道适配

#### 4.4.1 渠道能力差异（关键约束）

**Web 端**支持图文混排（Markdown 中 `![](url)` 行内渲染、`images` 字段渲染画廊），但**所有第三方渠道（wecom_kf / wecom / dingtalk / feishu）的图片与文本必须拆成多条独立消息发送**，无法在一条消息中图文混排。

| 渠道 | 单消息能力 | 图文混排支持 | 拆分发送支持 |
|------|----------|------------|------------|
| Web 端 | Markdown 渲染 + ImageGallery | ✅ 支持 | — |
| feishu | 文本消息 / 图片消息 / 文件消息（互斥） | ❌ 不支持 | ✅ 顺序发送多条 |
| dingtalk | 文本消息 / 图片消息（sampleImageMsg）/ 链接消息 | ❌ 不支持 | ✅ 顺序发送多条 |
| wecom_kf | 客服消息（text/image/link 互斥） | ❌ 不支持 | ✅ 顺序发送多条 |
| wecom | 文本卡片 / 图片消息 | ❌ 不支持 | ✅ 顺序发送多条 |

**核心约束**：渠道适配器必须把 `UnifiedResponse`（含 text + images + downloadable_files）拆分为**多条独立消息**按顺序发送，每条消息只能是单一类型（text / image / file / link）。

#### 4.4.2 拆分发送策略

**默认拆分顺序**（所有渠道通用）：

```
1. [文本消息]  response.text（已 markdown_to_plain_text 处理）
2. [图片消息]  response.images[0]   ← 逐张发送
3. [图片消息]  response.images[1]
   ...
4. [文件消息]  response.downloadable_files[0]   ← 逐个发送
   ...
```

**placement 字段的渠道降级语义**：

ImageRef 的 `placement`（"before_text" / "inline" / "after_text"）在 Web 端决定渲染位置，在渠道端**降级为统一的"文本之后发送"**——不保留前后语义，但通过**文本占位提示**补偿位置信息：

| placement | Web 端渲染 | 渠道端降级 |
|-----------|----------|----------|
| `before_text` | 图片在文本上方 | 文本前加「[图片]」占位符 + 图在文本后发送 |
| `inline` | 文本流中行内渲染 | 文本中插入位置加「[图片：{display_name}]」占位符 + 图在文本后发送 |
| `after_text` | 文本下方画廊 | 图在文本后发送（无占位符） |

**占位符规则**（仅渠道端，Web 端不需要）：
- `before_text`：在文本开头插入 `[图片：{display_name}]\n`，让用户知道后面有图
- `inline`：在 LLM 标记的图片位置（通过 Markdown `![alt](file_id:xxx)` 解析定位）插入 `[图片：{alt}]\n`
- `after_text`：不加占位符（图自然在文本之后）

**关键决策**：渠道端不尝试还原 Web 端的图文混排视觉，而是通过「文本占位 + 图随后发送」让用户能理解图文关系。

#### 4.4.3 渠道适配实现

| 渠道 | 适配方式 |
|------|---------|
| **feishu** | 已有 `media.upload_image(local_path)` + `send_image`（`feishu/adapter.py:492`）——从 ImageRef 解析 local_path，直接发 |
| **dingtalk** | 已有 `upload_from_url` + `sampleImageMsg`（`dingtalk/adapter.py:354`）——从 ImageRef.download_url 上传 |
| **wecom** | 当前只发 textcard，需补 `media.upload` + image msg 路径（`wecom/adapter.py:294-320` 需扩展） |
| **wecom_kf** | 客服消息支持图片 msg（`api_client.send_msg` msgtype="image"），需扩展适配器 |

**所有渠道的 send_message 改造模式**（统一抽象）：

```python
async def send_message(self, message: UnifiedResponse) -> bool:
    """
    渠道发送：按「文本 → 图片 → 文件」顺序拆分发送。
    单条失败不阻断后续，记 warning。
    """
    all_success = True

    # 1. 文本（含 placement 占位符）
    text = self._render_text_with_placeholders(message)
    if text:
        all_success = await self._send_text(text, message.reply_to)

    # 2. 图片（逐张发送，渠道特定上传方式）
    for ref in message.images:
        try:
            success = await self._send_image_ref(ref, message.reply_to)
            if not success:
                all_success = False
        except Exception as e:
            logger.warning(f"[{channel}] send image {ref.file_id} failed: {e}")
            all_success = False

    # 3. 可下载文件（现有逻辑保留）
    for file_info in message.downloadable_files:
        ...

    return all_success

def _render_text_with_placeholders(self, message: UnifiedResponse) -> str:
    """根据图片 placement 在文本中插入占位符（仅渠道端用）。"""
    text = message.text
    if not text or not message.images:
        return text
    # before_text: 开头插入
    # inline: 解析 Markdown ![]() 位置插入
    # after_text: 不插入
    ...
```

**UnifiedResponse 新增 images 字段**（对称于 downloadable_files）：

```python
# src/models/message.py 扩展
class UnifiedResponse(BaseModel):
    # ... 现有字段
    images: List[ImageRef] = Field(default_factory=list)  # 新增
```

渠道 `send_message` 适配器遍历 `response.images`，按各渠道 API 发送。

---

## 5. Layer 4-C：知识库图片资产

### 5.1 数据模型扩展（不改表结构）

**核心决策**：复用 `documents.metadata`（JSON）和 `chunks.metadata`（JSON）存图片资产，**不需要 DDL 变更**。

#### 5.1.1 documents.metadata 图片契约

```json
{
  "images": {
    "cover": "file_id_of_cover_image",        // 封面图（必须，用于列表预览）
    "gallery": ["file_id_1", "file_id_2"],    // 图集（可选）
    "inline": {                                // 内嵌图（按 chunk 锚点，可选）
      "chunk_0_top": "file_id_x"
    }
  }
}
```

#### 5.1.2 chunks.metadata 图片契约

```json
{
  "images": ["file_id_1", "file_id_2"],   // 该 chunk 关联的图片
  "image_role": "illustrative"            // illustrative|cover|diagram|screenshot
}
```

#### 5.1.3 documents 表现有未使用字段（`thumbnail_path/width/height/mime_type`）

**改造 service.py:280 INSERT 时填充这些字段**（针对 source_type 为图片/视频/带封面图的景点）。这些字段是**冗余但便于 SQL 直接查询**的版本，metadata.images 是结构化主权威。

### 5.2 景点知识库（attraction_resource）改造

#### 5.2.1 attraction_search 返回结构扩展

```python
# src/tools/knowledge/attraction_search_tool.py:141 扩展返回
{
    "doc_id": ...,
    "title": ...,
    "info": ...,
    "project_table": ...,
    "score": ..., "region": ..., "category": ..., "category_cn": ...,
    # 新增字段
    "cover_image": ImageRef | None,        # 封面图（最常用）
    "images": [ImageRef, ...]              # 图集（可选）
}
```

#### 5.2.2 图片资产导入路径

景点数据导入当前通过 `skills/travel-quote/scripts/attraction_retriever.py:import_attraction()`。改造点：

- 导入接口（`POST /api/v1/travel-quote/import/attraction-excel-kb`）支持上传一个 zip 包，里面 Excel + 景点图片目录
- Excel 里新增列 `图片` / `图集`，值为图片文件名（相对于图片目录）
- `import_attraction()` 解析时调用 ImageRegistry.register(source="knowledge_base", usage="thumbnail")，写入 documents.metadata.images

**关键约定**：知识库图片**不允许使用外部 URL**（防止链接失效），必须落地到 `storage/tenants/{tenant_id}/knowledge/images/`。

### 5.3 解析器图片提取能力（Phase 2，可选）

- Word/Excel/PPT/PDF 解析时提取内嵌图片 → ImageRegistry.register(source="knowledge_base", usage="inline") → 写入对应 chunk 的 metadata.images
- image_parser.py 当前是空壳，需要实现 OCR + 多模态描述（VLM）→ 生成可检索的文本 chunk

### 5.4 前端知识库管理 UI 扩展

- `KnowledgeBase.vue` 上传弹窗 accept 列表增加 `image/*`
- 文档列表新增「封面」缩略图列
- 文档预览页新增「图集」面板（ImageGallery）
- 景点管理页（`AttractionManager.vue`）每个景点行新增封面图上传 + 图集管理

---

## 6. Layer 4-B：文档生成中嵌入图片

### 6.1 通用 image_inliner 模块

**新增 `src/tools/_helpers.py` 旁的 `src/tools/_image_inliner.py`**，所有文档生成工具（word/excel/pdf/ppt）共用：

```python
# src/tools/_image_inliner.py

async def inline_images(
    markdown_or_html: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    syntax: Literal["markdown", "html"] = "markdown",
) -> Tuple[str, List[ImageRef]]:
    """
    扫描文本中的图片引用，下载/解析为本地路径，返回处理后的文本 + ImageRef 列表。

    支持的引用形式（markdown syntax）：
      ![alt](http://...)         → 远程 URL，fetch_to_local
      ![alt](file_id:file_xxx)   → ImageRef 直接引用（推荐，零拷贝）
      ![alt](kb://doc_id)        → 知识库 doc 的封面（domain-specific scheme）

    支持的引用形式（html syntax）：
      <img src="...">            → 同上解析

    返回的文本中所有图片 src 替换为本地绝对路径，可直接交给 Pandoc/python-docx/python-pptx。
    """
```

**关键设计**：引入**自定义 URL scheme** 让 LLM 也能稳定引用图片，无需 base64：

| Scheme | 含义 | 示例 |
|--------|------|------|
| `file_id:file_xxx` | 直接引用 ImageRef（最快） | `![](file_id:file_abc123)` |
| `kb://doc/{doc_id}` | 知识库文档封面 | `![](kb://doc/42)` |
| `kb://chunk/{chunk_id}` | 知识库 chunk 关联图 | `![](kb://chunk/108)` |
| `http(s)://...` | 远程 URL（inliner 负责下载） | `![](https://...)` |

### 6.2 word_process 工具改造

`src/tools/word/md_to_word.py:75` `convert()` 入口前调用 `inline_images(content)`：

```python
# 改造 md_to_word.py 的 convert()
async def convert(markdown_content: str, ...):
    # 新增：在 normalize_markdown 之后、_pandoc_convert 之前
    markdown_content, image_refs = await inline_images(
        markdown_content,
        tenant_id=current_tenant_id,
        user_id=current_user_id,
    )
    normalized = normalize_markdown(markdown_content)
    # ... 后续 Pandoc 转换
```

### 6.3 ppt_process / pdf_process 改造

- ppt_process 的 spec_builder 已经支持 ImageNode(path=...)（`spec_builder.py:432`），只需在 spec 构造时把 URL 替换成本地路径（通过 inline_images 处理 spec 中的 path）
- pdf_process 的 md_to_pdf：weasyprint 接收 HTML 后由 inliner 预处理 `<img src>`

### 6.4 excel 工具嵌图

openpyxl 支持 `ws.add_image`，但 Excel 报价单/明细单嵌图**需求较弱**（会撑大文件），暂不做。旅游顾问的 Excel 报价单保持纯表格。

### 6.5 旅游顾问行程文档的图片应用方案

基于 SUBAGENT.md 详细行程格式规范（5 列表格），图片嵌入方案如下（推荐 **方案 B**）：

**方案 A（不推荐）**：表格单元格内嵌图——Pandoc pipe_tables 不支持块级图片。

**方案 B（推荐）**：行程结构保持表格不动，**在表格后追加"景点图集"章节**：

```markdown
## 贵州天眼+荔波6天5晚研学行程

**出发日期**：2026-07-01
**团队构成**：30名初三学生 + 3位带队老师
**行程概要**：...

| 天数 | 时段 | 行程安排 | 游玩项目 | 备注 |
|------|------|----------|----------|------|
| D1 | 下午 | 贵阳接站... | — | ... |
| D2 | 上午 | 天文体验馆... | 专业讲解 | ... |
...

---

## 景点图集

### 小七孔景区
![小七孔景区](file_id:file_aaaa1)
*卧龙潭、翠谷瀑布、水上森林*

### FAST观景台
![FAST观景台](file_id:file_bbbb2)
*登台俯瞰天眼全貌*

### 天文体验馆
![天文体验馆](file_id:file_cccc3)
```

**方案 C（备选）**：行程改为 H3 分节列表形态（与 SUBAGENT.md「详细行程必须用表格」冲突，需协调规则）。

**Agent 行为改造**（SUBAGENT.md 第四步流程）：
1. 搜索景点时（attraction_search）拿到每个景点的 `cover_image`（ImageRef）
2. 输出详细行程时，**在 5 列表格后追加「景点图集」章节**，按行程中出现的景点顺序，每个景点用 `![title](file_id:file_xxx)` 插入封面图 + 一句话描述
3. 调用 `word_process` 时 context 包含图集章节 → inliner 把 file_id: 替换为本地路径 → Pandoc 嵌入 Word

---

## 7. 与现有规范的协调

### 7.1 file_usage.md 增补

在 [file_usage.md](file_usage.md) §2 分类表新增：

| 类别 | 目录 | 存储类型 | 租户隔离 | 说明 |
|------|------|---------|---------|------|
| **图片资产** | `tenants/{id}/images/{yyyy-mm}/` | 磁盘+Redis | 是 | ImageRegistry 统一管理的所有图片（来源：知识库/工具生成/上传/Web 抓取） |
| **知识库图片资产** | `tenants/{id}/knowledge/images/` | 磁盘+Redis | 是 | 知识库关联图片（与文档/分块关联） |

### 7.2 cache_usage.md 增补

Redis 新增 `uploaded_file:file_xxx` 用途的图片子类型（与 cp 共用，但 source 字段区分）：

| 缓存 | 键模式 | TTL | 失效时机 |
|------|-------|-----|---------|
| 图片资产（行内/嵌入） | `uploaded_file:file_xxx`（复用） | 86400s | 文档生成后立即清理（usage=embedded）|
| 图片资产（知识库） | `uploaded_file:file_xxx` | -1（永久） | 知识库文档删除时级联清理 |

### 7.3 backend_dev.md 增补

新增「图片资产使用规范」章节：
- 所有图片资产必须通过 ImageRegistry 注册，禁止业务代码直接拼 `storage/` 路径
- 所有传递图片的接口必须使用 ImageRef（禁止传 file_path/url/base64 裸字段）
- 知识库图片禁止使用外部 URL，必须落地到租户目录

### 7.4 frontend_dev.md 增补

新增「图片渲染规范」：
- 所有图片展示必须使用 ImageGallery 或 MdInlineImage 组件（禁止裸 `<img>`）
- 图片必须支持懒加载、点击放大
- 图片来源必须显示 source 标识

---

## 8. 开发计划（Phase 切分）

### Phase 0：基建（必须先行）
- [ ] 新增 `src/core/image_asset.py`（ImageRef 模型 + ImageRegistry 类）
- [ ] 改造 `cp_tool._register_download` 抽出可复用的注册逻辑（或直接调用 ImageRegistry）
- [ ] 测试：ImageRegistry 单元测试 + 与 cp 兼容性测试

### Phase 1：知识库图片资产 + 旅游顾问打通
**目标**：旅游顾问能生成带景点图片的 Word 行程
- [ ] 改造 `attraction_retriever.import_attraction()` 支持图片资产
- [ ] 改造景点 Excel 导入接口接受 zip（Excel + 图片目录）
- [ ] 改造 `attraction_search_tool` 返回 cover_image 字段
- [ ] 新增 `src/tools/_image_inliner.py`（markdown + file_id: scheme）
- [ ] 改造 `md_to_word.convert()` 在 Pandoc 前调用 inline_images
- [ ] 改造 `subagents/travel-consultant/SUBAGENT.md` 行程输出规范（追加"景点图集"章节）
- [ ] 前端景点管理页支持图片上传 + 缩略图列
- [ ] 端到端：上传景点 Excel+图片 → 搜索 → 出行程 → 生成带图 Word

### Phase 2：Agent 回复图片承载
**目标**：Agent 能在回复中直接显示图片（不仅是文档）
- [ ] 新增 `images` SSE 事件 + `make_image_event()`
- [ ] ChatMessage / UnifiedResponse 新增 images 字段（含 placement）
- [ ] 前端 `useAgent.ts` 监听 images 事件
- [ ] 前端 `ImageGallery.vue` + `MessageItem.vue` 渲染分支
- [ ] 前端 markdown.ts 自定义 image renderer
- [ ] 渠道适配器 send_message 支持 images（feishu/dingtalk 先打通，wecom 后跟）
- [ ] **渠道图文拆分发送**：所有渠道按「文本→图片→文件」顺序拆分多条发送，placement 降级为文本占位符（见 §4.4.2）

### Phase 3：高级能力（按需）
- [ ] 文档解析器提取内嵌图片（word_parser / pdf_parser 等）
- [ ] `image_parser.py` 实现（OCR + VLM 描述 → 可检索 chunk）
- [ ] `[[IMAGE:file_id]]` LLM 占位符协议（Agent 后处理替换为图片事件）
- [ ] 知识库 Web 上传支持图片格式（KnowledgeBase.vue accept）
- [ ] PPT/PDF 嵌图路径完善（pdf_process / ppt_process 接入 inliner）

### Phase 切分原则
- Phase 0 是基石，所有后续依赖
- Phase 1 直击本次旅游顾问需求，**最小集**
- Phase 2 解锁"Agent 回复显图"这一系统级能力（其他子智能体受益）
- Phase 3 是能力深化，按需投入

---

## 9. 关键决策与权衡

### 9.1 为什么用 ImageRef 而不是直接扩展 DownloadableFileInfo？

| 维度 | DownloadableFileInfo | ImageRef |
|------|---------------------|----------|
| 语义 | 通用文件下载 | 图片专用 |
| 字段 | file_id/name/size/url/mime | + width/height/source/usage/linked_doc_id |
| UI | 下载卡片 | 行内画廊 / 缩略图 / 嵌入文档 |
| 清理 | 统一 TTL | 按 source/usage 区分（知识库永久） |
| 审计 | 无来源 | 有 source/source_ref |

**结论**：图片语义比文件丰富，单独建模型；但底层 file_id/download_url 复用同一套设施。

### 9.2 为什么引入 file_id: / kb:// 自定义 scheme？

**问题**：LLM 生成 Markdown 时如何引用图片？
- 选项 A：直接 `![alt](http://...)`——LLM 不一定知道 URL
- 选项 B：base64 内嵌——污染 context、token 爆炸
- 选项 C：`![alt](file_id:file_xxx)`——LLM 从工具结果拿到 ImageRef，提取 file_id 即可
- 选项 D：`![alt](kb://doc/42)`——更语义化，inliner 负责解析

**结论**：C+D 组合。LLM 拿到工具结果中的 ImageRef，写 `file_id:file_xxx`；inliner 解析为本地路径交给 Pandoc。

### 9.3 为什么行程文档用「景点图集」章节而不是表格内嵌图？

- Pandoc pipe_tables 单元格不支持块级图片（事实）
- 改表格为列表形态会破坏 SUBAGENT.md 现有规范的报价引擎依赖（itinerary_text 一字不差传入）
- 「图集章节」与现有表格共存，零侵入

### 9.4 为什么知识库图片禁止外部 URL？

- 外部 URL 易失效（链接腐烂）
- 无法做租户隔离 / 计费 / 审计
- 知识库要求**长期稳定可用**，必须落地租户目录

### 9.5 为什么 SSE 用独立 `images` 事件而不是塞进 `response`？

- response 是流式 text chunk 拼接，混入图片会破坏流式渲染
- 独立事件让前端有专门的图片处理逻辑（懒加载、画廊布局）
- 持久化时 metadata.images 单独存储，结构清晰

### 9.6 为什么渠道端要拆分发送 + 占位符降级？

**问题**：Web 端支持图文混排（Markdown 行内图 + ImageGallery），但所有第三方渠道（feishu/dingtalk/wecom/wecom_kf）**单条消息只能是单一类型**（text 或 image 或 file），无法在一条消息中图文混排。

**选项对比**：
- 选项 A：渠道只发文本，图片丢弃——信息损失，用户体验差
- 选项 B：渠道只发图片，文本丢弃——更差
- 选项 C：拆分多条发送（文本 + 图片 + 文件）——顺序固定，但前后语境割裂
- 选项 D：拆分多条 + 文本占位符补偿位置——C 的增强，通过 `[图片：xxx]` 占位符让用户理解图文关系 ✅

**结论**：选项 D。拆分发送（统一顺序：文本→图片→文件）+ placement 降级为文本占位符。

**为什么不尝试还原图文混排**：
- 渠道 API 限制（无 markdown 渲染、无行内图）
- 还原成本高（需要把文本按图片位置切成多段，每段夹一张图发送，消息条数爆炸）
- 用户体验未必更好（消息太多反而混乱）

**占位符的设计权衡**：
- `before_text` / `inline` 才插占位符（用户需要知道"这里有图"）
- `after_text` 不插（图自然在文本之后，语义清晰）
- 占位符文本用 `[图片：{display_name}]` 而非 `[图]`——展示图片名帮助用户识别

---

## 10. 风险与待解问题

| 风险 | 影响 | 缓解 |
|------|------|------|
| Pandoc 远程图片 fetch 增加文档生成耗时 | 中 | inliner 走 Redis 缓存（同 URL 不重复下载）+ 超时控制 15s |
| 知识库图片资产膨胀磁盘 | 高 | Phase 1 只允许上传封面图，图集数量上限；Phase 3 加清理任务 |
| LLM 不稳定使用 `file_id:` scheme | 中 | SUBAGENT.md 给明确示例 + 模板；inliner 容错（找不到就跳过） |
| wecom 图片消息需要 media.upload + access_token | 中 | Phase 2 后跟，先打通 feishu/dingtalk |
| 历史消息（无 images 字段）回放 | 低 | metadata.images 可选字段，向后兼容 |
| 渠道图文拆分发送消息条数过多 | 中 | 单次回复图片上限（默认 5 张），超过转 downloadable_files |
| 渠道端 placement 占位符丢失语义 | 低 | 占位符包含 display_name 帮助识别，关键图（如景点封面）用户能关联 |

---

## 11. 验收标准

### Phase 1 完成验收
- [ ] 上传景点 Excel + 图片包，能在景点管理页看到封面缩略图
- [ ] attraction_search 返回 cover_image 字段
- [ ] 旅游顾问生成行程 Word 时，"景点图集"章节图片可见
- [ ] word_process 的 file_id: scheme 被 inliner 正确解析

### Phase 2 完成验收
- [ ] Agent 调用任何工具返回 ImageRef 时，前端能渲染图片画廊
- [ ] feishu/dingtalk 渠道能收到图片消息
- [ ] 历史消息（含图片）回放正常
- [ ] 图片懒加载 + 点击放大可用

---

## 附录 A：与各子智能体的应用场景映射

| 子智能体 | 图片需求场景 | 走的层 |
|---------|------------|-------|
| **travel-consultant** | 景点图、酒店图、行程文档配图 | Layer 1+2+4-B+C |
| **数据分析智能体** | 图表渲染（matplotlib/echarts → ImageRef） | Layer 1+2+4-A |
| **CRM** | 客户头像、跟进记录截图 | Layer 1+2+4-A+D |
| **社媒运营** | 内容配图、平台抓取图、生成图 | Layer 1+2+4-A+C |
| **电商客服** | 商品图、订单截图 | Layer 1+2+4-A+C |

所有场景按本规范统一接入，无需各做一套。

---

## 附录 B：相关代码改动清单（Phase 1+2）

### 后端
- 新增 `src/core/image_asset.py`（ImageRef + ImageRegistry）
- 改造 `src/tools/file/cp_tool.py`（抽出 _register_download 逻辑或调用 ImageRegistry）
- 改造 `src/knowledge/service.py:280` INSERT 填充 thumbnail_path/width/height/mime_type
- 改造 `src/skills/travel-quote/scripts/attraction_retriever.py` import_attraction 支持图片
- 改造 `src/tools/knowledge/attraction_search_tool.py:141` 返回 cover_image
- 新增 `src/tools/_image_inliner.py`
- 改造 `src/tools/word/md_to_word.py:75` convert() 调用 inline_images
- 改造 `src/core/agent_events.py` 新增 make_image_event
- 改造 `src/core/agent.py` 工具结果含 ImageRef 时 yield images 事件
- 改造 `src/models/message.py` UnifiedResponse 新增 images 字段
- 改造 `src/channels/{feishu,dingtalk,wecom}/*.py` send_message 支持 images

### 前端
- 新增 `frontend/src/components/ui/ImageGallery.vue`
- 改造 `frontend/src/components/MessageItem.vue` 渲染 images
- 改造 `frontend/src/types/index.ts` 新增 ImageRef 类型
- 改造 `frontend/src/composables/useAgent.ts` 监听 images SSE 事件
- 改造 `frontend/src/utils/markdown.ts` 自定义 image renderer
- 改造 `frontend/src/components/travel/AttractionManager.vue` 图片上传/缩略图
- 改造 `frontend/src/components/KnowledgeBase.vue` accept + 图片预览

### 数据/规范
- `deploy/db_update.sql`：documents 表 thumbnail_path/width/height/mime_type 字段使用规范（已存在，无需 DDL）
- `docs/system/file_usage.md`：新增图片资产目录条目
- `docs/system/cache_usage.md`：新增 uploaded_file: 图片子类型
- `.claude/rules/backend_dev.md`：新增图片资产使用规范
- `.claude/rules/frontend_dev.md`：新增图片渲染规范
- `subagents/travel-consultant/SUBAGENT.md`：行程输出规范追加"景点图集"章节
