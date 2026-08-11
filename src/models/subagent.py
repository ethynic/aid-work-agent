"""
Subagent数据模型

定义Subagent的配置、执行上下文和委托请求/响应模型
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum

from pydantic import BaseModel, Field


class SubagentTaskStatus(str, Enum):
    """子智能体任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CLARIFYING = "clarifying"  # 等待澄清
    CANCELLED = "cancelled"


class SubagentConfig(BaseModel):
    """
    Subagent配置定义
    
    从SUBAGENT.md文件解析得到的配置
    """
    # 基本信息
    name: str = Field(..., description="智能体名称")
    dir_name: str = Field(default="", description="目录名称（用于URL路由等场景）")
    description: str = Field(default="", description="智能体描述")
    version: str = Field(default="1.0.0", description="版本号")
    author: str = Field(default="unknown", description="作者")

    # 触发条件
    triggers: Dict[str, Any] = Field(default_factory=dict, description="触发条件配置")
    # triggers 结构:
    # {
    #     "file_patterns": ["*.py", "*.js"]
    # }
    
    # 工具配置
    tools: Dict[str, Any] = Field(default_factory=dict, description="工具配置")
    # tools 结构:
    # {
    #     "inherit": false,
    #     "allowed": ["web_search", "skill_execute"]
    # }
    
    # 技能访问配置
    skills: Dict[str, Any] = Field(default_factory=dict, description="技能配置")
    # skills 结构:
    # {
    #     "allowed": ["pdf", "code"]
    # }
    
    # 上下文约束
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文约束")
    # context 结构:
    # {
    #     "max_input_tokens": 4000,
    #     "max_output_tokens": 2000
    # }
    
    # 系统提示词
    system_prompt: str = Field(default="", description="系统提示词")
    
    # 委派配置
    delegatable_to: List[str] = Field(default_factory=list, description="可委派给的子智能体列表")
    allow_delegation: bool = Field(default=True, description="是否允许委派任务")
    
    # 元数据
    path: Optional[str] = Field(default=None, description="配置文件路径")
    dir: Optional[str] = Field(default=None, description="配置目录路径")

    # LLM 配置覆盖
    llm_provider: Optional[str] = Field(default=None, description="覆盖 LLM 提供者（如 deepseek），为空则使用全局默认")
    llm_model_codes: Optional[Dict[str, str]] = Field(
        default=None,
        description="各 provider 的 model_code 覆盖，如 {'deepseek': 'deepseek-v4-pro', 'qwen': 'qwen3.7-plus'}。"
                    "未列出的 provider 使用全局默认 model",
    )

    # 回复风格
    reply_style: Optional[str] = Field(default=None, description="回复风格ID（对应 src/prompts/styles/ 下的文件名）")

    # 业务数据页面配置
    business_pages: Optional[List[Dict[str, Any]]] = Field(default=None, description="业务数据页面列表")

    # 聊天工具栏额外按钮 id 列表（声明式 UI 配置，详见 plan-video-agent-phase1.md §1.5）
    # 取值如 ["video_gen"]；与 tools（LLM 函数调用工具）、skills（技能包）、business_pages（业务页面）语义独立
    # 加号上传按钮由 ChatInput 硬编码渲染，所有智能体共有，不在此字段中
    chat_toolbar: List[str] = Field(default_factory=list, description="聊天工具栏额外按钮 id 列表")

    # 上传文件类型限定（对齐 HTML <input accept> 语法），未声明时走 chat.default_upload_accept 全局默认
    # 取值如 "image/*" / "image/*,video/*" / ".pdf,.docx"
    upload_accept: Optional[str] = Field(default=None, description="聊天输入框加号按钮可选文件类型限定")

    # 知识库关联配置
    knowledge_sources: List[Dict[str, str]] = Field(default_factory=list, description="关联的知识库列表，每项含 source_type 和 display_name")

    # 来源标记
    from_db: bool = Field(default=False, description="是否来自数据库加载")

    class Config:
        use_enum_values = True
    
    def get_allowed_tools(self, inherit_default: bool = False) -> List[str]:
        if not self.tools:
            return []

        if self.tools.get("inherit", False):
            return []

        # 兼容前端使用的 "additional" 和原始的 "allowed" 两种字段名
        return self.tools.get("allowed", []) or self.tools.get("additional", [])

    def get_excluded_tools(self) -> List[str]:
        """获取需要从工具集中排除的工具名列表（黑名单）。

        与 inherit/allowed 组合使用：先按 inherit/allowed 确定工具集，再 pop 排除项。
        典型场景：video-agent inherit=true 但排除 paddleocr_doc_parsing（改用多模态 LLM 看图）。
        """
        if not self.tools:
            return []
        return self.tools.get("excluded", []) or []

    
    def get_allowed_skills(self) -> List[str]:
        """获取允许使用的技能列表"""
        if not self.skills:
            return []
        return self.skills.get("allowed", [])
    
    def matches_file(self, filename: str) -> bool:
        """
        检查文件名是否匹配触发模式
        
        Args:
            filename: 文件名
            
        Returns:
            是否匹配
        """
        import fnmatch
        patterns = self.triggers.get("file_patterns", [])
        filename_lower = filename.lower()
        for pattern in patterns:
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                return True
        return False


