"""公众号图片 VL 解析（WP10，设计 §6.2，P2 多模态解析提前）。

职责边界：只做「本地图片 → 多模态 LLM → 文字描述」，不做下载（image_downloader.py）
与计费落账（service.py 在业务提交后按张写 chat_records，fail-open）。

模型选择（负责人定版 2026-09-15 + 设计 §6.2）：
- **固定首选**：GLM-5.3-Flash（zhipu），不随主 provider 变化——主 provider 默认
  模型多为文本模型，只按默认模型求交集会让 VL 在多数部署里永不激活
- 其余候选来自 ``TokenCostPriceDB.list_multimodal_models()``（is_multimodal=TRUE，
  即经图片输入验证的已配置模型）：provider 配置的默认 model（settings.llm.{p}.model）
  命中多模态清单时，该 (provider, model) 是降级备选——不向 provider 发送未配置
  的模型名；failover 链上每个 provider 独立求交集，**全链目标均为多模态模型**
  （§6.2 failover 同过滤），无交集的 provider 不进入目标列表
- 无任何可用目标 → ``VisionParseOutcome.no_model=True``，调用方走 deferred，
  绝不降级发送到纯文本模型

调用形态（D2）：每张图独立一次 ``gateway.chat()``；单轮 user message = 固定解析
指令 + 1 张 image_url base64 data URL（复用 src/core/agent.py 构造惯例，超限先
压缩）；**不带**系统提示、会话历史与工具 schema；``use_failover=False``——目标即
绑定多模态模型，跨 provider 降级到文本模型既无法完成视觉任务又会按错误模型计价
（LLMGateway 文档同款约束）。

可靠性：Semaphore(3) 限流；单张失败（异常/空响应）重试 1 次；仍失败且存在次选
目标时按列表顺序降级尝试一次（同样为多模态模型）；解析结果为「图片无法识别」
计 failed（不计费，调用方不为其写账）。
"""
from __future__ import annotations

import asyncio
import base64
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ------------------------------- 常量 -------------------------------

VISION_PARSE_SOURCE_TYPE = "wechat_mp_image_parse"  # chat_records.source_type（按张计费）
UNRECOGNIZED_TEXT = "图片无法识别"  # 模型按指令约定输出的不可识别标记

# 解析指令（设计 §6.2：转述不发挥；措辞可微调语义不变）
PARSE_INSTRUCTION = (
    "转述图片中的文字信息与活动内容(时间/地点/优惠/产品名)，不评价不发挥；"
    f"无法识别时输出\"{UNRECOGNIZED_TEXT}\""
)

# 并发限流（对齐 crawler OCR 并发，设计 §6.2）
PARSE_CONCURRENCY = 3

# 固定首选 VL 模型（负责人定版 2026-09-15）：图片解析默认 GLM-5.3-Flash（zhipu），
# 不随主 provider 变化；zhipu 未配置 key 时跳过该目标，落入后续多模态交集候选
_PINNED_VISION_MODEL = "GLM-5.3-Flash"
_PINNED_VISION_PROVIDER = "zhipu"
# 单张 data URL 字节上限（对齐 agent.py _MULTIMODAL_MAX_BYTES：超出先压缩）
MAX_IMAGE_DATA_BYTES = 5 * 1024 * 1024

# 扩展名 → MIME（对齐 agent.py _IMAGE_EXT_MIME）
_IMAGE_EXT_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

# provider 尝试顺序：主 provider 优先，其余按 settings.llm.failover.providers 配置顺序
_PROVIDER_ORDER = ("qwen", "zhipu", "deepseek", "moonshot")


# ------------------------------- 结果结构 -------------------------------


@dataclass
class VisionTarget:
    """一个可用的多模态调用目标。"""

    provider: str
    model: str


@dataclass
class ImageParseSuccess:
    """单张图片解析成功（计费单位：1 张）。"""

    n: int
    description: str
    model: str
    provider: str
    usage: Dict[str, int] = field(default_factory=dict)  # 实际 token 用量（对账用）


@dataclass
class ImageParseFailure:
    """单张图片解析失败（重试后仍失败/无法识别/图片读取失败）。"""

    n: int
    reason: str  # 脱敏类别：error/empty_response/unrecognized/image_read_failed


@dataclass
class VisionParseOutcome:
    """一次批量解析结果。"""

    successes: List[ImageParseSuccess] = field(default_factory=list)
    failures: List[ImageParseFailure] = field(default_factory=list)
    no_model: bool = False  # 无可用多模态模型（调用方走 deferred）

    @property
    def descriptions(self) -> Dict[int, str]:
        return {s.n: s.description for s in self.successes}

    @property
    def ok_count(self) -> int:
        return len(self.successes)


# ------------------------------- 模型选择 -------------------------------


