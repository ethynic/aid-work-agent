# Word 文档处理工具设计文档

> 版本: v2.0 | 创建日期: 2026-05-07 | 最后更新: 2026-05-07 | 状态: Phase 2 开发中

---

## 1. 背景与动机

### 1.1 现状（v1 已完成）

Word 文档处理功能已从 Skill 迁移为 Tool，当前架构：

```
Agent LLM → 解析用户意图 → 指定 task 参数 → word_process 工具执行
```

Agent LLM 同时负责：理解用户意图 **和** 决定调用哪个 word 操作。这导致：
- Agent 需要理解 word 工具的 9 种操作类型及参数格式，消耗大量 context
- Agent 经常不调用工具，而是自己编造文件内容
- 操作细节暴露给 Agent，增加了 prompt 复杂度

### 1.2 v2 目标

引入 **Word 工具内部 LLM 路由**：

```
Agent LLM → 只传上下文（用户需求 + 相关内容 + 附件）→ word_process 工具
                                                        ↓
                                              内部 LLM 决定操作 + 参数
                                                        ↓
                                              执行 pipeline → 返回结果
```

Agent 只需要知道："用户要 Word → 调 word_process，把相关信息传过去"。

### 1.3 设计原则

1. **Agent 侧极简**：Agent 只传 `context`（用户需求描述 + 相关附件 + 对话内容），不指定操作类型
2. **内部 LLM 路由**：Word 工具内部调用 LLM，根据 context 判断需要执行哪些操作及参数
3. **上下文完整性**：Agent 传过来的 context 必须包含足够信息——附件路径、对话中的 Markdown 内容等
4. **内部过程不暴露**：Word 工具内部的 LLM 调用、pipeline 执行过程对 Agent 透明

---

## 2. 架构设计

### 2.1 整体流程

```
用户: "帮我把刚才的行程安排生成一份Word文档"
          │
          ▼
Agent LLM 看到 word_process 工具描述:
  "所有 Word 文档相关操作都通过本工具处理，把用户需求和相关信息传过来"
          │
          ▼
Agent 调用 word_process:
  context = "用户要求将之前对话中的行程安排生成Word文档。
            行程内容如下：
            # 贵州研学行程安排
            | 天数 | 主题 | ...
            ..."
          │
          ▼
Word 工具内部 LLM:
  分析 context → 判断操作: md_to_word
  提取参数: template=default, title="贵州研学行程安排", output_name="贵州研学行程安排.docx"
          │
          ▼
执行 pipeline: md_to_word(context中的Markdown)
          │
          ▼
返回: { success: true, download_url: "/api/files/xxx/download" }
```

### 2.2 模块结构

```
src/tools/word/
├── __init__.py                 # 导出 WordProcessTool
├── word_process_tool.py        # Agent 入口 + 内部 LLM 路由
├── word_router.py              # [新增] 内部 LLM 路由器
├── word_lib.py                 # 核心库（文件操作、样式管理、跨 run 替换）
├── word_reader.py              # Word 读取
├── word_to_md.py               # Word → Markdown
├── md_to_word.py               # Markdown → Word（含富文本表格渲染）
├── word_modifier.py            # 内容修改操作
├── word_formatter.py           # 格式化操作
├── word_differ.py              # 文档对比
├── template_manager.py         # 模板管理
└── assets/
    └── templates/              # 模板 JSON 文件
```

### 2.3 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    Agent（主智能体）                      │
│  只看到简短的工具描述，调用时传 context + file_paths       │
└───────────────────────┬─────────────────────────────────┘
                        │ word_process(context=..., file_paths=...)
                        ▼
┌─────────────────────────────────────────────────────────┐
│                 WordProcessTool（入口）                    │
│  1. 接收 Agent 传来的 context 和 file_paths              │
│  2. 调用内部 LLM 路由，决定操作类型和参数                  │
│  3. 路由失败则返回错误提示                                 │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  WordRouter（内部LLM）│
              │  分析 context →       │
              │  输出: task + params  │
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  Pipeline 执行引擎   │
              │  按序执行各操作      │
              │  自动注册下载文件    │
              └──────────┬──────────┘
                         ▼
              返回结果给 Agent
              (download_url / 内容 / 错误信息)
