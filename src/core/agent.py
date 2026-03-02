#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Master Agent - LLM-Driven Agent Loop

The agent uses LLM for:
1. Intent understanding
2. Task planning and decomposition
3. Deciding which tool/agent/skill to call
4. Executing tools and integrating results
5. Loading and executing Skills in sandbox environment
"""

import json
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator
from loguru import logger

from src.config.settings import settings
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.memory.short_term import ShortTermMemory
from src.models.message import UnifiedMessage
from src.models.user import User
from src.models.plan import TaskStatus
from src.core.skill_registry import SkillRegistry
from src.core.skill_executor import SkillExecutor
from src.core.sandbox import SandboxManager
from src.core.plan_manager import PlanManager


# Tool definitions for LLM function calling
AGENT_TOOLS = [
    {
        "name": "email_send",
        "description": "发送邮件给收件人",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "收件人邮箱地址列表"
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题"
                },
                "body": {
                    "type": "string",
                    "description": "邮件正文内容"
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "抄送收件人（可选）"
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_read",
        "description": "读取收件箱中的邮件",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "要读取的文件夹（inbox、sent等）",
                    "default": "inbox"
                },
                "limit": {
                    "type": "integer",
                    "description": "要获取的邮件数量",
                    "default": 10
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "是否只获取未读邮件",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": "在网络上搜索信息。重要：搜索关键词必须与用户提问的语言保持一致（用户用中文提问则用中文关键词搜索）",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词，必须与用户提问语言一致"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5
                }
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "ocr_image",
        "description": "使用OCR从图片中提取文字",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "图片文件路径"
                },
                "language": {
                    "type": "string",
                    "description": "OCR识别语言（如：ch表示中文，en表示英文）",
                    "default": "ch"
                }
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "doc_summarize",
        "description": "总结文档内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要总结的文档内容"
                },
                "max_length": {
                    "type": "integer",
                    "description": "摘要最大长度",
                    "default": 500
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "doc_translate",
        "description": "将文本翻译为其他语言",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要翻译的文本"
                },
                "target_lang": {
                    "type": "string",
                    "description": "目标语言（如：en表示英文，ja表示日文，ko表示韩文）"
                }
            },
            "required": ["text", "target_lang"]
        }
    },
    {
        "name": "clarify",
        "description": "当信息缺失时向用户询问澄清",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题"
                },
                "missing_info": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "缺失的信息项列表"
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "create_plan",
        "description": "为复杂任务创建执行计划。重要：如果任务属于专业领域（如招聘、代码审查、PDF处理），应该优先将步骤的tool设为delegate_to_subagent，而不是自己处理",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "任务的整体目标"
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_number": {
                                "type": "integer",
                                "description": "计划中的步骤编号"
                            },
                            "description": {
                                "type": "string",
                                "description": "该步骤的描述"
                            },
                            "tool": {
                                "type": "string",
                                "description": "该步骤要使用的工具。如果任务需要专业领域能力，应该使用delegate_to_subagent并指定subagent_name参数"
                            },
                            "parameters": {
                                "type": "object",
                                "description": "工具的参数。委派子智能体时，参数格式为{\"subagent_name\": \"子智能体名称\", \"task_description\": \"任务描述\"}"
                            },
                            "expected_output": {
                                "type": "string",
                                "description": "该步骤的预期输出"
                            }
                        },
                        "required": ["step_number", "description"]
                    },
                    "description": "要执行的步骤列表"
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["sequential", "parallel"],
                    "description": "如何执行步骤",
                    "default": "sequential"
                }
            },
            "required": ["goal", "steps"]
        }
    },
    {
        "name": "skill_execute",
        "description": "在技能的沙箱环境中执行命令。加载技能后使用此功能运行pdftotext、python脚本等命令。重要：使用简单命令，对于Python优先使用简单的一行命令或直接使用pypdf/pdfplumber",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "要使用的技能上下文名称"
                },
                "command": {
                    "type": "string",
                    "description": "要执行的命令。使用简单格式。例如：python -c \"from pypdf import PdfReader; r = PdfReader('file.pdf'); print(r.pages[0].extract_text())\""
                },
                "files": {
                    "type": "object",
                    "description": "可选的文件，使其在沙箱中可用（文件名 -> base64内容）",
                    "additionalProperties": {
                        "type": "string"
                    }
                }
            },
            "required": ["skill", "command"]
        }
    }
]


class MasterAgent:
    """
    Master Agent - LLM-driven agent with tool calling capability
    
    The agent loop:
    1. Receive user message
    2. LLM understands intent and plans tasks
    3. LLM decides which tools to call
    4. Execute tools and return results to LLM
    5. LLM integrates results and responds
    6. Repeat until task complete
    """
    
    def __init__(self):
        self.llm = llm_gateway
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry)
        self.memory = ShortTermMemory()
        
        skills_dir = Path(__file__).parent.parent / "skills"
        self.skill_registry = SkillRegistry(skills_dir)
        self.sandbox_manager = SandboxManager(prefer_docker=False)
        self.skill_executor = SkillExecutor(self.skill_registry, self.sandbox_manager)
        
        # 初始化计划管理器
        plans_dir = Path(__file__).parent.parent.parent / "plans"
        self.plan_manager = PlanManager(plans_dir)
        
        # 初始化子智能体注册表
        subagents_dir = Path(__file__).parent.parent.parent / "subagents"
        from src.subagents.registry import SubagentRegistry
        from src.subagents.executor import SubagentExecutor
        self.subagent_registry = SubagentRegistry(subagents_dir)
        # 注意：SubagentExecutor需要在_register_builtin_tools之后初始化，以便传递工具注册表
        # 所以这里先创建注册表，执行器在后面创建
        self.subagent_executor = None  # 延迟初始化
        
        self._register_builtin_tools()
        
        # 初始化子智能体执行器（需要先注册工具）
        self.subagent_executor = SubagentExecutor(
            self.memory, 
            self.subagent_registry,
            self.tool_registry,  # 传递工具注册表
            self.skill_registry  # 传递技能注册表
        )
        
        logger.info(f"Master Agent initialized with {len(self.skill_registry)} skills, {len(self.subagent_registry)} subagents")
    
    def _register_builtin_tools(self):
        """Register built-in tools"""
        from src.tools.email.email_tool import EmailSendTool, EmailReadTool
        from src.tools.ocr.ocr_tool import OCRImageTool, OCRPdfTool
        from src.tools.document.doc_tool import DocSummarizeTool, DocTranslateTool
        from src.tools.search.search_tool import WebSearchTool
        
        self.tool_registry.register(EmailSendTool())
        self.tool_registry.register(EmailReadTool())
        self.tool_registry.register(OCRImageTool())
        self.tool_registry.register(OCRPdfTool())
        self.tool_registry.register(DocSummarizeTool())
        self.tool_registry.register(DocTranslateTool())
        self.tool_registry.register(WebSearchTool())
        
        logger.info(f"Registered {len(self.tool_registry._tools)} tools")
    
    def _get_tools(self) -> List[Dict[str, Any]]:
        """Get tool definitions including skill tool and delegation tool"""
        tools = list(AGENT_TOOLS)
        
        if self.skill_registry:
            skill_tool = self.skill_registry.get_skill_tool_definition()
            tools.append(skill_tool)
        
        # 添加子智能体委派工具
        if self.subagent_registry and len(self.subagent_registry) > 0:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
            if delegation_tool:
                tools.append(delegation_tool)
        
        return tools
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        """Build system prompt for the agent"""
        
        skill_descriptions = self.skill_registry.get_descriptions() if self.skill_registry else "(暂无可用技能)"
        available_tools = [t["name"] for t in AGENT_TOOLS]
        available_skills = self.skill_registry.list_skills() if self.skill_registry else []
        
        # 子智能体信息
        subagent_descriptions = self.subagent_registry.get_descriptions() if self.subagent_registry else "(暂无可用子智能体)"
        available_subagents = self.subagent_registry.list_subagents() if self.subagent_registry else []
        
        # 委派工具说明
        delegation_guide = ""
        if available_subagents:
            delegation_guide = f"""
