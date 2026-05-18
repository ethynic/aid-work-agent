# 提示词系统重构实施计划

> 版本: v1.0 | 创建: 2026-05-04 | 状态: ✅ 已完成
> 设计文档: [docs/prompt_system_refactor_design.md](prompt_system_refactor_design.md)

## Context

`Agent._build_base_system_prompt()` (agent.py:432-729) 是一个 ~300 行的 f-string，承担 MASTER/SUBAGENT/STANDALONE 三种模式的系统提示词组装。提示词文本硬编码在 Python 代码中，修改任何文本都需要编辑 agent.py 这个 2000+ 行的核心文件。

目标：将提示词模板分离到 `.md` 文件，建立 `PromptManager` 统一管理，同时精简过时/冗余的提示词内容，清理废弃代码。

---

## 实施步骤

### Step 1: 创建 `src/prompts/renderer.py` — 模板渲染器  `✅ 已完成`

新建文件，实现 `{variable}` 占位符插值：
- 支持 `{{` / `}}` 转义为字面花括号（与 Python str.format 一致）
- 非严格模式：未知变量保留原样不报错（为未来 `{long_term_memory}` 等预留变量）
- 处理当前代码中 `{{{tool_usage_guides}}}` → 输出 `{<指南内容>}` 的三重花括号模式

### Step 2: 创建 `src/prompts/manager.py` — PromptManager  `✅ 已完成`

新建文件，核心类：
- `load_template(name)` — 加载 `.md` 模板（带缓存）
- `render(template_name, variables)` — 加载 + 渲染
- 模板目录：`Path(__file__).parent / "templates"`
- 启动时验证模板文件存在

### Step 3: 创建 `src/prompts/__init__.py`  `✅ 已完成`

导出 `PromptManager` 和 `render_template`。

### Step 4: 创建模板文件  `✅ 已完成`

**`src/prompts/templates/master_agent.md`** — MASTER 模式模板：
- 包含委派相关内容（subagent_matching_hint、子智能体列表、委派指南）
- 工作示例 **完全删除**（引用了不存在的 code-reviewer、pdf-expert）
- 语言规则精简为 1 行
- 超出能力处理精简为 2 行
- 技能规则精简为 4 条
- 预留 `{long_term_memory}` 注入点

**`src/prompts/templates/subagent_base.md`** — SUBAGENT/STANDALONE 模式模板：
- 不含委派内容
- 内置"重要限制"段落（不能委派）
- 同样精简语言规则、超出能力处理、技能规则

**设计决策**：使用两个独立模板而非一个模板+条件分支。原因：
- 两种模式有 6+ 处差异，条件分支会让模板难以阅读
- 共享内容有少量重复（工作流步骤 2-4、语言规则等），但可接受
- 避免 `sections/` 子目录的额外复杂度（当前只有两种模板）

### Step 5: 重构 `src/core/agent.py` — 替换 `_build_base_system_prompt`  `✅ 已完成`

**保留不变的部分**（计算逻辑，约 80 行）：
- lines 449-451: 模式判断
- lines 453-455: 技能/工具列表获取
- lines 458-500: SaaS 租户子智能体过滤逻辑
- lines 502-526: 委派指南和匹配提示生成

**替换的部分**（f-string 模板 + 条件追加，约 220 行）：
- lines 528-729 的全部模板文本 → 替换为 `PromptManager.render()` 调用

重构后的方法结构：
```python
def _build_base_system_prompt(self, include_delegation=True, subagent_constraint="", user=None):
    # 1. 计算动态数据（保留原逻辑）
    # 2. 组装 variables 字典
    # 3. 选择模板（master_agent.md 或 subagent_base.md）
    # 4. return self.prompt_manager.render(template_name, variables)
```

**同时在 `Agent.__init__` 中**：
- 添加 `self.prompt_manager = PromptManager()`

### Step 6: 清理 `SubagentExecutor` 废弃回调  `✅ 已完成`

