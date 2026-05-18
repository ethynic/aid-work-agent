# 数字员工管理功能 - 实施方案

## 一、需求概述

增加"数字员工管理"功能。数字员工就是子智能体（`subagents/` 目录所代表的功能）。

### 核心规则

| 类型 | 查看 | 修改 | 删除 | 另存为 |
|------|------|------|------|--------|
| 内置数字员工 | ✅ | ❌ | ❌ | ✅ → 定制 |
| 定制数字员工 | ✅ | ✅ | ✅ | ✅ → 新定制 |

### 可编辑字段

| 字段 | 说明 | 唯一性约束 |
|------|------|-----------|
| id | 目录名（如 `contract-archive-review`） | 不能与内置/其他定制重复 |
| name | 显示名称（如 `全筑合同归档自动化审核`） | 不能与内置/其他定制重复 |
| description | 描述 | 无 |
| capabilities | 能力标签列表 | 无 |
| triggers | 触发条件（file_patterns 等） | 无 |
| skills | 可选技能列表（多选） | 无 |
| system_prompt | 系统提示词（Markdown 格式，大文本框编辑+预览） | 无 |

### 存储策略

- 内置数字员工：`subagents/` 目录，属于代码，进入 git 仓库
- 定制数字员工：`storage/subagents2/` 目录，属于业务数据，不入 git
- 加载时合并两个目录

---

## 二、架构概览

```
前端页面 (DigitalEmployeeManager.vue)
    ↕ REST API
后端管理路由 (src/api/admin_subagent.py)
    ↕
SubagentRegistry (改造：支持加载 subagents/ + storage/subagents2/)
    ↕
SubagentLoader (复用：解析/序列化 SUBAGENT.md)
```

---

## 三、后端改动

### 3.1 改造 SubagentRegistry

**文件**: `src/subagents/registry.py`

**改动点**:

1. `__init__` 增加第二个目录参数 `custom_dir: Optional[Path] = None`
2. 初始化时同时加载内置目录和定制目录
3. 新增 `_builtin_names: Set[str]` 记录内置子智能体名称
4. 新增方法:

| 方法 | 说明 |
|------|------|
| `is_builtin(name: str) -> bool` | 判断是否为内置子智能体 |
| `get_builtin_subagents() -> Dict[str, SubagentConfig]` | 获取所有内置子智能体 |
| `get_custom_subagents() -> Dict[str, SubagentConfig]` | 获取所有定制子智能体 |
| `save_custom_subagent(agent_id: str, config: SubagentConfig, content: str) -> SubagentConfig` | 创建/更新定制子智能体（写入 `storage/subagents2/{id}/SUBAGENT.md`） |
| `delete_custom_subagent(agent_id: str) -> bool` | 删除定制子智能体 |
| `get_all_subagents_with_type() -> List[Dict]` | 返回所有子智能体列表，带 `type` 字段（builtin/custom） |
| `validate_id uniqueness(agent_id: str, exclude_id: str = None) -> bool` | 检查 id 是否唯一 |

### 3.2 改造 SubagentLoader

**文件**: `src/subagents/loader.py`

**改动点**:

1. 新增 `serialize_to_subagent_md(config: SubagentConfig, body: str = "") -> str` 静态方法，将 SubagentConfig 序列化回 SUBAGENT.md 格式（YAML frontmatter + body）
2. 新增 `save_subagent_md(subagents_dir: Path, agent_id: str, content: str) -> Path` 方法，将 SUBAGENT.md 写入指定目录
3. 新增 `delete_subagent_dir(subagents_dir: Path, agent_id: str) -> bool` 方法，删除指定子智能体目录

**序列化格式**（与现有 SUBAGENT.md 保持一致）:
```yaml
---
name: 智能体名称
description: 描述
version: 1.0.0
author: admin
capabilities:
  - capability1
triggers:
  file_patterns:
    - "*.pdf"
tools:
  inherit: true
skills:
  allowed:
    - skill-name
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---

## Markdown 正文（即 system_prompt 的详细内容）
```

### 3.3 新建管理 API

**文件**: `src/api/admin_subagent.py`

