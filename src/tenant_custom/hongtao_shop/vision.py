"""宏陶商城商品详情图 VL 解析（hongtao_shop 专用，照 wechat_mp/vision.py 模式复制）。

与 wechat_mp/vision.py 的差异（设计 §4）：
- 输入是**远程 OSS URL**（wechat_mp 为本地路径）：先流式下载（≤5MB 护栏）再 base64；
- 瓷砖定制指令：优先转述图上印刷的型号/系列/规格文字（反哺补全 96 个"编号:xx"
  占位商品的名称语义），无文字则一句客观白描；
- 图级缓存（bs_image_vision_cache，URL 为键）：OSS 文件名内含内容 hash，
  URL 不变即图不变，缓存命中零费用零解析；仅 detail_images 进 VL（pics/论坛图不做）；
- source_type=hongtao_shop_image_parse（独立计费伪模型，落账在 service 层，
  本模块不做计费——边界与 wechat_mp 相同）。

缓存语义：ok/unrecognized 为终态入缓存（unrecognized 不计费）；failed 不入缓存，
下一轮 run 重试（避免坏图永久卡死）。
"""
from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

# ------------------------------- 常量 -------------------------------

# chat_records.source_type（按张计费）；单一事实源在 bootstrap，防多处字面量漂移
from src.tenant_custom.hongtao_shop.bootstrap import VL_IMAGE_PARSE_MODEL

VISION_PARSE_SOURCE_TYPE = VL_IMAGE_PARSE_MODEL
UNRECOGNIZED_TEXT = "图片无法识别"

# 瓷砖定制指令（设计 §4：图上印刷文字优先——型号/系列/规格是检索主力；
# 无文字改一句客观白描；直接输出信息，禁止前缀/标签/说明性开场）
PARSE_INSTRUCTION = (
    "若瓷砖图上有印刷文字(型号/系列/规格/花色名等)：直接转述这些文字信息，"
    "转述不评价不发挥；若图上没有文字：用一句话客观描述这是什么或什么场景"
    "(如\"通体大理石纹理砖效果图\"\"地面铺贴完工实景\")，不评价不虚构不啰嗦；"
    "不要任何前缀、标签、标题行或说明性开场"
    "(如\"图片识别结果如下：\"\"标题：\"\"这张图片展示了…\")；"
    f"确实无法判读图片内容时才输出\"{UNRECOGNIZED_TEXT}\""
)

MAX_DESCRIPTION_CHARS = 100
PARSE_CONCURRENCY = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 单张下载字节上限（超限先压缩）
DOWNLOAD_TIMEOUT_SECONDS = 30.0
USER_AGENT = "aid-kb-ingest/1.0"

_DESCRIPTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"图片(?:识别|解析)结果(?:如下|为)?[:：]?"
    r"|识别结果(?:如下|为)?[:：]?"
    r"|标题[:：]"
    r"|描述[:：]"
    r"|内容[:：]"
    r"|这张图(?:片)?(?:展示|呈现|显示|拍摄)了?[:：]?"
    r"|图中(?:展示|呈现|显示)了?[:：]?"
    r")\s*"
)


def clean_description(text: str) -> str:
    """保守剥离 VL 产出开头的常见标签前缀（照 wechat_mp 同款实现）。"""
    if not text:
        return text
    cleaned = text.strip()
    for _ in range(3):
        stripped = _DESCRIPTION_PREFIX_RE.sub("", cleaned, count=1)
        if stripped == cleaned:
            break
        cleaned = stripped.strip()
    return cleaned


_PINNED_VISION_MODEL = "GLM-5.3-Flash"
_PINNED_VISION_PROVIDER = "zhipu"
_PROVIDER_ORDER = ("qwen", "zhipu", "deepseek", "moonshot")


# ------------------------------- 结果结构 -------------------------------


@dataclass
class VisionTarget:
    provider: str
    model: str


@dataclass
class ImageParseSuccess:
    url: str
    description: str
    model: str
    provider: str
    usage: Dict[str, int] = field(default_factory=dict)


@dataclass
class ImageParseFailure:
    url: str
    reason: str  # error/empty_response/unrecognized/download_failed


