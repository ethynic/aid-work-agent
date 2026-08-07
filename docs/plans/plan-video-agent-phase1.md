# 视频创作智能体 Phase 1 - 开发计划

> 关联：[产品定位设计文档](../system/content-production/video-agent-enterprise-positioning-design.md)（权威设计，本计划按其 §5.3.4 / §5.6 / §7.4 落地）
>
> 本计划覆盖整体 Phase 1 三大重点：①企业组织沉淀（素材库 / 视频库 / 提示词库）；②以会话聊天形式创作视频（精修/敏捷双模 + 工具栏）；③计费接入（VIDEO_GEN_USAGE_FACTOR + chat_records）。
>
> 整体 Phase 1 周期：5-7 周。三智能体开发流程（开发->测试->CodeReview）逐子阶段推进。
>
> **术语约定**：本计划中「Phase 1」指整体开发阶段（5-7 周）；下文「Phase 总览」表中的 Phase 0/1/2/.../7 是整体 Phase 1 内部的子阶段（如 Phase 0 配置、Phase 1 数据层等），勿混淆。

---

## 开发流程约定

遵循项目三智能体开发流程（[dev_workflow](../../.claude/rules/dev_workflow.md)）：每个 Phase 完成 -> 独立测试 -> CodeReview -> 提交。非平凡改动走完整流程，简单 typo/文档变更可简化。

**本计划不改 MVP 原型**：原「视频创作」页面（gen_sessions 表）保留作为演示原型，新功能独立开发，走 chat_sessions 表。

---

## Phase 总览

| Phase | 主题 | 周期 | 依赖 | 主要产出 |
|-------|------|------|------|---------|
| Phase 0 | 配置与基础设施 | 0.5-1 天 | - | env 变量、配置项、计费配置同步 |
| Phase 1 | 数据层 + 表结构 | 1-2 天 | Phase 0 | 素材库/视频库/提示词库三表 + chat_sessions 扩展 |
| Phase 2 | 计费链路接入 | 2-3 天 | Phase 0、Phase 1 | billing.py 扩展、video_gen 接 chat_records、租户余额拦截 |
| Phase 3 | 后端：会话化创作服务 | 3-4 天 | Phase 1、Phase 2 | VideoChatService、提示词引擎、留用入库 |
| Phase 4 | 后端：知识中心 API | 2-3 天 | Phase 1 | 素材库/视频库/提示词库 CRUD API |
| Phase 5 | 前端：会话化聊天 + 工具栏 | 4-5 天 | Phase 3、Phase 4 | 聊天框工具栏、视频生成参数弹框、文件卡片 |
| Phase 6 | 前端：知识中心菜单与页面 | 3-4 天 | Phase 4、Phase 5 | 素材库/视频库/提示词库三页面 + 菜单可见性 |
| Phase 7 | 端到端联调 + 三智能体流程验收 | 2-3 天 | 全部 | 精修/敏捷双模走通、留用入库、计费扣减 |

**总周期**：17-25 工作日（约 4-5 周）；含联调与验收约 5-7 周。

---

## Phase 0：配置与基础设施（0.5-1 天）

**目标**：env 变量、配置项、计费加价系数就位。所有后续 Phase 的前置基础。

> 对应设计文档：§7.4.2、§7.4.3

| 任务 | 产出 | 验收 |
|------|------|------|
| 0.1 新增 `VIDEO_GEN_USAGE_FACTOR` env | `.env.example` 增加一行 `VIDEO_GEN_USAGE_FACTOR=33` | 文件存在该行；`.env.example` 与 settings.py 一致 |
| 0.2 BillingConfig 扩展 | `src/config/settings.py:346` `BillingConfig` 新增 `video_gen_usage_factor: int = 33` | `settings.billing.video_gen_usage_factor` 可读，默认 33 |
| 0.3 config.yaml 同步 | `configs/config.yaml` 的 `billing:` 块新增 `video_gen_usage_factor: 33` | yaml 与 settings.py 字段名/默认值一致 |
| 0.4 单元测试 | `tests/unit/test_config.py` 增加 `test_billing_video_gen_usage_factor_default` | 默认 33；env 覆盖生效 |

**0.2 验收细节**：
```python
class BillingConfig(BaseModel):
    usage_factor: int = 100              # 主业务（已有，10 倍加价）
    video_gen_usage_factor: int = 33     # 视频创作（新增，3.3 倍加价）
```

---

## Phase 1：数据层 + 表结构（1-2 天）

**目标**：素材库、视频库、提示词库三张表 + chat_sessions 扩展。可与 Phase 0 并行。

> 对应设计文档：§5.6.1（菜单结构）、§5.6.3（提示词库三类）、§5.6.2（视频库与工作成果关系）

### 1.1 素材库表

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.1.1 建表 | `deploy/init-postgres.sql` 新增 `asset_library` 表；`deploy/db_update.sql` 增量 | 启动幂等建表 |

**asset_library 表结构**：

```sql
CREATE TABLE IF NOT EXISTS asset_library (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,                    -- 租户隔离
    user_id TEXT,                              -- 上传者（系统自动入库时可空）
    file_id TEXT NOT NULL,                     -- 关联 uploaded_file:{file_id}，下载入口
    display_name TEXT NOT NULL,                -- 显示名（如 "产品图_001.jpg"）
    mime_type TEXT NOT NULL,                   -- image/jpeg / video/mp4 等
    size_bytes BIGINT NOT NULL,
    source TEXT NOT NULL,                      -- video_chat / user_upload / other_agent_manual
    scene TEXT,                                -- 业务场景标签（product / model / bgm 等，可选）
    width INT,                                 -- 图片/视频宽
    height INT,                                -- 图片/视频高
    -- 肖像授权字段（仅 source=video_chat 且图为模特图时使用，第一阶段可空）
    portrait_authorized BOOLEAN DEFAULT FALSE,
    portrait_auth_expire_at TIMESTAMP,
    portrait_auth_scope TEXT,                  -- 授权范围（如 "电商展示"）
    metadata JSONB,                            -- 附加信息（如 EXIF、来源会话 ID）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant ON asset_library(tenant_id);
CREATE INDEX IF NOT EXISTS idx_asset_library_source ON asset_library(source);
CREATE INDEX IF NOT EXISTS idx_asset_library_tenant_scene ON asset_library(tenant_id, scene);
```

**字段说明**：
- `source`：来源标识。`video_chat` = 视频创作聊天自动入库；`user_upload` = 用户手动上传到素材库；`other_agent_manual` = 其他智能体附件手动收藏
- `portrait_*`：肖像授权字段第一阶段不强制（前端可选填），字段先建好，Phase 4 合规品牌阶段才强制校验
- 业务数据表必须字段：`tenant_id` / `user_id` / `created_at` 齐全（按 [database_dev.md](../../.claude/rules/database_dev.md) 规范）

### 1.2 提示词库表

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.2.1 建表 | `deploy/init-postgres.sql` 新增 `prompt_library` 表；`deploy/db_update.sql` 增量 | 启动幂等建表 |