```

---

## 3. Agent 侧接口设计

### 3.1 工具描述（Agent 看到的）

```
Word文档处理工具。所有与Word文档(.docx)相关的操作都通过本工具处理。

⚠️ 触发规则 — 遇到以下场景必须调用本工具：
- 用户要求生成、导出、创建Word文档
- 用户要求读取、分析、修改、格式化、对比Word文件
- 用户上传了.docx文件并要求处理
不要自己生成文件内容，一律交给本工具。

调用方式：
- 将用户的原始需求描述和相关内容放在 context 中
- 如果需要将对话内容转为Word，context 中必须包含完整的 Markdown 文本
- 用户上传的附件路径放在 file_paths 中
工具会自动判断并执行合适的操作。
```

### 3.2 输入参数

```python
class WordProcessInput(BaseModel):
    context: Optional[str] = Field(
        None,
        description="用户的原始需求描述和相关内容。"
                    "如果要把对话中的内容转为Word，这里必须包含完整的 Markdown 文本；"
                    "如果涉及附件操作，附件路径通过 file_paths 传入"
    )
    file_paths: Optional[List[str]] = Field(
        None,
        description="附件文件路径列表（用户上传的 .docx/.md 文件等）"
    )
```

Agent **不传** `task` 和 `params`，操作类型和参数完全由内部 LLM 决定。

### 3.3 Agent 调用示例

#### 场景 1：把对话中的内容生成 Word

```
Agent 调用:
  word_process(
    context = "用户要求将之前对话中讨论的行程安排生成Word文档。
              以下是行程安排的完整内容：
              # 贵州研学行程安排
              ## 5天4晚行程表
              | 天数 | 主题 | 核心安排 | 教育亮点 |
              |------|------|---------|---------|
              | Day 1 | 启程 | ... | ... |
              ..."
  )

工具内部 LLM 判断: md_to_word
工具执行: 将 context 中的 Markdown 转为 Word
返回: { success: true, download_url: "/api/files/xxx/download" }
```

#### 场景 2：用户上传了 Word 文件要求修改

```
Agent 调用:
  word_process(
    context = "用户要求将合同中的'XX公司'替换为'YY科技有限公司'，日期改为2026年",
    file_paths = ["/storage/uploads/tenant1/user1/file_abc123.docx"]
  )

工具内部 LLM 判断: modify, operations=[{replace_text, ...}]
工具执行: 读取文件 → 替换文本 → 保存副本
返回: { success: true, download_url: "/api/files/xxx/download" }
```

#### 场景 3：读取 Word 文件内容

```
Agent 调用:
  word_process(
    context = "用户想了解这个文档的内容",
    file_paths = ["/storage/uploads/tenant1/user1/file_abc123.docx"]
  )

工具内部 LLM 判断: read (或 word_to_md)
工具执行: 读取文档内容
返回: { success: true, content: "文档文本内容...", tables: [...] }
```

#### 场景 4：对比两份文档

```
Agent 调用:
  word_process(
    context = "用户要求对比这两个合同版本的差异",
    file_paths = ["/path/to/v1.docx", "/path/to/v2.docx"]
  )

工具内部 LLM 判断: diff
工具执行: 对比两份文档
返回: { success: true, diff_text: "...", change_count: 5 }
```

---

## 4. 内部 LLM 路由设计

### 4.1 路由器职责

`WordRouter` 是 Word 工具内部的 LLM 调用模块，负责：

1. 接收 Agent 传来的 `context` + `file_paths`
2. 调用 LLM 分析用户意图，输出结构化的执行计划
3. 返回 `{ task: "md_to_word", params: {...} }` 或 `{ task: "read,modify", params: {...} }`

### 4.2 路由 Prompt

```
你是 Word 文档处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作。

## 可用操作

