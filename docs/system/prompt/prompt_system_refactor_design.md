# 提示词系统重构设计文档

> 版本: v1.0 | 创建: 2026-04-30 | 状态: 待评审

## 1. 现状分析

### 1.1 当前架构

系统提示词通过 `Agent._build_base_system_prompt()` 这一个 ~260 行的方法组装，所有提示词文本以 Python f-string 硬编码在其中。没有任何独立的提示词管理模块。

```
Agent._build_base_system_prompt()
    ├─ 身份 + 核心工作流程     (硬编码 f-string)
    ├─ 子智能体匹配提示        (动态 + 硬编码示例)
    ├─ 分步工作流              (条件硬编码)
    ├─ 语言规则                (硬编码)
    ├─ 能力范围与可用资源       (动态: 工具/技能/子智能体列表)
    ├─ 超出能力的处理模板      (硬编码)
    ├─ 工具使用指南            (动态: 从各 Tool 类收集)
    ├─ 委派指南                (动态: delegate_tool 生成)
    ├─ 工作示例                (硬编码, 5个MASTER示例/2个非MASTER示例)
    ├─ 指导原则                (条件硬编码)
    ├─ 专业领域约束            (动态: SUBAGENT.md 的 body)
    ├─ 非委派警告              (硬编码, 仅子智能体)
    └─ 用户信息                (动态: user 对象)
```

### 1.2 已有的提示词基础设施（未使用）

项目中存在 `src/llm/prompts/` 模块，包含：

| 文件 | 内容 | 状态 |
|------|------|------|
| `system.py` | `SYSTEM_PROMPT`（旧版通用助手提示词）、`INTENT_PROMPT`（意图识别）、`PLANNING_PROMPT`（任务规划） | **完全未使用**。Agent 实际使用的是 `_build_base_system_prompt()` 中的提示词 |
| `templates.py` | `PromptTemplate`、`MessageTemplate`、`FewShotPromptTemplate`、预定义模板 | **未使用**。Agent 代码中没有 import 这些类 |

这是一个遗留模块，在重构为智能体驱动架构后被遗弃，但从未清理。

### 1.3 提示词散落位置统计

| 位置 | 提示词类型 | 行数(估) | 可维护性 |
|------|-----------|---------|---------|
| `src/core/agent.py` `_build_base_system_prompt()` | 系统提示词主体 | ~260 行 | 差：大 f-string |
| `src/core/agent.py` `_build_messages()` | 消息格式化逻辑 | ~100 行 | 中 |
| 各 Tool 类的 `usage_guide` 属性 | 工具级使用指南 | 每个工具 0-40 行 | 中：分散在各文件 |
| `src/tools/agent/delegate_tool.py` | 委派指南（动态） | ~10 行 | 可 |
| `src/tools/browser/browser_tool.py` | 浏览器操作指南 | ~40 行 | 可 |
| `src/tools/llm/content_generate_tool.py` | 内容生成指南 | ~15 行 | 可 |
| `subagents/*/SUBAGENT.md` | 子智能体系统提示词 | 每个 50-200 行 | 好：外部文件 |
| `src/skills/*/SKILL.md` | 技能说明（运行时注入） | 每个 50-500 行 | 好：外部文件 |
| `src/llm/prompts/` | 旧版提示词模块 | ~230 行 | 废弃 |

### 1.4 各 Tool 的 usage_guide 情况

| 工具 | usage_guide | 说明 |
|------|------------|------|
| `browser_open` 及浏览器系列 | ~40 行详细操作规则 | 唯一的大块指南 |
| `content_generate` | ~15 行好的/坏的 prompt 示例 | 有价值 |
| `delegate_to_subagent` | 动态生成（列出可用子智能体） | 有价值 |
| 其余 20+ 个工具 | `""`（空） | 不贡献任何内容 |

---

## 2. 问题诊断

### P1: 工作示例（`## 工作示例`）是否可以删除？

**当前内容**（MASTER 模式，5 个示例）：
1. HR 招聘任务 → 直接委派 hr-expert
2. 代码审查 → 直接委派 code-reviewer
3. PDF 文档处理 → 直接委派 pdf-expert
4. 搜索 + 发邮件 → 需要 create_plan
5. 超能力范围 → 拒绝并建议

**分析**：

**支持删除的理由**：
- 示例 2、3 引用了 `code-reviewer` 和 `pdf-expert` 子智能体，但当前系统中**不存在**这两个子智能体（只有 `trade-specialist`、`contract-archive-review`、`travel-consultant`）。这些示例实际上是**错误的引导**
- 现代 LLM（Qwen、GLM）在系统提示词中嵌入 few-shot 示例的效果有限，它们已经能从工具描述和工作流描述中理解用法
- 示例占用约 ~50 行提示词，消耗 token 但价值递减
- 工作流描述中的"第一步：分析需求"已经包含了决策逻辑的说明，示例只是重复说明