**接口清单**:

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/subagents` | 列出所有数字员工（内置+定制，标记类型） |
| GET | `/api/admin/subagents/{agent_id}` | 获取单个数字员工详情 |
| GET | `/api/admin/subagents/{agent_id}/content` | 获取 SUBAGENT.md 原始内容（用于编辑） |
| GET | `/api/admin/skills` | 获取可选 skill 列表（从 SkillRegistry 获取） |
| POST | `/api/admin/subagents` | 创建定制数字员工 |
| PUT | `/api/admin/subagents/{agent_id}` | 更新定制数字员工 |
| DELETE | `/api/admin/subagents/{agent_id}` | 删除定制数字员工 |
| POST | `/api/admin/subagents/{agent_id}/duplicate` | 另存为（内置/定制 → 新的定制） |
| POST | `/api/admin/subagents/{agent_id}/ai-enhance` | AI 完善（调用 LLM 优化 SUBAGENT.md 全部内容） |

**AI 完善**（`POST /api/admin/subagents/{agent_id}/ai-enhance`）:

| 项目 | 说明 |
|------|------|
| 响应方式 | **同步请求**，等待 LLM 完成后一次性返回 |
| 优化范围 | **全部内容**：YAML frontmatter（name、description、capabilities、triggers、skills 等）+ Markdown body（系统提示词） |
| 前端交互 | 弹出**对比视图**（改前 vs 改后），用户选择**接受/拒绝**；接受时自动替换编辑器内容 |
| 超时 | 建议 60 秒（LLM 生成可能较慢） |

**请求模型**:
```python
class AiEnhanceRequest(BaseModel):
    content: str  # 当前编辑器中的完整 SUBAGENT.md 内容（frontmatter + body）
```

**响应模型**:
```python
class AiEnhanceResponse(BaseModel):
    original_content: str   # 原始内容（用于前端对比）
    enhanced_content: str   # AI 优化后的完整内容
```

**后端实现要点**:
1. 构造系统提示词，指示 LLM 扮演"数字员工配置优化专家"角色
2. 将用户当前编辑的 `content` 作为用户消息发送给 LLM
3. LLM 返回优化后的完整 SUBAGENT.md 内容（保持 YAML frontmatter + Markdown body 格式）
4. 返回 `original_content` 和 `enhanced_content` 给前端做对比展示
5. **不自动保存**，由用户在前端对比后选择接受或拒绝

**LLM 调用**:
- 使用 `LLMGateway.chat()` 同步调用（非流式）
- 调用 `src/llm/gateway.py` 中的全局实例 `llm_gateway`
- `temperature=0.3`（低温度，保证输出稳定和格式正确）
- `max_tokens=8192`（SUBAGENT.md 可能较长）

**系统提示词设计**（内置在后端代码中）:
```
你是一个数字员工（子智能体）配置优化专家。用户会提供一个 SUBAGENT.md 文件的当前内容，请你优化它。

## 输出要求
- 输出**完整的 SUBAGENT.md 内容**（包含 YAML frontmatter 和 Markdown body），不要省略任何部分
- 保持 `---` 分隔的 YAML frontmatter + Markdown body 格式
- 不要输出任何解释说明，只输出优化后的内容

## 优化方向
1. **description**：使描述更精准、更专业，突出核心价值和适用场景
2. **capabilities**：补充遗漏的能力标签，移除不相关的标签，使用英文小写下划线命名
3. **triggers.file_patterns**：根据智能体用途补充合理的文件触发模式
4. **skills.allowed**：根据能力需要补充或调整技能配置
5. **system_prompt（Markdown body）**：
   - 优化角色定义和职责描述，使其更清晰具体
   - 补充工作流程、注意事项、禁止行为等关键内容
   - 确保提示词结构化，使用 Markdown 标题、列表、表格等格式
   - 保持专业性，语气严谨、清晰、简洁
```

**错误处理**: LLM 调用失败时返回标准错误响应（含 `debug` 字段），前端展示错误信息，不影响当前编辑内容。

**请求/响应模型**:

```python
# 创建/更新请求
class CreateSubagentRequest(BaseModel):
    agent_id: str                          # 目录名，如 "my-custom-agent"
    name: str                              # 显示名称
    description: str = ""
    capabilities: List[str] = []
    triggers: Dict[str, Any] = {}
    tools: Dict[str, Any] = {"inherit": True}
    skills: Dict[str, Any] = {}
    context: Dict[str, Any] = {}
    system_prompt: str = ""               # Markdown 格式

# 另存为请求
class DuplicateSubagentRequest(BaseModel):
    new_agent_id: str                      # 新的目录名
    new_name: str                          # 新的显示名称

# 列表响应中的单个项
class SubagentListItem(BaseModel):
    agent_id: str                          # 目录名
    name: str                              # 显示名称
    description: str
    capabilities: List[str]
    type: str                              # "builtin" | "custom"