**`src/subagents/executor.py`**：
- 移除 `__init__` 的 `build_base_prompt_func` 参数（line 58）
- 移除 docstring 中对应行（line 69）
- 移除 `self.build_base_prompt_func = ...` 赋值（line 76）

**`src/core/agent.py`**：
- 移除 `SubagentExecutor(...)` 调用中的 `self._build_base_system_prompt` 参数（line 182）

### Step 7: 清理废弃的 `src/llm/prompts/` 模块  `✅ 已完成`

经代码分析确认：
- `SYSTEM_PROMPT` — 零引用，安全删除
- `PLANNING_PROMPT` — 零引用，安全删除
- `INTENT_PROMPT` — 仅被 `intent_engine.py` 引用，而 `intent_engine.py` 仅被 `planner.py` 引用，`planner.py` 零引用（全链路死代码）
- `PromptTemplate` / `MessageTemplate` / `FewShotPromptTemplate` — 零引用

**操作**：
1. 删除 `src/llm/prompts/system.py`
2. 删除 `src/llm/prompts/templates.py`
3. 清空 `src/llm/prompts/__init__.py`（或删除整个目录）
4. 可选：删除 `src/core/intent_engine.py` 和 `src/core/planner.py`（同属死代码链）

### Step 8: 验证  `✅ 已完成`

1. **现有测试**：`tests/integration/test_agent_loop.py` 中的 `TestAgentBuildSystemPrompt` 必须通过
2. **手动对比**：重构前后分别调用 `_build_system_prompt()` for MASTER 和 SUBAGENT 模式，对比输出。预期差异仅为设计文档中明确的精简（删除工作示例、语言规则精简、超出能力精简、技能规则精简）
3. **关键内容检查**：
   - MASTER 提示词包含：`delegate_to_subagent`、`create_plan`、工具列表、技能描述、子智能体列表
   - SUBAGENT 提示词包含：`create_plan`、工具列表、"不能委派"限制
   - 两者都包含：核心工作流、语言规则（精简版）、指导原则

---

## 文件变更汇总

### 新增（5 个文件）
| 文件 | 说明 |
|------|------|
| `src/prompts/__init__.py` | 模块入口 |
| `src/prompts/renderer.py` | `{variable}` 模板渲染器 |
| `src/prompts/manager.py` | PromptManager 类 |
| `src/prompts/templates/master_agent.md` | MASTER 模式提示词模板 |
| `src/prompts/templates/subagent_base.md` | SUBAGENT/STANDALONE 模式提示词模板 |

### 修改（2 个文件）
| 文件 | 改动 |
|------|------|
| `src/core/agent.py` | `__init__` 添加 `self.prompt_manager`；`_build_base_system_prompt` 替换为 PromptManager 调用（~300行→~100行）；移除 SubagentExecutor 废弃参数 |
| `src/subagents/executor.py` | 移除 `build_base_prompt_func` 参数 |

### 删除（2-3 个文件）
| 文件 | 原因 |
|------|------|
| `src/llm/prompts/system.py` | 废弃代码，无运行时引用 |
| `src/llm/prompts/templates.py` | 废弃代码，无运行时引用 |
| `src/llm/prompts/__init__.py` | 清空或随目录删除 |

### 可选删除
| 文件 | 原因 |
|------|------|
| `src/core/intent_engine.py` | 死代码链（仅被 planner.py 引用，planner.py 零引用） |
| `src/core/planner.py` | 死代码（零引用） |

---

## 风险

| 风险 | 缓解 |
|------|------|
| 模板渲染改变提示词文本导致 LLM 行为变化 | 对比重构前后输出，仅接受设计文档明确的精简 |
| `{{{tool_usage_guides}}}` 三重花括号渲染错误 | 渲染器使用 `{{`/`}}` 转义机制，等价于 Python f-string |
| 模板文件在部署时找不到 | 使用 `Path(__file__)` 相对路径，跟随代码部署 |
| 删除 `src/llm/prompts/` 影响未知代码 | 已确认仅 `intent_engine.py` 引用 `INTENT_PROMPT`，且该文件为死代码 |
