#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PresentOptionsTool - 向用户出示选项卡片

在需要用户选择/确认的场景（分阶段工作流确认门、方案二选一等），把问题与候选选项
返回给前端：web 端在助手消息下方渲染编号按钮，点击即发送对应序号；用户也可直接
打字回复（序号或自由文本等效）。仅 frontend quickOptions 增强约定：
工具成功结果带 data.options 数组（≥2 项）即渲染按钮。
"""

from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class OptionItem(BaseModel):
    """单个选项"""
    key: str = Field(..., description="选项标识（点击后发送给用户的文本，通常为序号如 '1'）")
    label: str = Field(..., description="选项显示文本（简短）")
    description: Optional[str] = Field(None, description="选项补充说明（可缺省）")


class PresentOptionsInput(BaseModel):
    """选项卡片参数"""
    question: str = Field(..., description="向用户提出的问题")
    options: List[OptionItem] = Field(..., description="候选选项列表（2~6 个）")


class PresentOptionsTool(BaseTool):
    """选项卡片工具"""

    # 控制工具：不进普通 registry，由 ToolControlSet 构造。
    catalog = False
    name = "present_options"
    description = "向用户出示带候选选项的问题（选项卡片），供用户点击选择或打字回复"
    usage_guide = (
        "当任务需要用户在有限候选中做选择/确认时调用（如分阶段工作流的确认门）。"
        "options 传 2~6 个候选；同时应在回复正文中给出完整编号清单与必要上下文，"
        "因为渠道端无按钮、刷新后按钮会消失，文本编号列表是兜底。用户自由打字的诉求"
        "（如重命名/合并/增删）无法用卡片穷举时，预留一个「我要调整，请打字说明」类选项。"
    )
    display_name = "出示选项"
    category = "agent"
    InputModel = PresentOptionsInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        question = kwargs.get("question", "")
        options = kwargs.get("options", [])

        cleaned = []
        for opt in options:
            item = {"key": str(opt.get("key", "")), "label": str(opt.get("label", ""))}
            if opt.get("description"):
                item["description"] = str(opt["description"])
            if item["key"] and item["label"]:
                cleaned.append(item)

        if len(cleaned) < 2 or len(cleaned) > 6:
            return {
                "success": False,
                "error": "options 需要提供 2~6 个且每项含非空 key/label，实际 %d 个" % len(cleaned),
            }

        logger.info(f"PresentOptions tool called: question={question[:50]}... options={len(cleaned)}")

        return {
            "success": True,
            "question": question,
            "data": {"options": cleaned},
        }
