#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Master Agent - LLM-Driven Agent Loop

The agent uses LLM for:
1. Intent understanding
2. Task planning and decomposition
3. Deciding which tool/agent/skill to call
4. Executing tools and integrating results
"""

import json
import uuid
from typing import Optional, List, Dict, Any, AsyncGenerator
from loguru import logger

from src.config.settings import settings
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.memory.short_term import ShortTermMemory
from src.models.message import UnifiedMessage
from src.models.user import User


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
        "name": "respond",
        "description": "Send a response to the user",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message to send to user"
                },
                "type": {
                    "type": "string",
                    "enum": ["text", "help", "greeting", "error"],
                    "description": "Type of response"
                }
            },
            "required": ["message"]
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
        
        # Register built-in tools
        self._register_builtin_tools()
        
        logger.info("Master Agent initialized")
    
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
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        """Build system prompt for the agent"""
        prompt = """You are an intelligent work assistant. Your role is to help users complete various work tasks.

## Capabilities

You can help with:
- Email management (send, read, search emails)
- Document processing (summarize, translate)
- Web search (find information online)
- OCR (extract text from images/PDFs)

## Tool Usage

When a user asks you to do something:
1. Analyze the request to understand what needs to be done
2. Plan the steps needed to complete the task
3. Use the appropriate tools to execute each step
4. Integrate results and provide a helpful response

## Guidelines

- Always confirm important actions before executing (e.g., sending emails)
- Ask for clarification if the request is ambiguous
- Break down complex tasks into smaller steps
- Provide clear and concise responses
- If a tool fails, explain the issue and suggest alternatives

## Available Tools

You have access to these tools:
- create_plan: Create an execution plan for complex tasks (USE THIS FIRST for multi-step tasks)
- email_send: Send emails
- email_read: Read emails from inbox
- web_search: Search the web
- ocr_image: Extract text from images
- doc_summarize: Summarize documents
- doc_translate: Translate text
- clarify: Ask user for missing information
- respond: Send response to user

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
        user_input: str,
        session_id: str,
        user: Optional[User] = None
    ) -> List[Dict[str, Any]]:
        """Build message list for LLM"""
        messages = []
        
        # Get conversation history from memory
        history = self.memory.get_context(session_id)
        for msg in history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
        
        # Add current user message
        messages.append({
            "role": "user",
            "content": user_input
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
    
    async def process_message(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None
    ) -> AsyncGenerator[str, None]:
        """
        Process a user message and yield response chunks
        
        This is the main agent loop:
        1. Build context from memory
        2. Call LLM with tools
        3. Execute tool calls if any
        4. Return results to LLM
        5. Yield final response
        """
        logger.info(f"Processing message for session {session_id}: {user_input[:50]}...")
        
        # Store user message in memory
        self.memory.add(session_id, "user", user_input)
        
        # Build messages for LLM
        messages = self._build_messages(user_input, session_id, user)
        system_prompt = self._build_system_prompt(user)
        
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"Agent iteration {iteration}")
            
            # Call LLM with tools
            response = await self.llm.chat_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=AGENT_TOOLS
            )
            
            # Check if LLM wants to use tools
            tool_calls = response.get("tool_calls", [])
            content = response.get("content", "")
            
            # Log the raw response for debugging
            logger.debug(f"LLM response - content: {content[:100] if content else 'None'}..., tool_calls count: {len(tool_calls)}")
            
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
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls
            })
            
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
            
            # Add tool results to messages
            messages.append({
                "role": "user",
                "content": tool_results
            })
        
        if iteration >= max_iterations:
            logger.warning(f"Reached max iterations ({max_iterations})")
            yield "I apologize, but the task is taking too long. Please try again or break it into smaller steps."
    
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None
    ) -> str:
        """Process message and return complete response"""
        response_parts = []
        async for chunk in self.process_message(user_input, session_id, user):
            response_parts.append(chunk)
        return "".join(response_parts)


# Global agent instance
master_agent = MasterAgent()