@dataclass
class VisionParseOutcome:
    successes: List[ImageParseSuccess] = field(default_factory=list)
    failures: List[ImageParseFailure] = field(default_factory=list)
    no_model: bool = False

    @property
    def descriptions(self) -> Dict[str, str]:
        return {s.url: s.description for s in self.successes}


@dataclass
class CachedVision:
    """图级缓存行（或当轮新解析等价结构）。"""

    image_url: str
    description: Optional[str]
    model: Optional[str]
    status: str  # ok | unrecognized
    is_billed: bool = False


# ------------------------------- 模型选择（照 wechat_mp 复制） -------------------------------


def resolve_vision_targets(
    multimodal_models: Optional[List[Dict[str, Any]]] = None,
) -> List[VisionTarget]:
    """求「多模态模型清单 × provider 配置默认模型」交集，固定首选 GLM-5.3-Flash。"""
    from src.config.settings import settings

    if multimodal_models is None:
        try:
            from src.db.models import TokenCostPriceDB

            multimodal_models = TokenCostPriceDB.list_multimodal_models()
        except Exception as exc:  # noqa: BLE001 查库失败按无模型处理
            logger.bind(module="hongtao_shop").warning(
                "hongtao_shop 多模态模型清单查询失败（按无模型处理）: {}", type(exc).__name__
            )
            multimodal_models = []

    multimodal = {
        str(m.get("model_name") or "").strip().lower()
        for m in multimodal_models or []
        if m.get("model_name")
    }
    if not multimodal:
        return []

    ordered: List[str] = []
    primary = str(getattr(settings.llm, "provider", "") or "")
    if primary:
        ordered.append(primary)
    try:
        for name in (settings.llm.failover.providers or []):
            if name not in ordered:
                ordered.append(name)
    except Exception:  # noqa: BLE001
        pass
    for name in _PROVIDER_ORDER:
        if name not in ordered:
            ordered.append(name)

    targets: List[VisionTarget] = []
    pinned = _PINNED_VISION_MODEL.lower()
    if pinned in multimodal:
        zhipu_cfg = getattr(settings.llm, "zhipu", None)
        if zhipu_cfg is not None:
            try:
                if zhipu_cfg.get_effective_keys():
                    targets.append(
                        VisionTarget(provider=_PINNED_VISION_PROVIDER, model=_PINNED_VISION_MODEL)
                    )
            except Exception:  # noqa: BLE001
                pass
    for provider in ordered:
        cfg = getattr(settings.llm, provider, None)
        model = str(getattr(cfg, "model", "") or "").strip()
        if not model:
            continue
        try:
            if not cfg.get_effective_keys():
                continue
        except Exception:  # noqa: BLE001
            continue
        if model.lower() in multimodal:
            target = VisionTarget(provider=provider, model=model)
            if target not in targets:
                targets.append(target)
    return targets


# ------------------------------- 远程图下载 -------------------------------