**prompt_library 表结构**（三类别合并一表）：

```sql
CREATE TABLE IF NOT EXISTS prompt_library (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,                              -- 留用者 / 黑名单提交者
    category TEXT NOT NULL,                    -- kept（留用）/ blacklist（黑名单）/ template（模版，第一阶段仅通过 promote 接口由租户管理员创建，普通流程不写入）
    business_prompt TEXT NOT NULL,             -- 业务层提示词（中文，员工可读）
    craft_prompt TEXT NOT NULL,                -- 工艺层提示词（可灵 8 层框架结构化）
    model_params JSONB,                        -- 模型层参数（seed / negative_prompt / duration / ratio / resolution）
    industry_tag TEXT,                        -- 行业品类（美妆 / 服饰 / 食品等，可选）
    scene_tag TEXT,                            -- 场景标签（开箱 / 展示 / 氛围等，可选）
    -- 关联视频（留用时记录是哪个视频的提示词）
    source_video_file_id TEXT,                 -- 来源视频的 file_id（可空，黑名单必填，留用必填）
    source_chat_session_id TEXT,              -- 来源会话 ID（溯源）
    -- 黑名单专用
    dislike_reason TEXT,                       -- 不喜欢原因（可选填：光线偏暗/动作不自然/构图有问题/其他）
    -- 模版专用（第一阶段不写入，但字段先建好）
    promoted_from_kept_id INT,                 -- 由哪条留用记录升级而来
    promoted_by_user_id TEXT,                 -- 升级操作者（租户管理员）
    promoted_at TIMESTAMP,
    -- 通用
    metadata JSONB,                            -- 附加信息（如生成时用的模型、消耗积分）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_category ON prompt_library(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_prompt_library_tenant_scene ON prompt_library(tenant_id, scene_tag);
```

**设计要点**：
- 三类别（留用/黑名单/模版）合并一表，用 `category` 区分，便于后续升级操作（kept -> template 仅更新 category 与 promoted_* 字段）
- `business_prompt` / `craft_prompt` 双层存储，对应设计文档 §5.3.2 三层架构（模型层在 `model_params` JSONB 中）
- 模版字段（promoted_*）第一阶段不写入，但字段先建好，避免后续 ALTER TABLE

### 1.3 视频库（复用 `work_outcomes` 表，不新建表）

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.3.1 视频库视图 | 复用现有 `work_outcomes` 表（已存在，详见 `deploy/init-postgres.sql:2038`），通过 `outcome_type='file'` + `subagent_id='video-agent'` 过滤，视频专属字段存 `metadata` JSONB | 菜单「视频库」看到的是 work_outcomes 中视频创作类的记录 |

> 对应设计文档：§5.6.2 推荐方案 A（单一数据源）

**前置确认**：`work_outcomes` 表已存在，含 `outcome_type` / `file_id` / `file_name` / `metadata` JSONB / `user_id` / `session_id` / `created_at` 等字段（详见 `docs/system/work-outcome-record-design.md`），第一阶段暂不加字段，仅扩展 `metadata` JSONB 存视频专属元数据（prompt_library_id / generation_params / credit_cost），不改表结构

**视频专属元数据**（存 `work_outcomes.metadata` JSONB；`user_id` / `session_id` / `created_at` 由表本身字段承载，不重复存）：
```json
{
  "prompt_library_id": 123,           // 关联 prompt_library.id
  "generation_params": {              // 生成参数
    "duration_sec": 5,
    "ratio": "9:16",
    "resolution": "720p"
  },
  "credit_cost": 4.5                  // 消耗积分
}
```

### 1.4 chat_sessions 扩展（不破坏现有表）

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.4.1 subagent_id 复用 | 确认 `chat_sessions.subagent_id` 字段已存在（MVP 已加） | 视频创作会话通过 `subagent_id='video-agent'` 标识 |
| 1.4.2 metadata 扩展 | `chat_sessions.metadata` JSONB 增加 `video_gen_params` 字段（创作模式/时长/比例/分辨率/生成条数） | 不改表结构，仅扩展 JSONB 内容 |

**chat_sessions.metadata.video_gen_params 结构**：
```json
{
  "video_gen_params": {
    "mode": "refine",                  // refine（精修）/ agile（敏捷）
    "duration_sec": 5,
    "ratio": "9:16",
    "resolution": "720p",
    "card_count": 1                     // 精修固定 1；敏捷 1/2/3，默认 3（前端参数弹框默认值，详见 Phase 5.1.5）
  }
}
```

**关键**：不在 `chat_sessions` 加新列，仅扩展 `metadata` JSONB，避免破坏现有表结构与迁移成本。

### 1.5 subagent_definitions 扩展 chat_toolbar + upload_accept 字段（声明式 UI 配置）

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.5.1 表结构变更 | `deploy/init-postgres.sql:1299` `subagent_definitions` 表 ALTER 增加 `chat_toolbar JSONB DEFAULT '[]'` + `upload_accept TEXT` 两列；同步 `deploy/db_update.sql` 增量 | 启动幂等加列 |
| 1.5.2 SubagentConfig 扩展 | `src/models/subagent.py:24` `SubagentConfig` 新增 `chat_toolbar: List[str] = []` + `upload_accept: Optional[str] = None` 字段 | 两字段可读，默认空数组 / None |
| 1.5.3 SUBAGENT.md loader 解析 | `src/subagents/loader.py:160-178` frontmatter 字段映射增加 `chat_toolbar` 与 `upload_accept` | SUBAGENT.md 声明两字段后能被加载到 SubagentConfig |
| 1.5.4 API 暴露 | `GET /api/subagents/{name}` 等接口返回 `chat_toolbar` 与 `upload_accept` 字段 | 前端拉取 subagent 详情时能拿到按钮 id 列表与上传类型限制 |
| 1.5.5 video-agent SUBAGENT.md 新建 | 新建 `subagents/video-agent/SUBAGENT.md`（与现有 9 个子智能体并列，目录名用连字符对齐规范）；frontmatter 含 `name: 视频创作智能体` + `chat_toolbar: [video_gen]` + `upload_accept: "image/*"` + `triggers.keywords: [视频创作, 生成视频, 做个视频]` + `business_pages: [{title: 素材库, route: /assets}, {title: 视频库, route: /videos}, {title: 提示词库, route: /prompts}]` | video-agent 会话工具栏显示视频生成按钮（加号上传按钮为所有智能体共有，无需在 chat_toolbar 中声明）；加号上传限定只能选图片；主聊天输入「视频创作」时路由到 video-agent 而非 social-media-operations；前端菜单「视频创作智能体」下含 3 个二级菜单 |
| 1.5.6 social-media-operations 现状核验 | **已落地，仅需核验**：当前 `subagents/social-media-operations/SUBAGENT.md` 的 `name` 已为「社媒运营智能体」，`triggers.keywords` 已去掉「视频创作」（保留社媒运营/微信公众号/视频号/内容日历/发布计划），`business_pages[0].title` 已为「视频制作工作台」（route `/social-media` 保留），正文已含与 video-agent 的职责边界说明。本任务只需在 Phase 1 启动时核验上述字段与计划一致，无需修改文件 | 主聊天中「视频创作」关键词只路由到 video-agent；前端菜单显示一级「社媒运营智能体」（二级「视频制作工作台」-> /social-media）与一级「视频创作智能体」（二级素材库/视频库/提示词库）两个独立入口，无名称冲突 |
| 1.5.7 默认上传类型 | `configs/config.yaml` 新增 `chat.default_upload_accept`（取值 `"image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg,.gif,.ppt,.pptx"`，与现有 `frontend/src/components/ChatInput.vue:90` 硬编码 accept 保持一致）；前端在 subagent 未声明 `upload_accept` 时读此默认值。**加号上传按钮保持硬编码，所有智能体共有，不进入 chat_toolbar 注册表，无需 master_toolbar_buttons 配置** | 子智能体未声明 upload_accept 时走全局默认；video-agent 声明 `image/*` 后覆盖默认值 |