def resolve_vision_targets(
    multimodal_models: Optional[List[Dict[str, Any]]] = None,
) -> List[VisionTarget]:
    """求「多模态模型清单 × provider 配置默认模型」交集，返回按优先级排序的目标。

    - multimodal_models 不传时实时查库（TokenCostPriceDB.list_multimodal_models）
    - provider 顺序：主 provider 优先，其余按 failover 链配置顺序，末尾兜底其余
      已配置 key 的 provider（保持确定性排序，便于测试与运维预期）
    - 模型名比较不区分大小写（对齐 TokenCostPriceDB.get_by_model_name 口径）
    """
    from src.config.settings import settings

    if multimodal_models is None:
        try:
            from src.db.models import TokenCostPriceDB

            multimodal_models = TokenCostPriceDB.list_multimodal_models()
        except Exception as exc:  # noqa: BLE001 查库失败按无模型处理（不阻断加载）
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 多模态模型清单查询失败（按无模型处理）: {}", type(exc).__name__
            )
            multimodal_models = []

    multimodal = {
        str(m.get("model_name") or "").strip().lower()
        for m in multimodal_models or []
        if m.get("model_name")
    }
    if not multimodal:
        return []

    # provider 优先级：主 provider → failover 链 → 其余 provider（确定性）
    ordered: List[str] = []
    primary = str(getattr(settings.llm, "provider", "") or "")
    if primary:
        ordered.append(primary)
    try:
        for name in (settings.llm.failover.providers or []):
            if name not in ordered:
                ordered.append(name)
    except Exception:  # noqa: BLE001 failover 配置缺失时忽略
        pass
    for name in _PROVIDER_ORDER:
        if name not in ordered:
            ordered.append(name)

    targets: List[VisionTarget] = []

    # 固定首选目标（负责人定版 2026-09-15）：本场景默认 GLM-5.3-Flash（zhipu），
    # 不随主 provider 变化——主 provider 的默认模型多为文本模型，若只按默认模型
    # 求交集，VL 在大多数部署里永不激活。仍受两道门禁约束：zhipu key 已配置、
    # 模型在多模态清单（经图片输入验证）内。
    pinned = str(_PINNED_VISION_MODEL).strip().lower()
    if pinned in multimodal:
        zhipu_cfg = getattr(settings.llm, "zhipu", None)
        if zhipu_cfg is not None:
            try:
                if zhipu_cfg.get_effective_keys():
                    targets.append(
                        VisionTarget(provider="zhipu", model=_PINNED_VISION_MODEL)
                    )
            except Exception:  # noqa: BLE001 配置异常视同未配置
                pass

    for provider in ordered:
        cfg = getattr(settings.llm, provider, None)
        model = str(getattr(cfg, "model", "") or "").strip()
        if not model:
            continue
        # 该 provider 的 key 是否已配置（未配置的 provider 无法实际调用）
        try:
            if not cfg.get_effective_keys():
                continue
        except Exception:  # noqa: BLE001 配置异常视同未配置
            continue
        if model.lower() in multimodal:
            target = VisionTarget(provider=provider, model=model)
            if target not in targets:
                targets.append(target)
    return targets


# ------------------------------- 图片编码 -------------------------------


