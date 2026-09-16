"""
PPT LLM 内容规划器

调用 LLM 将用户输入（主题/大纲/Markdown）转化为结构化的 PPT 大纲 JSON。
"""

import json
import re
from typing import Any, Dict, Optional

from loguru import logger

from src.tools.context import resolve_llm_gateway
from src.tools.ppt.layout_registry import LAYOUT_IDS

_LAYOUT_LIST = "/".join(LAYOUT_IDS)

SYSTEM_PROMPT = f"""你是一个专业的PPT内容规划师。根据用户输入生成结构化PPT大纲。

硬性规则：
1. 每页只输出 layout id，必须从以下选择：{_LAYOUT_LIST}
2. 禁止输出 x/y/w/h/left/top/width/height 等绝对坐标或任何排版参数
3. 封面页必须有 title 和 subtitle
4. 目录页必须列出所有 section
5. 每 3-5 个内容页之间插入一个 section 分隔页
6. 最后必须是 summary 总结页
7. 标题不超过 20 字；单条文本不超过 30 字
8. bullets/timeline 不超过5项，stat/comparison/image/summary每组不超过4项
9. chart 不超过8个分类，table 每页不超过8行（含表头）
10. 内容放不下时主动拆成多页，不缩小字号、不添加坐标

布局与数据槽位：
- cover: 封面页（title, subtitle, presenter, date）
- toc: 目录页（sections 列表，每项有 number 和 title）
- section: 章节分隔页（number, title, intro）
- bullets: 要点列表（points 数组）
- stat: 数据亮点（stats 数组，每项 value/label/trend）
- chart: 图表（chart 对象，含 type/labels/series）
- comparison: 左右对比（left/right 对象，各含 title + items）
- timeline: 时间线（points 数组）
- table: 表格（rows 二维数组，首行为表头）
- image: 图片页（image_path + caption + points）
- summary: 总结页（takeaways + next_steps + contact）

配色方案选择（根据主题自动选择 1-18）：
- 商务/企业/金融 → 2 或 18
- 科技/互联网/AI → 7 或 15
- 教育/培训 → 4 或 10
- 健康/医疗 → 1
- 创意/设计 → 5 或 12
- 环保/自然 → 3 或 11
- 产品/营销 → 7 或 16

输出严格 JSON，不包含 markdown 代码块，不输出 type 和坐标：
{{"title":"PPT标题","theme_id":数字,"style":"soft","slides":[...]}}"""


class PPTPlanner:
    """PPT 工具内部 LLM 规划器。"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        # 优先用执行上下文中的 agent gateway（含子智能体 model_code 覆盖，与计费模型同源）
        return resolve_llm_gateway(self._gateway)

    async def plan_from_topic(self, topic: str, slide_count: Optional[int] = None,
                              theme_id: Optional[int] = None) -> Dict[str, Any]:
        """从主题生成大纲。"""
        prompt = f"请为以下主题生成一份PPT大纲：{topic}"
        if slide_count:
            prompt += f"\n期望页数：约{slide_count}页"
        if theme_id:
            prompt += f"\n指定配色方案ID：{theme_id}"

        return await self._call_llm(prompt)

    async def plan_from_content(self, content: str,
                                theme_id: Optional[int] = None) -> Dict[str, Any]:
        """从 Markdown 内容生成大纲。"""
        prompt = f"请将以下内容转换为PPT大纲：\n\n{content}"
        if theme_id:
            prompt += f"\n指定配色方案ID：{theme_id}"

        return await self._call_llm(prompt)

    async def _call_llm(self, prompt: str) -> Dict[str, Any]:
        """调用 LLM 并解析 JSON 结果。"""
        gateway = self._get_gateway()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await gateway.chat(
                messages=messages,
                temperature=0.3,
                # 大纲规划走主链路思考提升质量；不显式传 max_tokens——硬上限会被
                # 思考烧穿致 content 空（2026-09 生产事故模式），由网关按模型配置
                # 取官方上限（qwen3.8-flash=131072）
            )

            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                source="ppt_planner",
                model=gateway.get_model_name(),
            )

            content = response.get("content", "")
            return self._parse_json(content)

        except Exception as first_err:
            # 失败重试：关思考 + 仍不设 max_tokens 硬上限（推理已完成，
            # 重试只为直接产出结论；原样重试会复现思考烧穿）
            logger.warning(f"[PPTPlanner] LLM 首次调用失败，关思考重试: {first_err}")
            try:
                from src.llm.gateway import _lite_thinking_off_params
                response = await gateway.chat(
                    messages=messages,
                    temperature=0.3,
                    **_lite_thinking_off_params(gateway.get_provider_name()),
                )

                from src.services.session_record import record_background_llm_usage
                record_background_llm_usage(
                    response.get("usage") if isinstance(response, dict) else None,
                    source="ppt_planner_retry",
                    model=gateway.get_model_name(),
                )

                content = response.get("content", "")
                return self._parse_json(content)
            except Exception as e:
                logger.opt(exception=True).error(f"[PPTPlanner] LLM 调用失败（含关思考重试）: {e}")
                return {"error": f"LLM 规划失败: {e}"}

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """从 LLM 输出中提取 JSON。"""
        # 去除可能的 markdown 代码块标记
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = text.strip()

        try:
            plan = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 对象
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    plan = json.loads(match.group())
                except json.JSONDecodeError as e:
                    return {"error": f"JSON 解析失败: {e}"}
            else:
                return {"error": "LLM 输出中未找到有效 JSON"}

        # 基础校验
        if "slides" not in plan:
            return {"error": "大纲缺少 slides 字段"}
        if not isinstance(plan["slides"], list):
            return {"error": "slides 字段必须为数组"}

        return self._sanitize_plan(plan)

    @staticmethod
    def _sanitize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
        """Remove layout freedom and normalize legacy planner output deterministically."""
        warnings = [
            str(item) for item in plan.get("warnings", [])
            if isinstance(item, str) and item.strip()
        ]
        sanitized_slides = []
        legacy_layouts = {"content": "bullets"}
        coordinate_keys = {"x", "y", "w", "h", "left", "top", "width", "height", "position"}
        for index, raw_slide in enumerate(plan["slides"], start=1):
            slide = dict(raw_slide) if isinstance(raw_slide, dict) else {}
            legacy_type = str(slide.pop("type", "")).lower()
            layout = str(slide.get("layout") or legacy_layouts.get(legacy_type) or legacy_type).lower()
            if layout not in LAYOUT_IDS:
                warnings.append(
                    f"slide[{index}] layout '{layout or 'missing'}' replaced with 'bullets'"
                )
                layout = "bullets"
            removed = coordinate_keys.intersection(slide)
            for key in removed:
                slide.pop(key, None)
            if removed:
                warnings.append(
                    f"slide[{index}] removed forbidden positioning fields: {','.join(sorted(removed))}"
                )
            slide["layout"] = layout
            sanitized_slides.append(slide)
        result = dict(plan)
        result["slides"] = sanitized_slides
        if warnings:
            result["warnings"] = warnings
        else:
            result.pop("warnings", None)
        return result
