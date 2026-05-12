"""
PPT LLM 内容规划器

调用 LLM 将用户输入（主题/大纲/Markdown）转化为结构化的 PPT 大纲 JSON。
"""

import json
import re
from typing import Any, Dict, Optional

from loguru import logger

SYSTEM_PROMPT = """你是一个专业的PPT内容规划师。根据用户输入的主题或内容，生成一份结构化的PPT大纲。

规则：
1. 每页必须有明确的 type（cover/toc/section/content/summary）
2. 内容页的 layout 必须从以下选择：bullets/chart/comparison/stat/timeline/image
3. 封面页必须有 title 和 subtitle
4. 目录页必须列出所有 section
5. 每 3-5 个内容页之间插入一个 section 分隔页
6. 最后必须是 summary 总结页
7. 内容简洁精炼，每页要点不超过 5 条
8. 标题不超过 20 字，要点不超过 30 字

页面类型与布局：
- cover: 封面页（title, subtitle, presenter, date）
- toc: 目录页（sections 列表，每项有 number 和 title）
- section: 章节分隔页（number, title, intro）
- content: 内容页（layout + 对应数据）
  - bullets: 要点列表（points 数组）
  - stat: 数据亮点（stats 数组，每项 value/label/trend）
  - chart: 图表（chart 对象，含 type/labels/series）
  - comparison: 左右对比（left/right 对象，各含 title + items）
  - timeline: 时间线（points 数组）
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

输出严格的 JSON 格式（不要包含 markdown 代码块标记）：
{"title":"PPT标题","theme_id":数字,"style":"soft","slides":[...]}"""


class PPTPlanner:
    """PPT 工具内部 LLM 规划器。"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

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

        try:
            response = await gateway.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

            content = response.get("content", "")
            return self._parse_json(content)

        except Exception as e:
            logger.error(f"[PPTPlanner] LLM 调用失败: {e}", exc_info=True)
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

        return plan
