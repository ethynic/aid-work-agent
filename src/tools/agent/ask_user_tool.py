#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AskUserTool - 阻塞式结构化提问（AskForm）

当信息缺失且候选明确、或需要业务确认时，向用户发起结构化提问表单
（设计 docs/system/design-ask-user-interactive-question.md §4.1）：web 端渲染
提问卡片，渠道端渲染规范文字问答。工具本身只做内容硬校验并生成表单 id，
「提交前阻塞等待」语义由 runtime/controls.py 赋予（与 clarify 同模式）：
durable 链路 wait 附 form 进入等待，用户 reply（结构化 answers/skip 或纯文本）
经恢复链路回注为本工具的 tool result。

三工具分工（usage guide 同步声明）：ask_user=阻塞式结构化提问（推荐）；
clarify=阻塞式纯文本追问（兼容存量）；present_options=非阻塞建议按钮。
"""

import json
import uuid
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool

# 单次提问总载荷硬上限（UTF-8 字节，§4.1）。仅约束表单总体积；title/tag/
# prompt/label 等文案长度是生成/展示建议（usage guide 约束），不做逐字段拒绝门。
MAX_FORM_BYTES = 8 * 1024
QUESTION_TYPES = ("single", "multi", "text")


class AskOptionItem(BaseModel):
    """单个选项（key 由服务端按题内顺序生成，入参不携带任何 id）"""
    label: str = Field(..., description="选项显示文本（建议 ≤40 字，精炼）")
    description: Optional[str] = Field(None, description="选项补充说明（一句为宜）")


class AskQuestionItem(BaseModel):
    """单个问题"""
    prompt: str = Field(..., description="题干（精炼，建议 ≤60 字）")
    type: Literal["single", "multi", "text"] = Field(
        ..., description="single=单选（最多选一项）/ multi=多选（受 max_select 约束）/ text=自由文本")
    tag: Optional[str] = Field(None, description="分类标签（建议 ≤12 字）")
    required: bool = Field(False, description="是否必答")
    options: Optional[List[AskOptionItem]] = Field(
        None, description="候选选项：single/multi 必填 2~8 项；text 不得携带")
    allow_custom_text: bool = Field(
        True, description="选项下方是否保留自定义输入（默认保留，仅严格业务确认门才关闭）")
    custom_placeholder: Optional[str] = Field(None, description="自定义输入框占位文案")
    placeholder: Optional[str] = Field(None, description="text 题输入框占位文案")
    max_select: Optional[int] = Field(
        None, ge=2, description="multi 可选上限（2..选项数），缺省=选项数；single 恒为 1 不填")


class AskUserInput(BaseModel):
    """结构化提问参数：只含内容字段；form_id/question_id/option key 均由服务端生成"""
    title: Optional[str] = Field(None, description="表单标题（建议 ≤20 字）")
    intro: Optional[str] = Field(None, description="表单说明（建议 ≤200 字）")
    questions: List[AskQuestionItem] = Field(
        ..., min_length=1, max_length=5, description="问题列表（1~5 题，打包进同一个提问）")
    allow_skip: bool = Field(True, description="是否允许用户整体跳过（忽略按钮）")


def _option_entry(key, option):
    entry = {"key": key, "label": option["label"]}
    if option.get("description"):
        entry["description"] = str(option["description"])
    return entry


class AskUserTool(BaseTool):
    """结构化提问工具"""

    # 控制工具：不进普通 registry，由 ToolControlSet 构造。
    catalog = False
    name = "ask_user"
    description = "向用户发起结构化提问表单（单选/多选/自由文本，1~5 题打包），阻塞等待用户作答"
    usage_guide = (
        "当信息缺失且候选明确、或需要用户做业务确认/选择时调用：一次调用把 1~5 个问题"
        "打包进 questions，阻塞等待用户作答后继续（root 与子执行一致）。分工：开放式"
        "问题没有明确候选项时不用本工具，改用 clarify（纯文本追问）；不阻塞的建议按钮"
        "用 present_options。题型显式声明：互斥选择用 single（最多选一项），可并集的"
        "多选用 multi（受 max_select 约束），自由文本用 text（不得携带 options）。选项题"
        "默认保留自定义输入（allow_custom_text 默认 true，仅严格业务确认门才关闭）；"
        "每个选项 label 附一句 description 说明含义。文案精炼即可（title ≤20 字、"
        "tag ≤12 字、label ≤40 字为建议而非硬性限制）。单轮最多调用本工具 1 次，"
        "多题合并进 questions，不要连续多次提问。"
    )
    display_name = "向用户提问"
    category = "agent"
    InputModel = AskUserInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        生成 AskForm（硬校验 + 服务端 id 生成）；等待语义由 controls.py 赋予。

        Args:
            title: 表单标题（可选）
            intro: 表单说明（可选）
            questions: 问题列表 1~5 题
            allow_skip: 是否允许整体跳过（默认 True）

        Returns:
            {"success": True, "form": {...}} 或 {"success": False, "error": ...}
        """
        title = kwargs.get("title")
        intro = kwargs.get("intro")
        allow_skip = kwargs.get("allow_skip", True)
        questions = kwargs.get("questions")

        if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
            count = len(questions) if isinstance(questions, list) else "非列表"
            return {"success": False, "error": f"questions 需要提供 1~5 个问题，实际 {count}"}

        built = []
        for index, item in enumerate(questions, start=1):
            error, question = self._build_question(index, item)
            if error:
                return {"success": False, "error": error}
            built.append(question)

        form: Dict[str, Any] = {
            "form_id": "frm_" + uuid.uuid4().hex,
            "questions": built,
            "allow_skip": bool(allow_skip),
            # 预留字段，一期恒为 null（§5.5，超时语义属 Phase 3）
            "expires_at": None,
        }
        if title:
            form["title"] = str(title)
        if intro:
            form["intro"] = str(intro)

        payload_bytes = len(json.dumps(form, ensure_ascii=False).encode("utf-8"))
        if payload_bytes > MAX_FORM_BYTES:
            return {"success": False, "error": (
                f"提问总载荷 {payload_bytes} 字节超过上限 {MAX_FORM_BYTES} 字节（UTF-8），"
                "请精炼文案或减少选项")}

        logger.info(f"AskUser tool called: title={(title or '')[:50]} questions={len(built)} "
                    f"allow_skip={form['allow_skip']}")
        return {"success": True, "form": form}

    @staticmethod
    def _build_question(index, item):
        """单题硬校验与构建（§4.1）；返回（错误消息, question dict）。"""
        if not isinstance(item, dict):
            return f"questions[{index}] 必须是对象", None
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return f"questions[{index}] 缺少非空 prompt", None
        qtype = item.get("type")
        if qtype not in QUESTION_TYPES:
            return f"questions[{index}].type 必须是 single/multi/text，实际 {qtype!r}", None

        qid = f"q{index}"
        question: Dict[str, Any] = {"id": qid, "prompt": prompt, "type": qtype}
        if item.get("tag"):
            question["tag"] = str(item["tag"])
        if item.get("required"):
            question["required"] = True

        options = item.get("options") or []
        if qtype == "text":
            if options:
                return f"questions[{index}] 是 text 自由文本题，不能携带 options", None
            if item.get("placeholder"):
                question["placeholder"] = str(item["placeholder"])
            return None, question

        if not isinstance(options, list) or not 2 <= len(options) <= 8:
            count = len(options) if isinstance(options, list) else "非列表"
            return f"questions[{index}] 是 {qtype} 题，options 需要提供 2~8 项，实际 {count}", None
        for option in options:
            label = option.get("label") if isinstance(option, dict) else None
            if not isinstance(label, str) or not label.strip():
                return f"questions[{index}] 每个选项需要非空 label", None
        max_select = item.get("max_select")
        if qtype == "multi" and max_select is not None:
            if (isinstance(max_select, bool) or not isinstance(max_select, int)
                    or not 2 <= max_select <= len(options)):
                return (f"questions[{index}].max_select 需要 2..{len(options)}（选项数），"
                        f"实际 {max_select!r}"), None

        question["options"] = [_option_entry(f"{qid}o{position}", option)
                               for position, option in enumerate(options, start=1)]
        question["allow_custom_text"] = bool(item.get("allow_custom_text", True))
        if item.get("custom_placeholder"):
            question["custom_placeholder"] = str(item["custom_placeholder"])
        if qtype == "multi" and max_select is not None:
            question["max_select"] = max_select
        return None, question
