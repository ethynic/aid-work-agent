# 系统文件存储使用情况

> 本文档介绍智能体系统中所有文件存储的使用情况，包括目录结构、文件类型、命名规范和租户附件存储策略。
> 了解文件存储分布有助于排查磁盘占用、数据迁移和租户隔离问题。

---

## 1. 存储目录结构

### 1.1 标准目录结构（规范定义）

系统规定的标准租户附件存储结构（见 `backend_dev.md`）：

```
storage/
├── output/                    # 生成的输出文件（报表、演示文稿等）
├── tenants/                   # 租户附件标准目录（新）
│   └── {tenant_id}/
│       ├── conversation/      # 对话中产生的附件
│       ├── knowledge/         # 知识库文档
│       ├── export/            # 业务导出文件
│       ├── report/            # 统计报表
│       ├── avatar/            # 用户头像、企业 Logo
│       └── temp/              # 临时文件（需有清理机制）
└── uploads/                   # 旧上传目录（历史遗留，暂不迁移）
    ├── conversation/          # 旧共享对话附件（无租户隔离）
    ├── wecom/                 # 企业微信媒体文件
    ├── wecom_kf/              # 企业微信客服渲染图片
    └── tenant_{id}/           # 旧租户上传目录
        ├── conversation/
        ├── knowledge/
        └── user_{uid}/
```

### 1.2 磁盘实际状况

| 目录 | 状态 | 说明 |
|------|------|------|
| `storage/output/` | 空目录 | 已创建但未使用 |
| `storage/tenants/` | 空目录 | 新标准目录，工具函数已实现，但调用方很少 |
| `storage/uploads/conversation/` | **有文件** | 8 个旧 `.docx` 文件（含测试文件 `old.docx`、`handler_test.docx` 等） |
| `storage/uploads/tenant_79a20837564f/conversation/` | **有文件** | 7 个 `.docx` 文件 |
| `storage/uploads/tenant_79a20837564f/knowledge/` | **有文件** | 11 个文件（`.docx`、`.txt`、`.pdf`、`.md`） |
| `storage/uploads/tenant_9eb3e45cab83/user_2db103723d42/` | **有文件** | 4 个文件（图片、`.xlsx`、`.docx`） |
| `storage/uploads/wecom/` | 运行时创建 | 企业微信媒体文件，运行时动态创建 |
| `storage/uploads/wecom_kf/` | 运行时创建 | 企业微信客服渲染图片，运行时动态创建 |

> **重要**：`storage/tenants/` 已创建但为空，`uploads/` 中的现有文件暂不迁移，后续择机迁移。

---

## 2. 存储分类总览

系统文件按用途分为以下类别：

| 类别 | 目录 | 存储类型 | 租户隔离 | 说明 |
|------|------|---------|---------|------|
| 对话附件 | `uploads/conversation/` / `tenants/{id}/conversation/` | 磁盘 | 部分有 | 用户上传/Agent 生成的文档、图片 |
| 知识图文档 | `uploads/{tenant_id}/knowledge/` | 磁盘 | 是 | 上传后解析、向量化的知识文件 |
| 渠道媒体文件 | `uploads/wecom/`、`uploads/wecom_kf/` | 磁盘 | 否 | 企业微信下载的媒体文件和渲染图片 |
| 用户个人文件 | `uploads/tenant_{id}/user_{uid}/` | 磁盘 | 是 | 用户个人上传的混合类型文件 |
| 数据源文件 | `tenants/{tenant_id}/data_sources/` | 磁盘 | 是 | 数据分析智能体的数据源 |
| 数据分析产物 | `tenants/{tenant_id}/report/`、`tenants/{tenant_id}/temp/` | 磁盘 | 是 | 分析图表/导出表（report），中间聚合 CSV（temp） |
| 技能环境文件 | `tenants/{tenant_id}/skills/{skill_name}/.env` | 磁盘 | 是 | 技能的环境变量配置 |
| 长期记忆文件 | `tenants/{tenant_id}/memory/` | 磁盘 | 是 | 长期记忆存储文件 |
| 子智能体配置 | `subagents/{dir}/extra_{tenant_id}.md` | 磁盘 | 是 | 子智能体租户级额外配置 |
| 图片资产（通用） | `tenants/{id}/images/{yyyy-mm}/` | 磁盘+Redis | 是 | ImageRegistry 管理的非知识库图片（工具生成/上传/Web 抓取），TTL 24h |
| 知识库图片资产 | `tenants/{id}/knowledge/images/` | 磁盘+Redis | 是 | 知识库关联图片（景点封面、文档内嵌图等），永久存储 |
| 输出文件 | `output/` | 磁盘 | 待实现 | 生成的报表、演示文稿等 |