async def download_image_bytes(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    """流式下载远程图（≤5MB 护栏；非 200/异常返回 None 计 download_failed）。"""
    if not url.startswith(("http://", "https://")):
        return None
    try:
        request = client.build_request("GET", url, headers={"User-Agent": USER_AGENT})
        response = await client.send(request, stream=True)
        try:
            if response.status_code != 200:
                return None
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            await response.aclose()
    except Exception:  # noqa: BLE001
        return None


_FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def bytes_to_data_url(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> Optional[str]:
    """bytes → base64 data URL；超限/未知格式经 Pillow 压缩（照 wechat_mp 压缩序列）。"""
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            mime = _FORMAT_MIME.get(img.format or "")
            if mime and len(data) <= max_bytes:
                b64 = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{b64}"
            for quality in (85, 70, 55, 40):
                buf = BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=quality)
                if buf.tell() <= max_bytes:
                    break
            else:
                with Image.open(BytesIO(data)) as img2:
                    while buf.tell() > max_bytes:
                        w, h = img2.size
                        img2 = img2.convert("RGB").resize((max(1, w // 2), max(1, h // 2)))
                        buf = BytesIO()
                        img2.save(buf, format="JPEG", quality=40)
                        if max(img2.size) <= 64:
                            break
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except Exception:  # noqa: BLE001 损坏图片按失败处理
        return None


# ------------------------------- 解析器 -------------------------------


class VisionParser:
    """宏陶详情图 VL 解析器（异步；输入远程 URL 列表）。

    测试可注入 ``targets``、``gateway_factory``、``client``（下载隔离）。
    """

    def __init__(
        self,
        targets: Optional[List[VisionTarget]] = None,
        gateway_factory=None,
        client: Optional[httpx.AsyncClient] = None,
        concurrency: int = PARSE_CONCURRENCY,
        temperature: float = 0.2,
    ):
        self._targets = targets
        self._gateway_factory = gateway_factory or self._default_gateway_factory
        self._client = client
        self._semaphore = asyncio.Semaphore(concurrency)
        self._temperature = temperature
        self._gateways: Dict[Tuple[str, str], Any] = {}

    @staticmethod
    def _default_gateway_factory(provider: str, model: str):
        from src.llm.gateway import LLMGateway

        return LLMGateway(
            provider_name=provider, model_codes={provider: model}, use_failover=False
        )

    def _get_gateway(self, target: VisionTarget):
        key = (target.provider, target.model)
        if key not in self._gateways:
            self._gateways[key] = self._gateway_factory(target.provider, target.model)
        return self._gateways[key]

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS
            )
        return self._client

    def available(self) -> bool:
        if self._targets is None:
            self._targets = resolve_vision_targets()
        return bool(self._targets)

    async def describe_images(self, urls: List[str]) -> VisionParseOutcome:
        """并发解析 URL 列表；逐张独立，失败不中断其余。

        单张尝试序列：首选目标（初次 + 重试 1 次）→ 次选目标 1 次（全为多模态模型）。
        """
        outcome = VisionParseOutcome()
        if self._targets is None:
            self._targets = resolve_vision_targets()
        targets = self._targets or []
        if not targets:
            outcome.no_model = True
            return outcome

        attempts: List[VisionTarget] = [targets[0], targets[0]]
        if len(targets) > 1:
            attempts.append(targets[1])

        async def _run(url: str) -> None:
            result = await self._describe_one(url, attempts)
            if isinstance(result, ImageParseSuccess):
                outcome.successes.append(result)
            else:
                outcome.failures.append(result)

        await asyncio.gather(*(_run(u) for u in urls))
        outcome.successes.sort(key=lambda s: s.url)
        outcome.failures.sort(key=lambda f: f.url)
        return outcome

    async def _describe_one(self, url: str, attempts: List[VisionTarget]):
        async with self._semaphore:  # 下载同为网络调用，一并限流（对齐 wechat_mp 口径）
            data = await download_image_bytes(self._get_client(), url)
            if data is None:
                return ImageParseFailure(url=url, reason="download_failed")
        data_url = await asyncio.to_thread(bytes_to_data_url, data)
        if data_url is None:
            return ImageParseFailure(url=url, reason="download_failed")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PARSE_INSTRUCTION},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        last_reason = "error"
        for target in attempts:
            try:
                async with self._semaphore:
                    result = await self._get_gateway(target).chat(
                        messages=messages,
                        temperature=self._temperature,
                    )
            except Exception:  # noqa: BLE001 单张失败不中断其余
                last_reason = "error"
                continue
            content = str((result or {}).get("content") or "").strip()
            if not content:
                last_reason = "empty_response"
                continue
            if content == UNRECOGNIZED_TEXT:
                # 真不可判读：计 unrecognized 不计费（终态入缓存）
                return ImageParseFailure(url=url, reason="unrecognized")
            description = clean_description(content)
            if len(description) > MAX_DESCRIPTION_CHARS:
                description = description[:MAX_DESCRIPTION_CHARS]
            usage = (result or {}).get("usage") or {}
            return ImageParseSuccess(
                url=url,
                description=description,
                model=target.model,
                provider=target.provider,
                usage={
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                },
            )
        return ImageParseFailure(url=url, reason=last_reason)


# ------------------------------- 图级缓存 -------------------------------


def load_cached_visions(tenant_id: str, urls: List[str]) -> Dict[str, CachedVision]:
    """读缓存（同步，调用方 asyncio.to_thread 包裹）；无表/异常返回空 dict 不阻断。"""
    if not urls:
        return {}
    from src.db.database import get_db_connection

    cached: Dict[str, CachedVision] = {}
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT image_url, description, model, status, is_billed "
                "FROM bs_image_vision_cache "
                "WHERE tenant_id = %s AND image_url = ANY(%s)",
                (tenant_id, urls),
            )
            for row in cursor.fetchall():
                cached[row["image_url"]] = CachedVision(
                    image_url=row["image_url"],
                    description=row["description"],
                    model=row["model"],
                    status=row["status"],
                    is_billed=bool(row["is_billed"]),
                )
    except Exception as exc:  # noqa: BLE001 缓存读失败按全量 miss 处理
        logger.bind(module="hongtao_shop").warning(
            "hongtao_shop VL 缓存读取失败（按 miss 处理）: {}", type(exc).__name__
        )
    return cached


def save_vision_rows(tenant_id: str, visions: List[CachedVision]) -> None:
    """写缓存（upsert；仅 ok/unrecognized 终态，failed 不入缓存）。

    fail-open：写失败仅记日志不抛——缓存是优化不是正确性依赖，解析成果（与计费）
    已在调用方手中，下轮同 URL 会 miss 重解析重计费属可接受损耗，不能因缓存故障
    把整个产品的处理炸掉。
    """
    if not visions:
        return
    from src.db.database import get_db_connection

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for v in visions:
                if v.status not in ("ok", "unrecognized"):
                    continue
                cursor.execute(
                    """
                    INSERT INTO bs_image_vision_cache
                        (tenant_id, image_url, description, model, status, is_billed, parsed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (tenant_id, image_url) DO UPDATE
                    SET description = EXCLUDED.description,
                        model = EXCLUDED.model,
                        status = EXCLUDED.status,
                        is_billed = EXCLUDED.is_billed,
                        parsed_at = now()
                    """,
                    (tenant_id, v.image_url, v.description, v.model, v.status, v.is_billed),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 缓存写失败不阻断业务
        logger.bind(module="hongtao_shop").warning(
            "hongtao_shop VL 缓存写入失败（下轮将重复解析）: {}", type(exc).__name__
        )


async def describe_images_cached(
    tenant_id: str, urls: List[str], parser: Optional[VisionParser] = None
) -> Tuple[Dict[str, CachedVision], List[ImageParseSuccess], List[ImageParseFailure]]:
    """图级缓存判增量 → 解析新增 → 终态入缓存。

    返回 (vision_map, new_successes, new_failures)：
    - vision_map：URL → 最终可用视觉信息（含缓存命中与新解析，miss/failed 不在 map）
    - new_successes：本轮新解析成功（供 service 落 chat_records 按张计费）
    - new_failures：本轮新解析失败（failed 不入缓存，下一轮重试）
    """
    parser = parser or VisionParser()
    # 入口保序去重兜底（重复 URL 只解析/计费一次）
    urls = list(dict.fromkeys(urls))
    cached = await asyncio.to_thread(load_cached_visions, tenant_id, urls)
    misses = [u for u in urls if u not in cached]
    new_successes: List[ImageParseSuccess] = []
    new_failures: List[ImageParseFailure] = []
    if misses:
        outcome = await parser.describe_images(misses)
        new_successes = outcome.successes
        new_failures = outcome.failures
        rows = [
            CachedVision(
                image_url=s.url, description=s.description, model=s.model,
                status="ok", is_billed=True,
            )
            for s in outcome.successes
        ] + [
            CachedVision(
                image_url=f.url, description=None, model=None,
                status="unrecognized", is_billed=False,
            )
            for f in outcome.failures if f.reason == "unrecognized"
        ]
        await asyncio.to_thread(save_vision_rows, tenant_id, rows)
        for row in rows:
            cached[row.image_url] = row
    return cached, new_successes, new_failures
