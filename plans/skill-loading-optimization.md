# Skill 加载机制优化设计文档

## 一、现状分析

### 1.1 当前问题

| 问题 | 现状 | 要求 |
|------|------|------|
| 主智能体 skill 过滤 | 加载所有 skills，无过滤 | 通过 allow 名单配置决定 |
| Skill 匹配机制 | 关键词匹配，准确性低 | LLM 语义匹配 |
| Skill 上下文传递 | Skill 接收需要的 files/variables | 仅传 skill 需要的上下文 |
| Skill 内容加载 | 按需加载 | 保持按需加载 |
| 历史记录 | skill 结果作为 tool 消息 | 只保留 skill 执行结果摘要 |

### 1.2 现有设计符合要求的部分

1. ✅ **Skill 内容按需加载** - `load_skill()` 只有在调用时才加载完整 SKILL.md
2. ✅ **Skill 执行上下文** - `execute_skill_command(files, variables)` 只接收需要的参数
3. ✅ **子智能体 skill 约束** - `SUBAGENT.md` 中已有 `skills.allowed` 配置

### 1.3 现有设计不符合要求的部分

1. ❌ **主智能体无 allow 名单** - `Agent.__init__` 直接加载所有 skills
2. ❌ **Tool definition 暴露所有 skills** - `get_skill_tool_definition()` 返回全部 skill 列表
3. ❌ **关键词匹配** - `match_by_keyword()` 不准确，应废弃

---

## 二、优化设计方案

### 2.1 主智能体 Skill Allow 名单机制

#### 配置文件扩展

```yaml
# configs/config.yaml 新增 section

# Skill 配置
skills:
  # 主智能体允许使用的 skills 列表
  # 如果为空或不存在，使用默认配置（所有 skills）
  master_agent:
    allowed:
      - pdf           # PDF 处理
      - excel         # Excel 处理
      - email         # 邮件处理
      - web_search    # 网页搜索
      - content_generate  # 内容生成
    # denied: []     # 可选，明确拒绝列表

  # 子智能体默认配置（可在 SUBAGENT.md 中覆盖）
  subagent:
    # 默认 allowed 列表，如果子智能体未配置则使用此列表
    default_allowed:
      - pdf
      - email
```

#### Agent 初始化修改

```python
# src/core/agent.py

class Agent:
    def __init__(self, ...):
        # 技能系统
        skills_dir = Path(__file__).parent.parent / "skills"
        self.skill_registry = SkillRegistry(skills_dir)

        # 加载主智能体允许的 skills 列表
        self.allowed_skills = self._load_allowed_skills()
        # 只加载允许的 skills
        self.skill_registry.load_from_directory(skills_dir, allowed=self.allowed_skills)

        self.skill_executor = SkillExecutor(self.skill_registry)
```

#### SkillRegistry 修改

```python
# src/core/skill_registry.py

class SkillRegistry:
    def __init__(self, skills_dir: Optional[Path] = None):
        self._skills: Dict[str, Skill] = {}
        self._allowed: Optional[Set[str]] = None  # allow 名单
        self._loader: Optional[SkillLoader] = None

    def load_from_directory(
        self,
        skills_dir: Path,
        allowed: Optional[List[str]] = None,
    ) -> int:
        """
        从目录加载 Skill

        Args:
            skills_dir: Skill 目录路径
            allowed: 允许加载的 skill 名称列表，None 表示不限制
        """
        self._loader = SkillLoader(skills_dir)
        self._allowed = set(allowed) if allowed else None

        # 只加载允许的 skills
        all_skills = self._loader.skills
        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in all_skills.items()
                if name in self._allowed
            }
        else:
            self._skills = all_skills

        self._build_indices()
        logger.info(f"SkillRegistry loaded {len(self._skills)} skills (allowed={allowed})")
        return len(self._skills)

    def is_allowed(self, skill_name: str) -> bool:
        """检查 skill 是否在允许列表中"""
        if self._allowed is None:
            return True  # 无限制时都允许
        return skill_name in self._allowed
```

### 2.2 Tool Definition 优化

#### 改进前（暴露所有 skills）

```python
def get_skill_tool_definition(self) -> Dict:
    descriptions = self.get_descriptions()  # 列出所有 skills
    return {
        "name": "use_skill",
        "description": f"""加载一个技能...

可用技能:
{descriptions}  # 问题：列出所有 skills
...
"""
    }
```

#### 改进后（只包含允许的 skills）

```python
def get_skill_tool_definition(self) -> Dict:
    """获取 Skill 工具定义（仅包含允许的 skills）"""
    if not self._skills:
        return {
            "name": "use_skill",
            "description": "暂无可用技能",
            "input_schema": {...}
        }

    # 只生成允许的 skills 描述
    skill_list = "\n".join(
        f"- {name}: {skill.description}"
        for name, skill in self._skills.items()
    )

    return {
        "name": "use_skill",
        "description": f"""当任务需要特定技能支持时使用此工具。

适用场景：
- 处理文件（PDF/Word/Excel）时
- 需要翻译、总结、OCR 等能力时
- 需要发送邮件、搜索信息时

可用技能：
{skill_list}

请描述你的任务，系统会自动为你匹配合适的技能。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "要加载的技能名称"
                }
            },
            "required": ["skill"]
        }
    }
```