---

## 3. 文件存储详细情况

### 3.1 对话附件

用户在对话中上传或 Agent 生成的附件（文档、图片、文件等）。

**旧路径**：`storage/uploads/conversation/`（无租户隔离，共享目录）
**新路径**：`storage/tenants/{tenant_id}/conversation/`
**文件命名**：`file_{uuid12}.{ext}`（如 `file_af08155fe5d9.docx`）
**调用方**：部分代码已迁移到新路径，大多数仍使用旧路径

> **问题**：`storage/uploads/conversation/` 中的文件无租户隔离，包含早期测试文件（`old.docx`、`handler_test.docx`、`same1.docx` 等），这些是开发阶段的产物。

### 3.2 知识图文档

用户上传到知识库的文档，经过解析、分块、向量化后用于 RAG 检索。

**路径**：`storage/uploads/{tenant_id}/knowledge/`
**文件类型**：`.docx`、`.pdf`、`.txt`、`.md` 等
**文件命名**：`kb_{uuid12}.{ext}`（如 `kb_0ed5f778a51f.docx`）
**生命周期**：删除文档时会同步删除对应文件并清理空目录

**源文件**：`src/knowledge/service.py`

**配置项**：
- `STORAGE_MAX_KNOWLEDGE_FILE_SIZE` — 最大知识库文件大小（默认 50MB）

### 3.3 渠道媒体文件

#### 3.3.1 企业微信媒体文件

从企业微信服务端下载的媒体文件（图片、语音、视频等）。

**路径**：`storage/uploads/wecom/`
**触发时机**：收到企业微信消息时，通过 `media_id` 下载并保存
**配置**：通过 `media_upload_dir` 配置（默认 `./storage/uploads/wecom`）

**源文件**：`src/channels/wecom/media.py`、`src/channels/wecom/adapter.py`

#### 3.3.2 企业微信客服渲染图片

企业微信客服场景中，将 Markdown 表格等内容渲染为图片发送给用户。

**路径**：`storage/uploads/wecom_kf/`
**文件类型**：`.png`、`.jpg`
**触发时机**：渲染 Markdown 内容为图片时

**源文件**：`src/channels/wecom_kf/renderer.py`、`src/channels/wecom_kf/adapter.py`

### 3.4 用户个人文件

特定用户在租户下的个人上传文件。

**路径**：`storage/uploads/tenant_{tenant_id}/user_{user_id}/`
**文件类型**：混合（`.png`、`.xlsx`、`.docx` 等）
**隔离级别**：租户 + 用户二级隔离

### 3.5 数据源文件

数据分析智能体使用的数据源文件（Excel、CSV 等）。

**路径**：`storage/tenants/{tenant_id}/data_sources/`
**文件类型**：`.xlsx`、`.csv`、`.xls` 等
**用途**：数据分析的原始数据输入

**源文件**：`src/api/data_analysis.py`（`upload_excel` 端点）

### 3.6 技能环境变量文件

租户级技能的个性化配置。

**路径**：`storage/tenants/{tenant_id}/skills/{skill_name}/.env`
**用途**：存储技能的租户级环境变量配置
**读取模式**：只读，技能加载时读取