**chat_toolbar 字段语义**：
- 类型：`JSONB`，存储字符串数组，如 `["video_gen", "image_gen", "ppt_create"]`
- 语义：聊天输入框旁的「额外快捷操作」按钮 id 列表（业界同类产品也有叫 Tools / 快捷功能 / 能力入口），与现有 `tools`（LLM 函数调用工具，`deploy/init-postgres.sql:1307`）、`skills`（技能包）、`business_pages`（业务页面）语义独立，**不复用现有字段**
- 取值：第一阶段仅 `video_gen`；未来扩展 `image_gen` / `ppt_create` 等
- **加号上传按钮不在此字段中**：加号按钮由 ChatInput 硬编码渲染，所有智能体共有，与 `chat_toolbar` 并列。`chat_toolbar` 只控制「除加号外的额外按钮」

**upload_accept 字段语义**：
- 类型：`TEXT`，单值，对齐 HTML `<input accept>` 属性语法
- 语义：限定本子智能体聊天输入框加号按钮可选的文件类型；前端 ChatInput 拿到此值直接塞 `<input accept="...">`，零翻译
- 取值示例：
  - `"image/*"` -- 仅图片（video-agent 用此值）
  - `"image/*,video/*"` -- 图片 + 视频
  - `".pdf,.docx"` -- 指定后缀
  - `None`（不声明） -- 走 `chat.default_upload_accept` 全局默认（兼容现有行为）
- 背景：现有 `frontend/src/components/ChatInput.vue:90` 加号按钮 accept 是全局硬编码白名单，所有子智能体共用；视频创作智能体只需图片输入（产品图/模特图），需限定只传图片，避免用户误传 PDF/Word 等

**SUBAGENT.md frontmatter 示例**（video-agent）：
```yaml
---
name: 视频创作智能体
description: 会话化视频创作智能体，精修/敏捷双模 + 企业组织沉淀
version: 1.0.0
author: system
chat_toolbar:
  - video_gen
upload_accept: "image/*"
triggers:
  keywords:
    - 视频创作
    - 生成视频
    - 做个视频
tools:
  inherit: true
skills:
  allowed: []
business_pages:
  - title: 素材库
    route: /assets
  - title: 视频库
    route: /videos
  - title: 提示词库
    route: /prompts
---
（系统提示词正文）
```

> **icon 字段说明**：根据 [architecture.md 业务子菜单图标规范](../../.claude/rules/architecture.md)，租户前台侧边栏的「业务子菜单」图标由前端 `BusinessPageIcon.vue` 按 `title` 关键字匹配统一渲染，**不依赖 `business_pages[].icon` 字段**。故 SUBAGENT.md 中无需声明 icon；新增页面时需同步在 `BusinessPageIcon.vue` 的 `ICON_RULES` 中按 `title` 关键字新增规则。

**菜单结构**（来自 `MenuSidebar.vue` 渲染逻辑）：
- 一级菜单 = `subagent.name`（子智能体名）
- 二级菜单 = `business_pages[].title`（业务页面名）
- video-agent：「视频创作智能体」一级，下含「素材库 / 视频库 / 提示词库」3 个二级
- social-media-operations：「社媒运营智能体」一级，下含「视频制作工作台」（指向 `/social-media` 表单式工作台，MVP 原型）1 个二级

**与 social-media-operations 的职责边界**（Phase 1.5.6 核验现状已对齐）：

| 维度 | social-media-operations（已改名） | video-agent（新建） |
|------|----------------------------------|---------------------|
| 一级菜单名（subagent.name） | 社媒运营智能体 | 视频创作智能体 |
| 二级菜单（business_pages） | 视频制作工作台（→ `/social-media` 表单式工作台） | 素材库 / 视频库 / 提示词库 |
| 核心职责 | 社媒运营全流程（内容日历/发布计划/母版管理/审核交接/运营复盘）+ MVP 视频生成工作台 | 会话化视频生成（精修/敏捷双模/提示词引擎/企业组织沉淀） |
| trigger 关键词 | 社媒运营/微信公众号/视频号/内容日历/发布计划 | 视频创作/生成视频/做个视频 |
| 入口 | 表单式工作台 `/social-media`（MVP 原型保留） | 主聊天流 + 3 个知识中心页面 |
| 数据表 | gen_sessions / gen_cards（原型保留） | chat_sessions / work_outcomes / asset_library / prompt_library |

**关键**：triggers 关键词严格去重，「视频创作」只归 video-agent（路由层），避免主智能体委托时歧义；social-media-operations 的二级菜单「视频制作工作台」是页面标题（不参与路由触发），两者不冲突。

**主智能体 fallback**：`chat_sessions.subagent_id` 为空（主智能体会话）或子智能体未声明 `upload_accept` 时，前端读 `configs/config.yaml` 的 `chat.default_upload_accept`（取值与 `ChatInput.vue:90` 现有硬编码 accept 一致）。**加号上传按钮在所有智能体会话中都显示，无需任何声明**。

**关键**：`chat_toolbar` 是「声明式启用额外按钮」--前端按 id 注册表渲染额外按钮（如 video_gen）；新增额外按钮需前后端同步维护注册表（见 Phase 5.1.1）。`upload_accept` 是「声明式限定上传类型」--子智能体按需在 SUBAGENT.md 声明，未声明时走全局默认。**加号按钮本身硬编码在 ChatInput 中，不属于声明式管理范围**。

### 1.6 表初始化机制

| 任务 | 产出 | 验收 |
|------|------|------|
| 1.6.1 init_video_agent_tables | `src/video_agent/db.py`（新模块）增加 `init_video_agent_tables()` 函数 | 启动幂等建 2 张表（asset_library / prompt_library 若不存在）；视频库复用 `work_outcomes` 表，不新建 |
| 1.6.2 启动接入 | `src/db/database.py` `_init_postgresql` 末尾调用 `init_video_agent_tables()` | 应用启动自动建表 |