```

**管理员权限判断**:
- 使用 `get_current_user(request)` 获取当前用户
- 初期简单方案：检查 `phone` 是否在配置文件的管理员手机号列表中
- 配置文件 `configs/config.yaml` 新增:
  ```yaml
  admin:
    phones:
      - "13800138000"                       # 管理员手机号列表
  ```
- 非管理员返回 `403 {"success": false, "error": "无管理员权限"}`

**错误处理**: 所有接口遵循项目错误处理规范，返回 `debug` 字段并过滤敏感信息。

### 3.4 注册路由

**文件**: `src/main.py`

在路由注册区域添加:
```python
from src.api import admin_subagent
app.include_router(admin_subagent.router)
```

### 3.5 初始化入口

**文件**: `src/core/agent.py`（master_agent 初始化部分）

确保 `SubagentRegistry` 初始化时传入 `custom_dir=Path("./storage/subagents2")`，使合并加载生效。

---

## 四、前端改动

### 4.1 新建 API 模块

**文件**: `frontend/src/api/adminSubagent.ts`

封装所有管理 API 调用:

```typescript
// 获取所有数字员工列表
export async function listSubagents(): Promise<SubagentListItem[]>

// 获取单个数字员工详情
export async function getSubagentDetail(agentId: string): Promise<SubagentDetail>

// 获取 SUBAGENT.md 原始内容
export async function getSubagentContent(agentId: string): Promise<string>

// 获取可选 skills 列表
export async function listAvailableSkills(): Promise<string[]>

// 创建定制数字员工
export async function createSubagent(data: CreateSubagentRequest): Promise<ApiResponse>

// 更新定制数字员工
export async function updateSubagent(agentId: string, data: UpdateSubagentRequest): Promise<ApiResponse>

// 删除定制数字员工
export async function deleteSubagent(agentId: string): Promise<ApiResponse>

// 另存为
export async function duplicateSubagent(agentId: string, data: DuplicateRequest): Promise<ApiResponse>

// AI 完善（同步请求）
export async function aiEnhanceSubagent(agentId: string, content: string): Promise<AiEnhanceResponse>
```

### 4.2 新建管理页面组件

**文件**: `frontend/src/components/DigitalEmployeeManager.vue`

**页面布局（左右两栏）**:

```
┌─────────────────────────────────────────────────────┐
│  数字员工管理                          [ 新建定制 ]   │
├──────────────┬──────────────────────────────────────┤
│  数字员工列表  │          详情 / 编辑面板              │
│              │                                      │
│ ▸ 内置 (2)   │  ID: contract-archive-review         │
│   · 合同审核  │  名称: [全筑合同归档自动化审核]        │
│   · 贸易专员  │  描述: [________________________]    │
│              │  能力: [tag] [tag] [+]               │
│ ▸ 定制 (1)   │  触发: [file_patterns: *.pdf]        │
│   · 我的智能体│  技能: [☑ skill1] [☑ skill2] [▼]    │
│              │  系统提示词:                          │
│              │  ┌──────────────────────────────┐    │
│              │  │ ## 身份                      │    │
│              │  │ 你是...                      │    │
│              │  │                              │    │
│              │  └──────────────────────────────┘    │
│              │  [预览] [编辑] 切换                   │
│              │                                      │
│              │  [另存为] [保存] [删除]                │
└──────────────┴──────────────────────────────────────┘
```

**核心功能**:

1. **列表区域**:
   - 按类型分组显示（内置 / 定制）
   - 每个卡片显示 name、description 摘要、类型标签
   - 选中高亮

2. **只读详情视图**（内置数字员工）:
   - 所有字段以文本形式展示
   - 系统提示词以 Markdown 渲染展示
   - 底部仅显示"另存为"按钮

3. **编辑视图**（定制数字员工 / 新建 / 另存为）:
   - ID 输入框（创建时可编辑，更新时只读显示）
   - Name 输入框
   - Description 多行文本框
   - Capabilities 标签输入框（输入+回车添加，点 x 删除，输入时自动补全已有标签作为建议）
   - Triggers.file_patterns 文本输入（多行文本框，每行一个模式如 `*.pdf`，后端自动解析为 JSON）
   - Skills 多选下拉框（从 API 获取可选列表）
   - System Prompt 大文本框 + Markdown 预览切换（使用 `marked` 库渲染）
   - 操作按钮：**AI 完善** / 保存 / 删除（仅定制）/ 另存为

4. **AI 完善对比弹窗**:
   - 点击"AI 完善"按钮后，显示 loading 状态（"AI 正在优化中..."）
   - 完成后弹出对比视图弹窗：
     ```
     ┌─────────────────────────────────────────────────────┐
     │  AI 完善结果                               [×关闭]   │
     ├─────────────────────────┬───────────────────────────┤
     │  📝 优化前（原始内容）     │  ✨ 优化后（AI 建议）      │
     │                         │                           │
     │  ---                    │  ---                      │
     │  name: 外贸获客          │  name: 外贸获客智能体       │
     │  description: ...       │  description: 专业的...    │
     │  ...                    │  ...                      │
     │  ## 职责                │  ## 核心职责               │
     │  你是...                │  你是一位资深的外贸...       │
     │                         │                           │
     ├─────────────────────────┴───────────────────────────┤
     │              [ 拒绝 ]    [ 接受并应用 ]              │
     └─────────────────────────────────────────────────────┘
     ```
   - 左右双栏对比，YAML frontmatter 和 Markdown body 同时展示
   - "接受并应用"：将右侧内容替换到编辑器中（不自动保存）
   - "拒绝"：关闭弹窗，编辑器内容不变
   - "关闭"：同拒绝

5. **另存为弹窗**:
   - 输入新的 ID 和 Name
   - 验证唯一性

6. **Markdown 预览**:
   - 使用 `marked` 库（轻量级，需 npm install）
   - 编辑/预览模式切换

### 4.3 添加路由

**文件**: `frontend/src/main.ts`

```typescript
{
  path: '/admin/subagents',
  name: 'admin-subagents',
  component: () => import('./components/DigitalEmployeeManager.vue')
}
```

### 4.4 添加导航入口

**文件**: `frontend/src/components/AppHeader.vue`

在导航栏中增加"数字员工管理"入口。仅管理员可见（前端通过 user 角色判断）。

---

## 五、存储结构

```
storage/
  subagents2/                          # 不入 git
    my-custom-agent/
      SUBAGENT.md                     # 格式与 subagents/ 完全一致
    another-agent/
      SUBAGENT.md