### 3.7 长期记忆文件

租户级别的长期记忆存储。

**路径**：`storage/tenants/{tenant_id}/memory/memory_{user_id}.md`
**用途**：长期记忆文件，跨会话持久化
**迁移**：2026-09 前为 `storage/memory/{tenant_id}/`（含 `tenant_` 前缀变体），首次访问时由 LongTermMemory 自动迁移到新路径

### 3.8 子智能体租户级配置

子智能体的租户级别额外配置。

**路径**：`storage/subagents/{dir_name}/extra_{tenant_id}.md`
**用途**：子智能体定义的额外配置，按租户隔离

### 3.9 数据分析产物

数据分析工具（SmartDataAnalysisTool）生成的图表、导出表格与中间聚合数据。

**路径**：图表/导出表 `storage/tenants/{tenant_id}/report/`；中间聚合 CSV `storage/tenants/{tenant_id}/temp/`
**文件命名**：`{安全标题}_{yyyymmdd_hhmmss}.png|.xlsx`；中间 CSV `{变量名}.csv`
**调用方**：`src/tools/data_analysis/data_analyzer.py`、`analysis_agent.py`
**迁移**：2026-09 前写 `storage/analysis_charts/`、`storage/analysis_data/`（无法归属租户，历史文件另行处理）

### 3.10 图片资产（通用）

ImageRegistry 统一管理的非知识库图片资产，所有「工具生成 / 用户上传 / Web 抓取 / 截图」来源的图片都落入此目录。

**路径**：`storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}`
**文件命名**：`file_{uuid12}.{ext}`（与 cp 同前缀，复用 `uploaded_file:{file_id}` Redis 协议）
**TTL**：默认 86400s（24h），由 ImageRegistry.cleanup_temp 定期清理
**Redis 元信息**：`uploaded_file:{file_id}` Hash（含 source/usage/source_ref/linked_doc_id/width/height 等字段）
**调用方**：`src/core/image_asset.py` 的 `ImageRegistry.register(source="tool_generated"|"web_fetch"|"user_upload"|"screenshot")`
**关联模块**：[image-asset-pipeline-design.md](image-asset-pipeline-design.md)

### 3.11 知识库图片资产

知识库关联的图片（景点封面、文档内嵌图等），与文档/分块绑定的长期资产。

**路径**：`storage/tenants/{tenant_id}/knowledge/images/`（与 `knowledge/` 文档目录并列）
**TTL**：永久（Redis `uploaded_file:{file_id}` 不设 expire）
**清理时机**：知识库文档删除时级联清理（Phase 3 实现）
**调用方**：`src/core/image_asset.py` 的 `ImageRegistry.register(source="knowledge_base", usage="thumbnail"|"inline")`
**关联模块**：[image-asset-pipeline-design.md](image-asset-pipeline-design.md) §5

### 3.12 微信营销图片素材（2026-09-10 登记，P4-A）

微信营销自动化内容包的 image 块引用的图片素材（用户经工作台上传，MIME 以 PIL 头字节实测为准）。

**路径**：`storage/tenants/{tenant_id}/weixin-marketing/`（写入经 `get_tenant_storage_abs_path(tenant_id, "weixin-marketing", ...)`，storage_ref 登记该绝对路径——V-P2-1）
**文件命名**：`{asset_uuid}.{png|jpg|gif|webp|bmp}`（上传时生成 uuid4，独占创建 `xb`）
**DB 行**：`bs_weixin_marketing_assets`（storage_ref/sha256/mime/size/width/height/status/retention_until；ACL=租户+属主，行删文件删）
**TTL/留存**：`retention_until = 上传时 + weixin_marketing.retention_days`（默认 90 天）
**清理时机**：`retention_until` 已过且无 draft/published revision 引用的素材，由
`dispatch.assets_cleanup_tick` 后台任务批量硬删（行+文件，`weixin_marketing.assets_cleanup_interval_seconds`
间隔，默认 3600s，`enabled` 门控）；被引用素材受删除保护（409 ASSET_IN_USE）直到解引用。
**调用方**：`src/weixin_marketing/assets.py`（上传/列表/详情/删除/清理/Runtime 下载校验链）
**规范依据**：`docs/plans/weixin/plan-weixin-marketing-automation.md` §6.2