**支持保留的理由**：
- 示例 1（HR 招聘）和示例 4（搜索+发邮件）仍然有价值，帮助 LLM 理解何时需要 create_plan、何时直接委派
- 示例 5（超能力范围）展示了拒绝的标准格式

**结论**：**可以删除，影响很小。** 但建议保留一个简化版的决策逻辑说明替代完整示例。

### P2: 其他不合理或可优化的提示词部分

| # | 问题 | 严重程度 | 说明 |
|---|------|---------|------|
| P2.1 | 身份声明过于泛化 | 中 | "你是一个智能工作助手" 没有体现系统的实际定位（企业员工 AI 代理），不如参考 SUBAGENT.md 的做法，允许主智能体也有可配置的身份描述 |
| P2.2 | 工作流描述与工具 usage_guide 重复 | 中 | 工作流中"第三步：执行计划"的详细说明与 `create_plan` 工具返回的 `next_step_prompt` 功能重叠 |
| P2.3 | 技能使用规则占据过大篇幅 | 低 | 约 10 行的技能使用规则（use_skill → skill_execute → 最终回复），这部分内容已经在 `use_skill` 的工具描述中存在 |
| P2.4 | "超出能力的处理"模板占据过大篇幅 | 低 | 约 15 行的示例回复格式，LLM 天然具备这种能力，不需要如此详细的模板 |
| P2.5 | 语言规则过于啰嗦 | 低 | 3 行重复说明同一件事（用用户的语言回复），可以压缩为 1 句 |
| P2.6 | `_build_base_system_prompt` 同时承担 MASTER/SUBAGENT/STANDALONE 三种模式 | 高 | 260 行的方法中充满条件分支，可读性差，维护困难 |
| P2.7 | 旧版 `src/llm/prompts/` 模块从未被清理 | 低 | 废弃代码，不造成运行时问题但增加认知负担 |
| P2.8 | 提示词没有版本管理和 A/B 测试能力 | 中 | 任何提示词调整都只能全量上线，无法灰度 |

### P3: 提示词应该有独立管理模块

**当前痛点**：
1. 修改任何提示词文本都需要编辑 `agent.py` 这个 2000+ 行的核心文件
2. 提示词文本散落在 `agent.py`、各 Tool 类、`delegate_tool.py` 等多处
3. 没有提示词版本管理，无法追踪"改了什么、为什么改、效果如何"
4. 未来接入长期记忆时，需要在系统提示词中注入用户记忆内容，当前架构无法优雅地扩展

**行业实践**：
- 主流 Agent 框架（Claude Code、LangChain、AutoGPT）都将提示词模板从代码中分离
- 常见做法是用 `.md` 文件作为提示词模板，支持变量插值
- 每个 Agent 类型有独立的 `agent.md` 描述其基本行为

---

## 3. 重构设计

### 3.1 目标

1. 将提示词文本从 Python 代码中分离到 `.md` 文件
2. 建立 `PromptManager` 统一管理提示词的加载、组装和扩展
3. 支持未来长期记忆、用户偏好等动态上下文的注入
4. 清理不合理的提示词内容（删除工作示例、精简冗余）
5. 清理废弃的 `src/llm/prompts/` 模块

### 3.2 目录结构

```
src/
  prompts/                          # 新模块：提示词管理
    __init__.py                     # 导出 PromptManager
    manager.py                      # PromptManager 核心类
    templates/                      # 提示词模板文件
      master_agent.md               # 主智能体系统提示词模板
      subagent_base.md              # 子智能体基础模板（通用部分）
      sections/                     # 可复用的提示词片段
        workflow.md                  # 核心工作流
        tool_guide.md               # 工具使用指南（动态生成）
        skill_rules.md              # 技能使用规则
        delegation.md               # 委派决策逻辑
        subagent_constraint.md      # 子智能体限制说明
        out_of_scope.md             # 超出能力范围的处理
        language_rules.md           # 语言规则
        long_term_memory.md         # 长期记忆注入（未来）
    renderer.py                     # 模板渲染器（变量插值）
```

### 3.3 模板文件格式

模板使用 Markdown 文件 + `{变量}` 占位符格式：

**`templates/master_agent.md`**：

```markdown
你是一个智能工作助手。你的任务是帮助企业员工完成日常工作任务。

{delegation_hint}

{workflow}

---

{language_rules}

---

## 能力范围与限制

### 可用工具
{available_tools}

### 可用技能（⚠️ 技能不是工具！不能直接调用技能名称！必须先通过 use_skill 工具加载）
{skill_descriptions}

{skill_rules}
{subagent_section}

{out_of_scope}

---

{tool_usage_guides}

---

{principles}

{subagent_constraint}

{user_info}
```

