# 智能体回复风格系统设计

> 版本: 1.0
> 日期: 2026-05-26
> 状态: 已开发完成

## 1. 背景与动机

当前智能体的回复风格是硬编码在系统提示词模板和 SUBAGENT.md 中的，无法灵活切换。不同场景对回复风格有不同需求：

| 场景 | 期望风格 |
|------|---------|
| 面向普通员工的日常助手 | 拟人化，像同事对话，隐藏工具调用细节 |
| 面向技术/管理层的深度场景 | 信息密度高，展示推理过程和工具调用 |
| 面向客户的服务型子智能体 | 专业亲切，体现品牌形象 |

**核心诉求**：
1. 主智能体和子智能体都能选择一个回复风格
2. 风格定义独立存储为 `.md` 文件，便于扩展和定制
3. 子智能体作为独立智能体使用时，其自身风格配置优先于 master 默认风格
4. 用户可以通过"记住我的回复风格是XX"在长期记忆中设定风格偏好，该偏好优先级最高
5. 避免向大模型注入多个互相冲突的风格指令

## 2. 现有架构分析

### 2.1 系统提示词组装流程

```
_build_system_prompt()
  ├── MASTER 模式: _build_base_system_prompt(include_delegation=True)
  │     └── 渲染 master_agent.md 模板
  │
  └── SUBAGENT/STANDALONE 模式:
        ├── 加载 subagent_config.system_prompt
        ├── 加载 extra.md（租户定制）
        └── _build_base_system_prompt(subagent_constraint=合并后的内容)
              └── 渲染 subagent_base.md 模板
```

模板中的占位变量在 `_build_base_system_prompt()` 中组装，最终通过 `prompt_manager.render()` 渲染。

### 2.2 已有但未使用的风格基础设施

| 位置 | 字段 | 状态 |
|------|------|------|
| `src/memory/models.py:121` | `UserPreference.communication_style` | 字段存在，agent 循环未使用 |
| `src/saas/db/agent_instance_db.py:25` | `personality_traits` | 仅存储，未注入提示词 |
| `src/services/sentiment_service.py:25` | `suggested_response_tone` | 仅投诉场景使用 |

**结论**：现有字段粒度不够（`communication_style` 只是一个字符串标签），且都未接入提示词组装流程。需要一个独立、完整的风格定义机制。

## 3. 方案设计

### 3.1 风格文件定义

风格文件存放在 `src/prompts/styles/` 目录下，每个风格一个 `.md` 文件。

**文件命名规范**：`{style_id}.md`（小写，连字符分隔）

**文件格式**：纯 Markdown，内容直接作为系统提示词的一部分注入。文件名即为 `style_id`。

**示例**：`src/prompts/styles/human-like.md`（拟人风格）

```markdown
## 回复风格指南

你是一个真实的同事，不是 AI 助手。请在回复时遵循以下规则：

### 身份与语气
- 你是一个真实的人，用自然、亲切的语气交流，就像和同事面对面聊天
- 使用"我"来称呼自己，用"你"称呼用户
- 适当使用口语化表达（如"嗯"、"好的"、"没问题"），但不要过度
- 回复简洁，像真人对话一样，不要写长篇大论

### 信息呈现
- 直接给出结论或答案，不要解释你是怎么知道的
- 不要提及你使用了什么工具、技能或知识库
- 不要说"根据搜索结果"、"我查看了数据库"这类话
- 如果需要展示数据，直接整理成用户关心的形式呈现

### 禁止事项
- 禁止暴露内部工作过程（工具调用、技能加载、数据库查询等）
- 禁止使用"作为 AI 助手"、"我可以帮你"这类暴露 AI 身份的表达
- 禁止在回复中列出执行步骤或工具链路
- 禁止使用编号列表来组织回复内容（除非用户明确要求）
```

### 3.2 风格注册与加载

新增 `src/prompts/style_manager.py`：