**注意**：本计划新建模块 `src/video_agent/`（与现有 `src/video_gen/` 并列），不污染 MVP 原型代码。`video_gen/` 保留给原「视频创作」页面，`video_agent/` 承载新会话化能力。

---

## Phase 2：计费链路接入（2-3 天）

**目标**：视频创作每次大模型调用都写 chat_records 计费，与主业务计费对齐。Phase 1 的三大重点之一。

> 对应设计文档：§7.4 全节

### 2.1 billing.py 扩展

| 任务 | 产出 | 验收 |
|------|------|------|
| 2.1.1 calculate_credit_cost 扩展 | `src/services/billing.py` 的 `calculate_credit_cost()` 增加 `usage_factor_override: Optional[int] = None` 参数 | 传入时覆盖默认 usage_factor；不传时维持原行为 |
| 2.1.2 calculate_video_credit_cost 新增 | 新增 `calculate_video_credit_cost(seconds, cost_per_second_yuan, provider, model)` 函数 | 返回 `ceil(seconds × 单价 × video_gen_usage_factor × 100) / 100` |
| 2.1.3 单元测试 | `tests/unit/test_billing.py` 新增覆盖：默认 usage_factor、override 生效、视频按秒计费 | 6-8 个测试用例全绿 |

**2.1.2 实现要点**：
```python
from math import ceil
from src.config.settings import get_settings

def calculate_video_credit_cost(
    seconds: float,
    cost_per_second_yuan: float,
    provider: str,
    model: str,
) -> float:
    """视频生成按秒计费"""
    settings = get_settings()
    factor = settings.billing.video_gen_usage_factor
    return ceil(seconds * cost_per_second_yuan * factor * 100) / 100
```

### 2.2 单价表维护

| 任务 | 产出 | 验收 |
|------|------|------|
| 2.2.1 单价数据 | `token_cost_prices` 表增加 MiniMax-H3、wan2.7-r2v 记录，新增 `price_per_second` 字段 | 单价可查 |

**字段决策**：
- **方案 B**：`token_cost_prices` 新增 `price_per_second` 字段，专用于视频模型

### 2.3 video_agent 服务接入计费

| 任务 | 产出 | 验收 |
|------|------|------|
| 2.3.1 chat_records 写入 | `src/video_agent/service.py`（Phase 3 创建）在每次视频生成 API 调用前后写 chat_records | 单次 session 一条 chat_records（含 N 个 card 的 execution_details） |
| 2.3.2 status 流转 | pending（提交时预扣） -> completed（成功后按实际秒结算差额） -> refunded（失败退还预扣） | 三种状态都有 chat_records 记录 |
| 2.3.3 租户余额拦截 | 会话创建入口复用 `_check_tenant_credit_blocked`（src/main.py:556） | 余额不足时拒绝创建会话，返回友好提示 |

**2.3.1 chat_records 字段映射**：

| 字段 | 视频模型调用（Phase 1 启用） | 文本模型调用（Phase 1 仅预留接口，未来迭代启用，见 Phase 2.4） |
|------|------------|------------------------|
| `source_type` | `video_gen` | `video_gen` |
| `model` | `MiniMax-H3` / `wan2.7-r2v` | 文本模型名（如 `qwen-max`） |
| `prompt_tokens` | 0 | 实际 token 数 |
| `completion_tokens` | 0 | 实际 token 数 |
| `execution_details` | `{seconds, cost_per_second_yuan, provider_task_id, resolution, cards:[{card_id, seed, duration_sec, status}]}` | `{}`（或省略） |
| `credit_cost` | 按秒计算 | 按 token × usage_factor |

**关键约束**：
- `source_type = "video_gen"`，便于按场景统计 ROI
- 单次 session 一条 chat_records（含 N 个 card），避免记录爆炸
- FAILED 任务退还预扣（`status=refunded`），SUCCEEDED 后按实际秒数结算差额

### 2.4 文本模型计费（Phase 2 后启用）

第一阶段文本模型（提示词引擎）的 chat_records 写入，依赖 Phase 3 提示词引擎落地。Phase 1 预留接口，不实际写入。

| 任务 | 产出 | 验收 |
|------|------|------|
| 2.4.1 预留接口 | `src/video_agent/service.py` 提示词生成函数预留 `chat_records` 写入钩子（先空实现） | 接口存在，Phase 2 接入 |

---

## Phase 3：后端 - 会话化创作服务（3-4 天）

**目标**：基于 chat_sessions 的视频创作服务，含精修/敏捷双模 + 提示词引擎 + 留用入库。

> 对应设计文档：§5.3.4 全节

### 3.1 模块结构

新建模块 `src/video_agent/`，与 `src/video_gen/`（MVP 原型）并列：

```
src/video_agent/
├── __init__.py
├── db.py                          # init_video_agent_tables（Phase 1）
├── service.py                     # VideoChatService（本 Phase 创建）
├── prompt_engine.py               # 提示词引擎（精修/敏捷双模）
├── chat_integration.py            # 与主聊天循环的集成
└── providers/                     # 复用 video_gen 的 wanx_provider / minimax_provider
    └── __init__.py
```

**关键**：`video_agent` 复用 `video_gen` 的 provider 实现（wanx_provider.py / minimax_provider.py），不重复实现视频生成 API 调用。可通过 `from src.video_gen.wanx_provider import WanxProvider` 引入。

### 3.2 VideoChatService

| 任务 | 产出 | 验收 |
|------|------|------|
| 3.2.1 create_video_chat_session | `src/video_agent/service.py` 创建 `VideoChatService` 类 | 创建 chat_sessions 记录，subagent_id='video-agent'，metadata 含 video_gen_params |
| 3.2.2 handle_user_message | 处理用户消息：识别意图（精修/敏捷）、调用提示词引擎、提交视频生成 | 文本模型调用 -> 提示词 -> 视频模型调用 全链路通 |
| 3.2.3 keep_video | 用户留用视频：写 prompt_library（category=kept）+ 写 work_outcomes（outcome_type='file'，subagent_id='video-agent'，metadata 含视频专属字段） | 留用后提示词库、视频库都有记录 |
| 3.2.4 dislike_video | 用户不喜欢：写 prompt_library（category=blacklist，dislike_reason 可选填） | 黑名单有记录 |
| 3.2.5 continue_with_video | 基于已有视频微调：传 source_video_file_id 给提示词引擎 | 支持混合使用场景 |

### 3.3 提示词引擎（精修/敏捷双模）

| 任务 | 产出 | 验收 |
|------|------|------|
| 3.3.1 PromptEngine | `src/video_agent/prompt_engine.py` | 双模接口可调 |
| 3.3.2 精修模式 | `generate_prompt_refine(user_input, images, params) -> PromptResult` | 返回 1 段提示词（业务层+工艺层） |
| 3.3.3 敏捷模式 | `generate_prompts_agile(user_input, images, params, count) -> List[PromptResult]` | 返回 N 段略有不同的提示词 |
| 3.3.4 提示词差异化 | 敏捷模式 N 段提示词在元素参考/景别/运镜/光影上有差异 | N 段不完全相同 |