### 2.3 LLM 语义匹配（替代关键词匹配）

#### SkillMatcher 服务

```python
# src/core/skill_matcher.py

class SkillMatcher:
    """
    基于 LLM 的 Skill 语义匹配器

    核心思路：不再依赖关键词，而是让 LLM 根据用户意图和 skill 描述来匹配合适的 skill
    """

    def __init__(
        self,
        llm: BaseLLM,
        skill_registry: SkillRegistry,
    ):
        self.llm = llm
        self.skill_registry = skill_registry

    async def match(
        self,
        user_intent: str,
        context: Dict[str, Any],
        allowed_skills: Optional[List[str]] = None,
    ) -> List[SkillMatch]:
        """
        根据用户意图匹配合适的 skills

        Args:
            user_intent: 用户的原始输入或意图描述
            context: 上下文信息（上传文件、会话历史等）
            allowed_skills: 允许匹配的 skills 列表（用于子智能体约束）

        Returns:
            匹配的 skills 列表，按相关度排序
        """
        # 1. 获取可用的 skill 描述
        available_skills = self._get_available_skill_descriptions(allowed_skills)

        if not available_skills:
            return []

        # 2. 构建匹配提示
        prompt = self._build_matching_prompt(user_intent, context, available_skills)

        # 3. 调用 LLM 匹配
        response = await self.llm.agenerate([prompt])

        return self._parse_matching_response(response)

    def _get_available_skill_descriptions(
        self,
        allowed_skills: Optional[List[str]] = None,
    ) -> List[Dict]:
        """获取可用的 skill 描述列表"""
        descriptions = []
        for name, skill in self.skill_registry.list_skills().items():
            # 应用 allow 过滤
            if allowed_skills is not None and name not in allowed_skills:
                continue
            descriptions.append({
                "name": name,
                "description": skill.metadata.get("description", ""),
            })
        return descriptions

    def _build_matching_prompt(
        self,
        user_intent: str,
        context: Dict[str, Any],
        available_skills: List[Dict],
    ) -> str:
        """构建匹配提示"""

        # 提取上下文中的文件信息
        file_info = ""
        if context.get("attachments"):
            files = []
            for att in context["attachments"]:
                files.append(f"- {att['name']} ({att.get('mime_type', 'unknown')})")
            file_info = "\n用户上传的文件：\n" + "\n".join(files)

        # 构建 skill 列表
        skill_list = "\n".join([
            f"- **{s['name']}**: {s['description']}"
            for s in available_skills
        ])

        prompt = f"""你是一个技能匹配专家。根据用户的意图，从以下可用技能中选择最合适的技能。

用户意图：{user_intent}
{file_info}

可用技能：
{skill_list}

请分析用户意图，选择最合适的技能（可以选0个或多个）。

输出格式（JSON数组）：
[
  {{"name": "技能名", "reason": "匹配原因", "confidence": 0.95}},
  ...
]

规则：
1. 只选择与用户意图明确相关的技能
2. confidence 是 0-1 之间的置信度
3. 如果没有合适的技能，返回空数组 []
4. 匹配原因要简短说明为什么这个技能适合当前任务
"""
        return prompt

    def _parse_matching_response(self, response: str) -> List[SkillMatch]:
        """解析 LLM 返回的匹配结果"""
        import json
        try:
            matches = json.loads(response)
            return [SkillMatch(**m) for m in matches]
        except:
            return []
```

### 2.4 Skill 执行上下文优化

#### 原则

**Skill 只接收自己需要的上下文，不接收 agent 的完整上下文**

```python
# Skill 执行时传递的上下文
skill_context = {
    "skill_name": "pdf",
    "task_description": "提取 PDF 中的表格数据",
    "files": {
        "report.pdf": base64_encoded_content  # Skill 需要处理的文件
    },
    "variables": {
        "output_format": "csv"  # Skill 需要的参数
    },
    # 注意：不包含 agent 的历史对话、内存等
}

# Agent 上下文不传递给 Skill
agent_context = {
    "history": [...],      # ❌ 不传递给 skill
    "memory": {...},        # ❌ 不传递给 skill
    "session_id": "...",    # ✅ 传给 skill（用于上下文隔离）
}
```

#### SkillExecutor 执行签名

```python
async def execute_skill_command(
    self,
    skill_name: str,
    command: str,
    files: Optional[Dict[str, bytes]] = None,  # Skill 需要的文件
    variables: Optional[Dict[str, Any]] = None,  # Skill 需要的变量
    session_id: Optional[str] = None,
) -> ExecutionResult:
    """
    执行 Skill 命令

    注意：这里只接收 Skill 需要的 files 和 variables，
    不接收 agent 的完整上下文（history、memory 等）
    """
    # ... 执行逻辑
```

