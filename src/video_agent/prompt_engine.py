"""视频创作提示词引擎（精修/敏捷双模）

设计依据：docs/plans/plan-video-agent-phase1.md §3.3
关联设计文档：§5.3.2 三层架构（业务层 + 工艺层 + 模型层）

两种模式：
- 精修模式（refine）：调用文本模型 1 次，生成 1 段提示词，需用户在聊天中确认
- 敏捷模式（agile）：调用文本模型 1 次，让它一次返回 N 段差异化提示词，无需用户确认

提示词差异化（敏捷模式）：在元素参考 / 景别 / 运镜 / 光影上有差异

第一阶段简化：精修模式的用户确认通过聊天消息实现（智能体发提示词草稿 md 卡片 ->
用户回复"确认"或描述调整），不实现专门的提示词编辑面板 UI。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from src.llm.gateway import llm_gateway


@dataclass
class PromptResult:
    """单段提示词结果（业务层 + 工艺层 + 模型层参数）"""
    business_prompt: str           # 业务层（中文，员工可读）
    craft_prompt: str              # 工艺层（可灵 8 层框架结构化）
    model_params: Dict[str, Any] = field(default_factory=dict)  # 模型层参数（seed/negative_prompt/duration/ratio/resolution）


# 精修模式系统提示词：让文本模型生成 1 段「业务层 + 工艺层」结构化提示词
_REFINE_SYSTEM_PROMPT = """你是视频创作提示词工程师，专责把用户的中文需求转化为「业务层 + 工艺层」双层提示词。

## 输出格式（严格 JSON，不要 markdown 代码块）

{
  "business_prompt": "中文业务层描述，员工可读，说明这条视频要展示什么、目标受众、关键卖点",
  "craft_prompt": "工艺层结构化提示词，使用可灵 8 层框架：主体/元素参考/环境/景别/运镜/光影/色调/风格",
  "negative_prompt": "反向提示词（要避免的元素，如：模糊/变形/低质/水印）"
}

## 要求

1. business_prompt 用中文，30-80 字，员工能看懂
2. craft_prompt 用结构化中文+英文关键词混合，按可灵 8 层框架组织，每层 1-2 个关键词
3. negative_prompt 简短，3-5 个要避免的元素
4. 严格输出 JSON，不要任何额外文字"""


# 敏捷模式系统提示词：让文本模型一次生成 N 段差异化提示词
_AGILE_SYSTEM_PROMPT = """你是视频创作提示词工程师，需要一次生成 N 段略有差异的视频提示词。

## 差异化维度

每段提示词在以下维度上做差异：
- 元素参考：产品摆放角度 / 模特姿态 / 道具组合
- 景别：特写 / 中景 / 全景
- 运镜：推拉 / 摇移 / 固定
- 光影：自然光 / 暖光 / 冷光 / 侧光
- 色调：明亮 / 暖调 / 冷调 / 高对比

## 输出格式（严格 JSON 数组，不要 markdown 代码块）

[
  {
    "business_prompt": "第 1 段业务层中文描述",
    "craft_prompt": "第 1 段工艺层结构化提示词（可灵 8 层框架）",
    "negative_prompt": "第 1 段反向提示词"
  },
  {
    "business_prompt": "第 2 段业务层中文描述（与第 1 段在元素参考/景别/运镜/光影/色调上有差异）",
    "craft_prompt": "...",
    "negative_prompt": "..."
  }
]

## 要求