**PromptResult 数据结构**：
```python
@dataclass
class PromptResult:
    business_prompt: str           # 业务层（中文）
    craft_prompt: str              # 工艺层（可灵 8 层框架结构化）
    model_params: Dict[str, Any]   # 模型层参数（seed/negative_prompt 等）
```

**精修模式 vs 敏捷模式的引擎差异**：
- 精修模式：调用文本模型 1 次，生成 1 段提示词，需用户确认（在聊天中回 md 文件卡片，等用户回复"确认"）
- 敏捷模式：调用文本模型 1 次，让它生成 N 段差异化提示词（一次调用返回 N 段），无需用户确认，直接提交视频模型

**第一阶段简化**：精修模式的用户确认通过聊天消息实现（智能体发"提示词草稿"md 卡片 -> 用户回复"确认"或描述调整），不实现专门的提示词编辑面板 UI。

### 3.4 与主聊天循环集成

| 任务 | 产出 | 验收 |
|------|------|------|
| 3.4.1 subagent 注册 | 在子智能体注册表中注册 `video-agent`，触发 `delegate_to_subagent` 时路由到 VideoChatService | 主聊天中输入触发视频创作智能体接管 |
| 3.4.2 消息处理 | `chat_integration.py` 把 VideoChatService 接入 Agent 主循环 | 用户消息 -> 智能体响应 -> 工具调用 全链路通 |
| 3.4.3 视频文件卡片回显 | 视频生成结果作为 ImageRef/file_id 推送 SSE `images` 事件 | 前端收到视频文件卡片 |
| 3.4.4 等待消息 | 视频生成等待期间，智能体发送"生成视频，预计需要3分钟"消息 | 前端收到提示文本 |
| 3.4.5 提示词卡片推送（精修模式） | 精修模式下，提示词引擎生成的「提示词草稿」作为助手文本消息（Markdown 格式，含业务层+工艺层）通过 SSE `message` 事件推送；用户在聊天中回复"确认"或描述调整后，Agent 主循环识别并触发视频生成 | 精修模式下用户能看到提示词草稿、可回复确认或调整 |

**3.4.3 关键**：复用现有 ImageRef 机制（[image-asset-pipeline-design.md](../system/image-asset-pipeline-design.md)），视频文件通过 `ImageRegistry.register()` 注册为 `usage=inline` 的资产，SSE 自动推送。

**3.4.5 关键**：精修模式不实现专门的提示词编辑面板 UI（见 Phase 3.3 第一阶段简化），提示词草稿通过 Markdown 文本消息推送，用户在聊天框回复即可。Agent 主循环通过关键词识别（"确认"/"调整"/"修改"等）判断用户意图。

### 3.5 失败原因采集

| 任务 | 产出 | 验收 |
|------|------|------|
| 3.5.1 dislike_reason 字段 | 接收前端传来的 `dislike_reason`（可空），写入 prompt_library | 原因进库 |
| 3.5.2 原因预设 | 后端定义常用原因枚举：`光线偏暗/动作不自然/构图有问题/其他` | 前端可读 |

---

## Phase 4：后端 - 知识中心 API（2-3 天）

**目标**：素材库、视频库、提示词库三套 CRUD API。

> 对应设计文档：§5.6.1 菜单结构、§5.6.3 提示词库三类

### 4.1 API 端点总览

| 模块 | 端点 | 用途 |
|------|------|------|
| 素材库 | GET /api/video-agent/assets | 列表（分页 + 按 scene/source 筛选） |
| 素材库 | GET /api/video-agent/assets/{id} | 详情 |
| 素材库 | DELETE /api/video-agent/assets/{id} | 删除 |
| 素材库 | POST /api/video-agent/assets/manual | 手动上传到素材库（其他智能体附件收藏） |
| 视频库 | GET /api/video-agent/videos | 列表（work_outcomes 中 outcome_type='file' + subagent_id='video-agent'） |
| 视频库 | GET /api/video-agent/videos/{id} | 详情（含提示词溯源） |
| 视频库 | DELETE /api/video-agent/videos/{id} | 删除 |
| 提示词库 | GET /api/video-agent/prompts | 列表（按 category 筛选：kept/blacklist/template） |
| 提示词库 | GET /api/video-agent/prompts/{id} | 详情 |
| 提示词库 | POST /api/video-agent/prompts/{id}/promote | 升级为模版（仅租户管理员可调） |
| 提示词库 | DELETE /api/video-agent/prompts/{id} | 删除 |

**所有 API 必须遵循租户隔离规范**（[backend_dev.md SaaS 租户隔离规范](../../.claude/rules/backend_dev.md)）：通过 `request.state.tenant_id` 取租户，所有查询带 `tenant_id` 过滤。

### 4.2 各模块实现