```

**`.gitignore` 更新**:
```
storage/subagents2/
```

---

## 六、SUBAGENT.md 序列化规范

创建/更新定制数字员工时，后端将前端提交的数据序列化为标准的 SUBAGENT.md 格式:

```yaml
---
name: 智能体名称
description: 描述文本
version: 1.0.0
author: admin
capabilities:
  - capability1
  - capability2
triggers:
  file_patterns:
    - "*.pdf"
tools:
  inherit: true
skills:
  allowed:
    - skill-name-1
    - skill-name-2
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---

{system_prompt 的 Markdown 内容}
```

**注意**: YAML frontmatter 中的字段由结构化数据控制，`---` 分隔符之后的 Markdown 正文即 `system_prompt` 字段的内容。

---

## 七、唯一性校验

- **agent_id（目录名）**: 在所有内置 + 定制数字员工中必须唯一
- **name（显示名称）**: 在所有内置 + 定制数字员工中必须唯一
- 校验在保存时由后端执行，前端也做预校验提供即时反馈

---

## 八、实施顺序

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | `.gitignore` 添加 `storage/subagents2/` | 无 |
| 2 | `SubagentLoader` 增加 serialize/save/delete 方法 | 无 |
| 3 | `SubagentRegistry` 改造（合并加载 + CRUD + 管理员方法） | 步骤 2 |
| 4 | `configs/config.yaml` 添加 admin.phones 配置 | 无 |
| 5 | 新建 `src/api/admin_subagent.py` 管理路由（含 AI 完善接口） | 步骤 3, 4 |
| 6 | `src/main.py` 注册管理路由 | 步骤 5 |
| 7 | `src/core/agent.py` 确保初始化时传入 custom_dir | 步骤 3 |
| 8 | `npm install marked diff` 安装渲染和对比库 | 无 |
| 9 | 新建 `frontend/src/api/adminSubagent.ts`（含 aiEnhance 方法） | 无 |
| 10 | 新建 `frontend/src/components/DigitalEmployeeManager.vue`（含 AI 完善对比弹窗） | 步骤 8, 9 |
| 11 | `frontend/src/main.ts` 添加路由 | 步骤 10 |
| 12 | `frontend/src/components/AppHeader.vue` 添加导航入口 | 步骤 10 |