每个 `{变量}` 由 PromptManager 在运行时用动态数据填充。如果变量为空字符串，对应行自动移除（不留空行）。

### 3.4 PromptManager 设计

```python
class PromptManager:
    """
    提示词管理器 - 负责系统提示词的加载、组装和渲染
    
    职责：
    1. 加载 .md 模板文件
    2. 收集动态数据（工具列表、技能描述、子智能体描述、用户记忆等）
    3. 渲染最终系统提示词
    
    不负责：
    - 工具注册和执行（ToolRegistry）
    - 技能内容加载（SkillRegistry）
    - 消息历史管理（MemoryManager）
    """

    def __init__(self, templates_dir: str = None):
        self.templates_dir = templates_dir or str(Path(__file__) / "templates")
        self._templates: Dict[str, str] = {}  # 缓存已加载的模板
        self._sections: Dict[str, str] = {}    # 缓存已加载的片段

    def load_template(self, name: str) -> str:
        """加载模板文件（带缓存）"""
        
    def render_system_prompt(
        self,
        mode: AgentMode,
        # 动态数据
        available_tools: List[str] = None,
        skill_descriptions: str = "",
        subagent_descriptions: str = "",
        tool_usage_guides: str = "",
        delegation_guide: str = "",
        subagent_constraint: str = "",
        user: Optional[User] = None,
        # 未来扩展
        long_term_memory: str = "",
    ) -> str:
        """
        渲染系统提示词
        
        根据 mode 选择模板（master_agent.md 或 subagent_base.md），
        用动态数据填充模板变量，返回最终提示词。
        """
```

### 3.5 提示词内容精简方案

#### 删除：工作示例（`## 工作示例`）

完全删除 5 个示例。用简化的决策逻辑替代，嵌入工作流描述中：

```markdown
### 第一步：分析需求
1. 理解用户想要什么
2. 判断是否需要使用工具
3. 确定需要哪些工具/技能/子智能体

**决策规则：**
- 单一工具/子智能体能完成 → 直接调用，不需要 create_plan
- 多工具组合或多步骤 → 先 create_plan，再逐步执行
```

这比 5 个完整示例更简洁且不会过时。

#### 精简：语言规则（3 行 → 1 行）

```markdown
## 语言规则
始终使用与用户提问相同的语言进行回复和工具调用。
```

#### 精简：超出能力范围处理（15 行 → 5 行）

```markdown
### 超出能力的处理
明确告知用户无法完成、解释原因、提供替代方案。不要虚假承诺。
```

#### 精简：技能使用规则（10 行 → 4 行）

```markdown
**技能使用规则：**
1. 技能名称不是工具，必须先用 `use_skill(skill="技能名")` 加载
2. 加载后按操作指南调用相应工具（skill_execute / content_generate / web_search 等）
3. 完成后直接给出最终回复
```

#### 保留不动：工具使用指南

`_collect_tool_usage_guides()` 收集的各工具 usage_guide 保持现有机制不变。这些已经很好地模块化了（每个 Tool 类自己管理自己的指南）。

### 3.6 提示词各部分的保留/删除/精简决策

| 提示词部分 | 决策 | 理由 |
|-----------|------|------|
| 身份声明 | **保留**，移到模板文件 | 基础必要 |
| 核心工作流程 | **精简**，移到 `sections/workflow.md` | 去除冗余说明 |
| 子智能体匹配提示 | **保留**，移到 `sections/delegation.md` | 仅 MASTER 模式需要 |
| 分步工作流 | **精简**，合并到 workflow.md | 去除重复 |
| 语言规则 | **精简**，1 行 | 3 行变 1 行 |
| 可用工具/技能/子智能体 | **保留**，动态生成 | 核心必要 |
| 超出能力的处理 | **精简**，1-2 行 | LLM 天然具备 |
| 工具使用指南 | **保留**，现有机制不变 | 已模块化 |
| 委派指南 | **保留**，现有机制不变 | 动态内容 |
| **工作示例** | **删除** | 过时示例误导 LLM |
| 指导原则 | **精简**，3-4 行 | 7 行过多 |
| 专业领域约束 | **保留**，动态注入 | 子智能体配置 |
| 非委派警告 | **保留**，移到 subagent 模板 | 子智能体必要 |
| 用户信息 | **保留**，动态注入 | 基础必要 |
| **长期记忆**（未来） | **新增**，注入点预留 | Phase 3 需求 |

### 3.7 与 Agent 的集成方式

重构后的调用方式：

