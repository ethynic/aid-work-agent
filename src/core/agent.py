#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Master Agent - LLM-Driven Agent Loop

The agent uses LLM for:
1. Intent understanding
2. Task planning and decomposition
3. Deciding which tool/agent/skill to call
4. Executing tools and integrating results
5. Loading and executing Skills
"""

import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncGenerator, Callable, Coroutine, Any
from loguru import logger

from src.config.settings import settings
from src.core.agent_logger import log_agent_iteration
from src.llm.gateway import llm_gateway
from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor
from src.memory.short_term import ShortTermMemory
from src.models.message import UnifiedMessage
from src.models.user import User
from src.models.plan import TaskStatus
from src.core.skill_registry import SkillRegistry
from src.core.skill_executor import SkillExecutor
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
                    "type": "string",
                    "description": "收件人邮箱地址，多个地址用逗号分隔"
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
                    "type": "string",
                    "description": "抄送人邮箱地址，多个地址用逗号分隔（可选）"
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_read",
        "description": "收取用户邮箱中的邮件",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "邮件文件夹，默认INBOX",
                    "default": "INBOX"
                },
                "limit": {
                    "type": "integer",
                    "description": "收取邮件数量，默认10封",
                    "default": 10
                },
                "unseen_only": {
                    "type": "boolean",
                    "description": "是否只收取未读邮件，默认False",
                    "default": False
                },
                "from_filter": {
                    "type": "string",
                    "description": "发件人过滤条件（可选）"
                },
                "subject_filter": {
                    "type": "string",
                    "description": "主题过滤条件（可选）"
                }
            },
            "required": []
        }
    },
    {
        "name": "email_list_folders",
        "description": "列出邮箱中的所有文件夹及其邮件统计",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "content_generate",
        "description": "调用大模型生成内容，用于生成客户列表、撰写多语言邮件等。智能体需要提供详细的提示词来指导大模型生成所需内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "生成内容的提示词，由智能体组织。提示词应包含：1)角色/身份 2)任务描述 3)输入信息 4)输出格式要求 5)语言要求等"
                },
                "language": {
                    "type": "string",
                    "description": "生成内容的语言，如：zh（中文）、en（英语）、ru（俄语）、de（德语）、ja（日语）、ko（韩语）等"
                },
                "content_type": {
                    "type": "string",
                    "description": "内容类型，用于选择合适的提示词模板。可选值：customer_list（客户列表）、email（邮件）、market_report（市场报告）等"
                }
            },
            "required": ["prompt"]
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
        "name": "paddleocr_doc_parsing",
        "description": "使用PaddleOCR解析PDF或图片文档，返回每页的markdown文本内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_url": {
                    "type": "string",
                    "description": "文档URL地址，支持PDF或图片"
                },
                "file_path": {
                    "type": "string",
                    "description": "本地文件路径"
                },
                "file_type": {
                    "type": "integer",
                    "description": "文件类型：0=PDF，1=图片。不填则自动检测"
                }
            },
            "required": []
        }
    },
    {
        "name": "doc_summarize",
        "description": "对文本内容进行摘要总结",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "需要总结的文本内容"
                },
                "length": {
                    "type": "string",
                    "description": "摘要长度：short（简短）、medium（中等）、long（详细）",
                    "default": "medium"
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
        "description": "为复杂任务创建执行计划。⚠️ 注意：如果任务只需要调用一个工具或一个子智能体，不需要创建计划，直接调用该工具即可。只有需要多个步骤协调的任务才需要创建计划。",
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
        "description": "在技能上下文中执行命令。加载技能后使用此功能运行pdftotext、python脚本等命令。重要：使用简单命令，对于Python优先使用简单的一行命令或直接使用pypdf/pdfplumber",
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
                    "description": "可选的文件，使其在执行环境中可用（文件名 -> base64内容）",
                    "additionalProperties": {
                        "type": "string"
                    }
                }
            },
            "required": ["skill", "command"]
        }
    },
    {
        "name": "browser_open",
        "description": "打开指定网址的网页，等待页面加载完成",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要打开的网页URL，必须以http://或https://开头"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，用于管理多个会话，默认为'default'"
                },
                "headless": {
                    "type": "boolean",
                    "description": "是否无头模式运行，true为不显示浏览器窗口，false为显示窗口，默认false",
                    "default": False
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_click",
        "description": "点击网页中的指定元素（按钮、链接等）",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS选择器，如 '.submit-btn', '#submit', 'button[type=\"submit\"]'"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "timeout": {
                    "type": "integer",
                    "description": "等待元素出现的超时时间（毫秒），默认10000",
                    "default": 10000
                }
            },
            "required": ["selector"]
        }
    },
    {
        "name": "browser_fill",
        "description": "填写网页表单中的输入框、文本域等元素",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS选择器，如 'input[name=\"username\"]', '#password'"
                },
                "value": {
                    "type": "string",
                    "description": "要填写的值"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "timeout": {
                    "type": "integer",
                    "description": "等待元素出现的超时时间（毫秒），默认10000",
                    "default": 10000
                }
            },
            "required": ["selector", "value"]
        }
    },
    {
        "name": "browser_get_content",
        "description": "获取网页的文本内容、HTML结构或特定元素的内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS选择器，如果为空则获取整个页面的内容"
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "html", "markdown"],
                    "description": "返回格式，text为纯文本，html为HTML源码，markdown为Markdown格式，默认text",
                    "default": "text"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                }
            },
            "required": []
        }
    },
    {
        "name": "browser_navigate",
        "description": "在当前页面进行导航操作：前进、后退、刷新",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["back", "forward", "reload"],
                    "description": "导航动作：back后退，forward前进，reload刷新"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_close",
        "description": "关闭浏览器或特定会话",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "要关闭的会话ID，如果为空则关闭所有会话"
                }
            },
            "required": []
        }
    },
    {
        "name": "browser_screenshot",
        "description": "对当前网页进行截图并保存",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "截图保存路径，如 './screenshot.png'，默认为 './screenshot.png'",
                    "default": "./screenshot.png"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "full_page": {
                    "type": "boolean",
                    "description": "是否截取整个页面，默认false只截取当前可视区域",
                    "default": False
                }
            },
            "required": []
        }
    },
    {
        "name": "file_read",
        "description": "读取文本文件的内容，支持自动检测文件编码（UTF-8、GBK、GB2312等），适用于各种文本文件格式",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径，可以是绝对路径或相对路径"
                },
                "encoding": {
                    "type": "string",
                    "description": "文件编码（可选），如果不指定则自动检测。常用编码：utf-8, gbk, gb2312, ascii等"
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（可选），从第几行开始读取，默认为1"
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（可选），读到第几行，默认读取到文件末尾"
                },
                "max_size": {
                    "type": "integer",
                    "description": "最大读取字节数（可选），默认为10MB，防止读取超大文件"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "file_list",
        "description": "列出指定目录下的文件和子目录，支持过滤和递归遍历",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "要列出的目录路径，默认为当前目录"
                },
                "pattern": {
                    "type": "string",
                    "description": "文件名匹配模式（可选），支持通配符，如 *.py, *.txt 等"
                },
                "recursive": {
                    "type": "boolean",
                    "description": "是否递归遍历子目录，默认False"
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "是否显示隐藏文件（以.开头的文件），默认False"
                }
            },
            "required": []
        }
    },
    {
        "name": "upload_to_remote",
        "description": "将文件上传到 SMB 或 FTP 服务器。如果目标路径的凭据未配置，工具会返回凭据配置链接，用户完成配置后可继续上传。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要上传的本地文件路径（可以是绝对路径或上传目录下的文件名）"
                },
                "remote_path": {
                    "type": "string",
                    "description": "远程服务器路径，格式示例: /share/folder (SMB) 或 /var/www/uploads (FTP)"
                },
                "connection_type": {
                    "type": "string",
                    "enum": ["smb", "ftp"],
                    "description": "连接类型: smb 或 ftp"
                },
                "filename": {
                    "type": "string",
                    "description": "上传后的文件名（可选，默认使用原文件名）"
                }
            },
            "required": ["file_path", "remote_path", "connection_type"]
        }
    }
]


class Agent:
    """
    统一的智能体类 - 支持主智能体和子智能体模式
    
    主智能体模式 (is_master=True):
    - 有委派任务给子智能体的能力
    - system prompt 包含委派规则
    - 管理子智能体的创建和执行
    
    子智能体模式 (is_master=False):
    - 没有委派能力
    - system prompt 不包含委派规则
    - 由主智能体创建，在独立线程中运行
    - 有自己的 plan 和执行流程
    
    The agent loop:
    1. Receive user message
    2. LLM understands intent and plans tasks (主智能体必须plan，闲聊除外)
    3. LLM decides which tools to call (子智能体不能委派)
    4. Execute tools and return results to LLM
    5. LLM integrates results and responds
    6. Repeat until task complete
    """
    
    def __init__(
        self,
        is_master: bool = True,
        subagent_config=None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        parent_plan_manager=None,
    ):
        """
        初始化智能体
        
        Args:
            is_master: 是否为主智能体
            subagent_config: 子智能体配置（子智能体模式时必需）
            session_id: 会话ID（子智能体模式时使用）
            execution_id: 执行ID（子智能体模式时使用）
            parent_plan_manager: 父智能体的计划管理器（子智能体模式时使用，用于记录执行过程）
        """
        self.is_master = is_master
        self.subagent_config = subagent_config
        self.session_id = session_id
        self.execution_id = execution_id
        self.parent_plan_manager = parent_plan_manager
        
        # 共享组件
        self.llm = llm_gateway
        self.tool_registry = ToolRegistry()
        self.tool_executor = ToolExecutor(self.tool_registry)
        self.memory = ShortTermMemory()
        
        # 技能系统
        skills_dir = Path(__file__).parent.parent / "skills"

        # 读取配置的 allowed 列表
        from src.config.settings import settings
        if is_master:
            # 主智能体：从配置读取 allowed 列表
            allowed_skills = settings.skills.master_agent.allowed if settings.skills.master_agent.allowed else None
            self.skill_registry = SkillRegistry()
            self.skill_registry.load_from_directory(skills_dir, allowed=allowed_skills)
            logger.info(f"Master Agent loaded {len(self.skill_registry)} skills (allowed={allowed_skills})")
        else:
            # 子智能体：从 SUBAGENT.md 读取 allowed 列表
            allowed_skills = None
            if subagent_config and hasattr(subagent_config, 'get_allowed_skills'):
                subagent_allowed = subagent_config.get_allowed_skills()
                if subagent_allowed:
                    allowed_skills = subagent_allowed
            self.skill_registry = SkillRegistry()
            self.skill_registry.load_from_directory(skills_dir, allowed=allowed_skills)
            logger.info(f"Subagent '{subagent_config.name if subagent_config else 'unknown'}' loaded {len(self.skill_registry)} skills (allowed={allowed_skills})")

        self.skill_executor = SkillExecutor(self.skill_registry)
        
        # 计划管理器
        plans_dir = Path(__file__).parent.parent.parent / "plans"
        self.plan_manager = PlanManager(plans_dir)
        
        # 主智能体特有：子智能体注册表和执行器
        if is_master:
            # 初始化子智能体注册表
            subagents_dir = Path(__file__).parent.parent.parent / "subagents"
            from src.subagents.registry import SubagentRegistry
            self.subagent_registry = SubagentRegistry(subagents_dir)
            
            # 注册内置工具
            self._register_builtin_tools()
            
            # 初始化子智能体执行器
            from src.subagents.executor import SubagentExecutor
            self.subagent_executor = SubagentExecutor(
                self.memory, 
                self.subagent_registry,
                self.tool_registry,
                self.skill_registry,
                self._build_base_system_prompt,
            )
            
            logger.info(f"Master Agent initialized with {len(self.skill_registry)} skills, {len(self.subagent_registry)} subagents")
        else:
            # 子智能体：没有委派能力
            self.subagent_registry = None
            self.subagent_executor = None
            
            # 注册受限的工具（根据子智能体配置）
            self._register_builtin_tools()
            self._filter_tools_by_config()
            
            logger.info(f"Subagent initialized: {subagent_config.name if subagent_config else 'unknown'}")
    
    def _register_builtin_tools(self):
        """Register built-in tools"""
        from src.tools.email.email_tool import EmailSendTool, EmailReadTool, EmailListFoldersTool
        from src.tools.ocr import PaddleOCRDocParsingTool
        from src.tools.document.doc_tool import DocSummarizeTool, DocTranslateTool
        from src.tools.search.search_tool import WebSearchTool
        from src.tools.browser.browser_tool import (
            BrowserOpenTool,
            BrowserClickTool,
            BrowserFillTool,
            BrowserGetContentTool,
            BrowserNavigateTool,
            BrowserCloseTool,
            BrowserScreenshotTool,
        )
        from src.tools.file.file_reader_tool import FileReaderTool, FileListTool
        from src.tools.file.upload_to_remote import UploadToRemoteTool
        from src.tools.llm.content_generate_tool import ContentGenerateTool
        from src.models.user import UserEmail, EncryptionType
        
        # 创建默认用户邮箱配置
        default_user_email = UserEmail(
            email_address="luwei@tulin.cn",
            smtp_server="smtp.ym.163.com",
            smtp_port=994,
            smtp_user="luwei@tulin.cn",
            smtp_password="p@$$w0rd",
            smtp_encryption=EncryptionType.SSL,
            imap_server="imap.ym.163.com",
            imap_port=993,
            imap_encryption=EncryptionType.SSL,
        )
        
        self.tool_registry.register(EmailSendTool(default_user_email))
        self.tool_registry.register(EmailReadTool(default_user_email))
        self.tool_registry.register(EmailListFoldersTool(default_user_email))
        self.tool_registry.register(PaddleOCRDocParsingTool())
        self.tool_registry.register(DocSummarizeTool())
        self.tool_registry.register(DocTranslateTool())
        self.tool_registry.register(WebSearchTool())
        
        # 注册浏览器工具
        self.tool_registry.register(BrowserOpenTool())
        self.tool_registry.register(BrowserClickTool())
        self.tool_registry.register(BrowserFillTool())
        self.tool_registry.register(BrowserGetContentTool())
        self.tool_registry.register(BrowserNavigateTool())
        self.tool_registry.register(BrowserCloseTool())
        self.tool_registry.register(BrowserScreenshotTool())
        
        # 注册文件工具
        self.tool_registry.register(FileReaderTool())
        self.tool_registry.register(FileListTool())
        self.tool_registry.register(UploadToRemoteTool())
        
        # 注册LLM内容生成工具
        self.tool_registry.register(ContentGenerateTool())
        
        logger.info(f"Registered {len(self.tool_registry._tools)} tools")
    
    def _filter_tools_by_config(self):
        """根据子智能体配置过滤可用工具"""
        if self.is_master or not self.subagent_config:
            return
        
        # 获取允许的工具列表
        allowed_tools = self.subagent_config.get_allowed_tools()
        
        # 如果配置为继承，保留所有工具
        if self.subagent_config.tools.get("inherit", False):
            logger.info(f"Subagent {self.subagent_config.name} inherits all tools")
            return
        
        # 否则只保留允许的工具
        if allowed_tools:
            all_tools = list(self.tool_registry._tools.keys())
            for tool_name in all_tools:
                if tool_name not in allowed_tools:
                    self.tool_registry._tools.pop(tool_name, None)
            logger.info(f"Subagent {self.subagent_config.name} filtered to {len(self.tool_registry._tools)} tools: {allowed_tools}")
        else:
            # 如果没有指定允许的工具，清除所有工具
            self.tool_registry._tools.clear()
            logger.info(f"Subagent {self.subagent_config.name} has no tools allowed")
    
    def _get_tools(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions including skill tool and delegation tool
        
        主智能体：包含委派工具
        子智能体：不包含委派工具
        """
        tools = list(AGENT_TOOLS)
        
        # 添加技能工具
        if self.skill_registry:
            skill_tool = self.skill_registry.get_skill_tool_definition()
            tools.append(skill_tool)
        
        # 仅主智能体：添加子智能体委派工具
        if self.is_master and self.subagent_registry and len(self.subagent_registry) > 0:
            delegation_tool = self.subagent_registry.get_delegation_tool_definition()
            if delegation_tool:
                tools.append(delegation_tool)
        
        return tools

    def _get_tool_display_name(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        将工具名称转换为用户友好的显示名称

        Args:
            tool_name: 原始工具名称
            tool_args: 工具参数

        Returns:
            用户友好的显示名称
        """
        # 特殊工具的特殊显示
        if tool_name == "web_search":
            keyword = tool_args.get("keyword", "")
            return f"网络搜索「{keyword[:20]}...」"
        elif tool_name == "email_send":
            to = tool_args.get("to", "")
            return f"发送邮件至「{to}」"
        elif tool_name == "email_read":
            folder = tool_args.get("folder", "INBOX")
            limit = tool_args.get("limit", 10)
            return f"读取邮件（{folder}，{limit}封）"
        elif tool_name == "content_generate":
            content_type = tool_args.get("content_type", "")
            return f"生成内容（{content_type}）"
        elif tool_name == "browser_open":
            url = tool_args.get("url", "")
            return f"打开网页「{url[:30]}...」"
        elif tool_name == "delegate_to_subagent":
            subagent_name = tool_args.get("subagent_name", "")
            return f"调用{subagent_name}子智能体"
        elif tool_name == "skill_execute":
            skill = tool_args.get("skill", "")
            return f"执行技能「{skill}」"
        elif tool_name == "use_skill":
            skill = tool_args.get("skill", "")
            return f"加载技能「{skill}」"
        elif tool_name == "file_read":
            file_path = tool_args.get("file_path", "")
            return f"读取文件「{file_path}」"
        elif tool_name == "doc_summarize":
            return "总结文档"
        elif tool_name == "doc_translate":
            target = tool_args.get("target_lang", "")
            return f"翻译文档为{target}"
        elif tool_name == "paddleocr_doc_parsing":
            file_path = tool_args.get("file_path", "")
            file_url = tool_args.get("file_url", "")
            source = file_path if file_path else file_url
            return f"解析文档「{source}」"
        elif tool_name == "create_plan":
            return "创建执行计划"
        else:
            # 通用工具显示
            display_names = {
                "email_list_folders": "获取邮件夹列表",
                "browser_click": "点击网页元素",
                "browser_fill": "填写网页表单",
                "browser_get_content": "获取网页内容",
                "browser_navigate": "网页导航",
                "browser_close": "关闭浏览器",
                "browser_screenshot": "网页截图",
                "file_list": "列出文件",
            }
            return display_names.get(tool_name, tool_name)

    def _build_base_system_prompt(
        self,
        include_delegation: bool = True,
        subagent_constraint: str = "",
        user: Optional[User] = None
    ) -> str:
        """
        构建基础系统提示词（可复用）
        
        Args:
            include_delegation: 是否包含子智能体委派相关内容（子智能体不应包含）
            subagent_constraint: 子智能体的额外约束（追加到基础提示词后面）
            user: 用户信息
            
        Returns:
            系统提示词
        """
        # 如果是子智能体，强制不包含委派内容
        if not self.is_master:
            include_delegation = False
        
        skill_descriptions = self.skill_registry.get_descriptions() if self.skill_registry else "(暂无可用技能)"
        available_tools = [t["name"] for t in AGENT_TOOLS]
        available_skills = self.skill_registry.list_skills() if self.skill_registry else []
        
        # 子智能体信息（仅主智能体使用）
        subagent_descriptions = ""
        available_subagents = []
        if include_delegation and self.subagent_registry:
            subagent_descriptions = self.subagent_registry.get_descriptions() if self.subagent_registry else "(暂无可用子智能体)"
            available_subagents = self.subagent_registry.list_subagents() if self.subagent_registry else []
        
        # 委派工具说明（仅主智能体使用）
        delegation_guide = ""
        subagent_matching_hint = ""
        
        if include_delegation and available_subagents:
            delegation_guide = f"""
### delegate_to_subagent（委派给专业子智能体）
当任务需要专业领域能力时，直接委派给子智能体：

**可用子智能体：**
{subagent_descriptions}

**🚨 重要：直接委派，不需要先创建计划！**
如果任务只需要委派给一个子智能体就能完成（不需要其他工具或步骤），**直接调用`delegate_to_subagent`工具**，不需要先调用`create_plan`。子智能体会自己创建和执行计划。

**⚠️ task_description 必须包含完整信息！**
如果用户上传了文件（图片、文档等），必须在 task_description 中包含以下信息：
- 文件的完整路径（已在消息中提供，格式如 "Full path: `/path/to/file.jpg`"）
- 文件名称和大小
- 例如：`task_description="处理产品图片，文件路径：/tmp/skill_ws_xxx/product.jpg"`

**使用场景：**
- 代码审查任务 → 直接调用 `delegate_to_subagent(subagent_name="code-reviewer", task_description="...")`
- HR相关任务 → 直接调用 `delegate_to_subagent(subagent_name="hr-expert", task_description="...")`
- 外贸获客任务 → 直接调用 `delegate_to_subagent(subagent_name="foreign-trade-ai", task_description="...")`
- PDF文档处理 → 直接调用 `delegate_to_subagent(subagent_name="pdf-expert", task_description="...")`

**调用示例：**
```
delegate_to_subagent(
    subagent_name="foreign-trade-ai",
    task_description="帮我在中亚地区匹配LED灯客户。已上传产品图片：Full path: `/tmp/skill_ws_xxx/led_light.jpg`，请从中提取产品信息进行客户匹配"
)
```
"""
            subagent_matching_hint = f"""
**⚡ 关键：优先判断是否可以直接委派**
在分析需求时，首先检查任务是否属于以下专业领域：
{subagent_descriptions}

**决策逻辑：**
1. 如果任务**只需要委派给一个子智能体**就能完成 → **直接调用`delegate_to_subagent`，不需要`create_plan`**
2. 如果任务**需要多个工具组合或多个步骤** → 先调用`create_plan`创建计划，然后在计划中指定委派

例如：
- 招聘AI产品经理 → 直接委派给 `hr-expert`（单步任务，不需要计划）
- 代码审查 → 直接委派给 `code-reviewer`（单步任务，不需要计划）
- PDF提取表格 → 直接委派给 `pdf-expert`（单步任务，不需要计划）
- 搜索信息+邮件发送 → 需要`create_plan`创建多步骤计划
"""

        # 基础工作流程（根据是否包含委派调整）
        if include_delegation:
            workflow_step1 = """### 第一步：分析需求
1. 理解用户想要什么
2. **首先判断是否可以完全由一个子智能体完成**
   - 如果只需要委派给一个子智能体 → **直接调用`delegate_to_subagent`，跳过计划创建**
   - 如果需要多个工具组合或多步骤 → 需要先`create_plan`
3. 判断是否需要使用工具
4. 确定需要哪些工具/技能/子智能体"""
        else:
            workflow_step1 = """### 第一步：分析需求
1. 理解用户想要什么
2. 判断是否需要使用工具
3. 确定需要哪些工具/技能"""

        prompt = f"""你是一个智能工作助手。你的任务是帮助用户完成各种工作任务。

## 🚨 核心工作流程（必须严格遵守）

**每个用户请求都必须遵循以下流程：**
{subagent_matching_hint}
{workflow_step1}

### 第二步：创建执行计划（仅在需要时）
**⚠️ 并非所有任务都需要创建计划！**

**不需要创建计划的情况：**
- 任务只需要调用一个子智能体 → 直接调用`delegate_to_subagent`
- 任务只需要调用一个工具 → 直接调用该工具

**需要创建计划的情况：**
- 任务需要多个工具组合使用
- 任务需要多个步骤协调执行
- 任务涉及并行处理

调用 `create_plan` 时需要提供：
- goal: 任务目标（用户需求的总结）
- steps: 执行步骤列表，每个步骤包含：
  - step_number: 步骤编号
  - description: 步骤描述
  - tool: 使用的工具名称
  - parameters: 工具参数
  - expected_output: 预期输出
- execution_mode: "sequential"（顺序）或 "parallel"（并行）

### 第三步：执行计划（关键！）
**⚠️ 创建计划后，必须立即执行计划中的步骤！不要只是描述计划，要实际调用工具！**

执行方式：
1. 创建计划后，`create_plan` 会返回第一步的工具和参数
2. **立即调用返回的工具**，而不是回复用户"正在执行"
3. 等待工具执行结果
4. 继续执行下一步（如果有）
5. 收集并整合所有结果

### 第四步：汇报结果
1. 总结执行结果
2. 展示关键信息
3. 如有失败，说明原因和建议

**⚠️ 重要规则：创建计划后不要回复用户！**
- 创建计划后，不要对用户说"正在执行"、"请稍候"之类的话
- 而是直接调用计划中指定的工具
- 只有当所有工具都执行完毕后，才向用户汇报最终结果

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

**⚠️ 重要：技能不是工具！不能直接将技能名作为函数调用。**
技能必须通过 `use_skill(skill="技能名")` 加载后，再通过 `skill_execute` 执行具体命令。
例如要查天气，不能直接调用 weather，必须：use_skill(skill="weather") → skill_execute(skill="weather", command="curl -s 'wttr.in/City?format=3'")
{f'''
### 可用子智能体
{subagent_descriptions}''' if include_delegation else ''}

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

### create_plan（仅多步骤任务需要）
**⚠️ 如果任务只需要一个工具或一个子智能体，直接调用该工具，不需要创建计划！**

只有当任务需要多个步骤协调时才创建计划：

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
- **⚠️ 技能不是工具！绝不能直接调用技能名（如 weather），必须通过此工具加载！**
- 使用方式：use_skill(skill="技能名")
- 加载后会获得技能的详细指令，然后通过 skill_execute 执行
- 常见错误：直接调用 weather/get_weather 等不存在的工具 ❌ → 正确做法是 use_skill(skill="weather") ✅

### skill_execute
- **重要**：执行技能命令的唯一工具！
- 用于执行技能文档中描述的命令（如 python scripts/xxx.py）
- **必须**在 use_skill 之后调用，用于实际执行技能中的命令
- 格式：skill_execute(skill="技能名", command="实际命令")

### clarify
- 当信息不足时向用户询问
{delegation_guide}
---

## 工作示例

**示例1：HR招聘任务（直接委派，不需要计划）**
用户: "我要招聘一名AI产品经理"
1. 分析：这是招聘任务，只需要hr-expert子智能体就能完成
2. **直接调用** delegate_to_subagent(subagent_name="hr-expert", task_description="协助招聘AI产品经理，包括JD编写、薪酬调研、面试设计")
3. 整合子智能体的结果并回复用户
{f'''
**示例2：代码审查任务（直接委派，不需要计划）**
用户: "帮我审查这段代码的安全性"
1. 分析：这是代码审查任务，只需要code-reviewer子智能体
2. **直接调用** delegate_to_subagent(subagent_name="code-reviewer", task_description="审查代码安全性")
3. 整合子智能体的审查结果并回复用户

**示例3：PDF文档处理（直接委派，不需要计划）**
用户: "帮我提取这个PDF中的表格数据"
1. 分析：这是PDF处理任务，只需要pdf-expert子智能体
2. **直接调用** delegate_to_subagent(subagent_name="pdf-expert", task_description="提取PDF中的表格数据")
3. 整合结果并回复用户

**示例4：搜索+发送邮件（需要计划）**
用户: "帮我搜索春节档电影，然后发邮件给同事"
1. 分析：需要两个步骤（搜索+发邮件），需要创建计划
2. 调用 create_plan(goal="搜索电影并发送邮件", steps=[...], execution_mode="sequential")
3. 调用 web_search(keyword="2026年春节档电影")
4. 调用 email_send(to=["colleague@example.com"], subject="春节档电影推荐", body="...")
5. 整合结果并回复用户

**示例5：超能力范围**''' if include_delegation else '''**示例2：超能力范围**'''}
用户: "帮我订一张机票"
回复: "抱歉，我目前无法直接预订机票。建议您使用携程、去哪儿等平台，或者我可以帮您搜索航班信息。"

---

## 指导原则

- **智能决策**：如果任务只需要一个子智能体或一个工具，直接调用，不需要创建计划
- **规划复杂任务**：只有需要多个步骤协调的任务才需要先创建执行计划
- **透明化**：让用户知道你在做什么，展示计划（如果有的话）
- **诚实**：超出能力时明确告知，不要虚假承诺
- **有帮助**：即使无法完成，也要提供有用的建议
- **跟踪进度**：计划会被记录，用户可以查看进度（如果创建了计划）
{f'''- **善用专家**：专业任务直接委派给专业子智能体''' if include_delegation else '''- **专注任务**：专注于当前任务，使用可用工具高效完成'''}

高效使用工具完成任务。在行动前始终思考任务要求。
"""

        # 追加子智能体约束（如果有）
        if subagent_constraint:
            prompt += f"""

---

## 专业领域约束

{subagent_constraint}
"""
        
        # 子智能体特别说明：不能委派任务
        if not include_delegation:
            prompt += """

---

## ⚠️ 重要限制

**你不能委派任务给其他子智能体！**

作为子智能体，你的职责是：
1. 独立完成主智能体委托的任务
2. 使用可用的工具和技能执行任务
3. 如果需要分解任务，自己创建执行计划并执行
4. 如果遇到超出能力范围的问题，向主智能体报告

你**不能**调用 `delegate_to_subagent` 工具，因为这是主智能体才有的委派能力。
"""
        
        if user:
            prompt += f"\n\n## 当前用户\n姓名: {user.name}\nID: {user.user_id}\n"
        
        return prompt
    
    def _build_system_prompt(self, user: Optional[User] = None) -> str:
        """
        Build system prompt for the agent
        
        主智能体：包含委派能力
        子智能体：不包含委派能力，使用子智能体配置的约束
        """
        if self.is_master:
            return self._build_base_system_prompt(include_delegation=True, user=user)
        else:
            # 子智能体：使用配置中的系统提示词
            subagent_constraint = ""
            if self.subagent_config and self.subagent_config.system_prompt:
                subagent_constraint = self.subagent_config.system_prompt
            return self._build_base_system_prompt(
                include_delegation=False,
                subagent_constraint=subagent_constraint,
                user=user
            )
    
    def _build_messages(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """Build message list for LLM from memory"""
        messages = []
        
        history = self.memory.get_context(session_id)
        
        # 追踪待处理的 tool_call_ids
        pending_tool_calls = set()
        
        for i, msg in enumerate(history):
            # 处理不同类型的消息
            role = msg.get("role", "user")
            
            if role == "tool":
                # 工具结果消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", "")
                })
                # 移除已处理的 tool_call_id
                tc_id = msg.get("tool_call_id", "")
                if tc_id in pending_tool_calls:
                    pending_tool_calls.discard(tc_id)
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    # 检查是否所有待处理的 tool_calls 都有对应的 tool response
                    # 如果有未匹配的 tool_calls，将其作为普通 assistant message 处理
                    has_pending = bool(pending_tool_calls)
                    if has_pending:
                        # 有未处理的 tool_calls，先清理之前的 assistant message
                        # 这通常表示之前的对话有消息丢失，跳过 tool_calls
                        logger.warning(f"发现未匹配的 tool_calls，清除并作为普通消息处理")
                        messages.append({
                            "role": "assistant",
                            "content": msg.get("content", "")
                        })
                    else:
                        # 正常情况：添加带 tool_calls 的 assistant message
                        messages.append({
                            "role": "assistant",
                            "content": msg.get("content", ""),
                            "tool_calls": tool_calls
                        })
                        # 记录待处理的 tool_call_ids
                        for tc in tool_calls:
                            tc_id = tc.get("id", "")
                            if tc_id:
                                pending_tool_calls.add(tc_id)
                else:
                    # 普通 assistant message
                    messages.append({
                        "role": "assistant",
                        "content": msg.get("content", "")
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
        
        # 构建下一步执行提示
        next_step_prompt = ""
        if steps:
            first_step = steps[0]
            tool = first_step.get("tool", "")
            params = first_step.get("parameters", {})
            description = first_step.get("description", "")
            
            if tool:
                next_step_prompt = f"\n\n**下一步操作：** 立即调用 `{tool}` 工具执行步骤1。"
                if tool == "delegate_to_subagent" and "subagent_name" in params:
                    subagent_name = params["subagent_name"]
                    task_desc = params.get("task_description", description)
                    next_step_prompt += f"\n\n请调用：\n```\n{tool}(\n  subagent_name=\"{subagent_name}\",\n  task_description=\"{task_desc}\"\n)\n```"
        
        # 返回结果
        result = {
            "success": True,
            "plan_id": plan.plan_id,
            "plan": {
                "goal": goal,
                "steps": steps,
                "execution_mode": execution_mode
            },
            "message": f"计划创建成功，共{len(steps)}个步骤。计划已保存到: plans/{session_id}.md{next_step_prompt}",
            "is_simple_task": len(steps) == 1,
            "next_step": {
                "step_number": 1,
                "tool": steps[0].get("tool") if steps else None,
                "parameters": steps[0].get("parameters") if steps else None,
                "description": steps[0].get("description") if steps else None,
            } if steps else None
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

        # 检查 skill 是否在允许列表中
        if hasattr(self.skill_registry, 'is_allowed') and not self.skill_registry.is_allowed(skill_name):
            allowed = self.skill_registry.get_allowed_list()
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not allowed. Available: {allowed or 'all'}",
                "available_skills": allowed
            }

        skill = self.skill_registry.get(skill_name)
        skill_content = self.skill_registry.get_content(skill_name)

        if skill_content is None:
            available = self.skill_registry.list_skills()
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found",
                "available_skills": available
            }

        logger.info(f"后端日志：_handle_use_skill 加载技能", extra={
            "skill_name": skill_name,
            "skill_content_length": len(skill_content) if skill_content else 0
        })

        # 获取 skill 描述用于摘要
        skill_desc = skill.description if skill else ""

        logger.info(f"后端日志：_handle_use_skill 返回技能内容，LLM需要决定是否调用skill_execute")

        return {
            "success": True,
            "skill_name": skill_name,
            "content": skill_content,
            "message": f"✅ Skill '{skill_name}' loaded. {skill_desc}"
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
        Handle skill_execute tool call - execute command directly in runtime environment

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
        
        # 处理脚本路径 - 将相对路径转换为绝对路径
        processed_command = command
        if skill.scripts:
            for script_path in skill.scripts:
                script_name = script_path.name
                # 替换 scripts/script_name 格式
                processed_command = processed_command.replace(
                    f"scripts/{script_name}",
                    str(script_path.absolute())
                )
                # 替换 ./scripts/script_name 格式
                processed_command = processed_command.replace(
                    f"./scripts/{script_name}",
                    str(script_path.absolute())
                )

        # 自动替换 {user_id} 和 {session_id} 占位符
        # LLM 可能自己编造 user_id，这里强制使用 session 中的真实值
        real_user_id = None
        real_session_id = None
        if session_id:
            from src.db.models import SessionDB
            session_info = SessionDB.get_by_id(session_id)
            if session_info:
                real_user_id = session_info.get("user_id")
                real_session_id = session_id
                logger.info(f"后端日志：skill_execute 获取真实 user_id={real_user_id}")

        # 替换占位符
        if "{user_id}" in processed_command and real_user_id:
            processed_command = processed_command.replace("{user_id}", real_user_id)
            logger.info(f"后端日志：已替换 {{user_id}} 占位符")
        if "{session_id}" in processed_command and real_session_id:
            processed_command = processed_command.replace("{session_id}", real_session_id)
            logger.info(f"后端日志：已替换 {{session_id}} 占位符")

        # 如果命令中仍然包含 --user-id 且值看起来像 LLM 编造的（包含日期等），强制替换
        # LLM 编造的典型格式：user_20260325, user_123, test_user 等
        import re
        # 匹配 --user-id "xxx" 或 --user-id 'xxx' 或 --user-id xxx
        user_id_pattern = r'--user-id["\s]+["\']?([^"\'\s]+)["\']?'
        matches = re.findall(user_id_pattern, processed_command)
        for old_user_id in matches:
            # 检查是否像 LLM 编造的（简单判断：包含数字或 test_ 开头）
            if old_user_id != real_user_id and real_user_id:
                # 强制替换为真实值
                processed_command = re.sub(
                    rf'--user-id["\s]+["\']?{re.escape(old_user_id)}["\']?',
                    f'--user-id "{real_user_id}"',
                    processed_command
                )
                logger.info(f"后端日志：强制替换 LLM 编造的 user_id '{old_user_id}' -> '{real_user_id}'")
        
        decoded_files = {}
        if files:
            for filename, content_b64 in files.items():
                try:
                    decoded_files[filename] = base64.b64decode(content_b64)
                except Exception as e:
                    logger.warning(f"Failed to decode file {filename}: {e}")

        try:
            # 注意：user_id 和 session_id 已经在上面替换命令占位符时获取过了
            # processed_command 中的 user_id 已经被替换为真实值
            if workdir and workdir.exists():
                result = await self.skill_executor.execute_skill_command(
                    skill_name=skill_name,
                    command=processed_command,
                    files=decoded_files if decoded_files else None,
                    session_id=real_session_id,
                    user_id=real_user_id
                )
            else:
                result = await self.skill_executor.execute_skill_command(
                    skill_name=skill_name,
                    command=processed_command,
                    files=decoded_files if decoded_files else None,
                    session_id=real_session_id,
                    user_id=real_user_id
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
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> Dict[str, Any]:
        """
        Handle delegate_to_subagent tool call - delegate task to a subagent

        Args:
            subagent_name: Name of the subagent to delegate to
            task_description: Description of the task
            context_needed: Keywords for context filtering (optional)
            session_id: Session ID for memory access
            progress_callback: 进度回调函数，用于实时传递子智能体执行进度

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

            # 创建子智能体专用的回调包装器
            # 子智能体的 send_progress/send_tool_start/send_tool_result 已经将消息包装为 dict，
            # 而主智能体的 progress_callback (send_progress) 会再包装一层 {"type": "progress", "data": ...}
            # 这里需要提取子智能体事件中的实际内容，作为字符串传给上层，避免重复包装
            async def subagent_progress_wrapper(event):
                """将子智能体的事件转发给上层 progress_callback，避免嵌套包装

                Args:
                    event: 子智能体传递的事件，可能是字符串或字典
                """
                if not progress_callback:
                    return
                if isinstance(event, str):
                    # 字符串直接传给上层，由 send_progress 包装一次
                    await progress_callback(event)
                elif isinstance(event, dict):
                    event_type = event.get("type", "")
                    event_data = event.get("data", "")
                    if event_type == "progress":
                        # progress 事件：提取 data 字符串，由上层 send_progress 包装一次
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    elif event_type in ("tool_start", "tool_result"):
                        # tool_start/tool_result 事件已经是完整格式，直接传给上层
                        # 上层 main.py 的 sync_progress_callback 会原样保存到 progress 列表
                        # 但由于 progress_callback 是 send_progress（只接受字符串），这里需要特殊处理
                        # 暂时将 tool 事件转为 progress 字符串传递，避免嵌套
                        tool_name = event.get("toolName", event.get("tool_name", ""))
                        if event_type == "tool_start":
                            await progress_callback(f"🔧 正在执行 {tool_name}...")
                        elif event_type == "tool_result":
                            success = event.get("success", True)
                            if success:
                                await progress_callback(f"✅ {tool_name} 执行完成")
                            else:
                                error = event.get("result", {}).get("error", "未知错误") if isinstance(event.get("result"), dict) else str(event.get("result", ""))
                                await progress_callback(f"❌ {tool_name} 执行失败: {error}")
                    elif event_type == "thinking":
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    else:
                        await progress_callback(str(event))
                else:
                    await progress_callback(str(event))

            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
                progress_callback=subagent_progress_wrapper,
            )
            
            if not response.success:
                return {
                    "success": False,
                    "error": response.error or "Delegation failed"
                }
            
            # Wait for result
            record = await self.subagent_executor.wait_for_result(
                response.execution_id,
                timeout=7200  # 2 hours timeout
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
        attachments: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
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

        # 进度消息辅助函数
        async def send_progress(message: str):
            if progress_callback:
                await progress_callback({"type": "progress", "data": message})

        # 工具开始执行回调
        async def send_tool_start(tool_name: str, tool_args: dict):
            if progress_callback:
                await progress_callback({
                    "type": "tool_start",
                    "toolName": tool_name,
                    "toolArgs": tool_args
                })

        # 工具执行结果回调
        async def send_tool_result(tool_name: str, result: any, success: bool):
            if progress_callback:
                await progress_callback({
                    "type": "tool_result",
                    "toolName": tool_name,
                    "result": result,
                    "success": success
                })

        # LLM思考中回调
        async def send_thinking(message: str):
            if progress_callback:
                await progress_callback({"type": "thinking", "data": message})

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
                # 构建文件路径信息（无论是否有 auto_loaded_skill 都添加）
                files_context = ""
                if uploaded_files_info:
                    files_context = "\n\n**📎 Uploaded files available:**\n"
                    for f in uploaded_files_info:
                        files_context += f"- File: `{f['name']}`\n"
                        files_context += f"  Full path: `{f['path']}`\n"
                        files_context += f"  Size: {f['size']} bytes\n"
                    files_context += "\n**IMPORTANT: When delegating to subagent, include the file paths above in task_description!**\n"
                
                enhanced_input = timestamp_context + f"{user_input}\n\n[Attachments]\n" + "\n".join(attachment_info) + files_context
        
        self.memory.add(session_id, "user", enhanced_input)
        
        messages = self._build_messages(session_id)
        system_prompt = self._build_system_prompt(user)
        
        if auto_loaded_skill:
            skill_content = self.skill_registry.get_content(auto_loaded_skill)
            if skill_content:
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
        
        max_iterations = 20  # Prevent infinite loops
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
            
            # 后端日志：记录Agent迭代信息（关联LLM request_id）
            log_agent_iteration(
                iteration=iteration,
                request_id=response.get("request_id", ""),
                user_id=user.user_id if user else "",
                session_id=session_id,
                model=self.llm.get_model_name(),
                provider=self.llm.get_provider_name(),
                has_tool_calls=bool(tool_calls),
                tool_calls_count=len(tool_calls),
                tool_names=[tc.get("function", {}).get("name", tc.get("name", "")) for tc in tool_calls] if tool_calls else [],
                content_length=len(content) if content else 0,
                usage=response.get("usage"),
            )
            
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

                # 发送最终回复进度
                await send_progress("✅ 任务完成，正在生成回复...")

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

                # 获取工具的用户友好名称
                tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                # 发送工具开始执行事件
                await send_tool_start(tool_name, tool_args)
                await send_progress(f"🔧 正在执行 {tool_display_name}...")

                logger.info(f"Executing tool: {tool_name} with args: {json.dumps(tool_args, ensure_ascii=False)}")

                # Handle create_plan specially - create real plan and save to MD
                if tool_name == "create_plan":
                    plan_result = self._handle_create_plan(
                        args=tool_args,
                        session_id=session_id,
                        user_query=user_input,
                    )
                    # 发送工具执行结果
                    await send_tool_result(tool_name, plan_result, plan_result.get("success", True))
                    await send_progress(f"📋 执行计划已创建")
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
                    clarify_result = {
                        "success": True,
                        "question": question,
                        "missing_info": missing_info
                    }
                    # 发送工具执行结果
                    await send_tool_result(tool_name, clarify_result, True)
                    await send_progress(f"❓ 需要澄清: {question[:50]}...")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": clarify_result
                    })
                    continue

                # Handle use_skill - load skill content and inject into conversation
                if tool_name == "use_skill":
                    skill_name = tool_args.get("skill", "")
                    skill_result = self._handle_use_skill(skill_name)
                    # 发送工具执行结果
                    await send_tool_result(tool_name, skill_result, skill_result.get("success", True))
                    await send_progress(f"📦 已加载技能: {skill_name}")
                    tool_results.append({
                        "tool_call_id": tool_id,
                        "content": skill_result
                    })
                    continue

                # Handle skill_execute - execute command directly
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

                    # 发送技能执行完成进度
                    # 发送工具执行结果
                    await send_tool_result(tool_name, skill_exec_result, skill_exec_result.get("success", True))
                    if skill_exec_result.get("success"):
                        stdout = skill_exec_result.get("stdout", "")
                        preview = stdout[:100] if stdout else ""
                        await send_progress(f"✅ 技能「{skill_name}」执行完成: {preview}...")
                    else:
                        error = skill_exec_result.get("error", "未知错误")
                        await send_progress(f"❌ 技能「{skill_name}」执行失败: {error}")

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
                    await send_progress(f"🚀 正在调用{subagent_name}子智能体处理任务...")

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
                        progress_callback=send_progress,
                    )

                    # 发送工具执行结果
                    await send_tool_result(tool_name, delegation_result, delegation_result.get("success", True))

                    # 子智能体执行完成进度
                    if delegation_result.get("success"):
                        summary = delegation_result.get("summary", "")
                        preview = summary[:100] if summary else ""
                        await send_progress(f"✅ {subagent_name}子智能体任务完成: {preview}...")
                    else:
                        error = delegation_result.get("error", "未知错误")
                        await send_progress(f"❌ {subagent_name}子智能体执行失败: {error}")

                    # 如果子智能体生成了内容（content_generate），实时展示给用户
                    if delegation_result.get("generated_contents"):
                        for content in delegation_result["generated_contents"]:
                            yield f"\n📝 **内容生成结果：**\n\n{content}\n\n"

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
                    logger.info(f"[TOOL_RESULT] {tool_name}: type={type(result).__name__}")

                    # 发送工具执行完成事件
                    tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                    if isinstance(result, dict):
                        success = result.get("success", True)
                        await send_tool_result(tool_name, result, success)
                        if success:
                            # 根据不同工具显示不同结果预览
                            if tool_name == "content_generate":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                await send_progress(f"✅ {tool_display_name}完成\n📝 {preview}")
                            elif tool_name == "web_search":
                                results = result.get("results", [])
                                await send_progress(f"✅ {tool_display_name}完成，找到{len(results)}条结果")
                            elif tool_name == "email_send":
                                await send_progress(f"✅ {tool_display_name}成功")
                            elif tool_name == "file_read":
                                content = result.get("content", "")
                                preview = content[:80] + "..." if len(content) > 80 else content
                                await send_progress(f"✅ {tool_display_name}完成\n📄 {preview}")
                            elif tool_name == "browser_open":
                                await send_progress(f"✅ {tool_display_name}成功")
                            else:
                                await send_progress(f"✅ {tool_display_name}执行完成")
                        else:
                            error = result.get("error", "未知错误")
                            await send_progress(f"❌ {tool_display_name}失败: {error}")
                    else:
                        await send_tool_result(tool_name, result, True)
                        await send_progress(f"✅ {tool_display_name}执行完成")

                    # 对于 content_generate 工具，将结果格式化为可展示的内容并立即输出
                    if tool_name == "content_generate":
                        if isinstance(result, dict):
                            success = result.get("success")
                            content = result.get("content", "")
                            logger.info(f"[CONTENT_GEN] success={success}, content_len={len(content) if content else 0}")
                            if success and content:
                                yield f"\n📝 **内容生成结果：**\n\n{content}\n\n"
                        else:
                            logger.warning(f"[CONTENT_GEN] Unexpected result type: {type(result)}")

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
                    # 发送工具执行结果（失败）
                    await send_tool_result(tool_name, {"error": error_msg}, False)
                    await send_progress(f"❌ {self._get_tool_display_name(tool_name, tool_args)}执行出错: {str(e)}")
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
    
    async def execute_as_subagent(
        self,
        task_description: str,
        parent_session_id: str,
        task_record=None,
        progress_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
    ) -> Dict[str, Any]:
        """
        作为子智能体执行任务

        流程：
          2. 执行计划中的任务
        3. 将执行记录同步到主智能体的计划管理器

        Args:
            task_description: 任务描述
            parent_session_id: 父智能体的session ID
            task_record: 任务记录（用于状态更新）
            progress_callback: 进度回调函数，用于实时传递执行进度到主界面

        Returns:
            执行结果
        """
        if self.is_master:
            raise RuntimeError("execute_as_subagent() is only for subagent mode")

        # 进度消息辅助函数
        async def send_progress(message: str):
            if progress_callback:
                await progress_callback({"type": "progress", "data": message})

        # 工具开始执行回调
        async def send_tool_start(tool_name: str, tool_args: dict):
            if progress_callback:
                await progress_callback({
                    "type": "tool_start",
                    "toolName": tool_name,
                    "toolArgs": tool_args
                })

        # 工具执行结果回调
        async def send_tool_result(tool_name: str, result: any, success: bool):
            if progress_callback:
                await progress_callback({
                    "type": "tool_result",
                    "toolName": tool_name,
                    "result": result,
                    "success": success
                })

        # LLM思考中回调
        async def send_thinking(message: str):
            if progress_callback:
                await progress_callback({"type": "thinking", "data": message})

        logger.info(f"\n{'='*60}\n[SUBAGENT] execute_as_subagent started\n{'='*60}")
        logger.info(f"[SUBAGENT] config.name: {self.subagent_config.name}")
        logger.info(f"[SUBAGENT] session_id: {self.session_id}")
        logger.info(f"[SUBAGENT] execution_id: {self.execution_id}")
        logger.info(f"[SUBAGENT] task_description: {task_description}")
        
        try:
            # 步骤1：构建消息（子智能体不使用历史消息，只使用任务描述）
            messages = []
            
            # 添加当前时间上下文
            from datetime import datetime
            current_time = datetime.now()
            timestamp_context = (
                f"[当前时间: {current_time.strftime('%Y年%m月%d日 %H:%M:%S')}, "
                f"{current_time.strftime('%A')}, "
                f"今年是{current_time.year}年]\n\n"
            )
            
            # 添加任务描述
            messages.append({
                "role": "user",
                "content": timestamp_context + task_description
            })
            
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            
            # 步骤2：让LLM理解任务并创建计划（如果需要）
            # 子智能体在第一次迭代时可能会调用 create_plan
            max_iterations = 20
            iteration = 0
            final_result = None
            final_summary = ""
            subagent_plan_created = False
            generated_content_list = []  # 存储所有生成的内容
            
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"[SUBAGENT] Iteration {iteration}")

                # 发送迭代进度
                # await send_progress(f"🔄 [{self.subagent_config.name}] 第{iteration}轮思考中...")

                # 更新进度
                if task_record:
                    progress = min(90.0, iteration * 5.0)
                    task_record.update_progress(progress, f"Processing iteration {iteration}")
                
                # 打印LLM调用信息（与主智能体一致）
                logger.debug(f"\n{'='*60}\n"
                            f"[DEBUG] Subagent Iteration {iteration} - Full Prompt\n"
                            f"{'='*60}\n"
                            f"[System Prompt]:\n{system_prompt}\n"
                            f"{'-'*60}\n"
                            f"[Messages]:\n{json.dumps(messages, ensure_ascii=False, indent=2)}\n"
                            f"{'-'*60}\n"
                            f"[Tools]: {json.dumps([t.get('name', t.get('function', {}).get('name', 'unknown')) for t in tools], ensure_ascii=False)}\n"
                            f"{'='*60}")
                
                # 调用LLM
                response = await self.llm.chat_with_tools(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools
                )
                
                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])
                
                # 后端日志：记录子智能体迭代信息（关联LLM request_id）
                log_agent_iteration(
                    iteration=iteration,
                    request_id=response.get("request_id", ""),
                    user_id="",
                    session_id=self.session_id or "",
                    model=self.llm.get_model_name(),
                    provider=self.llm.get_provider_name(),
                    has_tool_calls=bool(tool_calls),
                    tool_calls_count=len(tool_calls),
                    tool_names=[tc.get("function", {}).get("name", tc.get("name", "")) for tc in tool_calls] if tool_calls else [],
                    content_length=len(content) if content else 0,
                    usage=response.get("usage"),
                )
                
                # 打印LLM响应信息
                logger.debug(f"\n{'='*60}\n"
                            f"[DEBUG] Subagent LLM Response - Iteration {iteration}\n"
                            f"{'='*60}\n"
                            f"[Content]:\n{content if content else '(None)'}\n"
                            f"{'-'*60}\n"
                            f"[Tool Calls]: {len(tool_calls)} call(s)\n"
                            f"{json.dumps(tool_calls, ensure_ascii=False, indent=2) if tool_calls else '(None)'}\n"
                            f"{'='*60}")
                
                # 如果没有工具调用，任务完成
                if not tool_calls:
                    final_result = {"content": content}
                    final_summary = content[:500] if content else "Task completed"
                    break
                
                # 添加助手消息
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls
                })
                
                # 执行工具调用
                for tc in tool_calls:
                    if "function" in tc:
                        tool_name = tc["function"].get("name", "")
                        args_raw = tc["function"].get("arguments", "{}")
                        if isinstance(args_raw, str):
                            try:
                                tool_args = json.loads(args_raw) if args_raw else {}
                            except json.JSONDecodeError:
                                tool_args = {}
                        else:
                            tool_args = args_raw
                    else:
                        tool_name = tc.get("name", "")
                        tool_args = tc.get("arguments", {})
                    
                    if not tool_name:
                        continue

                    logger.info(f"[SUBAGENT] Executing tool: {tool_name}")

                    # 发送工具执行进度
                    tool_display_name = self._get_tool_display_name(tool_name, tool_args)
                    # 发送工具开始执行事件
                    await send_tool_start(tool_name, tool_args)
                    await send_progress(f"🔧 [{self.subagent_config.name}] 正在执行 {tool_display_name}...")

                    # 处理 create_plan（子智能体创建自己的计划）
                    if tool_name == "create_plan":
                        plan_result = self._handle_create_plan(
                            args=tool_args,
                            session_id=self.session_id,
                            user_query=task_description,
                        )
                        tool_result = plan_result
                        subagent_plan_created = True
                        # 发送工具执行结果
                        await send_tool_result(tool_name, plan_result, plan_result.get("success", True))

                        # 同步到父智能体的计划管理器
                        if self.parent_plan_manager:
                            # 这里可以添加逻辑，将子智能体的计划同步到父智能体的计划记录中
                            logger.info(f"[SUBAGENT] Syncing plan to parent plan manager")
                    
                    # 处理技能工具
                    elif tool_name == "use_skill":
                        skill_name = tool_args.get("skill", "")
                        skill_result = self._handle_use_skill(skill_name)
                        tool_result = skill_result
                        # 发送工具执行结果
                        await send_tool_result(tool_name, skill_result, skill_result.get("success", True))
                    elif tool_name == "skill_execute":
                        skill_name = tool_args.get("skill", "")
                        command = tool_args.get("command", "")
                        files = tool_args.get("files", {})
                        skill_exec_result = await self._handle_skill_execute(
                            skill_name=skill_name,
                            command=command,
                            files=files,
                            session_id=self.session_id,
                        )
                        tool_result = skill_exec_result
                        # 发送工具执行结果
                        await send_tool_result(tool_name, skill_exec_result, skill_exec_result.get("success", True))

                        # 同步到父智能体的计划管理器
                        if self.parent_plan_manager and subagent_plan_created:
                            # 获取当前计划中的任务
                            plan = self.plan_manager.get_plan(self.session_id)
                            if plan:
                                task = self.plan_manager.get_next_pending_task(self.session_id)
                                if task and task.tool_name == "skill_execute":
                                    self.plan_manager.mark_task_running(self.session_id, task.task_id)
                                    if skill_exec_result.get("success"):
                                        self.plan_manager.mark_task_completed(
                                            self.session_id, task.task_id, skill_exec_result
                                        )
                                    else:
                                        self.plan_manager.mark_task_failed(
                                            self.session_id, task.task_id,
                                            skill_exec_result.get("error", "Unknown error")
                                        )
                    else:
                        # 执行普通工具
                        try:
                            result = await self.tool_executor.execute(tool_name, tool_args)
                            tool_result = result

                            # 发送工具执行完成进度
                            if isinstance(result, dict):
                                success = result.get("success", True)
                                # 发送工具执行结果
                                await send_tool_result(tool_name, result, success)
                                if success:
                                    await send_progress(f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成")
                                else:
                                    error = result.get("error", "未知错误")
                                    await send_progress(f"❌ [{self.subagent_config.name}] {tool_display_name}失败: {error}")
                            else:
                                await send_tool_result(tool_name, result, True)
                                await send_progress(f"✅ [{self.subagent_config.name}] {tool_display_name}执行完成")

                            # 对于 content_generate 工具，保存生成的内容
                            if tool_name == "content_generate" and isinstance(result, dict):
                                generated_content = result.get("content", "")
                                if generated_content:
                                    generated_content_list.append(generated_content)
                                    logger.info(f"[SUBAGENT] content_generate: saved content length={len(generated_content)}")
                            
                            # 同步到父智能体的计划管理器
                            if self.parent_plan_manager and subagent_plan_created:
                                # 获取当前计划中的任务
                                plan = self.plan_manager.get_plan(self.session_id)
                                if plan:
                                    task = self.plan_manager.get_next_pending_task(self.session_id)
                                    if task:
                                        self.plan_manager.mark_task_running(self.session_id, task.task_id)
                                        if result.get("success", True):
                                            self.plan_manager.mark_task_completed(
                                                self.session_id, task.task_id, result
                                            )
                                        else:
                                            self.plan_manager.mark_task_failed(
                                                self.session_id, task.task_id,
                                                result.get("error", "Tool execution failed")
                                            )
                        except Exception as e:
                            tool_result = {"error": str(e)}
                            # 发送工具执行结果（失败）
                            await send_tool_result(tool_name, {"error": str(e)}, False)

                            # 标记任务失败
                            if self.parent_plan_manager and subagent_plan_created:
                                plan = self.plan_manager.get_plan(self.session_id)
                                if plan:
                                    task = self.plan_manager.get_next_pending_task(self.session_id)
                                    if task:
                                        self.plan_manager.mark_task_failed(
                                            self.session_id, task.task_id, str(e)
                                        )
                    
                    # 添加工具结果
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": str(tool_result)
                    })

            # 发送子任务完成消息
            await send_progress(f"✅ [{self.subagent_config.name}] 任务完成，正在整合结果...")

            logger.info(f"[SUBAGENT] Task completed with summary: {final_summary[:200]}")
            
            # 如果有生成的内容，合并到结果中
            if generated_content_list and final_result:
                combined_content = "\n\n".join(generated_content_list)
                if isinstance(final_result, dict):
                    final_result["generated_contents"] = generated_content_list
                    final_result["combined_content"] = combined_content
                else:
                    final_result = {
                        "content": str(final_result),
                        "generated_contents": generated_content_list,
                        "combined_content": combined_content
                    }
            
            return {
                "result": final_result,
                "summary": final_summary,
                "generated_contents": generated_content_list,  # 包含所有生成的内容
                "token_usage": {"input": 0, "output": 0}  # TODO: 实际统计
            }
            
        except Exception as e:
            import traceback
            logger.error(f"[SUBAGENT] Execution failed: {e}")
            logger.error(f"[SUBAGENT] Traceback:\n{traceback.format_exc()}")
            return {
                "result": None,
                "summary": f"Failed: {e}",
                "error": str(e)
            }


# Global agent instance (默认为主智能体)
master_agent = Agent(is_master=True)
agent = master_agent  # 别名，向后兼容