### delegate_to_subagent（委派给专业子智能体）
当任务需要专业领域能力时，可以委派给子智能体：

**可用子智能体：**
{subagent_descriptions}

**使用场景：**
- 代码审查任务 → 委派给 `code-reviewer`
- HR相关任务 → 委派给 `hr-expert`
- PDF文档处理 → 委派给 `pdf-expert`

**调用示例：**
```
delegate_to_subagent(
    subagent_name="code-reviewer",
    task_description="审查这段Python代码的安全性和性能"
)
```
"""
        
        # 构建子智能体快速匹配提示
        subagent_matching_hint = ""
        if available_subagents:
            subagent_matching_hint = f"""
**⚡ 关键：优先判断是否需要委派子智能体**
在分析需求时，首先检查任务是否属于以下专业领域：
{subagent_descriptions}

如果任务匹配某个子智能体的能力描述，**应该优先委派**，而不是自己处理。
例如：
- 招聘、薪酬、员工管理相关 → 委派给 `hr-expert`
- 代码审查、安全审计 → 委派给 `code-reviewer`
- PDF文档处理 → 委派给 `pdf-expert`
"""

        prompt = f"""你是一个智能工作助手。你的任务是帮助用户完成各种工作任务。

## 🚨 核心工作流程（必须严格遵守）