class SubagentExecutionContext(BaseModel):
    """
    子智能体执行上下文
    
    记录一次子智能体执行的完整上下文信息
    """
    # 基本信息
    execution_id: str = Field(..., description="执行ID（唯一）")
    session_id: str = Field(..., description="所属session（与主智能体共享）")
    subagent_name: str = Field(..., description="subagent名称")
    
    # 状态
    status: SubagentTaskStatus = Field(
        default=SubagentTaskStatus.PENDING, 
        description="执行状态"
    )
    
    # 任务信息
    task_description: str = Field(default="", description="任务描述")
    task_parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    parent_agent: str = Field(default="", description="委托方智能体名称")
    
    # 执行
    thread_id: Optional[str] = Field(default=None, description="子线程ID")
    progress_percent: float = Field(default=0.0, description="进度百分比")
    current_step: str = Field(default="", description="当前步骤描述")
    
    # 澄清
    clarification_request: Optional[str] = Field(default=None, description="澄清请求")
    clarification_answer: Optional[str] = Field(default=None, description="澄清答案")
    
    # 结果
    result: Optional[Dict[str, Any]] = Field(default=None, description="执行结果")
    error: Optional[str] = Field(default=None, description="错误信息")
    summary: str = Field(default="", description="执行摘要")
    
    # 元数据
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    token_usage: Dict[str, int] = Field(default_factory=dict, description="Token使用统计")
    
    class Config:
        use_enum_values = True
    
    def start(self) -> None:
        """标记执行开始"""
        self.status = SubagentTaskStatus.RUNNING
        self.updated_at = datetime.now()
    
    def complete(self, result: Dict[str, Any], summary: str = "") -> None:
        """
        标记执行完成
        
        Args:
            result: 执行结果
            summary: 执行摘要
        """
        self.status = SubagentTaskStatus.COMPLETED
        self.result = result
        self.summary = summary
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
        self.progress_percent = 100.0
    
    def fail(self, error: str) -> None:
        """
        标记执行失败
        
        Args:
            error: 错误信息
        """
        self.status = SubagentTaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def request_clarification(self, question: str) -> None:
        """
        请求澄清
        
        Args:
            question: 澄清问题
        """
        self.status = SubagentTaskStatus.CLARIFYING
        self.clarification_request = question
        self.updated_at = datetime.now()
    
    def answer_clarification(self, answer: str) -> None:
        """
        回答澄清
        
        Args:
            answer: 澄清答案
        """
        self.clarification_answer = answer
        self.status = SubagentTaskStatus.RUNNING
        self.updated_at = datetime.now()
    
    def update_progress(self, percent: float, step: str = "") -> None:
        """
        更新进度
        
        Args:
            percent: 进度百分比
            step: 当前步骤
        """
        self.progress_percent = min(100.0, max(0.0, percent))
        self.current_step = step
        self.updated_at = datetime.now()
    
    def is_terminal(self) -> bool:
        """检查是否为终态"""
        return self.status in [
            SubagentTaskStatus.COMPLETED,
            SubagentTaskStatus.FAILED,
            SubagentTaskStatus.CANCELLED
        ]


class DelegationRequest(BaseModel):
    """委托请求"""
    task_description: str = Field(..., description="任务描述")
    task_parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")
    requester: str = Field(default="", description="请求方智能体名称")
    target_subagent: Optional[str] = Field(default=None, description="目标子智能体名称（可选，自动匹配）")
    context_filter: Optional[List[str]] = Field(default=None, description="需要传递的上下文过滤")
    timeout: int = Field(default=300, description="超时时间（秒）")


def extract_llm_config(raw: Any) -> tuple[Optional[str], Optional[Dict[str, str]]]:
    """DB JSONB 字段 -> (provider, model_codes)。

    兼容三种输入：
    - None：返回 (None, None)
    - 旧字符串格式（如 "deepseek"）：返回 ("deepseek", None)
    - 新 JSONB 格式 {"provider": "deepseek", "model_codes": {...}}：拆分返回
    """
    if raw is None:
        return None, None
    if isinstance(raw, str):
        return raw or None, None
    if isinstance(raw, dict):
        provider = raw.get("provider") or None
        model_codes = raw.get("model_codes")
        if isinstance(model_codes, dict):
            model_codes = {k: v for k, v in model_codes.items() if isinstance(v, str) and v} or None
        else:
            model_codes = None
        return provider, model_codes
    return None, None


def pack_llm_config(provider: Optional[str], model_codes: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """(provider, model_codes) -> DB JSONB 字段。

    两者都为空时返回 None；否则返回 {"provider": ..., "model_codes": {...}}。
    """
    if not provider and not model_codes:
        return None
    return {
        "provider": provider or None,
        "model_codes": dict(model_codes) if model_codes else {},
    }


class DelegationResponse(BaseModel):
    """委托响应"""
    success: bool = Field(..., description="是否成功")
    execution_id: str = Field(default="", description="执行ID")
    subagent_name: str = Field(default="", description="实际执行的子智能体名称")
    result: Optional[Dict[str, Any]] = Field(default=None, description="执行结果")
    error: Optional[str] = Field(default=None, description="错误信息")
    summary: str = Field(default="", description="执行摘要")
    token_usage: Dict[str, int] = Field(default_factory=dict, description="Token使用统计")
