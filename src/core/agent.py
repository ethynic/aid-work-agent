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
from src.core.skill_registry import SkillRegistry
from src.core.skill_executor import SkillExecutor
from src.core.sandbox import SandboxManager


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
        "description": "为复杂任务创建执行计划。当任务需要多个步骤时，在执行工具前先使用此功能",
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
                                "description": "该步骤要使用的工具（如适用）"
                            },
                            "parameters": {
                                "type": "object",
                                "description": "工具的参数"
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
        
        self._register_builtin_tools()
        
        logger.info(f"Master Agent initialized with {len(self.skill_registry)} skills")
    
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
        """Get tool definitions including skill tool"""
        tools = list(AGENT_TOOLS)
        
        if self.skill_registry:
            skill_tool = self.skill_registry.get_skill_tool_definition()
            tools.append(skill_tool)
        
        return tools
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        """Build system prompt for the agent"""
        
        skill_descriptions = self.skill_registry.get_descriptions() if self.skill_registry else "(暂无可用技能)"
        
        prompt = f"""你是一个智能工作助手。你的任务是帮助用户完成各种工作任务。

## 重要语言规则

**你必须始终使用与用户提问相同的语言进行回复和工具调用！**
- 用户用中文提问 → 你用中文回复，工具参数使用中文
- 用户用英文提问 → 你用英文回复，工具参数使用英文
- 搜索关键词必须与用户提问语言保持一致！

## 能力范围

你可以帮助用户：
- 邮件管理（发送、读取、搜索邮件）
- 文档处理（摘要、翻译）
- 网络搜索（查找网络信息）
- OCR识别（从图片/PDF中提取文字）
- PDF处理（读取、提取、合并、拆分PDF）

## 技能系统

技能是可按需加载的专业知识模块。当任务匹配技能描述时，请先加载技能以获取详细指导。

可用技能：
{skill_descriptions}

**重要提示**：当任务匹配技能领域时，首先调用 `use_skill` 工具：

**文件处理技能：**
- 用户上传的文件匹配技能（如 .pdf 文件 → 使用 "pdf" 技能）
- 用户提到处理特定文件类型
- 涉及文件操作的任务（合并、拆分、转换、提取）

**领域特定技能：**
- 用户询问技能覆盖的专业主题
- 需要领域特定知识或方法的任务
- 与技能描述或关键词匹配的请求

**通用规则**：始终检查技能描述是否与用户任务匹配。如果匹配，首先加载技能以获取专家指导和适当的工具。

## 技能使用流程

**技能加载工作流：**

1. **识别技能需求**：检查用户任务是否匹配任何可用技能
2. **加载技能**：调用 `use_skill` 并传入技能名称以获取详细指导
3. **遵循指导**：阅读技能内容中的专家方法、工具和最佳实践
4. **执行**：使用适当的工具（如文件处理技能使用 `skill_execute`）
5. **返回结果**：向用户展示结果

**示例工作流：**

*文件处理（PDF示例）：*
- 用户上传 "report.pdf" 并要求"提取所有表格"
- 你调用 `use_skill "pdf"` 加载PDF处理知识
- 技能提供 `pdfplumber.extract_tables()` 等方法
- 你调用 `skill_execute` 执行相应命令
- 向用户返回提取的表格

*领域特定（数据分析示例）：*
- 用户要求"分析此数据集中的销售趋势"
- 你调用 `use_skill "data_analysis"` 加载分析方法
- 技能提供统计方法和可视化工具
- 你根据技能指导执行分析
- 向用户展示洞察和可视化结果

## 工具使用

当用户要求你做某事时：
1. **检查技能**：确定是否有技能匹配任务领域
2. 如需要，先加载相关技能
3. 分析请求，理解需要做什么
4. 规划完成任务所需的步骤
5. 使用适当的工具执行每个步骤
6. 整合结果并提供有用的回复

## 指导原则

- **主动加载技能**：当任务匹配技能领域时，在开始工作前先加载
- **遵循技能指导**：技能包含专家知识和最佳实践
- **文件技能使用skill_execute**：处理文件技能时，使用 `skill_execute` 运行命令
- **适应技能类型**：不同技能可能提供不同的工具和方法 - 遵循其指导
- 在执行重要操作前确认（如发送邮件）
- 如请求模糊，请询问澄清
- 将复杂任务分解为更小的步骤
- 提供清晰简洁的回复
- 如工具失败，解释问题并建议替代方案

## 可用工具

你可以使用以下工具：
- use_skill: 加载技能获取专业知识（领域特定任务优先使用此工具）
- skill_execute: 在技能沙箱环境中执行命令（加载技能后使用）
- create_plan: 为复杂任务创建执行计划（多步骤任务优先使用此工具）
- email_send: 发送邮件
- email_read: 读取收件箱邮件
- web_search: 搜索网络（关键词必须与用户提问语言一致）
- ocr_image: 从图片中提取文字
- doc_summarize: 总结文档
- doc_translate: 翻译文本
- clarify: 向用户询问缺失信息

## 规划指导

对于需要多个步骤的复杂任务，你必须：
1. 首先调用 create_plan 概述执行计划
2. 然后按顺序执行每个步骤
3. 最后总结结果

需要规划的任务示例：
- "写一份研究报告并发送邮件"（需要：搜索 → 摘要 → 发送邮件）
- "翻译文档并发送"（需要：翻译 → 发送邮件）
- "搜索信息并创建摘要"（需要：搜索 → 摘要）

对于简单任务如问候或单一操作，可以直接响应而无需规划。

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
    
    def _handle_create_plan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle create_plan tool call - print and return the plan
        
        Args:
            args: Plan arguments containing goal, steps, and execution_mode
        
        Returns:
            Plan result dictionary
        """
        goal = args.get("goal", "")
        steps = args.get("steps", [])
        execution_mode = args.get("execution_mode", "sequential")
        
        # Build plan output
        plan_output = []
        plan_output.append("=" * 60)
        plan_output.append("📋 执行计划 (Execution Plan)")
        plan_output.append("=" * 60)
        plan_output.append(f"🎯 目标 (Goal): {goal}")
        plan_output.append(f"🔄 执行模式 (Mode): {execution_mode}")
        plan_output.append("-" * 60)
        plan_output.append("📝 步骤 (Steps):")
        
        for step in steps:
            step_num = step.get("step_number", "?")
            description = step.get("description", "")
            tool = step.get("tool", "N/A")
            params = step.get("parameters", {})
            expected = step.get("expected_output", "")
            
            plan_output.append(f"\n  步骤 {step_num}: {description}")
            if tool != "N/A":
                plan_output.append(f"    🔧 工具: {tool}")
                if params:
                    plan_output.append(f"    📊 参数: {json.dumps(params, ensure_ascii=False)}")
                if expected:
                    plan_output.append(f"    📤 预期输出: {expected}")
        
        plan_output.append("-" * 60)
        plan_output.append("✅ 计划创建完成，开始执行...")
        plan_output.append("=" * 60)
        
        # Print to log
        plan_str = "\n".join(plan_output)
        logger.info(f"\n{plan_str}")
        
        # Also print to console for visibility
        print(plan_str)
        
        return {
            "success": True,
            "plan": {
                "goal": goal,
                "steps": steps,
                "execution_mode": execution_mode
            },
            "message": "Plan created successfully. Please execute the steps in order."
        }
    
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
                
                # Handle create_plan specially - print the plan and return success
                if tool_name == "create_plan":
                    plan_result = self._handle_create_plan(tool_args)
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
                    
                    skill_exec_result = await self._handle_skill_execute(
                        skill_name=skill_name,
                        command=command,
                        files=files,
                        session_id=session_id,
                        workdir=session_workspace
                    )
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_exec_result
                    })
                    continue
                
                # Execute the tool
                try:
                    result = await self.tool_executor.execute(tool_name, tool_args)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": result
                    })
                    result_preview = str(result)[:200] if result else "None"
                    logger.debug(f"Tool result: {result_preview}...")
                except Exception as e:
                    error_msg = f"Tool execution failed: {str(e)}"
                    logger.error(error_msg)
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": error_msg,
                        "is_error": True
                    })
            
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