---

## 4. 文件命名规范

### 4.1 标准命名（推荐）

格式：`{prefix}_{uuid12}.{ext}`

| 前缀 | 用途 | 示例 |
|------|------|------|
| `file_` | 对话附件 | `file_af08155fe5d9.docx` |
| `kb_` | 知识图文档 | `kb_0ed5f778a51f.docx` |
| `doc_` | 文档记录（业务 ID） | `doc_abc123def456` |
| `chunk_` | 文本块记录（业务 ID） | `chunk_abc123def456` |
| `kc_` | 知识分类记录（业务 ID） | `kc_abc123def456` |

UUID 为 12 位十六进制字符串，保证文件名唯一性，避免覆盖冲突。

### 4.2 非标准命名（历史遗留）

部分早期文件使用原始文件名，未做 UUID 前缀：

- `storage/uploads/conversation/` 中的 `old.docx`、`new.docx`、`handler_test.docx`、`b.docx`、`same1.docx`、`same2.docx`、`original.docx`

这些是开发和测试阶段产生的文件，后续清理时可删除。

---

## 5. 文件工具函数

### 5.1 租户附件路径工具

`src/core/storage.py` 提供标准租户附件路径操作函数：

| 函数 | 用途 |
|------|------|
| `get_tenant_storage_dir(tenant_id, scene)` | 获取租户存储相对路径 |
| `ensure_tenant_storage_dir(tenant_id, scene)` | 创建目录并返回路径 |
| `get_tenant_storage_path(tenant_id, scene, filename)` | 获取完整文件相对路径 |
| `get_tenant_storage_abs_path(tenant_id, scene, filename)` | 获取文件绝对路径 |

### 5.2 文件路径兼容逻辑

部分模块实现了新旧路径的兼容桥接：

- `upload_to_remote.py:_resolve_file_path()` — 优先使用新路径，降级到旧 `uploads/` 目录
- 知识服务 — 通过 `ensure_tenant_storage_dir(tenant_id, "knowledge")` 统一写入 `storage/tenants/{tenant_id}/knowledge/`

---

## 6. 存储配置

### 6.1 配置项（`src/config/settings.py`）

```python
class StorageConfig(BaseModel):
    base_dir: str = "storage"                              # 存储根目录
    uploads_dir: str = "storage/uploads"                   # 上传目录
    max_knowledge_file_size: int = 50 * 1024 * 1024        # 最大知识库文件大小 (50MB)
    max_general_file_size: int = 20 * 1024 * 1024          # 最大通用文件大小 (20MB)
```

### 6.2 环境变量

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `STORAGE_MAX_KNOWLEDGE_FILE_SIZE` | 最大知识库文件大小（MB） | `50` |
| `STORAGE_MAX_GENERAL_FILE_SIZE` | 最大通用文件大小（MB） | `20` |

---

## 7. 文件清理策略

### 7.1 现有清理机制

| 清理类型 | 触发方式 | 说明 |
|---------|---------|------|
| 知识图文件删除 | 手动删除文档时 | `delete_document()` 同步删除文件并清理空目录 |
| 错误日志清理 | 手动端点 | 保留 30 天，清理 `log/agent` 下的错误日志 |

### 7.2 缺失的清理机制

| 缺失项 | 影响 |
|--------|------|
| 临时文件清理 | `temp/` 目录没有自动清理机制 |
| 过期对话附件清理 | 删除会话时不自动清理附件 |
| 孤立文件清理 | 无定时扫描清理孤立/孤儿文件 |
| 渠道媒体文件清理 | 企业微信媒体文件无过期清理 |