1. 每段 business_prompt 用中文，30-80 字
2. 每段 craft_prompt 用可灵 8 层框架（主体/元素参考/环境/景别/运镜/光影/色调/风格）
3. N 段之间必须在至少 3 个差异化维度上有明显不同
4. 严格输出 JSON 数组，不要任何额外文字"""


def _build_user_prompt(
    user_input: str,
    image_count: int,
    duration_sec: int,
    ratio: str,
    resolution: str,
    count: int = 1,
) -> str:
    """构建给文本模型的用户提示词"""
    parts = [
        f"用户需求：{user_input}",
        f"参考图片：{image_count} 张" if image_count > 0 else "无参考图片",
        f"视频时长：{duration_sec} 秒",
        f"视频比例：{ratio}",
        f"分辨率：{resolution}",
    ]
    if count > 1:
        parts.append(f"请生成 {count} 段差异化提示词")
    return "\n".join(parts)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取 JSON 对象（容错：去 markdown 代码块包裹）"""
    cleaned = text.strip()
    # 去掉 ```json ... ``` 包裹
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试找到第一个 { 和最后一个 }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """从文本中提取 JSON 数组（容错：去 markdown 代码块包裹）"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        return None
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                return None
        return None


def _build_prompt_result(
    obj: Dict[str, Any],
    duration_sec: int,
    ratio: str,
    resolution: str,
    seed: Optional[int] = None,
) -> PromptResult:
    """从 JSON 对象构造 PromptResult"""
    business = obj.get("business_prompt") or obj.get("business") or ""
    craft = obj.get("craft_prompt") or obj.get("craft") or ""
    negative = obj.get("negative_prompt") or obj.get("negative") or ""
    if not business or not craft:
        raise ValueError(f"提示词 JSON 缺少必要字段: business_prompt/craft_prompt, got keys={list(obj.keys())}")
    model_params: Dict[str, Any] = {
        "duration": duration_sec,
        "ratio": ratio,
        "resolution": resolution,
        "negative_prompt": negative,
    }
    if seed is not None:
        model_params["seed"] = seed
    return PromptResult(
        business_prompt=business.strip(),
        craft_prompt=craft.strip(),
        model_params=model_params,
    )


class PromptEngine:
    """视频创作提示词引擎（精修/敏捷双模）"""

    async def generate_prompt_refine(
        self,
        user_input: str,
        image_count: int,
        duration_sec: int = 5,
        ratio: str = "9:16",
        resolution: str = "720P",
    ) -> PromptResult:
        """精修模式：调用文本模型 1 次，生成 1 段提示词

        Args:
            user_input: 用户的中文需求描述
            image_count: 参考图片数量（0 表示无图）
            duration_sec: 视频时长（秒）
            ratio: 视频比例（9:16 / 16:9 / 1:1 / 4:3 / 3:4）
            resolution: 分辨率（720P / 1080P / 768P / 2K）

        Returns:
            PromptResult 单段提示词

        Raises:
            ValueError: 文本模型返回内容无法解析为合法 JSON
            Exception: LLM 调用失败
        """
        user_prompt = _build_user_prompt(
            user_input, image_count, duration_sec, ratio, resolution, count=1,
        )
        messages = [
            {"role": "system", "content": _REFINE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        logger.info(f"[PromptEngine] 精修模式调用文本模型, user_input={user_input[:50]}")
        resp = await llm_gateway.chat(messages=messages, temperature=0.7, max_tokens=2000)
        content = resp.get("content") or ""
        obj = _extract_json_object(content)
        if obj is None:
            raise ValueError(f"文本模型返回内容无法解析为 JSON: {content[:200]}")
        return _build_prompt_result(obj, duration_sec, ratio, resolution)

    async def generate_prompts_agile(
        self,
        user_input: str,
        image_count: int,
        count: int = 3,
        duration_sec: int = 5,
        ratio: str = "9:16",
        resolution: str = "720P",
    ) -> List[PromptResult]:
        """敏捷模式：调用文本模型 1 次，一次生成 N 段差异化提示词

        Args:
            user_input: 用户的中文需求描述
            image_count: 参考图片数量
            count: 生成段数（1-3，默认 3）
            duration_sec: 视频时长（秒）
            ratio: 视频比例
            resolution: 分辨率

        Returns:
            List[PromptResult] N 段差异化提示词

        Raises:
            ValueError: 文本模型返回内容无法解析为合法 JSON 数组
            Exception: LLM 调用失败
        """
        if count < 1:
            count = 1
        if count > 3:
            count = 3
        user_prompt = _build_user_prompt(
            user_input, image_count, duration_sec, ratio, resolution, count=count,
        )
        messages = [
            {"role": "system", "content": _AGILE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        logger.info(f"[PromptEngine] 敏捷模式调用文本模型, count={count}, user_input={user_input[:50]}")
        resp = await llm_gateway.chat(messages=messages, temperature=0.9, max_tokens=4000)
        content = resp.get("content") or ""
        arr = _extract_json_array(content)
        if arr is None:
            raise ValueError(f"文本模型返回内容无法解析为 JSON 数组: {content[:200]}")
        if len(arr) == 0:
            raise ValueError("文本模型返回空数组")
        results: List[PromptResult] = []
        for idx, obj in enumerate(arr[:count]):
            try:
                results.append(_build_prompt_result(obj, duration_sec, ratio, resolution))
            except ValueError as e:
                logger.warning(f"[PromptEngine] 敏捷模式第 {idx + 1} 段提示词解析失败: {e}")
        if not results:
            raise ValueError("所有提示词段都解析失败")
        return results


# 模块级单例（惰性）
_prompt_engine_instance: Optional[PromptEngine] = None


def get_prompt_engine() -> PromptEngine:
    """返回 PromptEngine 单例"""
    global _prompt_engine_instance
    if _prompt_engine_instance is None:
        _prompt_engine_instance = PromptEngine()
    return _prompt_engine_instance