1. read - 读取Word文档的文本内容和表格
2. analyze - 分析Word文档的结构（段落样式、字体、表格布局、页面设置）
3. word_to_md - 将Word文档转换为Markdown文本
4. md_to_word - 将Markdown文本转换为Word文档
5. modify - 修改Word文档内容（替换文本、增删段落/表格等）
6. format - 格式化Word文档（字体、段落格式、页面设置、页眉页脚）
7. fill_template - 填充Word模板中的变量占位符（{{变量名}} 或 [变量名]）
8. list_templates - 列出可用的文档模板
9. diff - 对比两个Word文档的差异

## 判断规则

- context 中包含 Markdown 格式内容且要求生成/导出 Word → md_to_word
- context 要求读取/查看/了解 Word 文件内容 → read（如果有文件）
- context 要求修改/编辑/替换 Word 文件 → modify
- context 要求格式化/排版 Word 文件 → format
- context 要求填充模板/替换变量 → fill_template
- context 要求对比/比较两个文件 → diff
- context 要求查看模板 → list_templates
- 有文件且只要求转为 Markdown → word_to_md
- 操作可组合，如 "先读取再修改" → read,modify

## 输出格式

严格输出 JSON，不要输出其他内容：
{
  "task": "md_to_word",
  "params": {
    "template": "default",
    "title": "从内容中提取的标题",
    "output_name": "从内容推断的文件名.docx"
  },
  "reason": "用户要求将行程安排生成Word，context中包含完整的Markdown表格内容"
}

如果是 modify 操作，params 中应包含 operations 列表：
{
  "task": "modify",
  "params": {
    "operations": [
      {"type": "replace_text", "target": "XX公司", "replacement": "YY科技有限公司"}
    ],
    "output_name": "修改后的文件名.docx"
  },
  "reason": "用户要求替换合同中的公司名称"
}

## 用户上下文
{context}

## 附件文件
{file_paths}
```

### 4.3 路由器实现

```python
# src/tools/word/word_router.py

class WordRouter:
    """Word 工具内部 LLM 路由器"""

    def __init__(self):
        from src.llm.gateway import LLMGateway
        self.gateway = LLMGateway()

    async def route(self, context: str, file_paths: list = None) -> dict:
        """
        根据上下文决定操作类型和参数。

        Returns:
            {"task": "md_to_word", "params": {...}} 或
            {"task": "read,modify", "params": {...}}
        """
        prompt = self._build_prompt(context, file_paths)
        response = await self.gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return self._parse_response(response)

    def _build_prompt(self, context, file_paths):
        # 组装路由 prompt（见 4.2）
        ...

    def _parse_response(self, response):
        # 解析 LLM 输出为结构化 JSON
        ...
```

### 4.4 LLM 调用开销

- 内部路由使用与主智能体相同的 LLM 提供者，temperature=0，max_tokens=512
- 只输出一个 JSON，token 消耗极少（~200 token）
- 路由失败时直接返回错误提示，不使用关键词 fallback

---

## 5. 内部操作详情（Agent 不可见）

以下内容供工具内部使用，Agent 不会看到。

### 5.1 操作类型与参数

| 操作 | 输入 | 输出 |
|------|------|------|
| read | file_paths | 文本内容、表格数据、文档元信息 |
| analyze | file_paths | 段落结构、表格详情、页面设置 |
| word_to_md | file_paths | Markdown 文本 |
| md_to_word | context(Markdown) + params(template, title, output_name) | download_url |
| modify | file_paths + params.operations | download_url |
| format | file_paths + params.format_operations | download_url |
| fill_template | file_paths + params.variables | download_url |
| list_templates | 无 | 模板列表 |
| diff | file_paths(2个) + params.output_format | 差异报告 |

### 5.2 Pipeline 机制

支持逗号分隔的多操作组合，按序执行，前一步输出自动传递给后一步：

```
read,modify        → 先读取内容，再执行修改
md_to_word,format  → 先转Word，再格式化
read,fill_template,format → 读取→填充变量→调整格式
```

### 5.3 下载注册

所有产生文件的操作自动注册到 `uploaded_files`，返回 `file_id` + `download_url`（`/api/files/{file_id}/download`）。

### 5.4 文件存储

文件存储在 `storage/uploads/{tenant_id}/{user_id}/` 目录下，与用户上传文件共用同一存储体系。

---

## 6. Agent 传上下文的关键要求

这是整个设计的核心——Agent 传过来的 `context` 质量直接决定工具能否正确执行。

### 6.1 必须传的内容

| 场景 | context 中必须包含 | 原因 |
|------|-------------------|------|
| 生成 Word | 完整的 Markdown 文本（不是摘要） | md_to_word 需要完整的 Markdown 内容作为输入 |
| 修改文件 | 具体的修改指令（替换什么、替换为什么） | 内部 LLM 需要构造 operations 列表 |
| 填充模板 | 变量名和值的映射 | fill_template 需要 variables 字典 |
| 读取文件 | 无特殊要求 | 只需 file_paths |

### 6.2 附件处理

- 用户上传的 .docx 文件路径通过 `file_paths` 传入
- Agent 在 context 中应说明对附件的操作意图（"读取这个文件"、"修改这个合同"等）
- 文件路径来自对话上下文中的附件信息

### 6.3 对话内容转 Word

这是最常见的场景。Agent 必须：
1. 从对话历史中提取需要转为 Word 的完整内容
2. 将内容以 Markdown 格式放入 `context`
3. 说明用户想要的文档标题、模板偏好等（如果有）

**错误示例**（context 太简短）：
```
context = "用户要求生成行程Word"
```

**正确示例**（包含完整内容）：
```
context = "用户要求将行程安排生成Word文档。
以下是完整的行程内容：