**每个用户请求都必须遵循以下流程：**
{subagent_matching_hint}
### 第一步：分析需求
1. 理解用户想要什么
2. **首先判断是否匹配专业子智能体**（如招聘任务匹配hr-expert）
3. 判断是否需要使用工具
4. 确定需要哪些工具/技能/子智能体

### 第二步：创建执行计划
**重要：除简单问候外，所有任务都必须先调用 `create_plan` 创建计划！**

调用 `create_plan` 时需要提供：
- goal: 任务目标（用户需求的总结）
- steps: 执行步骤列表，每个步骤包含：
  - step_number: 步骤编号
  - description: 步骤描述
  - tool: 使用的工具名称（如果需要委派子智能体，工具填写 `delegate_to_subagent`）
  - parameters: 工具参数（委派时参数为 `{{"subagent_name": "子智能体名称", "task_description": "任务描述"}}`）
  - expected_output: 预期输出
- execution_mode: "sequential"（顺序）或 "parallel"（并行）

### 第三步：执行计划
1. 按计划顺序执行每个步骤
2. 调用相应的工具或委派给子智能体
3. 收集并整合结果

### 第四步：汇报结果
1. 总结执行结果
2. 展示关键信息
3. 如有失败，说明原因和建议

---

## 重要语言规则

**你必须始终使用与用户提问相同的语言进行回复和工具调用！**
- 用户用中文提问 → 你用中文回复，工具参数使用中文
- 用户用英文提问 → 你用英文回复，工具参数使用英文
- 搜索关键词必须与用户提问语言保持一致！

---

## 能力范围与限制

### 可用工具
{', '.join([f'`{t}`' for t in available_tools])}

### 可用技能
{skill_descriptions}

### 可用子智能体
{subagent_descriptions}

### 超出能力的处理
当用户的请求超出你的能力范围时：
1. **明确告知用户**：说明这个任务无法完成
2. **解释原因**：说明缺少什么能力或工具
3. **提供替代方案**：
   - 推荐用户可以使用的其他工具或服务
   - 建议如何分步骤完成任务
   - 指出完成该任务需要的条件

**示例回应：**
> "抱歉，我目前无法直接执行XXX操作。完成这个任务需要：
> 1. XXX工具/权限
> 2. 或者您可以尝试使用YYY服务
> 3. 或者您可以先ZZZ，然后我可以帮助您..."

---

## 工具使用指南

### create_plan（必须首先使用）
**所有非问候类请求都必须先创建计划！**

```
create_plan(
    goal="用户的目标",
    steps=[
        {{"step_number": 1, "description": "步骤描述", "tool": "工具名", "parameters": {{}}, "expected_output": "预期输出"}},
        ...
    ],
    execution_mode="sequential"
)
```

### web_search
- 关键词必须与用户语言一致
- 用于查询实时信息、新闻、数据等

### use_skill
- 当任务匹配技能描述时使用
- 加载后按技能指导执行

### skill_execute
- 用于执行技能中的命令
- 处理文件、运行脚本等

### clarify
- 当信息不足时向用户询问
{delegation_guide}
---

## 工作示例

**示例1：搜索信息**
用户: "今年春节贺岁档有哪些电影"
1. 调用 create_plan(goal="查找春节电影信息", steps=[...], execution_mode="sequential")
2. 调用 web_search(keyword="2026年春节贺岁档电影")
3. 整理结果并回复用户

**示例2：HR招聘任务（必须委派给hr-expert）**
用户: "我要招聘一名AI产品经理"
1. 分析：这是招聘任务，匹配hr-expert子智能体的能力
2. 调用 create_plan(
     goal="协助招聘AI产品经理",
     steps=[{{"step_number": 1, "description": "委派给HR专家处理招聘任务", "tool": "delegate_to_subagent", "parameters": {{"subagent_name": "hr-expert", "task_description": "协助招聘AI产品经理，包括JD编写、薪酬调研、面试设计"}}, "expected_output": "招聘方案"}}],
     execution_mode="sequential"
   )
3. 调用 delegate_to_subagent(subagent_name="hr-expert", task_description="协助招聘AI产品经理...")
4. 整合子智能体的结果并回复用户

**示例3：代码审查任务（必须委派给code-reviewer）**
用户: "帮我审查这段代码的安全性"
1. 分析：这是代码审查任务，匹配code-reviewer子智能体的能力
2. 调用 create_plan(
     goal="代码安全审查",
     steps=[{{"step_number": 1, "description": "委派给代码审查专家", "tool": "delegate_to_subagent", "parameters": {{"subagent_name": "code-reviewer", "task_description": "审查代码安全性"}}, "expected_output": "审查报告"}}],
     execution_mode="sequential"
   )