> **注意**：`storage/uploads/conversation/` 中的测试文件（`old.docx`、`handler_test.docx` 等）是永久驻留的，不会自动清理。

---

## 8. 路径双轨制问题

### 8.1 现状

系统存在**两套并存的路径体系**：

| 特性 | 旧路径（`uploads/`） | 新路径（`tenants/`） |
|------|---------------------|---------------------|
| 目录结构 | `uploads/{tenant_id}/{scene}/` | `tenants/{tenant_id}/{scene}/` |
| 租户隔离 | 部分有（`tenant_{id}`） | 完整（`{tenant_id}`） |
| 使用方 | ~15+ 源文件 | 2~3 个源文件 |
| 工具函数 | 各模块自行拼路径 | `src/core/storage.py` 统一函数 |

### 8.2 旧路径使用方（活跃）

以下模块仍写入 `uploads/` 旧路径：

| 模块 | 路径 |
|------|------|
| 企业微信媒体 | `storage/uploads/wecom` |
| 企业微信客服渲染 | `storage/uploads/wecom_kf` |
| 知识服务 | `storage/uploads/{tenant_id}/knowledge/` |
| 文件工具 | `storage/uploads/{tenant_id}/{user_id}/` |
| 旅行报价 | `storage/uploads/{tenant_id}/knowledge` |
| Word 工具 | `storage/uploads/{tenant_id}/conversation/` |

### 8.3 新路径使用方（活跃）

以下模块已使用 `tenants/` 新路径：

| 模块 | 路径 |
|------|------|
| 企业微信客服消息 | `tenants/{tenant_id}/`（通过 `ensure_tenant_storage_dir`） |
| 外部客户附件 | `tenants/{tenant_id}/`（优先新路径，兼容旧路径） |
| 数据分析上传 | `tenants/{tenant_id}/data_sources/`（通过 `ensure_tenant_storage_dir`） |

### 8.4 迁移策略

- **现有文件暂不迁移**：`uploads/` 中的现有文件保留原处，后续择机迁移
- **新代码必须使用新路径**：所有新增的文件存储操作必须使用 `src/core/storage.py` 中的工具函数
- **兼容桥接**：部分模块实现了新旧路径的兼容逻辑，读取时先尝试新路径再降级旧路径

---

## 9. 文件存储总览

```
storage/
├── output/                                    # 旧输出目录（2026-09 起废弃，遗留文件另行处理）
├── tenants/                                   # 新标准目录
│   └── {tenant_id}/
│       ├── conversation/                      # 对话附件（新）
│       ├── knowledge/                         # 知识图文档（新）
│       │   └── images/                        # 知识库图片资产（永久）
│       ├── images/                            # 图片资产（通用）
│       │   └── {yyyy-mm}/                     # 按月份分桶，file_{uuid12}.{ext}
│       ├── export/                            # 业务导出文件
│       ├── report/                            # 统计报表 / 数据分析产物
│       ├── memory/                            # 长期记忆文件
│       ├── avatar/                            # 头像/Logo
│       ├── temp/                              # 临时文件
│       └── skills/{skill_name}/.env           # 技能环境变量
├── uploads/                                   # 旧目录（暂不迁移）
│   ├── conversation/                          # 旧共享对话附件
│   ├── wecom/                                 # 企业微信媒体
│   ├── wecom_kf/                              # 企业微信客服渲染
│   └── tenant_{id}/
│       ├── conversation/                      # 旧租户对话附件
│       ├── knowledge/                         # 旧知识图文档
│       └── user_{uid}/                        # 旧用户个人文件
├── memory/                                    # 旧长期记忆目录（首次访问自动迁移到 tenants/{tid}/memory/）
├── analysis_charts/                           # 旧分析图表目录（2026-09 起废弃）
├── analysis_data/                             # 旧分析数据目录（2026-09 起废弃）
└── subagents/
    └── {dir}/
        └── extra_{tenant_id}.md               # 子智能体租户配置
```
