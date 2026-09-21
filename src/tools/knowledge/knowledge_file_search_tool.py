"""知识库文件搜索工具 - 按文件名/标题定位文档，供 Agent 调用

与 knowledge_base_search（内容语义检索）互补：本工具回答「知识库里有没有
某个文件、路径在哪」，返回 file_path 供 LLM 用 read 工具分页读取全文。

背景（2026-09-20 生产案例）：租户提示词按文件名引用知识库文档时，LLM 拿
标题当 read 路径连败 3 次。本工具让 LLM 第一轮即可定位文件。
"""

from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.knowledge.retriever.tenant_range import load_shared_ranges, resolve_category_scope


class KnowledgeFileSearchInput(BaseModel):
    """按文件名搜索知识库文档参数"""
    file_name: str = Field(
        ...,
        description="文件名或标题关键词，如「数据分析助手·融合知识库.md」，"
        "也可以只传部分关键词如「融合知识库」",
    )
    exact: Optional[bool] = Field(
        False,
        description="是否精确匹配文件名（默认 False 模糊包含匹配；"
        "精确匹配时也兼容只传文件名不带扩展名）",
    )
    source_type: Optional[str] = Field(
        None,
        description="文档来源类型过滤，不传则搜索全部类型",
    )


class KnowledgeFileSearchTool(BaseTool):
    """知识库文件搜索工具（按文件名定位）"""

    name = "knowledge_file_search"
    description = (
        "按文件名/标题在知识中心定位文档，返回文件路径（file_path）等信息。"
        "当系统提示词或用户提到知识库中的某个文件（按文件名/标题引用）时，"
        "先用本工具定位拿到 file_path，再用 read 工具读取内容；"
        "知识库文档不能用 read 直接按标题读取。"
        "注意：本工具只按文件名匹配，不搜内容；"
        "不知道文件名、想按问题找相关内容时，改用 knowledge_base_search。"
    )
    display_name = "查找知识库文件"
    InputModel = KnowledgeFileSearchInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示文件名关键词"""
        base = self.display_name
        if tool_args:
            file_name = tool_args.get("file_name", "")
            if file_name:
                return f"{base}「{file_name}」"
        return base

    async def execute(self, **kwargs) -> Dict[str, Any]:
        file_name = kwargs.get("file_name")
        exact = kwargs.get("exact")
        source_type = kwargs.get("source_type")

        # LLM 可能以字符串传 exact（如 "true"/"False"），统一归一为 bool
        if isinstance(exact, str):
            exact = exact.strip().lower() in ("true", "1", "yes")

        if not file_name or not str(file_name).strip():
            return {
                "success": False,
                "error": "文件名不能为空",
                "results": [],
                "count": 0,
            }

        from src.tools.context import current_tool_execution_context
        context = current_tool_execution_context()
        tenant_id = context.tenant_id if context else None
        subagent_id = context.subagent_id if context else None

        # 栏目授权收口（收窄 / 拒绝）；未配置精细授权时行为与现状完全一致
        source_type, rejection = resolve_category_scope(tenant_id, subagent_id, source_type)
        if rejection:
            return {
                "success": False,
                "error": "本次检索已拒绝：请求的栏目未授权",
                "results": [],
                "count": 0,
                **rejection,
            }

        # 共享范围：仅子智能体 + 租户模式生效（主智能体 subagent_id 为空，恒为空）。
        # 从 DB 权威表读取，不信任 LLM 传入参数。
        # source_type 为授权收窄 list 时共享侧不过滤栏目（精确对本身已约束）。
        shared_ranges = []
        if tenant_id and subagent_id:
            shared_ranges = load_shared_ranges(
                tenant_id, subagent_id,
                source_type if isinstance(source_type, str) else None,
            )

        logger.info(
            f"后端日志：知识库文件搜索 tenant_id={tenant_id}, subagent_id={subagent_id}, "
            f"file_name={file_name}, exact={exact}, source_type={source_type}, "
            f"shared_ranges={shared_ranges}"
        )

        # 延迟导入：避免工具模块顶层拉起 service 全链
        from src.knowledge.service import knowledge_service

        result = knowledge_service.search_documents_by_title(
            tenant_id=tenant_id,
            file_name=str(file_name),
            shared_ranges=shared_ranges,
            exact=bool(exact),
            source_type=source_type,
        )

        if not result.get("success"):
            return result

        for item in result.get("results", []):
            if item.get("file_path"):
                item["read_hint"] = (
                    f"可调用 read(file_path=\"{item['file_path']}\") 分页读取此文件内容"
                )

        # 0 命中时给 LLM 明确的改路引导（误用自愈闭环：内容需求应走语义检索）
        if result.get("count", 0) == 0:
            result["note"] = (
                "知识库中没有按此文件名匹配到的文档。"
                "可尝试更短的关键词，或改用 knowledge_base_search 按内容检索。"
            )

        result["_no_truncate"] = True
        return result