```python
"""回复风格管理器"""

import os
from pathlib import Path
from loguru import logger


class StyleManager:
    """管理回复风格文件的加载和解析"""

    STYLES_DIR = Path(__file__).parent / "styles"

    def __init__(self):
        self._styles: dict[str, str] = {}  # style_id -> content
        self._load_all()

    def _load_all(self):
        """加载所有风格文件"""
        if not self.STYLES_DIR.exists():
            logger.warning(f"Styles directory not found: {self.STYLES_DIR}")
            return
        for f in self.STYLES_DIR.glob("*.md"):
            style_id = f.stem  # 文件名（不含扩展名）作为 style_id
            self._styles[style_id] = f.read_text(encoding="utf-8").strip()
            logger.info(f"Loaded reply style: {style_id}")

    def get_style(self, style_id: str) -> str | None:
        """获取风格内容，不存在返回 None"""
        return self._styles.get(style_id)

    def list_styles(self) -> list[str]:
        """列出所有可用的风格 ID"""
        return list(self._styles.keys())

    def reload(self):
        """重新加载所有风格（热更新用）"""
        self._styles.clear()
        self._load_all()
```

### 3.3 风格配置位置

风格可以配置在四个层级（优先级从高到低）：

| 优先级 | 配置位置 | 适用场景 | 说明 |
|--------|---------|---------|------|
| 0（最高） | 用户长期记忆中的 `reply_style` 字段 | 用户主动设定 | 用户说"记住我喜欢拟人风格"后写入记忆，尊重用户个人偏好 |
| 1 | `SUBAGENT.md` frontmatter | 子智能体独立使用 | 子智能体定义自己的固定风格 |
| 2 | `config.yaml` 全局默认 | 主智能体 | 所有智能体的默认风格 |
| 3 | 内置无风格 | — | 不配置则不注入任何风格指令 |

**配置方式**：

**方式一：config.yaml 全局默认**
```yaml
agent:
  reply_style: human-like  # 默认回复风格 ID，对应 src/prompts/styles/human-like.md
```

**方式二：SUBAGENT.md frontmatter**
```yaml
---
name: 旅游顾问
description: 通用旅游行业 AI 顾问
reply_style: professional  # 该子智能体使用 professional 风格
---
```

### 3.4 风格解析优先级逻辑

```
确定当前回复风格:

1. 用户长期记忆中是否有 reply_style 字段？
   └── YES → 使用用户记忆中的风格（最终决定，跳过后续判断）

2. 当前是 SUBAGENT/STANDALONE 模式？
   ├── YES → subagent_config.reply_style 是否配置？
   │         ├── YES → 使用子智能体风格（忽略全局配置）
   │         └── NO  → 使用全局默认风格（如果配置了的话）
   └── NO (MASTER) → 使用全局默认风格（如果配置了的话）

3. 解析到的风格 ID 在 StyleManager 中存在？
   ├── YES → 注入风格内容
   └── NO  → 记录 warning 日志，不注入任何风格（降级为无风格）
```

**关键规则**：同一时刻只会向大模型注入一个风格的指令，不会出现多个风格叠加。

### 3.5 注入位置

在两个模板文件的末尾（`{user_info_section}` 之后）新增 `{reply_style_section}` 占位变量：

**master_agent.md** 末尾：
```
{long_term_memory}
{user_info_section}
{reply_style_section}
```

**subagent_base.md** 末尾：
```
{long_term_memory}
{user_info_section}
{reply_style_section}
```

注入的格式为：

```
---

## 回复风格

{风格文件内容}
```

放在系统提示词的最后，这样风格指令会在所有基础指令之后、最靠近实际对话的位置，对大模型的输出行为产生更强的引导效果。

### 3.6 代码修改清单

#### 新增文件

| 文件 | 说明 |
|------|------|
| `src/prompts/styles/human-like.md` | 拟人风格定义 |
| `src/prompts/style_manager.py` | 风格管理器 |

#### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/prompts/templates/master_agent.md` | 末尾添加 `{reply_style_section}` |
| `src/prompts/templates/subagent_base.md` | 末尾添加 `{reply_style_section}` |
| `src/core/agent.py` `_build_base_system_prompt()` | 解析风格优先级，组装 `reply_style_section` |
| `src/core/agent.py` `_handle_remember_intent()` | 新增回复风格相关的意图识别模式 |
| `src/models/subagent.py` `SubagentConfig` | 添加 `reply_style` 字段 |
| `src/memory/long_term.py` `LongTermMemory` | 新增 `get_reply_style()` / `set_reply_style()` 方法 |
| `src/subagents/loader.py` | 解析 SUBAGENT.md 中的 `reply_style` 字段 |
| `src/config/settings.py` | 添加 `agent.reply_style` 配置项 |
| `configs/config.yaml` | 添加 `agent.reply_style` 配置 |

#### 不需要修改的文件

- 风格系统不涉及数据库变更（无 `deploy/init-postgres.sql` 或 `deploy/db_update.sql`）
- 前端无需改动（本次仅后端提示词注入）
- 渠道系统不受影响

### 3.7 数据模型变更

**SubagentConfig 新增字段**（`src/models/subagent.py`）：

```python
class SubagentConfig(BaseModel):
    # ... 现有字段 ...

    # 回复风格
    reply_style: Optional[str] = Field(default=None, description="回复风格ID（对应 src/prompts/styles/ 下的文件名）")
```

**用户长期记忆文件格式变更**

用户长期记忆存储在 `storage/memory/{tenant_id}/{user_id}.md` 中，采用 YAML frontmatter + Markdown 格式。在 frontmatter 中新增 `reply_style` 字段：

```yaml
---
reply_style: human-like    # 用户设定的回复风格ID，优先级最高
tags: []
---
（记忆正文内容...）
```

用户可以通过自然语言设定风格偏好（如"记住我喜欢拟人风格"、"帮我记住回复风格用简洁模式"），智能体检测到这类意图后写入记忆文件的 `reply_style` 字段。

**Settings 新增配置**（`src/config/settings.py`）：

```python
class AgentSettings(BaseModel):
    reply_style: Optional[str] = None  # 默认回复风格 ID

class Settings(BaseModel):
    # ... 现有字段 ...
    agent: AgentSettings = AgentSettings()
```

### 3.8 核心代码逻辑

`_build_base_system_prompt()` 中的风格解析（伪代码）：

```python
def _build_base_system_prompt(self, include_delegation=True, subagent_constraint="", user=None):
    # ... 现有逻辑 ...

    # 解析回复风格
    reply_style_section = ""
    style_id = self._resolve_reply_style(user)
    if style_id:
        style_content = self.style_manager.get_style(style_id)
        if style_content:
            reply_style_section = f"\n\n---\n\n## 回复风格\n\n{style_content}"
        else:
            logger.warning(f"Reply style '{style_id}' not found, skipping")

    # 添加到模板变量
    variables["reply_style_section"] = reply_style_section
    # ... 渲染模板 ...


def _resolve_reply_style(self, user: Optional[User] = None) -> str | None:
    """解析当前应使用的回复风格（优先级从高到低）"""

    # 优先级 0（最高）：用户长期记忆中的 reply_style
    if user and settings.memory.long_term.enabled:
        try:
            ltm = LongTermMemory(storage_dir=settings.memory.long_term.storage_dir)
            user_style = ltm.get_reply_style(
                tenant_id=self._get_effective_tenant_id(),
                user_id=user.user_id,
            )
            if user_style:
                return user_style
        except Exception as e:
            logger.warning(f"Failed to read user reply_style from memory: {e}")

    # 优先级 1：子智能体/独立模式且配置了 reply_style
    if self.mode != AgentMode.MASTER and self.subagent_config:
        if self.subagent_config.reply_style:
            return self.subagent_config.reply_style

    # 优先级 2：全局默认
    return settings.agent.reply_style
```

用户记忆中 `reply_style` 的读写需要 `LongTermMemory` 新增两个方法：