3. 调用 delegate_to_subagent(subagent_name="code-reviewer", task_description="审查代码安全性")
4. 整合子智能体的审查结果并回复用户

**示例4：PDF文档处理（必须委派给pdf-expert）**
用户: "帮我提取这个PDF中的表格数据"
1. 分析：这是PDF处理任务，匹配pdf-expert子智能体的能力
2. 调用 create_plan(
     goal="提取PDF表格数据",
     steps=[{{"step_number": 1, "description": "委派给PDF专家处理", "tool": "delegate_to_subagent", "parameters": {{"subagent_name": "pdf-expert", "task_description": "提取PDF中的表格数据"}}, "expected_output": "表格数据"}}],
     execution_mode="sequential"
   )
3. 调用 delegate_to_subagent(subagent_name="pdf-expert", task_description="提取PDF表格数据")
4. 整合结果并回复用户

**示例5：超能力范围**
用户: "帮我订一张机票"
回复: "抱歉，我目前无法直接预订机票。建议您使用携程、去哪儿等平台，或者我可以帮您搜索航班信息。"

---

## 指导原则

- **先规划后执行**：除简单问候外，必须先创建执行计划
- **透明化**：让用户知道你在做什么，展示计划
- **诚实**：超出能力时明确告知，不要虚假承诺
- **有帮助**：即使无法完成，也要提供有用的建议
- **跟踪进度**：计划会被记录，用户可以查看进度
- **善用专家**：专业任务委派给专业子智能体

