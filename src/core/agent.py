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
        "description": "Send an email to recipient(s)",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of recipient email addresses"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject"
                },
                "body": {
                    "type": "string",
                    "description": "Email body content"
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC recipients (optional)"
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_read",
        "description": "Read emails from inbox",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Folder to read (inbox, sent, etc.)",
                    "default": "inbox"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of emails to retrieve",
                    "default": 10
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Only retrieve unread emails",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for information",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5
                }
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "ocr_image",
        "description": "Extract text from an image using OCR",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file"
                },
                "language": {
                    "type": "string",
                    "description": "Language for OCR (e.g., ch, en)",
                    "default": "ch"
                }
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "doc_summarize",
        "description": "Summarize a document",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Document content to summarize"
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum summary length",
                    "default": 500
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "doc_translate",
        "description": "Translate text to another language",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to translate"
                },
                "target_lang": {
                    "type": "string",
                    "description": "Target language (e.g., en, ja, ko)"
                }
            },
            "required": ["text", "target_lang"]
        }
    },
    {
        "name": "clarify",
        "description": "Ask user for clarification when information is missing",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question to ask the user"
                },
                "missing_info": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of missing information items"
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "create_plan",
        "description": "Create an execution plan for complex tasks. Use this BEFORE executing tools when the task requires multiple steps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The overall goal of the task"
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_number": {
                                "type": "integer",
                                "description": "Step number in the plan"
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of what this step does"
                            },
                            "tool": {
                                "type": "string",
                                "description": "Tool to use for this step (if applicable)"
                            },
                            "parameters": {
                                "type": "object",
                                "description": "Parameters for the tool"
                            },
                            "expected_output": {
                                "type": "string",
                                "description": "Expected output of this step"
                            }
                        },
                        "required": ["step_number", "description"]
                    },
                    "description": "List of steps to execute"
                },
                "execution_mode": {
                    "type": "string",
                    "enum": ["sequential", "parallel"],
                    "description": "How to execute the steps",
                    "default": "sequential"
                }
            },
            "required": ["goal", "steps"]
        }
    },
    {
        "name": "skill_execute",
        "description": "Execute a command in a skill's sandbox environment. Use this after loading a skill to run commands like pdftotext, python scripts, etc. IMPORTANT: Use simple commands, For Python, prefer simple one-liners or use pypdf/pdfplumber directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Name of the skill context to use"
                },
                "command": {
                    "type": "string",
                    "description": "Command to execute. Use simple format. For Python: python -c \"from pypdf import PdfReader; r = PdfReader('file.pdf'); print(r.pages[0].extract_text())\""
                },
                "files": {
                    "type": "object",
                    "description": "Optional files to make available in the sandbox (filename -> base64 content)",
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
        
        skill_descriptions = self.skill_registry.get_descriptions() if self.skill_registry else "(no skills available)"
        
        prompt = f"""You are an intelligent work assistant. Your role is to help users complete various work tasks.

## Capabilities

You can help with:
- Email management (send, read, search emails)
- Document processing (summarize, translate)
- Web search (find information online)
- OCR (extract text from images/PDFs)
- PDF processing (read, extract, merge, split PDFs)

## Skills

Skills are specialized knowledge modules that you can load on-demand. When a task matches a skill description, load the skill FIRST to get detailed instructions.

Available skills:
{skill_descriptions}

**IMPORTANT**: Use the `use_skill` tool IMMEDIATELY when:
- User uploads a file that matches a skill (e.g., .pdf file → use_skill "pdf")
- User mentions working with a specific file type
- Task description matches a skill's description

## Working with Skills and Files

When a user uploads a file (like a PDF), the workflow is:

1. **Skill Auto-Loading**: If the file matches a skill, it's automatically loaded with detailed instructions
2. **Understand the Request**: Analyze what the user wants to do (extract text? extract tables? merge? split?)
3. **Choose the Right Method**: From the skill content, select the appropriate tool/command
4. **Execute**: Use `skill_execute` to run the command in a sandbox environment
5. **Return Results**: Present the results to the user

Example workflow for PDF:
- User uploads "report.pdf" and asks "extract all tables"
- PDF skill is auto-loaded with instructions
- You see methods like `pdfplumber.extract_tables()` in the skill
- You call `skill_execute` with: `skill="pdf", command="python -c 'import pdfplumber; ...'"`
- Return the extracted tables to the user

## Tool Usage

When a user asks you to do something:
1. Check if a skill should be loaded first (file uploads, specific file types)
2. Analyze the request to understand what needs to be done
3. Plan the steps needed to complete the task
4. Use the appropriate tools to execute each step
5. Integrate results and provide a helpful response

## Guidelines

- Always load relevant skills BEFORE attempting domain-specific work
- When working with files, use `skill_execute` to run commands in the sandbox
- Always confirm important actions before executing (e.g., sending emails)
- Ask for clarification if the request is ambiguous
- Break down complex tasks into smaller steps
- Provide clear and concise responses
- If a tool fails, explain the issue and suggest alternatives

## Available Tools

You have access to these tools:
- use_skill: Load a skill to get specialized knowledge (USE THIS FIRST for domain-specific tasks)
- skill_execute: Execute commands in a skill's sandbox environment (use after loading a skill)
- create_plan: Create an execution plan for complex tasks (USE THIS FIRST for multi-step tasks)
- email_send: Send emails
- email_read: Read emails from inbox
- web_search: Search the web
- ocr_image: Extract text from images
- doc_summarize: Summarize documents
- doc_translate: Translate text
- clarify: Ask user for missing information

## Planning Guidelines

For complex tasks that require multiple steps, you MUST:
1. First call create_plan to outline the execution plan
2. Then execute each step in order
3. Finally summarize the results

Examples of tasks that need planning:
- "Write a research report and send it by email" (requires: search → summarize → email)
- "Translate a document and send it" (requires: translate → email)
- "Search for information and create a summary" (requires: search → summarize)

For simple tasks like greetings or single actions, you can respond directly without planning.

Use tools efficiently to complete tasks. Always think through the task before acting.
"""
        
        if user:
            prompt += f"\n\n## Current User\nName: {user.name}\nID: {user.user_id}\n"
        
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
