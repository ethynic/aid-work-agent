"""BOSS 简历云端 VL 识别服务（简历识别去 OCR 化设计 §4.1）

客户端（boss-resume-assistant）只做「滚动截图 + 拼接」，文本识别挪到云端：
拼接长图经 Pillow 纵切成带重叠横带，一次性发给 GLM-5.3-Flash 多模态
（llm_gateway.chat_direct 指定通道直连），一次调用输出「姓名 + 简历总结 +
职位匹配评分 + key_info」结构化 JSON（v2：不再逐字转写全文，输出短、延迟低）。替代客户端 RapidOCR/WinRT 本地 OCR 链路
（大量客户机 DLL/DirectML 环境报错，是简历链路最大的不稳定源）。

切片参数依据（设计 §4.1）：
- 带高 1800 / 重叠 400（device px）：重叠 > 2 行文字高度，切缝处被切断的行必然在
  相邻带完整出现，提示词声明重叠去重即可；
- 图高 ≤ 2400 整图单发（省切片与多图开销，也规避短简历被无意义切缝）；
- 带数上限 MAX_VL_BANDS=10（模型单请求图片数保护）：16 段拼接最坏 ~10600px 约 8 带；
  超限加大带高重切保证 ≤10（宁可单带文字更多，绝不丢内容）。

计费边界（设计 §4.3）：本服务**不**调 record_background_llm_usage——简历识别费
按份在工具层入库成功后 record_tool_usage('boss_resume_recognition') 单独扣，
LLM token 不再另计，避免「识别费 + token 费」双份计费；调用本身经 gateway 常规
日志可追溯。

姓名交叉校验 resume_name_matches 是客户端 ocrNameMatches（ResumeReader.ts）的
严格语义移植：归一化去空白、头部 400 字窗口、长度 n-1/n/n+1 滑窗 Levenshtein ≤1。
这是防张冠李戴的关键防线（用户铁律：候选人姓名绝不能错），容差只覆盖 1 字识别
误差，绝不放宽。

服务层无 DB 副作用（计费/入库都在工具层），可独立单测（gateway 全 mock）。
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from PIL import Image

from src.config.settings import settings
from src.llm.gateway import llm_gateway
from src.services import recruiting_match_service as _match_service

# 横带高（device px）：GLM-5.3-Flash 对整页截图的可靠阅读尺寸内取大带，减少带数
VL_BAND_HEIGHT = 1800
# 相邻横带重叠（device px）：> 2 行文字高度，切缝处被切断的行必在相邻带完整出现
VL_BAND_OVERLAP = 400
# 拼接图高不超过该值时整图单发不切（单带即可，无切缝风险）
VL_SINGLE_IMAGE_MAX_HEIGHT = 2400
# 单次 VL 请求横带数上限（模型图片数保护；超限加大带高重切，绝不丢内容）
MAX_VL_BANDS = 10


class ResumeVLError(Exception):
    """简历 VL 识别链路失败（解码/切片/LLM 调用/空输出），fail-loud 由工具层转错误码"""


class ResumeVLModelError(ValueError):
    """请求指定的模型不合法（非 provider/model 格式或不在白名单），接口层转 422"""


def resolve_model_spec(model_param: Optional[str] = None) -> Tuple[str, str]:
    """解析 VL 识别的目标模型为 (provider, model)。

    - model_param 缺省 → 配置默认 settings.resume_vl.model（provider/model 语法，
      默认 zhipu/GLM-5.3-Flash——GLM-5 系原生多模态，主链路 provider 可能是纯文本
      模型，VL 调用绝不能随主链路漂移）；
    - 显式指定 → 必须与白名单 settings.resume_vl.allowed_models 完全匹配才放行：
      识别费是按份固定积分，放行任意模型会造成「指定贵模型 + 固定低价」的计费错配；
    - 不合法抛 ResumeVLModelError（ValueError 子类，接口层转 422，与入参校验错误统一）。
    """
    spec = (model_param or "").strip() or settings.resume_vl.model
    allowed = [a.strip() for a in (settings.resume_vl.allowed_models or [])]
    # 缺省/显式等于配置默认的模型免白名单（配置本身即授权）：运维改了 model 忘同步
    # allowed_models 时，缺省调用不至于全部 422；显式指定其他模型仍受白名单约束
    if spec != settings.resume_vl.model and allowed and spec not in allowed:
        raise ResumeVLModelError(
            f"模型 {spec} 不在允许列表内（允许：{'、'.join(allowed)}）；"
            "模型用 provider/model 语法（如 zhipu/GLM-5.3-Flash）"
        )
    provider, sep, model = spec.partition("/")
    if not sep or not provider.strip() or not model.strip():
        raise ResumeVLModelError(
            f"模型标识 {spec} 格式非法：须为 provider/model（如 zhipu/GLM-5.3-Flash）"
        )
    return provider.strip(), model.strip()


# ============== 拼接长图切片 ==============


def _band_tops(height: int, band_height: int) -> List[int]:
    """计算各横带顶部 y（device px）：步进 = 带高 - 重叠，末带贴底保证盖满全图。

    带高 ≥ 图高时返回 [0]（单带）；末带不足带高时按剩余高度截断（crop 时钳制边界）。
    """
    if band_height >= height:
        return [0]
    step = band_height - VL_BAND_OVERLAP
    tops: List[int] = []
    top = 0
    while top + band_height < height:
        tops.append(top)
        top += step
    tops.append(top)  # 末带：要么正好盖到底，要么不足带高（裁剪时钳制）
    return tops


def slice_stitched_image(png_base64: str) -> List[str]:
    """拼接长图（PNG base64）→ 带重叠横带 base64 列表（按阅读顺序）。

    解码失败（base64 非法 / 非 PNG / 空图）抛 ResumeVLError（fail-loud，
    调用方该份按失败处理，绝不静默返回空带列表）。
    """
    if not (png_base64 or "").strip():
        raise ResumeVLError("拼接长图 base64 为空，无法切片")
    try:
        raw = base64.b64decode(png_base64)
    except (binascii.Error, ValueError) as e:
        raise ResumeVLError(f"拼接长图 base64 解码失败: {e}") from e
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001 Pillow 对损坏文件抛的异常类型不固定
        raise ResumeVLError(f"拼接长图解码失败（非 PNG 或文件损坏）: {e}") from e
    width, height = img.size
    if width <= 0 or height <= 0:
        raise ResumeVLError(f"拼接长图尺寸非法: {width}x{height}")

    band_height = VL_BAND_HEIGHT
    if height > VL_SINGLE_IMAGE_MAX_HEIGHT:
        tops = _band_tops(height, band_height)
        if len(tops) > MAX_VL_BANDS:
            # 超限重切：带高按公式放大后横带数必 ≤ MAX_VL_BANDS（推导见设计 §4.1），
            # 公式解 N·Bh ≥ H + (N-1)·O 的最小整数解；宁大勿小保证不超带数上限
            band_height = max(
                band_height,
                math.ceil((height + (MAX_VL_BANDS - 1) * VL_BAND_OVERLAP) / MAX_VL_BANDS),
            )
            tops = _band_tops(height, band_height)
            logger.info(
                f"后端日志：简历拼接长图过高（{height}px），带高加大到 {band_height} 重切，"
                f"横带数 {len(tops)}"
            )
    else:
        tops = [0]  # 矮图整图单发（无切缝风险，提示词去重逻辑对其无副作用）

    bands: List[str] = []
    for top in tops:
        bottom = min(top + band_height, height)
        crop = img.crop((0, top, width, bottom))
        buf = BytesIO()
        crop.save(buf, format="PNG")
        bands.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    return bands


# ============== VL 评估（姓名 + 总结 + 评分 + key_info，v2 合并调用） ==============

# 提示词要点（设计 §4.1 v2）：先读姓名（决策⑨姓名核对前置）/ 连续截图重叠去重 /
# 仅依据图中可见内容 / 单个 JSON 输出。每条对应一类已知模型跑偏：不声明「同一份简历」
# 会输出逐图描述；不声明「重叠去重」会在切缝处重复段落；不禁止 Markdown 会包 ``` 代码块。
_EVALUATE_PROMPT_TMPL = (
    "下面的图片是候选人「{candidate_name}」的简历页面从上到下连续滚动截图后拼接的内容"
    "（可能被切分为多条首尾重叠的横带，按顺序阅读）。请完成简历评审：\n"
    "1. 第一步：从简历顶部姓名栏读出候选人姓名，填入 name_seen（原文照抄，"
    "不要按期望姓名猜测——若与期望姓名不一致更要如实输出，后续由系统判定）；\n"
    "2. 用不超过200字总结这位候选人（resume_summary：身份/年限/技术栈/亮点）；\n"
    "3. 评分：{score_instruction}\n"
    "4. 提取结构化关键信息 key_info；\n"
    "相邻横带之间存在重叠内容，重叠部分只保留一份。\n"
    "【输出要求】只输出一个 JSON 对象（不要 markdown 代码块、不要任何其他文字），结构示例：\n"
    '{{"name_seen": "简历姓名栏原文", "resume_summary": "≤200字人物总结", '
    '"score": 82, "match_summary": "≤100字评分理由", "key_info": '
    '{{"years_of_experience": 6, "education": "本科", "current_company": "xx科技", '
    '"core_skills": ["PHP"], "highlights": ["日活十万级 SaaS 主导"], '
    '"ai_tool_usage": "熟练：Cursor 日常开发", "salary_expectation": "15-25K", "concerns": []}}}}\n'
    "【铁律】仅依据图中可见内容填写，图中不可见的字段填 null、数组字段给 []，严禁编造。"
)

# 有职位上下文时的评分指令（评分规则沿袭简历-职位匹配设计 §3）
_SCORE_WITH_JOB = (
    "score 为 0-100 整数，衡量简历与下方职位要求的匹配度，越匹配越高，"
    "硬性要求不满足时显著扣分；match_summary 不超过100字说明给分依据。\n"
    "【职位信息】\n{job_lines}"
)

# 无职位上下文：只总结不评分（沿袭原「未关联职位跳过评分」语义）
_SCORE_WITHOUT_JOB = (
    "本次未提供职位要求，score 与 match_summary 输出 null（不要猜测评分），"
    "只输出 name_seen / resume_summary / key_info。"
)


def _job_lines(job_ctx: Optional[Dict[str, Any]]) -> str:
    """职位上下文拼入提示词（隐含要求路径沿袭 recruiting_match_service._build_messages 规则）"""
    if not job_ctx:
        return ""
    requirements = job_ctx.get("job_requirements")
    if isinstance(requirements, dict) and requirements:
        return "结构化职位要求（JSON）：" + json.dumps(requirements, ensure_ascii=False)
    lines = ["- 职位名称：" + (job_ctx.get("job_name") or "（未知）")]
    if job_ctx.get("notes"):
        lines.append(f"- 职位备注：{job_ctx['notes']}")
    for s in (job_ctx.get("opening_scripts") or [])[:3]:
        content = (s.get("content") or "")[:200]
        lines.append(f"- 初次开场话术《{s.get('title') or ''}》：{content}")
    if len(lines) == 1:
        lines.append("- （无更多信息，仅按职位名称推断）")
    return "\n".join(lines)


def _parse_evaluation_payload(content: str) -> Optional[Dict[str, Any]]:
    """整包解析 VL 输出：JSON dict + name_seen 非空 + score 清洗 + key_info 清洗。

    复用 recruiting_match_service 的 _parse_json/_extract_score/_clean_key_info（评分解析
    唯一实现，不复制两份）。name_seen 空 = 解析失败（走重试）——姓名是防张冠李戴的
    判定依据，缺失绝不能放行。
    """
    data = _match_service._parse_json(content)
    if not isinstance(data, dict):
        return None
    name_seen = data.get("name_seen")
    if not (isinstance(name_seen, str) and name_seen.strip()):
        return None
    summary = data.get("resume_summary")
    match_summary = data.get("match_summary")
    return {
        "name_seen": name_seen.strip(),
        "resume_summary": summary.strip() if isinstance(summary, str) and summary.strip() else None,
        "score": _match_service._extract_score(data.get("score")),
        "match_summary": (
            match_summary.strip()[:100] if isinstance(match_summary, str) and match_summary.strip() else None
        ),
        "key_info": _match_service._clean_key_info(data.get("key_info")),
    }


async def evaluate_resume(
    band_images_b64: List[str],
    candidate_name: str,
    job_ctx: Optional[Dict[str, Any]] = None,
    model_param: Optional[str] = None,
) -> Dict[str, Any]:
    """拼接横带一次发 VL 评估，返回 {name_seen, resume_summary, score, match_summary, key_info, usage, model}。

    - candidate_name（页面/会话上下文的期望姓名）传入提示词：模型第一步读简历姓名栏输出
      name_seen，服务端再用 resume_name_matches 判定（决策⑨：模型只读名，匹配与否由服务端
      确定性判定，不依赖模型判断力）；
    - job_ctx 为 None → 提示词要求 score/match_summary 输出 null（沿袭「未关联职位跳过评分」）；
    - 超时/异常/JSON 不合法重试 1 次；仍失败抛 ResumeVLError（调用方不扣费）；
    - 本函数不记 LLM token 账（识别费由调用方按份扣，见模块 docstring）。
    """
    if not band_images_b64:
        raise ResumeVLError("VL 识别入参为空：无横带图片")
    provider_name, model = resolve_model_spec(model_param)
    if job_ctx:
        score_instruction = _SCORE_WITH_JOB.format(job_lines=_job_lines(job_ctx))
    else:
        score_instruction = _SCORE_WITHOUT_JOB
    prompt = _EVALUATE_PROMPT_TMPL.format(
        candidate_name=candidate_name, score_instruction=score_instruction
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for band_b64 in band_images_b64:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{band_b64}"}}
        )
    messages = [{"role": "user", "content": content}]

    last_error = "未知错误"
    for attempt in (1, 2):
        try:
            # 温度 0.1：结构化评审要确定性（与 recruiting_match_service 同取向）
            response = await llm_gateway.chat_direct(
                provider_name, model, messages=messages, temperature=0.1
            )
        except Exception as e:  # noqa: BLE001 LLM 异常统一走重试，最终转 ResumeVLError
            last_error = f"LLM 调用失败: {type(e).__name__}: {e}"
            logger.warning(
                f"后端日志：简历 VL 评估失败（第 {attempt} 次）provider={provider_name} "
                f"model={model}: {last_error}"
            )
            continue
        text = (response.get("content") or "").strip() if isinstance(response, dict) else ""
        if not text:
            last_error = "VL 返回空文本"
            logger.warning(
                f"后端日志：简历 VL 评估输出为空（第 {attempt} 次）provider={provider_name} model={model}"
            )
            continue
        parsed = _parse_evaluation_payload(text)
        if parsed is not None:
            return {**parsed, "usage": response.get("usage"), "model": model}
        last_error = "VL 输出 JSON 不合法或缺 name_seen"
        logger.warning(
            f"后端日志：简历 VL 评估输出解析失败（第 {attempt} 次）provider={provider_name} model={model}"
        )
    raise ResumeVLError(f"简历 VL 评估失败（重试后仍失败）：{last_error}")


# ============== 姓名交叉校验（ocrNameMatches 的严格移植） ==============

# 只看文本头部前 400 字符（姓名在简历第一行；限窗口避免长文正文随机相似串误命中）
RESUME_NAME_MATCH_WINDOW = 400


def _levenshtein(a: str, b: str) -> int:
    """经典 Levenshtein（两行滚动数组）。滑窗长度 ≤ 姓名+1（≤31）、窗口 400，性能无忧"""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ac = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ac == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def resume_name_matches(name: str, text: str) -> bool:
    """判断「姓名是否在识别文本头部模糊命中」（防张冠李戴）。

    与客户端 ocrNameMatches（ResumeReader.ts）语义严格一致：
    - 归一化去全部空白（识别/截图文本里姓名常被打散成「康 嘉 润」）；
    - 精确包含优先；再用长度 n-1/n/n+1 滑窗 + Levenshtein ≤1 覆盖漏 1 字/错 1 字/
      多 1 字噪声尾巴三种 1 字误差；
    - 容差再放宽会把无关姓名误放行（错存比跳过危害大），绝不放宽；
    - 空姓名/空文本 → False（无法校验即不放行，fail-safe）。
    """
    target = re.sub(r"\s+", "", name or "")
    normalized = re.sub(r"\s+", "", text or "")
    if not target or not normalized:
        return False
    head = normalized[:RESUME_NAME_MATCH_WINDOW]
    if target in head:
        return True
    n = len(target)
    for w in (n - 1, n, n + 1):
        if w < 1 or w > len(head):
            continue  # n-1<1（单字姓名）跳过该档
        for i in range(0, len(head) - w + 1):
            if _levenshtein(head[i:i + w], target) <= 1:
                return True
    return False