高效使用工具完成任务。在行动前始终思考任务要求。
"""
        
        if user:
            prompt += f"\n\n## 当前用户\n姓名: {user.name}\nID: {user.user_id}\n"
        
        return prompt
    
    def _build_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """Build message list for LLM from memory"""
        messages = []
        
        history = self.memory.get_context(session_id)
        for msg in history:
            # 处理不同类型的消息
            role = msg.get("role", "user")
            
            if role == "tool":
                # 工具结果消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", "")
                })
            elif role == "assistant" and "tool_calls" in msg:
                # 包含工具调用的assistant消息
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": msg.get("tool_calls", [])
                })
            else:
                # 普通消息
                messages.append({
                    "role": role,
                    "content": msg.get("content", "")
                })
        
        return messages
    
    def _handle_create_plan(
        self,
        args: Dict[str, Any],
        session_id: str,
        user_query: str,
    ) -> Dict[str, Any]:
        """
        Handle create_plan tool call - create real ExecutionPlan and save to MD file
        
        Args:
            args: Plan arguments containing goal, steps, and execution_mode
            session_id: Session identifier
            user_query: Original user query
        
        Returns:
            Plan result dictionary with plan summary
        """
        goal = args.get("goal", user_query)
        steps = args.get("steps", [])
        execution_mode = args.get("execution_mode", "sequential")
        
        # 检查步骤是否使用了不可用的工具
        available_tools = [t["name"] for t in AGENT_TOOLS]
        available_skills = self.skill_registry.list_skills() if self.skill_registry else []
        available_subagents = self.subagent_registry.list_subagents() if self.subagent_registry else []
        
        unavailable_tools = []
        for step in steps:
            tool = step.get("tool", "")
            if tool and tool not in available_tools and tool not in available_skills:
                # skill_execute 和 delegate_to_subagent 是特殊工具
                if not tool.startswith("skill_") and tool != "delegate_to_subagent":
                    # 检查是否是可用的子智能体
                    if tool not in available_subagents:
                        unavailable_tools.append(tool)
        
        # 创建真实的执行计划
        plan = self.plan_manager.create_plan(
            session_id=session_id,
            user_query=user_query,
            steps=steps,
            execution_mode=execution_mode,
            available_tools=available_tools,
            available_skills=available_skills,
        )
        
        # 构建计划展示输出
        plan_output = []
        plan_output.append("=" * 60)
        plan_output.append("📋 执行计划 (Execution Plan)")
        plan_output.append("=" * 60)
        plan_output.append(f"🎯 目标: {goal}")
        plan_output.append(f"🆔 计划ID: {plan.plan_id}")
        plan_output.append(f"🔄 执行模式: {execution_mode}")
        plan_output.append(f"📝 步骤数: {len(steps)}")
        plan_output.append("-" * 60)
        plan_output.append("📝 步骤详情:")
        
        for i, step in enumerate(steps, 1):
            description = step.get("description", "")
            tool = step.get("tool", "N/A")
            params = step.get("parameters", {})
            expected = step.get("expected_output", "")
            
            plan_output.append(f"\n  步骤 {i}: {description}")
            if tool != "N/A":
                plan_output.append(f"    🔧 工具: {tool}")
                if params:
                    plan_output.append(f"    📊 参数: {json.dumps(params, ensure_ascii=False)}")
                if expected:
                    plan_output.append(f"    📤 预期输出: {expected}")
        
        plan_output.append("-" * 60)
        
        # 如果是简单任务（只有一个步骤），提示可以直接执行
        if len(steps) == 1:
            plan_output.append("✅ 单步任务，直接执行...")
        else:
            plan_output.append("✅ 计划创建完成，开始按步骤执行...")
        
        plan_output.append("=" * 60)
        
        # 如果有不可用的工具，添加警告
        if unavailable_tools:
            plan_output.append("\n⚠️ 注意: 以下工具不可用:")
            for tool in unavailable_tools:
                plan_output.append(f"  - {tool}")
            plan_output.append("\n建议: 这些能力可能需要其他方式实现，请参考可用工具和技能列表。")
        
        # Print to log
        plan_str = "\n".join(plan_output)
        logger.info(f"\n{plan_str}")
        
        # Also print to console for visibility
        print(plan_str)
        
        # 返回结果
        result = {
            "success": True,
            "plan_id": plan.plan_id,
            "plan": {
                "goal": goal,
                "steps": steps,
                "execution_mode": execution_mode
            },
            "message": f"计划创建成功，共{len(steps)}个步骤。计划已保存到: plans/{session_id}.md",
            "is_simple_task": len(steps) == 1,
        }
        
        if unavailable_tools:
            result["warnings"] = {
                "unavailable_tools": unavailable_tools,
                "suggestion": "部分工具不可用，请检查或寻找替代方案"
            }
        
        return result
    
    def _handle_use_skill(self, skill_name: str) -> Dict[str, Any]:
        """
        Handle use_skill tool call - load skill content and return it
        
        Args:
            skill_name: Name of the skill to load
        
        Returns:
            Skill content dictionary
        """
        if not skill_name:
            return {
                "success": False,
                "error": "No skill name provided",
                "available_skills": self.skill_registry.list_skills() if self.skill_registry else []
            }
        
        if not self.skill_registry:
            return {
                "success": False,
                "error": "Skill registry not initialized"
            }
        
        skill_content = self.skill_registry.get_content(skill_name)
        
        if skill_content is None:
            available = self.skill_registry.list_skills()
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found",
                "available_skills": available
            }
        
        logger.info(f"Loaded skill: {skill_name}")
        
        return {
            "success": True,
            "skill_name": skill_name,
            "content": skill_content,
            "message": f"Skill '{skill_name}' loaded successfully. Follow the instructions in the skill content to complete the task."
        }
    
    async def _handle_skill_execute(
        self,
        skill_name: str,
        command: str,
        files: Optional[Dict[str, str]] = None,
        session_id: Optional[str] = None,
        workdir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Handle skill_execute tool call - execute command in sandbox
        
        Args:
            skill_name: Name of the skill
            command: Command to execute
            files: Optional files dict (filename -> base64 content)
            session_id: Session ID for context isolation
            workdir: Working directory for command execution
            
        Returns:
            Execution result dictionary
        """
        import base64
        
        if not skill_name:
            return {
                "success": False,
                "error": "No skill name provided"
            }
        
        if not command:
            return {
                "success": False,
                "error": "No command provided"
            }
        
        skill = self.skill_registry.get(skill_name)
        if not skill:
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found",
                "available_skills": self.skill_registry.list_skills()
            }
        
        decoded_files = {}
        if files:
            for filename, content_b64 in files.items():
                try:
                    decoded_files[filename] = base64.b64decode(content_b64)
                except Exception as e:
                    logger.warning(f"Failed to decode file {filename}: {e}")
        
        try:
            if workdir and workdir.exists():
                result = await self.sandbox_manager.execute_command(
                    command,
                    skill.sandbox_config,
                    workdir
                )
            else:
                result = await self.skill_executor.execute_skill_command(
                    skill_name=skill_name,
                    command=command,
                    files=decoded_files if decoded_files else None,
                    session_id=session_id
                )
            
            return {
                "success": result.success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "duration": result.duration,
                "timed_out": result.timed_out,
                "error": result.error
            }
            
        except Exception as e:
            logger.error(f"Skill execute failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _handle_delegate_to_subagent(
        self,
        subagent_name: str,
        task_description: str,
        context_needed: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle delegate_to_subagent tool call - delegate task to a subagent
        
        Args:
            subagent_name: Name of the subagent to delegate to
            task_description: Description of the task
            context_needed: Keywords for context filtering (optional)
            session_id: Session ID for memory access
            
        Returns:
            Delegation result dictionary
        """
        if not subagent_name:
            return {
                "success": False,
                "error": "No subagent name provided"
            }
        
        if not task_description:
            return {
                "success": False,
                "error": "No task description provided"
            }
        
        # Check if subagent exists
        config = self.subagent_registry.get(subagent_name)
        if not config:
            return {
                "success": False,
                "error": f"Subagent '{subagent_name}' not found",
                "available_subagents": self.subagent_registry.list_subagents()
            }
        
        try:
            # Generate task ID
            import uuid
            task_id = f"delegate_{uuid.uuid4().hex[:8]}"
            
            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
            )
            
            if not response.success:
                return {
                    "success": False,
                    "error": response.error or "Delegation failed"
                }
            
            # Wait for result
            record = await self.subagent_executor.wait_for_result(
                response.execution_id,
                timeout=300  # 5 minutes timeout
            )
            
            if record:
                return {
                    "success": record.status == "completed",
                    "subagent_name": subagent_name,
                    "execution_id": response.execution_id,
                    "result": record.result,
                    "summary": record.summary,
                    "error": record.error,
                    "token_usage": record.token_usage
                }
            
            return {
                "success": False,
                "error": "Delegation timed out"
            }
            
        except Exception as e:
            logger.error(f"Delegation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def process_message(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and yield response chunks
        
        This is the main agent loop:
        1. Build context from memory
        2. Check for file attachments and auto-load relevant skills
        3. Save uploaded files to skill workspace
        4. Call LLM with tools
        5. Execute tool calls if any
        6. Return results to LLM
        7. Yield final response
        
        Args:
            user_input: User's text input
            session_id: Session identifier
            user: Optional user information
            attachments: Optional list of file attachments, each with:
                - type: attachment type (e.g., "file", "image")
                - name: filename
                - url: file URL or path (optional)
                - mime_type: MIME type (optional)
                - content: base64 encoded file content (optional)
        """
        import base64
        import tempfile
        from datetime import datetime
        
        logger.info(f"Processing message for session {session_id}: {user_input[:50]}...")
        
        # Add timestamp context to help LLM understand current time
        current_time = datetime.now()
        timestamp_context = (
            f"[当前时间: {current_time.strftime('%Y年%m月%d日 %H:%M:%S')}, "
            f"{current_time.strftime('%A')}, "
            f"今年是{current_time.year}年]\n\n"
        )
        
        enhanced_input = timestamp_context + user_input
        auto_loaded_skill = None
        uploaded_files_info = []
        session_workspace = None
        
        if attachments:
            attachment_info = []
            for att in attachments:
                att_type = att.get("type", "file")
                att_name = att.get("name", "unknown")
                att_url = att.get("url", "")
                att_mime = att.get("mime_type", "")
                att_content = att.get("content", "")
                
                attachment_info.append(f"- {att_name} ({att_type}, {att_mime or 'unknown type'})")
                
                if self.skill_registry:
                    matched_skill = self.skill_registry.match_by_file(att_name)
                    if matched_skill:
                        auto_loaded_skill = matched_skill
                        logger.info(f"Auto-matched skill '{matched_skill}' for file: {att_name}")
                
                if att_content or att_url:
                    if session_workspace is None:
                        session_workspace = Path(tempfile.mkdtemp(prefix=f"skill_ws_{session_id}_"))
                        logger.info(f"Created session workspace: {session_workspace}")
                    
                    file_path = session_workspace / att_name
                    
                    try:
                        if att_content:
                            file_bytes = base64.b64decode(att_content)
                            file_path.write_bytes(file_bytes)
                            uploaded_files_info.append({
                                "name": att_name,
                                "path": str(file_path),
                                "size": len(file_bytes)
                            })
                            logger.info(f"Saved uploaded file: {file_path} ({len(file_bytes)} bytes)")
                        elif att_url and Path(att_url).exists():
                            import shutil
                            shutil.copy(att_url, file_path)
                            uploaded_files_info.append({
                                "name": att_name,
                                "path": str(file_path),
                                "size": file_path.stat().st_size
                            })
                            logger.info(f"Copied file from URL: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to save file {att_name}: {e}")
            
            if attachment_info:
                enhanced_input = timestamp_context + f"{user_input}\n\n[Attachments]\n" + "\n".join(attachment_info)
        
        self.memory.add(session_id, "user", enhanced_input)
        
        messages = self._build_messages(session_id)
        system_prompt = self._build_system_prompt(user)
        
        if auto_loaded_skill:
            skill_content = self.skill_registry.get_content(auto_loaded_skill)
            if skill_content:
                files_context = ""
                if uploaded_files_info:
                    files_context = "\n\n**Uploaded files available for processing:**\n"
                    for f in uploaded_files_info:
                        files_context += f"- `{f['name']}` at path: `{f['path']}` ({f['size']} bytes)\n"
                    files_context += "\nUse the `skill_execute` tool to run commands on these files.\n"
                    files_context += f"Session workspace: `{session_workspace}`\n"
                
                skill_injection = f"""<skill-auto-loaded name="{auto_loaded_skill}">
{skill_content}
</skill-auto-loaded>

The above skill has been automatically loaded because you received a file that matches this skill. 
{files_context}
Analyze the user's request and choose the appropriate method from the skill to process the file.
Use `skill_execute` tool to run commands like pdftotext, python scripts, etc."""

                messages.append({
                    "role": "user",
                    "content": skill_injection
                })
                logger.info(f"Auto-injected skill '{auto_loaded_skill}' into conversation with {len(uploaded_files_info)} files")
        
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Agent iteration {iteration}")
            
            tools = self._get_tools()
            
            logger.debug(f"\n{'='*60}\n"
                        f"[DEBUG] Agent Iteration {iteration} - Full Prompt\n"
                        f"{'='*60}\n"
                        f"[System Prompt]:\n{system_prompt}\n"
                        f"{'-'*60}\n"
                        f"[Messages]:\n{json.dumps(messages, ensure_ascii=False, indent=2)}\n"
                        f"{'-'*60}\n"
                        f"[Tools]: {json.dumps([t.get('name', t.get('function', {}).get('name', 'unknown')) for t in tools], ensure_ascii=False)}\n"
                        f"{'='*60}")
            
            response = await self.llm.chat_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools
            )
            
            tool_calls = response.get("tool_calls", [])
            content = response.get("content", "")
            
            logger.debug(f"\n{'='*60}\n"
                        f"[DEBUG] LLM Response - Iteration {iteration}\n"
                        f"{'='*60}\n"
                        f"[Content]:\n{content if content else '(None)'}\n"
                        f"{'-'*60}\n"
                        f"[Tool Calls]: {len(tool_calls)} call(s)\n"
                        f"{json.dumps(tool_calls, ensure_ascii=False, indent=2) if tool_calls else '(None)'}\n"
                        f"{'='*60}")
            
            # Filter out empty tool calls and parse tool info
            valid_tool_calls = []
            for tc in tool_calls:
                # Handle both OpenAI format (tc["function"]["name"]) and simplified format (tc["name"])
                if "function" in tc:
                    tool_name = tc["function"].get("name", "")
                    # arguments might be a JSON string, parse it
                    args_raw = tc["function"].get("arguments", "{}")
                    if isinstance(args_raw, str):
                        try:
                            tool_args = json.loads(args_raw) if args_raw else {}
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse tool arguments: {args_raw}")
                            tool_args = {}
                    else:
                        tool_args = args_raw
                else:
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("arguments", {})
                
                # Skip empty tool names
                if not tool_name:
                    logger.warning(f"Skipping tool call with empty name, args: {tool_args}")
                    continue
                
                valid_tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": tool_name,
                    "arguments": tool_args
                })
            
            # If no valid tool calls, we're done
            if not valid_tool_calls:
                # Store assistant response in memory
                self.memory.add(session_id, "assistant", content)
                
                # Yield the final response
                if content:
                    yield content
                break
            
            # Add assistant message with tool calls to history
            assistant_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls
            }
            messages.append(assistant_message)
            
            # Save assistant message with tool calls to memory
            self.memory.add_message(session_id, assistant_message)
            
            # Execute each tool call
            tool_results = []
            for tc in valid_tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                tool_id = tc["id"]
                
                logger.info(f"Executing tool: {tool_name} with args: {json.dumps(tool_args, ensure_ascii=False)}")
                
                # Handle create_plan specially - create real plan and save to MD
                if tool_name == "create_plan":
                    plan_result = self._handle_create_plan(
                        args=tool_args,
                        session_id=session_id,
                        user_query=user_input,
                    )
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": plan_result
                    })
                    continue
                
                # Handle clarify - ask user for clarification (no external tool needed)
                if tool_name == "clarify":
                    question = tool_args.get("question", "")
                    missing_info = tool_args.get("missing_info", [])
                    logger.info(f"Clarify tool called: question={question[:50]}...")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": {
                            "success": True,
                            "question": question,
                            "missing_info": missing_info
                        }
                    })
                    continue
                
                # Handle use_skill - load skill content and inject into conversation
                if tool_name == "use_skill":
                    skill_name = tool_args.get("skill", "")
                    skill_result = self._handle_use_skill(skill_name)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_result
                    })
                    continue
                
                # Handle skill_execute - execute command in sandbox
                if tool_name == "skill_execute":
                    skill_name = tool_args.get("skill", "")
                    command = tool_args.get("command", "")
                    files = tool_args.get("files", {})
                    
                    # 标记任务开始（如果计划中存在）
                    plan = self.plan_manager.get_plan(session_id)
                    skill_task_id = None
                    if plan:
                        task = self.plan_manager.get_next_pending_task(session_id)
                        if task and task.tool_name == "skill_execute":
                            skill_task_id = task.task_id
                            self.plan_manager.mark_task_running(session_id, skill_task_id)
                    
                    skill_exec_result = await self._handle_skill_execute(
                        skill_name=skill_name,
                        command=command,
                        files=files,
                        session_id=session_id,
                        workdir=session_workspace
                    )
                    
                    # 标记任务完成（使用保存的task_id）
                    if skill_task_id:
                        if skill_exec_result.get("success"):
                            self.plan_manager.mark_task_completed(
                                session_id, skill_task_id, skill_exec_result
                            )
                        else:
                            self.plan_manager.mark_task_failed(
                                session_id, skill_task_id, 
                                skill_exec_result.get("error", "Unknown error")
                            )
                    
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_exec_result
                    })
                    continue
                
                # Handle delegate_to_subagent - delegate task to subagent
                if tool_name == "delegate_to_subagent":
                    subagent_name = tool_args.get("subagent_name", "")
                    task_description = tool_args.get("task_description", "")
                    context_needed = tool_args.get("context_needed", [])
                    
                    logger.info(f"Delegating to subagent: {subagent_name}, task: {task_description[:50]}...")
                    
                    # 标记任务开始（如果计划中存在）
                    plan = self.plan_manager.get_plan(session_id)
                    delegate_task_id = None
                    if plan:
                        task = self.plan_manager.get_next_pending_task(session_id)
                        if task:
                            delegate_task_id = task.task_id
                            self.plan_manager.mark_task_running(session_id, delegate_task_id)
                    
                    # 执行委派
                    delegation_result = await self._handle_delegate_to_subagent(
                        subagent_name=subagent_name,
                        task_description=task_description,
                        context_needed=context_needed,
                        session_id=session_id,
                    )
                    
                    # 标记任务完成
                    if delegate_task_id:
                        if delegation_result.get("success"):
                            self.plan_manager.mark_task_completed(
                                session_id, delegate_task_id, delegation_result
                            )
                        else:
                            self.plan_manager.mark_task_failed(
                                session_id, delegate_task_id,
                                delegation_result.get("error", "Unknown error")
                            )
                    
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": delegation_result
                    })
                    continue
                
                # 获取当前计划中的任务（用于状态跟踪）
                plan = self.plan_manager.get_plan(session_id)
                current_task_id = None
                if plan:
                    current_task = self.plan_manager.get_next_pending_task(session_id)
                    if current_task:
                        current_task_id = current_task.task_id
                        self.plan_manager.mark_task_running(session_id, current_task_id)
                
                # Execute the tool
                try:
                    result = await self.tool_executor.execute(tool_name, tool_args)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": result
                    })
                    result_preview = str(result)[:200] if result else "None"
                    logger.debug(f"Tool result: {result_preview}...")
                    
                    # 标记任务完成（使用保存的task_id）
                    if current_task_id:
                        if result.get("success", True):
                            self.plan_manager.mark_task_completed(
                                session_id, current_task_id, result
                            )
                        else:
                            self.plan_manager.mark_task_failed(
                                session_id, current_task_id,
                                result.get("error", "Tool execution failed")
                            )
                        
                except Exception as e:
                    error_msg = f"Tool execution failed: {str(e)}"
                    logger.error(error_msg)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": error_msg,
                        "is_error": True
                    })
                    
                    # 标记任务失败
                    if current_task_id:
                        self.plan_manager.mark_task_failed(
                            session_id, current_task_id, error_msg
                        )
            
            # Add tool results to messages and memory
            # Each tool result should be a separate message with role "tool"
            for tool_result in tool_results:
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_result["tool_call_id"],
                    "content": tool_result["content"]
                }
                messages.append(tool_message)
                # Save tool result to memory
                self.memory.add_message(session_id, tool_message)
        
        if iteration >= max_iterations:
            logger.warning(f"Reached max iterations ({max_iterations})")
            yield "I apologize, but the task is taking too long. Please try again or break it into smaller steps."
    
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Process message and return complete response"""
        response_parts = []
        async for chunk in self.process_message(user_input, session_id, user, attachments):
            response_parts.append(chunk)
        return "".join(response_parts)


# Global agent instance
master_agent = MasterAgent()
