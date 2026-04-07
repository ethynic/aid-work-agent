#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClarifyTool - 向用户询问澄清

当信息缺失时向用户询问补充信息。
"""

from typing import Dict, Any, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class ClarifyInput(BaseModel):
    """澄清问题参数"""
    question: str = Field(..., description="向用户提出的问题")
    missing_info: Optional[List[str]] = Field(None, description="缺失的信息项列表")


class ClarifyTool(BaseTool):
    """澄清工具"""

    name = "clarify"
    description = "当信息缺失时向用户询问澄清"
    display_name = "澄清问题"
    category = "agent"
    InputModel = ClarifyInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        向用户提出澄清问题

        Args:
            question: 向用户提出的问题
            missing_info: 缺失的信息项列表

        Returns:
            澄清结果字典
        """
        question = kwargs.get("question", "")
        missing_info = kwargs.get("missing_info", [])

        logger.info(f"Clarify tool called: question={question[:50]}...")

        return {
            "success": True,
            "question": question,
            "missing_info": missing_info
        }