# 贵州研学行程安排
## 5天4晚行程表

| 天数 | 主题 | 核心安排 | 教育亮点 |
|------|------|---------|---------|
| Day 1 | **启程** | 贵阳集合 | 了解背景 |
| Day 2 | **仰望** | 参观天眼 | 科学原理 |
..."
```

---

## 7. HTTP API 接口

API 层绕过 Agent 和内部 LLM，直接调用子模块。保持现有设计不变。

| 端点 | 功能 |
|------|------|
| `POST /api/word/md-to-docx` | Markdown 转 Word |
| `POST /api/word/docx-to-md` | Word 转 Markdown |
| `POST /api/word/read` | 读取 Word 文档 |
| `POST /api/word/analyze` | 分析文档结构 |
| `POST /api/word/modify` | 修改文档内容 |
| `POST /api/word/format` | 格式化文档 |
| `POST /api/word/fill-template` | 填充模板变量 |
| `POST /api/word/diff` | 对比文档差异 |
| `GET /api/word/templates` | 列出模板 |

---

## 8. 实施计划

### Phase 1: v1 已完成 ✅

基础工具实现、Pipeline 引擎、HTTP API、单元测试。所有 9 种操作已实现并通过测试。

### Phase 2: 内部 LLM 路由 ✅ 已完成

| 序号 | 任务 | 状态 |
|------|------|------|
| P2-1 | 精简 Agent 侧工具描述，只暴露 context + file_paths | ✅ 已完成 |
| P2-2 | 移除 task/params 输入参数，Agent 不再指定操作类型 | ✅ 已完成 |
| P2-3 | 新增 `word_router.py`，实现内部 LLM 路由 | ✅ 已完成 |
| P2-4 | 路由失败时直接返回错误提示（无 fallback） | ✅ 已完成 |
| P2-5 | Agent 侧上下文传递引导（在工具描述中强调必须传完整内容） | ✅ 已完成 |

---

## 9. 风险与注意事项

1. **内部 LLM 调用延迟**：路由会增加一次 LLM 调用（~500ms），所有 Word 操作都必须经过 LLM 路由
2. **LLM 路由出错**：内部 LLM 可能输出无效 JSON 或错误的操作选择。路由失败时直接返回错误提示给 Agent
3. **Agent 传的 context 质量不稳定**：Agent 可能传简短的描述而非完整内容。工具描述中已强调必须传完整内容，但仍需在工具内部做防御性检查
4. **LLM 调用成本**：每次 word 工具调用多一次 LLM 请求（~200 token），成本可控
5. **Markdown 转 Word 的表格渲染**：已支持 `**bold**`、`<br>` 换行、列宽自动分配，但复杂 Markdown（嵌套表格、合并单元格）仍有限制