```python
# agent.py
class Agent:
    def __init__(self, ...):
        self.prompt_manager = PromptManager()
        # ... 其他初始化不变

    def _build_system_prompt(self, user=None) -> str:
        return self.prompt_manager.render_system_prompt(
            mode=self.mode,
            available_tools=[t["name"] for t in self._get_tools()],
            skill_descriptions=self.skill_registry.get_descriptions(),
            subagent_descriptions=self.subagent_registry.get_descriptions() if self.subagent_registry else "",
            tool_usage_guides=self._collect_tool_usage_guides(),
            delegation_guide=self._delegate_tool.get_usage_guide(...) if self._delegate_tool else "",
            subagent_constraint=self.subagent_config.system_prompt if self.subagent_config else "",
            user=user,
        )
```

`_build_base_system_prompt()` 方法被完全替代为 `PromptManager.render_system_prompt()`。

### 3.8 清理废弃模块

| 操作 | 目标 | 说明 |
|------|------|------|
| 删除 | `src/llm/prompts/system.py` | 旧版提示词，未使用 |
| 删除 | `src/llm/prompts/templates.py` | 旧版模板类，未使用 |
| 更新 | `src/llm/prompts/__init__.py` | 清空导出或删除整个目录 |

如果 `src/llm/prompts/` 没有被任何业务代码 import（需确认），可以直接删除整个目录。

---

## 4. 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/prompts/__init__.py` | 模块入口 |
| `src/prompts/manager.py` | PromptManager 核心类 |
| `src/prompts/renderer.py` | 模板渲染器（变量插值） |
| `src/prompts/templates/master_agent.md` | 主智能体提示词模板 |
| `src/prompts/templates/subagent_base.md` | 子智能体提示词模板 |
| `src/prompts/templates/sections/workflow.md` | 核心工作流片段 |
| `src/prompts/templates/sections/delegation.md` | 委派决策片段 |
| `src/prompts/templates/sections/skill_rules.md` | 技能使用规则片段 |
| `src/prompts/templates/sections/subagent_constraint.md` | 子智能体限制片段 |
| `src/prompts/templates/sections/out_of_scope.md` | 超出能力处理片段 |
| `src/prompts/templates/sections/language_rules.md` | 语言规则片段 |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/core/agent.py` | `__init__` 中创建 PromptManager；`_build_system_prompt()` 委托给 PromptManager；删除 `_build_base_system_prompt()` |
| `src/subagents/executor.py` | 如果有调用 `_build_base_system_prompt` 的地方，改为使用 PromptManager |

### 删除文件

| 文件 | 说明 |
|------|------|
| `src/llm/prompts/system.py` | 旧版废弃提示词 |
| `src/llm/prompts/templates.py` | 旧版废弃模板类 |

---

## 5. 实施步骤

### Step 1: 创建 PromptManager 基础设施

1. 创建 `src/prompts/` 目录结构
2. 实现 `renderer.py`：简单的 `{变量}` 插值渲染器
3. 实现 `manager.py`：模板加载 + 渲染逻辑
4. 编写单元测试

### Step 2: 编写提示词模板文件

1. 从 `_build_base_system_prompt()` 中提取文本到 `.md` 文件
2. 按精简方案调整内容（删除工作示例、精简冗余部分）
3. 用 `{变量}` 标记动态插值点

### Step 3: 集成到 Agent

1. Agent 初始化时创建 PromptManager
2. 替换 `_build_system_prompt()` 的实现
3. 删除 `_build_base_system_prompt()` 方法
4. 运行测试确认无回归

### Step 4: 清理废弃代码

1. 确认 `src/llm/prompts/` 无业务引用
2. 删除旧版提示词模块
3. 更新相关 import

### Step 5: 预留长期记忆注入点

1. 在模板中预留 `{long_term_memory}` 插值点
2. PromptManager.render_system_prompt() 接受 `long_term_memory` 参数
3. Phase 3 实现长期记忆后，直接传入即可

---

## 6. 风险评估

| 风险 | 可能性 | 影响 | 应对 |
|------|--------|------|------|
| 精简提示词导致 LLM 行为变化 | 中 | 中 | 逐段精简，每段精简后人工测试对比 |
| 模板文件路径在生产环境中找不到 | 低 | 高 | 使用 `Path(__file__)` 相对路径，跟随代码部署 |
| 变量插值与 Markdown 语法冲突 | 低 | 低 | 渲染器转义处理 `{}` |
| 子智能体 executor 依赖 `_build_base_system_prompt` 回调 | 低 | 中 | executor 已通过参数接收回调，改为传递 PromptManager 方法即可 |

---

## 7. 后续扩展

### 长期记忆注入（Phase 3）

在模板中预留：
```markdown
{long_term_memory_section}
```

当 `long_term_memory` 参数非空时，渲染为：
```markdown
## 用户记忆
{long_term_memory}
```

### 提示词 A/B 测试

未来可扩展 PromptManager 支持：
- 多版本模板文件（`master_agent_v2.md`）
- 按用户/租户切换模板版本
- 记录使用的模板版本到日志，用于效果分析

### 动态提示词调整

支持从数据库或配置文件加载提示词覆盖，无需重启服务。