| 任务 | 产出 | 验收 |
|------|------|------|
| 4.2.1 素材库 API | `src/api/video_agent_assets.py` | 4 个端点可调，租户隔离生效 |
| 4.2.2 视频库 API | `src/api/video_agent_videos.py` | 3 个端点可调，能溯源到提示词 |
| 4.2.3 提示词库 API | `src/api/video_agent_prompts.py` | 4 个端点可调，promote 仅租户管理员可调 |
| 4.2.4 路由注册 | `src/main.py` include_router | /api/video-agent/* 可访问 |
| 4.2.5 单元测试 | `tests/integration/test_video_agent_apis.py` | 各端点正常 + 异常路径覆盖 |

### 4.3 提示词库升级模版

| 任务 | 产出 | 验收 |
|------|------|------|
| 4.3.1 promote 实现 | `src/api/video_agent_prompts.py` 的 promote 端点实现 | kept 记录升级为 template：复制一条新记录（category=template, promoted_from_kept_id, promoted_by_user_id, promoted_at） |
| 4.3.2 权限校验 | promote 端点校验 `current_user.role == tenant_admin` | 非租户管理员调用返回 403 |

**4.3.1 设计要点**：升级是「复制一条新记录」，原 kept 记录保留（保持溯源链）。模版与原留用记录通过 `promoted_from_kept_id` 关联。

---

## Phase 5：前端 - 会话化聊天 + 工具栏（4-5 天）

**目标**：聊天界面新增视频生成工具栏 + 参数弹框 + 文件卡片。

> 对应设计文档：§5.3.4.3 工具栏、§5.3.4.12 UI 改造重点

### 5.1 工具栏与参数弹框（配置驱动）

> 对应 Phase 1.5 的 `chat_toolbar` 字段。不同子智能体会话显示不同按钮组，前端按 id 注册表渲染。

| 任务 | 产出 | 验收 |
|------|------|------|
| 5.1.1 按钮注册表 | `frontend/src/components/chat/toolbar-buttons/registry.ts`（新模块）：id -> 组件 + 元信息（图标/标签/排序）映射；首期注册 `video_gen`（视频生成）。**加号上传按钮为所有智能体共有，由 ChatInput 硬编码渲染，不放入注册表** | 注册表可被 ChatToolbar 遍历渲染；新增额外按钮只需在此注册 |
| 5.1.2 ChatToolbar 组件 | `frontend/src/components/chat/ChatToolbar.vue`（新组件）：接收 `buttonIds: string[]` prop，按注册表顺序渲染额外按钮；空数组时整体不显示。ChatToolbar 与 ChatInput 中硬编码的加号按钮并列渲染 | 传入 `['video_gen']` 渲染一个视频生成按钮；传入 `[]` 不显示额外按钮（加号仍在） |
| 5.1.3 ChatInput 集成 | 修改 `frontend/src/components/ChatInput.vue:90`，将硬编码 accept 改为 `<input :accept="currentUploadAccept">`，`currentUploadAccept` 取自当前会话 subagent 的 `upload_accept`，未声明时 fallback 到 `chat.default_upload_accept`；并在加号按钮旁渲染 `<ChatToolbar :button-ids="currentToolbarButtons" />`，`currentToolbarButtons` 取自当前会话 subagent 的 `chat_toolbar`。**加号上传按钮保持硬编码，所有智能体共有，不在 chat_toolbar 中声明** | 不同子智能体会话显示不同的额外按钮 + 不同上传类型限制；video-agent 加号只能选图片 + 显示视频生成按钮；主智能体仅显示加号、可传 PDF/Word 等 |
| 5.1.4 视频生成按钮 | `frontend/src/components/chat/toolbar-buttons/VideoGenButton.vue`：点击打开参数弹框，确认后写入 `useVideoGenParams` 状态 | 仅在 video-agent 会话显示该按钮，点击弹出参数弹框 |
| 5.1.5 参数弹框 | `frontend/src/components/chat/VideoGenParamsDialog.vue` | 弹框含 5 个参数（创作模式/时长/比例/分辨率/生成条数） |
| 5.1.6 参数状态管理 | `frontend/src/composables/useVideoGenParams.ts` | 参数状态在会话内持久化，发送时附加到消息 |
| 5.1.7 subagent 详情拉取 | 进入会话时 `GET /api/subagents/{name}` 拿到 `chat_toolbar`，前端 store 缓存并按会话隔离 | 切换会话时工具栏按钮组正确切换 |

**参数弹框字段**：

| 参数 | 控件 | 选项 | 默认值 |
|------|------|------|--------|
| 创作模式 | Radio | 精修模式 / 敏捷模式 | 精修模式 |
| 视频时长 | Select | 5s / 10s / 15s / 30s | 5s |
| 视频比例 | Select | 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / auto | auto |
| 分辨率 | Select | 按当前会话视频模型动态渲染：MiniMax-H3 -> 768p / 2K；wan2.7-r2v -> 720p / 1080p（详见 `src/video_gen/base.py:27` 与各 provider 实现） | MiniMax-H3 默认 768p；wan2.7-r2v 默认 720p |
| 生成条数 | Select | 精修模式固定 1 条（禁用）；敏捷模式可选 1/2/3 条 | 精修=1，敏捷=3 |

**联动逻辑**：选择「精修模式」时，生成条数下拉禁用并固定为 1；选择「敏捷模式」时，生成条数可选 1/2/3。**分辨率选项需根据当前会话使用的视频模型动态渲染**（模型选择由后端 provider 工厂决定，前端通过 subagent 详情或会话参数获取当前模型）。

**注册表扩展规范**（未来新增额外按钮时遵循）：
1. 在 `frontend/src/components/chat/toolbar-buttons/` 下新建 `<ButtonName>.vue` 组件
2. 在 `registry.ts` 注册 id -> 组件映射，含 `icon` / `label` / `order` 元信息
3. 在需要启用的子智能体 SUBAGENT.md frontmatter 加 `chat_toolbar: [<new_id>]`
4. **加号上传按钮不在注册表管理范围**，由 ChatInput 硬编码渲染，所有智能体共有

### 5.2 文件卡片组件

| 任务 | 产出 | 验收 |
|------|------|------|
| 5.2.1 视频卡片 | `frontend/src/components/chat/VideoFileCard.vue` | 卡片显示视频预览 + 操作按钮 |
| 5.2.2 操作按钮 | 卡片底部含：预览 / 下载 / 留用 / 不喜欢 / 查看提示词 | 5 个按钮均可触发回调 |
| 5.2.3 提示词预览 | 点击「查看提示词」展开 md 文件预览（业务层+工艺层） | 弹框或抽屉显示完整提示词 |
| 5.2.4 留用勾选 | 留用按钮旁默认勾选「收入提示词库」，可取消 | 默认勾选 |
| 5.2.5 不喜欢原因 | 点击「不喜欢」弹出原因快选 chip + 自定义输入 | 选填，可空提交 |
| 5.2.6 已消耗积分 | 卡片右上角显示已消耗积分 | 实时显示 |

**5.2.1 卡片布局**：
```
┌─────────────────────────────┐
│ [视频缩略图/预览]      已消耗 4.5 积分│
│                             │
│ 视频 1 - 美妆开箱.mp4         │
│                             │
│ [预览] [下载] [查看提示词]    │
│ [留用 ☑收入提示词库] [不喜欢] │
└─────────────────────────────┘
```

### 5.3 聊天流集成

| 任务 | 产出 | 验收 |
|------|------|------|
| 5.3.1 消息渲染 | 聊天消息支持渲染 VideoFileCard 类型 | 视频结果在聊天中显示为卡片 |
| 5.3.2 等待提示 | 视频生成等待期间显示"生成视频，预计需要 3 ~ 5 分钟"消息 | 等待期间有可见反馈 |
| 5.3.3 多卡片处理 | 敏捷模式 3 条视频分 3 条消息发送 | 每条消息一个卡片 |
| 5.3.4 图片上传限定 | video-agent 会话通过 `upload_accept: "image/*"` 限定加号按钮只能选图片（多图、拖拽、粘贴均受限） | 用户在 video-agent 会话点击加号，文件选择器只显示图片；拖拽/粘贴非图片被拒绝；其他子智能体不受影响 |

**5.3.4 关键**：现有 `ChatInput.vue:90` 加号按钮的 accept 是全局硬编码白名单（图片+PDF+Office 等），所有子智能体共用。Phase 1.5 的 `upload_accept` 字段提供子智能体级别覆盖能力--video-agent 声明 `upload_accept: "image/*"` 后，Phase 5.1.3 把 ChatInput.vue:90 的硬编码 accept 改为读 `currentUploadAccept`，文件选择器自动只显示图片。**加号按钮本身保持硬编码，所有智能体共有，不进入 chat_toolbar 注册表**。多图上传、拖拽、粘贴均由 HTML accept 属性 + ChatInput 现有逻辑自动处理。

### 5.4 历史会话复用

| 任务 | 产出 | 验收 |
|------|------|------|
| 5.4.1 历史会话入口 | 视频创作智能体的历史会话出现在左侧菜单下方历史会话区 | 与其他智能体一致 |
| 5.4.2 历史会话恢复 | 点击历史会话恢复完整上下文（含视频卡片、提示词） | 历史会话可查看 |

---

## Phase 6：前端 - 知识中心菜单与页面（3-4 天）

**目标**：素材库、视频库、提示词库三页面 + 菜单可见性控制。

> 对应设计文档：§5.6.1 菜单结构

### 6.1 菜单可见性与结构

> **决策**：复用现有订阅能力开关，不新增订阅查询接口。现有链路：后端 `GET /api/saas/permissions/my/allowed-agents`（`src/saas/api/permissions.py:244`）返回「租户订阅 + 用户授权」双重过滤后的 agent_id 列表；前端 `useSubagentList.ts:39` 在租户模式下调用 `getMyAllowedAgents()`，返回的 `availableSubagents` 已经过滤；`MenuSidebar.vue:1019` 的 `groupedBusinessPages` 基于此 prop 计算，未订阅智能体自动不出现在菜单。

| 任务 | 产出 | 验收 |
|------|------|------|
| 6.1.1 订阅过滤链路核验 | **无需新建 `subscription.ts`**。核验 `useSubagentList.ts` 在租户模式下通过 `getMyAllowedAgents()` 返回的列表已排除未订阅的 video-agent；演示模式下 `listSubagents()` 返回全部子智能体（含 video-agent） | 租户未订阅 video-agent 时，前端拿到的 `availableSubagents` 不含 video-agent；演示模式含 video-agent |
| 6.1.2 菜单条件渲染核验 | **无需修改 `MenuSidebar.vue` 渲染逻辑**。核验 `MenuSidebar.vue:1019-1026` 的 `groupedBusinessPages` 直接基于 `availableSubagents` prop 计算，未订阅的 video-agent 一级菜单 + 三个二级菜单（素材库/视频库/提示词库）整体不渲染 | 未订阅租户看不到 video-agent 一级菜单与三个二级菜单；已订阅租户正常显示 |
| 6.1.3 管理后台授权入口 | 核验管理后台「租户授权」页面（`POST /api/saas/permissions/tenant/{tenant_id}/agents`，`src/saas/api/permissions.py:83`）的 agent 选择器中自动出现 video-agent（来自 `available-agents` 接口，video-agent 加入 `subagents/` 目录后自动被收录） | 平台管理员可为租户授权 video-agent；授权后该租户菜单出现 video-agent |
| 6.1.4 菜单结构对齐 | 确认 `MenuSidebar.vue` 渲染逻辑：一级菜单 = `subagent.name`，二级菜单 = `business_pages[].title`；video-agent 一级「视频创作智能体」下含「素材库/视频库/提示词库」3 个二级；social-media-operations 一级「社媒运营智能体」下含「视频制作工作台」（指向 `/social-media` 表单式工作台，MVP 原型）1 个二级 | 菜单结构符合 §5.6.1；两个一级菜单无名称冲突，二级菜单互不重叠 |

### 6.2 素材库页面

| 任务 | 产出 | 验收 |
|------|------|------|
| 6.2.1 AssetLibrary.vue | `frontend/src/components/video-agent/AssetLibrary.vue` | 列表页：分页 + 按 scene/source 筛选 |
| 6.2.2 API 客户端 | `frontend/src/api/videoAgent.ts`（含素材库/视频库/提示词库三套 API 封装） | 11 个 API 方法封装 |
| 6.2.3 路由注册 | `frontend/src/main.ts` 注册 `/t/:tenant_id/assets` 路由 | 路由可访问 |
| 6.2.4 手动上传 | 素材库页面提供「上传素材」按钮，支持图片/视频上传 | 手动上传可用 |

**列表页规范**：遵循 [list-page-convention.md](../../.claude/rules/list-page-convention.md)，使用 BaseTable + BasePagination，搜索区按 scene/source 筛选。

### 6.3 视频库页面

| 任务 | 产出 | 验收 |
|------|------|------|
| 6.3.1 VideoLibrary.vue | `frontend/src/components/video-agent/VideoLibrary.vue` | 列表页：显示视频缩略图、名称、留用者、留用时间、消耗积分 |
| 6.3.2 提示词溯源 | 点击视频可查看其生成提示词（关联 prompt_library） | 溯源链可见 |
| 6.3.3 路由注册 | `/t/:tenant_id/videos` | 可访问 |

### 6.4 提示词库页面

| 任务 | 产出 | 验收 |
|------|------|------|
| 6.4.1 PromptLibrary.vue | `frontend/src/components/video-agent/PromptLibrary.vue` | 列表页：按 category 分 Tab（留用/黑名单/模版） |
| 6.4.2 提示词详情 | 点击查看完整提示词（业务层+工艺层+模型参数） | 详情页/弹框可查看 |
| 6.4.3 升级为模版 | 留用提示词行有「升级为模版」按钮（仅租户管理员可见） | 调用 promote API |
| 6.4.4 路由注册 | `/t/:tenant_id/prompts` | 可访问 |

---

## Phase 7：端到端联调 + 三智能体流程验收（2-3 天）

**目标**：精修/敏捷双模走通、留用入库、计费扣减全链路验证。

### 7.1 端到端验证场景

| 场景 | 步骤 | 验收 |
|------|------|------|
| 7.1.1 精修模式完整流程 | 上传图片 -> 描述需求 -> AI 生成提示词 md 卡片 -> 用户确认 -> 视频生成 -> 留用入库 | 提示词库有 kept 记录、视频库有视频记录、chat_records 有计费记录 |
| 7.1.2 敏捷模式完整流程 | 上传图片 -> "帮我生成电商产品介绍视频" + 敏捷+3条 -> 3 个视频卡片 -> 留用第 2 个 | 第 2 个提示词入库、其他 2 个不入库（除非用户也点留用） |
| 7.1.3 混合使用场景 | 敏捷模式 3 条都不满意 -> 查看第 1 个提示词 -> 切换精修模式 -> "基于第 1 条提示词修改为..." -> 确认生成 | 精修模式可基于敏捷结果继续 |
| 7.1.4 不喜欢 + 黑名单 | 视频卡片点「不喜欢」+ 选填原因 | 黑名单有记录、原因进库 |
| 7.1.5 计费扣减 | 单次会话消耗积分 = 视频秒数 × 单价 × video_gen_usage_factor | chat_records.credit_cost 正确，租户余额扣减 |
| 7.1.6 余额不足拦截 | 租户余额为 0 时创建会话 | 拒绝创建，返回友好提示 |
| 7.1.7 历史会话恢复 | 关闭会话后从历史会话区点击恢复 | 视频卡片、提示词、参数都恢复 |

### 7.2 三智能体流程验收

| 任务 | 产出 | 验收 |
|------|------|------|
| 7.2.1 开发完成自测 | 全部 Phase 0-6 完成，自测通过 | 开发者自测绿灯 |
| 7.2.2 测试智能体 | 独立测试 + 回归 + 启动安全检查 | 测试全绿 |
| 7.2.3 CodeReview 智能体 | 独立审查 + 修复必要问题 | CR 通过 |
| 7.2.4 提交前验证 | 主控者做 import/build 终检 + fetch + commit + push | 提交成功，服务器更新无异常 |

### 7.3 性能与边界验证

| 任务 | 产出 | 验收 |
|------|------|------|
| 7.3.1 多模态上传性能 | 多图上传（5+ 张）响应时间 < 5s | 不超时 |
| 7.3.2 敏捷模式 3 条并发 | 3 条视频生成 API 并发调用 | 3 条都成功返回 |
| 7.3.3 长会话上下文 | 单会话 50+ 轮对话后性能不退化 | 响应时间在可接受范围 |
| 7.3.4 计费幂等 | 视频生成 FAILED 退还预扣，重复结算不发生 | chat_records 状态正确 |

---

## 风险与依赖

### 主要风险

| 风险 | 影响 | 对策 |
|------|------|------|
| 文本模型提示词质量不达标 | Phase 3 提示词引擎效果差 | 预置通用模板库兜底（设计文档 §9.3）；模板库 Phase 1 不实现，作为 Phase 3 效果不达标时的应急方案，未来迭代落地 |
| 视频生成 API 限流 | Phase 7 联调受阻 | 敏捷模式 3 条并发可能触发限流，提前确认 provider 限流策略 |

### 横向依赖

| 依赖项 | 状态 | 影响 Phase |
|--------|------|-----------|
| 现有聊天框支持图片上传 | ✅ 已支持（加号触发文件选择，多图/拖拽/粘贴均可用），但全局 accept 白名单未限定类型 | Phase 5.3.4（通过 `upload_accept` 字段在 video-agent 限定只传图片） |
| 现有「工作成果」表 | ✅ 已存在（`work_outcomes`，`deploy/init-postgres.sql:2038`） | Phase 1.3 已确定复用 |
| `token_cost_prices` 表视频模型记录 | 待 Phase 2.2 落地（新增 MiniMax-H3 / wan2.7-r2v 记录 + `price_per_second` 字段） | Phase 2.2 |
| ImageRegistry 机制 | 已就绪（[image-asset-pipeline](../system/image-asset-pipeline-design.md)） | Phase 3.4.3 |
| 主聊天循环 + subagent 注册 | 已就绪 | Phase 3.4 |
| `_check_tenant_credit_blocked` | 已就绪（src/main.py:556） | Phase 2.3 |

---

## 验收标准（Phase 1 整体）

### 功能验收

- [ ] **会话化创作**：用户在聊天框上传图片 + 描述需求 + 选择精修/敏捷模式，能生成视频
- [ ] **精修模式**：AI 生成提示词 md 卡片，用户确认后提交视频模型，单条视频返回
- [ ] **敏捷模式**：AI 静默生成 N 段提示词，N 条视频分 N 条消息返回，无需用户确认
- [ ] **混合使用**：敏捷不满意可切换精修模式基于已有视频继续调整
- [ ] **提示词显式化**：每个视频卡片可查看完整提示词（业务层+工艺层）
- [ ] **留用入库**：点击「留用」后提示词进提示词库（默认勾选「收入提示词库」）
- [ ] **黑名单入库**：点击「不喜欢」+ 选填原因后提示词进黑名单
- [ ] **素材库**：视频创作聊天中上传的图片自动入库，可按 scene/source 筛选
- [ ] **视频库**：留用视频自动入库，可溯源到提示词
- [ ] **提示词库**：三类（留用/黑名单/模版）分 Tab 显示，租户管理员可升级模版
- [ ] **计费扣减**：每次视频生成都写 chat_records，credit_cost 正确扣减
- [ ] **余额拦截**：余额不足时拒绝创建会话
- [ ] **菜单可见性**：未订阅视频创作智能体的租户看不到三个新菜单

### 三智能体流程验收

- [ ] 开发完成自测通过
- [ ] 测试智能体：单元测试 + 集成测试 + 启动安全检查 全绿
- [ ] CodeReview 智能体：P0/P1 问题修复，可安全提交
- [ ] 主控者 import/build 终检通过
- [ ] 提交并 push，服务器更新无异常

---

## 附录 A：与现有 MVP 的关系

| 维度 | 现有 MVP（保留为原型） | Phase 1 新功能 |
|------|---------------------|--------------|
| 入口 | 「视频创作」独立页面 | 主聊天流，子智能体接管 |
| 数据表 | gen_sessions / gen_cards | chat_sessions（复用）+ work_outcomes（复用，outcome_type='file'）+ asset_library / prompt_library（新建） |
| 视频模型调用 | src/video_gen/wanx_provider.py | 复用，从 src/video_agent/ 调用 |
| 计费 | 无 | chat_records（source_type=video_gen） |
| 提示词引擎 | 模板填空 | 文本模型生成（精修/敏捷双模） |
| 提示词沉淀 | 无 | prompt_library 三类（留用/黑名单/模版） |
| 素材管理 | 一次会话内 | asset_library 跨会话 |
| 视频管理 | gen_cards 表 | work_outcomes（outcome_type='file' + subagent_id='video-agent'） |

**关键**：MVP 原型完全独立，新功能不破坏原页面。`src/video_gen/` 模块保留，`src/video_agent/` 是新模块。Provider 层（wanx_provider / minimax_provider）被两套共用，但不修改其实现。

---

## 附录 B：设计点决策汇总

> 对应定位文档 §10.2。所有设计点已在正文落地，无待确认项。

| 设计点 | 决策结果 | 落地位置 |
|--------|---------|---------|
| 视频库元数据扩展 | 复用 `work_outcomes.metadata` JSONB（方案 A1），不改表结构 | Phase 1.3 |
| 提示词库表结构 | 单表 + `category` 字段区分 kept/blacklist/template | Phase 1.2 |
| 计费单价表字段 | 方案 B：`token_cost_prices` 新增 `price_per_second` 字段，专用于视频模型 | Phase 2.2 |
| 聊天框图片上传限定 | Phase 1.5 新增 `upload_accept` 字段，SUBAGENT.md 声明 `"image/*"`，前端 ChatInput 读此值覆盖默认 accept；加号按钮保持硬编码 | Phase 1.5、5.3.4 |
| 子智能体注册与职责边界 | 新建 `subagents/video-agent/`（连字符对齐规范）；`social-media-operations` 已改名为「社媒运营智能体」，triggers 已去掉「视频创作」，二级菜单「视频制作工作台」与 video-agent 知识中心三个二级菜单互不重叠 | Phase 1.5、3.4 |
| 菜单可见性机制 | 复用现有订阅能力开关：`GET /api/saas/permissions/my/allowed-agents` + `useSubagentList.ts` 已过滤，无需新增订阅查询接口；`MenuSidebar` 现有 `groupedBusinessPages` 基于已过滤的 `availableSubagents` prop 计算，未订阅自动不渲染 | Phase 6.1 |

---

*文档结束。本计划为 Phase 1 开发依据，所有设计点已决策，可启动开发。*
