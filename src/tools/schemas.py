#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tool Schemas - LLM Function Calling Definitions

This module defines the JSON Schema for all tools available to the LLM agent.
These schemas are used for LLM function calling and are SEPARATE from the
actual tool implementations in ToolRegistry.

When adding a new tool, you must update BOTH:
1. The schema in this file (AGENT_TOOLS list)
2. The registration in Agent._register_builtin_tools()
"""

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
        "description": "在技能上下文中执行命令。加载技能后使用此功能运行pdftotext、python脚本等命令。重要：使用简单命令，对于Python优先使用简单的一行命令或直接使用pypdf/pdfplumber。注意：对于引导式技能（无脚本的技能），可能不需要执行命令。",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "要使用的技能上下文名称"
                },
                "command": {
                    "type": "string",
                    "description": "要执行的命令（可选）。对于引导式技能可能不需要执行命令。"
                },
                "files": {
                    "type": "object",
                    "description": "可选的文件，使其在执行环境中可用（文件名 -> base64内容）",
                    "additionalProperties": {
                        "type": "string"
                    }
                }
            },
            "required": ["skill"]
        }
    },
    {
        "name": "skill_complete",
        "description": (
            "标记当前技能执行完成。当技能指南中的所有步骤都已执行完毕时调用此工具。"
            "调用后系统会自动清理技能过程中的中间消息，仅保留最终结果摘要。"
            "⚠️ 必须在 use_skill 之后、技能所有步骤完成后才能调用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "已完成执行的技能名称"
                },
                "summary": {
                    "type": "string",
                    "description": "技能执行的最终结果摘要（1-3句话），将替代所有中间过程存入对话历史"
                }
            },
            "required": ["skill", "summary"]
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
        "name": "browser_snapshot",
        "description": "获取当前页面的语义快照，返回结构化的页面表示。必须先调用此工具获取快照，才能使用browser_click/fill/select等工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "mode": {
                    "type": "string",
                    "enum": ["standard", "interactive", "compact"],
                    "description": "快照模式：standard标准模式，interactive交互模式（推荐），compact紧凑模式",
                    "default": "interactive"
                }
            },
            "required": []
        }
    },
    {
        "name": "browser_click",
        "description": "点击页面元素（语义快照驱动）。通过自然语言描述要点击的元素，系统自动在快照中匹配。**必须先调用browser_snapshot获取语义快照！**示例：description=\"登录按钮\"",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "要点击元素的自然语言描述，如'登录按钮'、'报销申请'"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（毫秒），默认10000",
                    "default": 10000
                }
            },
            "required": ["description"]
        }
    },
    {
        "name": "browser_fill",
        "description": "填写表单字段（语义快照驱动）。通过自然语言描述字段，系统自动在快照中匹配。**必须先调用browser_snapshot获取语义快照！**示例：field=\"用户名\", value=\"张三\"",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "要填写的字段描述，如'用户名'、'报销金额'"
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
                    "description": "超时时间（毫秒），默认10000",
                    "default": 10000
                }
            },
            "required": ["field", "value"]
        }
    },
    {
        "name": "browser_select",
        "description": "选择下拉选项（语义快照驱动）。通过自然语言描述下拉框和选项，系统自动在快照中匹配。**必须先调用browser_snapshot获取语义快照！**示例：field=\"部门\", option=\"技术研发部\"",
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "下拉选择框的描述，如'部门'、'报销类型'"
                },
                "option": {
                    "type": "string",
                    "description": "要选择的选项，如'技术研发部'"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                }
            },
            "required": ["field", "option"]
        }
    },
    {
        "name": "browser_find",
        "description": "根据语义描述查找页面元素，返回匹配结果和备选列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "元素的语义描述，如'登录按钮'、'报销金额输入框'"
                },
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "scope": {
                    "type": "string",
                    "enum": ["viewport", "page"],
                    "description": "搜索范围：viewport当前视口，page整页（默认page）",
                    "default": "page"
                }
            },
            "required": ["description"]
        }
    },
    {
        "name": "browser_get_path",
        "description": "获取当前的浏览器操作路径历史。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "输出格式：text文本格式，json为JSON格式",
                    "default": "text"
                }
            },
            "required": []
        }
    },
    {
        "name": "browser_backtrack",
        "description": "回溯到之前的页面状态。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "浏览器会话ID，默认为'default'"
                },
                "steps": {
                    "type": "integer",
                    "description": "回溯的步数，默认为1",
                    "default": 1
                }
            },
            "required": []
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
    },
    {
        "name": "knowledge_base_search",
        "description": "从企业知识库中检索相关信息，回答用户问题。当用户询问关于公司制度、文档资料、产品信息等问题时使用此工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用户问题或查询关键词"
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的相关段落数量，默认 10",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
]