### 2.5 历史记录优化

#### 原则

**Agent 历史只记录 skill 执行的结果摘要，不记录完整 skill 内容**

```python
# 改进前：完整 skill 内容注入历史
{
    "role": "tool",
    "tool_call_id": "xxx",
    "content": "<skill-loaded name=\"pdf\">\n# PDF处理技能\n...\n</skill-loaded>\n\nFollow the instructions..."
}

# 改进后：只记录执行结果摘要
{
    "role": "tool",
    "tool_call_id": "xxx",
    "content": "✅ Skill 'pdf' loaded successfully. 执行了 pdftotext 命令，提取了 5 页文本内容。"
}
```

#### `_handle_use_skill` 修改

```python
def _handle_use_skill(self, skill_name: str) -> Dict[str, Any]:
    """加载 skill 内容并返回摘要"""

    # 1. 检查是否在允许列表
    if not self.skill_registry.is_allowed(skill_name):
        return {
            "success": False,
            "error": f"Skill '{skill_name}' not allowed. Available: {list(self.skill_registry.list_skills())}"
        }

    # 2. 加载 skill 内容
    skill_content = self.skill_registry.get_content(skill_name)
    if skill_content is None:
        return {
            "success": False,
            "error": f"Skill '{skill_name}' not found"
        }

    # 3. 返回执行结果（而不是完整内容注入）
    return {
        "success": True,
        "skill_name": skill_name,
        "message": f"✅ Skill '{skill_name}' loaded. {skill.description}",
        "content": skill_content  # 完整内容仍返回，但由调用方决定如何使用
    }
```

---

## 三、子智能体 Skill 约束

### 3.1 SUBAGENT.md 配置

```yaml
# subagents/trade-specialist/SUBAGENT.md

skills:
  # 明确允许的 skills 列表
  allowed:
    - pdf
    - email
    - web_search
  # denied: []  # 可选，明确拒绝

# 约束逻辑：
# 1. 只允许使用列表中的 skills
# 2. 调用 SkillMatcher 时传入 allowed 列表
# 3. SkillRegistry 也应用相同过滤
```

### 3.2 执行时应用约束

```python
# src/core/agent.py - execute_as_subagent

async def execute_as_subagent(
    self,
    task_description: str,
    parent_session_id: str,
    task_record=None,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """作为子智能体执行任务"""

    # 1. 获取子智能体允许的 skills
    allowed_skills = self.subagent_config.get("skills", {}).get("allowed", None)

    # 2. 创建受限的 SkillRegistry
    if allowed_skills is not None:
        self.skill_registry = SkillRegistry(skills_dir)
        self.skill_registry.load_from_directory(skills_dir, allowed=allowed_skills)

    # 3. 后续执行时自动受约束
    ...
```

---

## 四、实施计划

### Phase 1: 主智能体 Allow 名单机制
- [ ] 扩展 `configs/config.yaml`，添加 `skills.master_agent.allowed` 配置
- [ ] 修改 `Agent.__init__`，读取配置并传递给 SkillRegistry
- [ ] 修改 `SkillRegistry.load_from_directory`，支持 `allowed` 参数过滤
- [ ] 修改 `get_skill_tool_definition`，只返回允许的 skills

### Phase 2: LLM 语义匹配
- [ ] 创建 `src/core/skill_matcher.py`
- [ ] 实现 `_build_matching_prompt` 和 `_parse_matching_response`
- [ ] 废弃 `match_by_keyword` 方法（或保留为兜底）

### Phase 3: 历史记录优化
- [ ] 修改 `_handle_use_skill`，返回执行结果摘要
- [ ] 确保 skill 完整内容不直接暴露到历史中

### Phase 4: 子智能体约束完善
- [ ] 确认 `SUBAGENT.md` 的 `skills.allowed` 配置被正确读取
- [ ] 在 `execute_as_subagent` 中应用 allowed 过滤

---

## 五、配置示例

### 5.1 主智能体配置

```yaml
# configs/config.yaml

skills:
  master_agent:
    allowed:
      - pdf
      - excel
      - email
      - web_search
      - content_generate
```

### 5.2 子智能体配置

```yaml
# subagents/trade-specialist/SUBAGENT.md

skills:
  allowed:
    - pdf
    - email
```

### 5.3 Skill 定义

```yaml
# skills/pdf/SKILL.md

---
name: pdf
category: file
description: 处理 PDF 文件的技能。适用于读取 PDF 内容、提取文本和表格、解析文档结构。
---

# PDF 处理技能

## 功能
- 读取 PDF 文本内容
- 提取表格数据
- 搜索 PDF 内容
```

---

## 六、风险与注意事项

1. **向后兼容**：现有 agent 配置需要平滑迁移
2. **配置优先级**：子智能体 SUBAGENT.md 中的 allowed 应覆盖全局配置
3. **调试日志**：添加详细的 skill 加载和匹配日志
4. **性能考虑**：LLM 语义匹配有延迟，可考虑缓存

---

**请审核以上设计文档，如有问题或需要调整，请反馈意见。**