def build_image_data_url(
    path: str, max_bytes: int = MAX_IMAGE_DATA_BYTES
) -> Optional[str]:
    """本地图片 → OpenAI image_url base64 data URL（复用 agent.py 构造惯例）。

    - 字节超限或扩展名异常时用 Pillow 重编码压缩（JPEG 质量逐档下调 + 必要时减半边长）
    - 读取/解码失败返回 None（调用方计该张 failed）
    - base64 仅 in-memory 传给 LLM，不持久化
    """
    ext = Path(path).suffix.lstrip(".").lower()
    mime = _IMAGE_EXT_MIME.get(ext)
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        logger.bind(module="wechat_mp").warning(
            "wechat_mp 图片读取失败 path_excluded reason={}", type(exc).__name__
        )
        return None

    if mime and len(data) <= max_bytes:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    # 超限压缩（JPEG 逐档降质；GIF/WEBP 等一律转静态 JPEG）
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            for quality in (85, 70, 55, 40):
                buf = BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=quality)
                if buf.tell() <= max_bytes:
                    break
            else:
                # 仍超限：边长减半后重复降质序列
                with Image.open(BytesIO(data)) as img2:
                    while buf.tell() > max_bytes:
                        w, h = img2.size
                        img2 = img2.convert("RGB").resize((max(1, w // 2), max(1, h // 2)))
                        buf = BytesIO()
                        img2.save(buf, format="JPEG", quality=40)
                        if max(img2.size) <= 64:  # 兜底防死循环
                            break
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:  # noqa: BLE001 损坏图片按读取失败处理
        logger.bind(module="wechat_mp").warning(
            "wechat_mp 图片压缩失败 reason={}", type(exc).__name__
        )
        return None


# ------------------------------- 解析器 -------------------------------


class VisionParser:
    """公众号图片 VL 解析器（异步；调用方为 service 管道）。

    测试可注入 ``targets``（绕过查库）与 ``gateway_factory``（(provider, model) →
    带 chat() 的网关替身）隔离真实 LLM。
    """

    def __init__(
        self,
        targets: Optional[List[VisionTarget]] = None,
        gateway_factory=None,
        concurrency: int = PARSE_CONCURRENCY,
        temperature: float = 0.2,
    ):
        self._targets = targets
        self._gateway_factory = gateway_factory or self._default_gateway_factory
        self._semaphore = asyncio.Semaphore(concurrency)
        self._temperature = temperature
        self._gateways: Dict[Tuple[str, str], Any] = {}

    @staticmethod
    def _default_gateway_factory(provider: str, model: str):
        # use_failover=False：目标即绑定多模态模型；settings failover 链上的备用
        # provider 默认模型不保证多模态，跨 provider 降级会违反「不发纯文本模型」
        # 门禁（设计 §6.2），多模态目标间的降级由 resolve_vision_targets 目标列表承担
        from src.llm.gateway import LLMGateway

        return LLMGateway(
            provider_name=provider, model_codes={provider: model}, use_failover=False
        )

    def _get_gateway(self, target: VisionTarget):
        key = (target.provider, target.model)
        if key not in self._gateways:
            self._gateways[key] = self._gateway_factory(target.provider, target.model)
        return self._gateways[key]

    def available(self) -> bool:
        """是否存在可用多模态目标（无 → 调用方走 deferred，不发纯文本模型）。"""
        if self._targets is None:
            self._targets = resolve_vision_targets()
        return bool(self._targets)

    # ---------------- 批量入口 ----------------

    async def describe_images(
        self, images: List[Tuple[int, str]], tenant_id: str
    ) -> VisionParseOutcome:
        """并发解析图片列表 [(n, local_path)]；逐张独立，失败不中断其余。

        单张尝试序列：首选目标（初次 + 重试 1 次）→ 次选目标 1 次（仍为多模态）。
        """
        outcome = VisionParseOutcome()
        if self._targets is None:
            self._targets = resolve_vision_targets()
        targets = self._targets or []
        if not targets:
            outcome.no_model = True
            return outcome

        # 尝试序列：首选目标重试 1 次；有次选目标再降级 1 次（全为多模态模型）
        attempts: List[VisionTarget] = [targets[0], targets[0]]
        if len(targets) > 1:
            attempts.append(targets[1])

        async def _run(n: int, path: str) -> None:
            result = await self._describe_one(n, path, attempts)
            if isinstance(result, ImageParseSuccess):
                outcome.successes.append(result)
            else:
                outcome.failures.append(result)

        await asyncio.gather(*(_run(n, p) for n, p in images))
        # 按图片序号稳定排序（并发完成顺序不定，保证 outcome 可预期）
        outcome.successes.sort(key=lambda s: s.n)
        outcome.failures.sort(key=lambda f: f.n)
        return outcome

    # ---------------- 单张 ----------------

    async def _describe_one(
        self, n: int, path: str, attempts: List[VisionTarget]
    ):
        """解析单张：按尝试序列调用；成功返回 ImageParseSuccess，否则 ImageParseFailure。"""
        data_url = await asyncio.to_thread(build_image_data_url, path)
        if data_url is None:
            return ImageParseFailure(n=n, reason="image_read_failed")

        messages = self._build_messages(data_url)
        last_reason = "error"
        for target in attempts:
            usage: Dict[str, int] = {}
            try:
                async with self._semaphore:  # 并发限流覆盖每次网络调用
                    result = await self._get_gateway(target).chat(
                        messages=messages,
                        temperature=self._temperature,
                    )
            except Exception as exc:  # noqa: BLE001 单张失败不中断其余图片
                last_reason = "error"
                logger.bind(module="wechat_mp").debug(
                    "wechat_mp VL 单张调用失败 n={} provider={} reason={}",
                    n, target.provider, type(exc).__name__,
                )
                continue
            content = str((result or {}).get("content") or "").strip()
            usage = self._extract_usage(result)
            if not content:
                last_reason = "empty_response"
                continue
            if content == UNRECOGNIZED_TEXT:
                # 按指令约定的不可识别输出：计 failed 且不计费（设计 §6.2 口径）
                return ImageParseFailure(n=n, reason="unrecognized")
            return ImageParseSuccess(
                n=n, description=content, model=target.model,
                provider=target.provider, usage=usage,
            )
        return ImageParseFailure(n=n, reason=last_reason)

    @staticmethod
    def _build_messages(data_url: str) -> List[Dict[str, Any]]:
        """单轮 user message = 固定指令 + 1 张图（无系统提示/历史/工具 schema）。"""
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PARSE_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

    @staticmethod
    def _extract_usage(result: Optional[Dict[str, Any]]) -> Dict[str, int]:
        """提取 provider 上报的 token 用量（对账用，不参与收费金额）。"""
        usage = (result or {}).get("usage") or {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }


# ------------------------------- 计费计算 -------------------------------


def calculate_image_parse_credit_cost(
    price_per_call: float, usage_factor: int
) -> float:
    """按张积分：credit_cost = ceil(price_per_call × usage_factor × 100) / 100。

    对齐 ASR 按次计费公式（src/services/billing.py.calculate_asr_credit_cost_with_breakdown）；
    默认 0.01 元/张 × 100 = 1 积分/张。
    """
    if price_per_call <= 0:
        return 0.0
    return max(math.ceil(price_per_call * usage_factor * 100) / 100, 0.0)
