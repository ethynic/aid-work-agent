"""
SmartDataAnalysisTool — 对外工具接口

BaseTool 子类，注册到主智能体工具列表。
接收用户分析需求，内部 Agent 循环自动完成：检索匹配表 → 加载数据 → 编排分析步骤 → 返回结果。
"""

import uuid
from typing import Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class AnalyzeDataInput(BaseModel):
    requirement: str = Field(
        description="用户的原始分析需求（自然语言）"
    )
    tables_metadata: Optional[List[dict]] = Field(
        None,
        description="[可选] 已匹配到的相关表 Metadata 列表。不传则由分析智能体自动搜索匹配数据表"
    )
    session_id: Optional[str] = Field(
        None,
        description="当前会话 ID，用于 DataFrame 缓存"
    )


class SmartDataAnalysisTool(BaseTool):
    name = "analyze_data"
    description = (
        "智能数据分析工具。接收用户的分析需求，自动搜索匹配相关数据表，"
        "编排分析步骤（查询、聚合、关联、对比、趋势、透视、计算、可视化等），"
        "一次性完成分析并返回结果摘要和图表文件。"
    )
    display_name = "智能数据分析"
    category = "data_analysis"
    InputModel = AnalyzeDataInput

    async def execute(self, **kwargs) -> Dict[str, object]:
        requirement = kwargs["requirement"]
        tables_metadata = kwargs.get("tables_metadata")
        session_id = kwargs.get("session_id")

        # 1. 初始化 DataAnalyzer
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        analyzer = DataAnalyzer(session_id=session_id)

        # 2. 解析 tenant_id
        tenant_id = None
        try:
            from src.saas.context import get_current_tenant_id
            tenant_id = get_current_tenant_id()
        except Exception:
            pass

        # 3. 预加载表（仅加载带 table_id/doc_id 的完整 metadata；简化结构交给 AnalysisAgent 自行检索加载）
        if tables_metadata:
            for meta in tables_metadata:
                table_id = meta.get("table_id") or meta.get("doc_id")
                if not table_id:
                    continue
                try:
                    await analyzer.load_table(meta)
                except Exception as e:
                    logger.error(f"[SmartDataAnalysisTool] 加载表失败: {e}", exc_info=True)
                    return {"success": False, "error": f"加载数据表失败: {e}"}

        # 4. 运行 AnalysisAgent
        try:
            from src.core import master_agent
            from src.tools.data_analysis.analysis_agent import AnalysisAgent

            analysis_id = f"analysis_{uuid.uuid4().hex[:8]}"
            agent = AnalysisAgent(
                llm_gateway=master_agent.llm,
                analyzer=analyzer,
                analysis_id=analysis_id,
                tables_metadata=tables_metadata,
                tenant_id=tenant_id,
            )
            result = await agent.run(requirement)
        except Exception as e:
            logger.error(f"[SmartDataAnalysisTool] AnalysisAgent 运行失败: {e}", exc_info=True)
            return {"success": False, "error": f"分析执行失败: {e}"}

        return result

    def get_display_name(self, tool_args=None) -> str:
        base = self.display_name
        if tool_args:
            requirement = tool_args.get("requirement", "")
            if requirement:
                preview = requirement[:20] + "..." if len(requirement) > 20 else requirement
                return f"{base}「{preview}」"
        return base