```python
# src/memory/long_term.py 新增方法

def get_reply_style(self, tenant_id: str, user_id: str) -> Optional[str]:
    """读取用户记忆文件中的 reply_style 字段"""
    # 解析 frontmatter 中的 reply_style 字段，存在则返回，不存在返回 None

def set_reply_style(self, tenant_id: str, user_id: str, style_id: str) -> None:
    """写入用户记忆文件的 reply_style 字段"""
    # 更新或新增 frontmatter 中的 reply_style 字段
```

用户通过自然语言设定风格时的意图识别，在 `_handle_remember_intent()` 中扩展匹配模式：

```python
# 新增匹配模式
style_patterns = [
    r'记住.*回复风格[是为用]\s*(.+)',
    r'以后.*用(.+?)风格回复',
    r'我的回复风格[是为]\s*(.+)',
]
```

### 3.9 初始化时机

`StyleManager` 在 `Agent.__init__()` 中初始化：

```python
class Agent:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self.style_manager = StyleManager()
```

由于 `master_agent` 是全局单例，`StyleManager` 也只会初始化一次。

## 4. 内置风格定义

### 4.1 human-like（拟人风格）

**适用场景**：面向普通员工的日常助手，主智能体默认风格。

**核心原则**：
- 像真实同事一样对话，隐藏 AI 工作过程
- 直接给结论，不解释获取信息的途径
- 口语化但不过度，简洁有力
- 禁止暴露工具调用、技能加载等内部细节

（完整内容见 `src/prompts/styles/human-like.md`）

### 4.2 后续可扩展的风格（本次不实现）

| 风格 ID | 名称 | 说明 |
|---------|------|------|
| `professional` | 专业助手风格 | 信息密度高，展示推理过程，适合技术/管理层 |
| `concise` | 极简风格 | 只输出关键结果，一句废话都没有 |
| `detailed` | 详尽风格 | 详细展示每一步过程和依据 |

新增风格只需在 `src/prompts/styles/` 下添加对应的 `.md` 文件，无需改动代码。

## 5. 与现有系统的关系

### 5.1 与 extra.md（租户定制）的关系

`extra.md` 和 `reply_style` 是两个独立维度：
- `extra.md`：定义子智能体的**领域知识和人设**（如旅游顾问的专业知识、称呼方式）
- `reply_style`：定义**回复的呈现方式**（是否暴露工具调用、信息密度等）

两者可以同时生效，注入到系统提示词的不同位置。`extra.md` 在 `{subagent_constraint_section}` 中，`reply_style` 在末尾的 `{reply_style_section}` 中。

### 5.2 与 UserPreference.communication_style 的关系

`UserPreference.communication_style` 是**用户级别**的偏好，粒度较粗（只是一个字符串标签如 "neutral"），且当前未被 agent 循环使用。

本次设计选择在**长期记忆文件**的 frontmatter 中存储 `reply_style`，而非复用 `UserPreference.communication_style`，原因是：
1. `UserPreference` 是进程内内存模型（`MemoryManager` 维护），多 worker 下不共享
2. 长期记忆文件持久化在磁盘上，天然跨 worker 共享
3. 用户说"记住我的风格"时，写入长期记忆更符合语义直觉

`UserPreference.communication_style` 字段保留但不再使用，未来可考虑移除。

### 5.3 与 personality_traits 的关系

`agent_instances` 表的 `personality_traits` 是子智能体实例的性格标签（如 ["friendly", "patient"]），目前仅存储未使用。本次的 `reply_style` 是更完整的风格定义（包含具体的行为指令），与 `personality_traits` 互补但不冲突。

## 6. 风险与注意事项

1. **风格文件内容质量**：风格指令的效果高度依赖 prompt engineering 质量，需要实际测试调优
2. **Token 消耗**：风格内容会增加系统提示词长度（预计 200-400 tokens），需评估对成本的影响
3. **风格冲突**：如果 SUBAGENT.md 的 body 中已包含类似风格指令（如旅游顾问的人设），可能与注入的风格产生冲突。建议在既有子智能体中显式设置 `reply_style` 来统一管理，或从 body 中移除重复的风格描述
4. **热更新**：风格文件修改后需要重启服务才能生效（可通过 `StyleManager.reload()` 支持热更新，本次不实现）
