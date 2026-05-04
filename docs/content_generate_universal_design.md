# content_generate 通用化升级 + 工具使用指南自包含 设计文档

> 创建日期: 2026-04-30

## 1. 问题背景

### 1.1 content_generate 场景通用性不足

`content_generate` 工具仅支持 7 种预设场景，未匹配场景 fallback 到弱的通用提示词。

### 1.2 工具使用指南硬编码在 Agent 中

agent.py 的 `_build_base_system_prompt()` 中硬编码了约 204 行工具使用指南，导致：
- 工具无法"自我介绍"，修改指南必须改 agent.py
- 新增工具时必须在 agent.py 追加说明，容易遗漏
- 工具定义和使用指南分离，维护成本高

## 2. 设计方案

### 2.1 content_generate：两阶段调用

大模型是"提示词的架构师"，`content_generate` 是"执行者"。变量由调用方从对话中提取并嵌入 prompt。

### 2.2 工具使用指南自包含

在 `BaseTool` 中增加 `usage_guide` 属性和 `get_usage_guide()` 方法：
- **`usage_guide`**：静态字符串，工具自己声明使用说明
- **`get_usage_guide(**kwargs)`**：默认返回 `self.usage_guide`，支持子类重写提供动态内容
- **`ToolRegistry.get_usage_guides()`**：收集所有注册工具的指南
- **`Agent._collect_tool_usage_guides()`**：收集注册工具 + 虚拟工具的指南
- Agent 系统提示词中通过 `{self._collect_tool_usage_guides()}` 动态注入

## 3. 变更内容

### 3.1 BaseTool 新增接口

**文件**: `src/tools/base.py`

- 新增 `usage_guide: str = ""` 类属性
- 新增 `get_usage_guide(**kwargs) -> str` 方法（默认返回 `self.usage_guide`）

**文件**: `src/tools/registry.py`

- 新增 `get_usage_guides(**kwargs) -> str` 方法

### 3.2 各工具迁移 usage_guide

从 agent.py 迁移到各工具类中：

| 工具 | 文件 | 指南特点 |
|------|------|----------|
| `create_plan` | `src/tools/plan/create_plan_tool.py` | 静态 |
| `web_search` | `src/tools/search/search_tool.py` | 静态 |
| `use_skill` | `src/tools/skill/use_skill_tool.py` | 静态 |
| `content_generate` | `src/tools/llm/content_generate_tool.py` | 静态（五要素教学+示例） |
| `skill_execute` | `src/tools/skill/skill_execute_tool.py` | 静态 |
| `skill_complete` | `src/tools/skill/skill_complete_tool.py` | 静态 |
| `clarify` | `src/tools/agent/clarify_tool.py` | 静态 |
| `browser_open` | `src/tools/browser/browser_tool.py` | 静态（Browser 工具组共享） |
| `create_scheduled_task` | `src/tools/scheduler/scheduled_task_tool.py` | 静态 |
| `delegate_to_subagent` | `src/tools/agent/delegate_tool.py` | **动态**（重写 `get_usage_guide()`，接受 `subagent_descriptions` 参数） |

### 3.3 content_generate 增强

**文件**: `src/tools/llm/content_generate_tool.py`

- Input Schema description 增强为五要素指导
- 工具 description 改为"通用内容生成工具"
- 通用 fallback 系统提示词增强为 7 条规则
- 新增 `usage_guide` 包含完整的使用教学方法

### 3.4 Agent 系统提示词重构

**文件**: `src/core/agent.py`

- 新增 `_collect_tool_usage_guides()` 方法
- `_build_base_system_prompt()` 中 `## 工具使用指南` 部分从硬编码改为 `{self._collect_tool_usage_guides()}`
- 删除约 204 行硬编码的工具指南
- `delegation_guide` 从硬编码改为从 `DelegateToSubagentTool.get_usage_guide()` 动态获取

## 4. 向后兼容性

| 维度 | 兼容性 |
|------|--------|
| BaseTool 子类 | 完全兼容，`usage_guide` 默认空字符串 |
| content_generate 参数 | 字段名/类型不变 |
| 现有 SKILL.md / SUBAGENT.md | 无需修改 |
| 工具返回结构 | 不变 |

## 5. 新增工具的流程

1. 在工具类中设置 `usage_guide = "使用说明文本"`
2. 如果需要动态内容，重写 `get_usage_guide(**kwargs)` 方法
3. 无需修改 agent.py

## 6. 验证

- `pytest tests/unit/test_content_generate.py` — 7 个测试全部通过
- 全部单元测试 133 passed（13 failed 为预先存在的问题）
- 工具指南动态收集验证通过
