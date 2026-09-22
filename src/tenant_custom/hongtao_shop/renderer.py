"""宏陶商城产品 → 知识文档渲染（renderer，纯函数模块）。

设计 §3（v1.7 结构化正文）：正文只含稳定内容（无库存/销量/评分/同步日期，
消除 hash 抖动），易变数值只进 metadata；正文为「产品信息」字段区——
产品名称/型号/颜色/工艺/卖点/适用空间/其他，由 VL 按字段转述（vision 指令）
+ 名称/接口字段聚合而成；商品ID/上架时间等标识字段只进 metadata；
论坛实拍素材不再进正文计数（关联变化只走 metadata 轻量更新，不触发重嵌）。

hash 口径（设计 §5.1）：sha256(pipeline_version + 渲染正文全文)。pipeline_version
独立于契约演进——渲染/分块管线升级时 +1 触发全量重判。

分块：一个产品 = 一条知识 = 一个检索单元 → 单 chunk 走 precomputed_chunks 旁路
（先例 excel_parser 行级分块），仅超平台 6000 字符护栏才回退 TextChunker 切分。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from src.tenant_custom.hongtao_shop.fetcher import _s, extract_detail
from src.tenant_custom.hongtao_shop.joiner import catalog_model, extract_full_tokens
from src.knowledge.chunker import MAX_EMBEDDING_CHUNK_CHARS, TextChunker
from src.knowledge.parsers import ParsedChunk

# 渲染/分块管线版本（升级触发全量重判重嵌，与业务字段无关）
PIPELINE_VERSION = "hts-render-v3"

BRAND = "宏陶商城"
TITLE_MAX_CHARS = 120

# 占位名商品判定（实测 96/674 为 "编号:xx" 形态）
PLACEHOLDER_NAME_PREFIX = "编号"

# VL 按字段转述的字段名（vision.PARSE_INSTRUCTION 约定；渲染器按「字段名：」
# 前缀逐行解析，无前缀的行归入「其他」——兼容旧格式缓存的全量转述文本）
VL_FIELD_NAMES = ("型号", "尺寸", "系列", "颜色", "工艺", "卖点", "适用空间", "其他")

# 名称色名提取：去掉前缀型号段（TFG157013松烟黛墨 → 松烟黛墨）
_NAME_MODEL_PREFIX_RE = re.compile(r"^[A-Za-z]{2,5}\d{5,7}[A-Za-z]?")


@dataclass
class RenderedProduct:
    """一个产品的渲染结果。"""

    title: str
    content_md: str
    metadata: Dict[str, Any]
    content_hash: str


def compute_content_hash(content_md: str) -> str:
    """hash = sha256(pipeline_version + 正文全文)（设计 §5.1）。"""
    return hashlib.sha256((PIPELINE_VERSION + "\n" + content_md).encode("utf-8")).hexdigest()


# 源站为国内商城：上架时间固定 UTC+8 口径（依赖服务器时区会在容器 UTC 环境
# 与源站差一天，跨环境迁移导致正文变 → hash 翻转 → 674 条全量重嵌计费）
_CN_TZ = timezone(timedelta(hours=8))


def _createtime_to_date(createtime: str) -> str:
    if createtime.isdigit():
        return datetime.fromtimestamp(int(createtime), tz=_CN_TZ).strftime("%Y-%m-%d")
    return ""


def _effect_pics(item: Dict[str, Any]) -> List[str]:
    """效果图轮播（pic 首图 + pics，去重保序）。"""
    images: List[str] = []
    for u in [item.get("pic") or "", *(item.get("pics") or [])]:
        u = _s(u)
        if u and u not in images:
            images.append(u)
    return images


def _suspected_models_from_vl(vl_map: Dict[str, Any], ordered_urls: List[str]) -> List[str]:
    """从 VL 描述段提取疑似型号/系列（完整 token 形态，按 detail 图序去重，≤3 个）。

    供占位名商品补全名称语义（设计 §3.1：图上印刷的型号/系列/规格由 VL 指令优先提取）。
    """
    tokens: List[str] = []
    for url in ordered_urls:
        cached = vl_map.get(url)
        description = (getattr(cached, "description", None) or "") if cached else ""
        for token in extract_full_tokens(description):
            if token.upper() not in [t.upper() for t in tokens]:
                tokens.append(token)
    return tokens[:3]


def _parse_vl_fields(description: str) -> Dict[str, List[str]]:
    """按「字段名：内容」前缀逐行解析 VL 转述（v1.7 指令产物）。

    无字段前缀的行归入「其他」（兼容旧格式缓存的全量转述与一句白描）。
    """
    fields: Dict[str, List[str]] = {f: [] for f in VL_FIELD_NAMES}
    for raw_line in description.splitlines():
        line = raw_line.strip().lstrip("-•·").strip()
        if not line:
            continue
        matched = False
        for field in VL_FIELD_NAMES:
            prefix = field + "："
            if line.startswith(prefix) and len(line) > len(prefix):
                fields[field].append(line[len(prefix):].strip())
                matched = True
                break
            # 半角冒号容错
            prefix_half = field + ":"
            if line.startswith(prefix_half) and len(line) > len(prefix_half):
                fields[field].append(line[len(prefix_half):].strip())
                matched = True
                break
        if not matched:
            fields["其他"].append(line)
    return fields


def _merge_dedup(*parts: str) -> List[str]:
    """合并去重（大小写不敏感，保序）。"""
    merged: List[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and p.upper() not in [m.upper() for m in merged]:
            merged.append(p)
    return merged


def render_product(
    item: Dict[str, Any],
    vl_map: Dict[str, Any],
    forum_media: List[Dict[str, Any]],
    sync_date: str,
    ingested_at: str = "",
) -> RenderedProduct:
    """渲染一个产品 → (title, content_md, metadata, content_hash)。

    - item：fetcher 产物（价格字段已弃）；
    - vl_map：URL → CachedVision（describe_images_cached 产物）；
    - forum_media：该产品的关联帖列表 [{post_id, catename, content, images, video}]。
    """
    pid = _s(item.get("id"))
    name = _s(item.get("name")) or f"商品{pid}"
    procode = _s(item.get("procode"))
    sellpoint = _s(item.get("sellpoint"))
    cid = _s(item.get("cid"))
    created = _createtime_to_date(_s(item.get("createtime")))
    model = catalog_model(item)
    pics = _effect_pics(item)
    detail_text, detail_imgs = extract_detail(_s(item.get("detail")))
    video = _s(item.get("video"))

    lines: List[str] = [f"# {name}", ""]

    # VL 字段聚合：多图按 detail_imgs 顺序合并去重（防 DB 行序抖动引起 hash 漂移）；
    # unrecognized/failed 无描述的跳过；旧格式缓存（无字段前缀）整体落入「其他」
    vl_fields: Dict[str, List[str]] = {f: [] for f in VL_FIELD_NAMES}
    for url in detail_imgs:
        cached = vl_map.get(url)
        if cached is None:
            continue
        description = (getattr(cached, "description", None) or "").strip()
        if getattr(cached, "status", "") != "ok" or not description:
            continue
        for field, values in _parse_vl_fields(description).items():
            for value in values:
                if value.upper() not in [v.upper() for v in vl_fields[field]]:
                    vl_fields[field].append(value)

    # 占位名商品：VL 提取到疑似型号/系列时补全名称语义（标题保留原名，设计 v1.1）
    suspected: List[str] = []
    if name.startswith(PLACEHOLDER_NAME_PREFIX):
        suspected = _suspected_models_from_vl(vl_map, detail_imgs)

    # 「产品信息」字段区（v1.7：结构化正文；标识类字段只进 metadata 不进正文）
    name_color = _NAME_MODEL_PREFIX_RE.sub("", name).strip()
    if not name_color or name_color == name:
        name_color = ""
    color_values = _merge_dedup(*([name_color] if name_color else []), *vl_fields["颜色"])
    craft_values = _merge_dedup(sellpoint, *vl_fields["系列"], *vl_fields["工艺"])
    other_values = _merge_dedup(
        *(["尺寸 " + "、".join(vl_fields["尺寸"])] if vl_fields["尺寸"] else []),
        *(["详情说明：" + detail_text] if detail_text else []),
        *vl_fields["其他"],
        *(["疑似型号/系列：" + "、".join(suspected)] if suspected else []),
    )
    info_lines = [f"- 产品名称：{name}"]
    if model:
        info_lines.append(f"- 型号：{model}")
    if color_values:
        info_lines.append(f"- 颜色：{'；'.join(color_values)}")
    if craft_values:
        info_lines.append(f"- 工艺：{'；'.join(craft_values)}")
    if vl_fields["卖点"]:
        info_lines.append(f"- 卖点：{'；'.join(vl_fields['卖点'])}")
    if vl_fields["适用空间"]:
        info_lines.append(f"- 适用空间：{'；'.join(vl_fields['适用空间'])}")
    if other_values:
        info_lines.append(f"- 其他：{'；'.join(other_values)}")
    lines += ["## 产品信息", "", *info_lines, ""]
    lines += ["---", "", f"数据来源：{BRAND}商品接口。", ""]

    content_md = "\n".join(lines)
    title = name[:TITLE_MAX_CHARS] or f"商品{pid}"

    trace: Dict[str, Any] = {
        "source": "hongtao_shop",
        "native_id": pid,
        "sync_date": sync_date,
    }
    if ingested_at:
        trace["ingested_at"] = ingested_at

    metadata: Dict[str, Any] = {
        "name": name,
        "model": model or "",
        "procode": procode,
        "sellpoint": sellpoint,
        "cid": cid,
        "listing_date": created,
        "stock": _s(item.get("stock")),
        "sales": _s(item.get("sales")),
        "comment_score": _s(item.get("comment_score")),
        "comment_num": _s(item.get("comment_num")),
        "pics": pics,
        "detail_images": detail_imgs,
        "video": video,
        "forum_media": forum_media,
    }
    # trace.content_hash 由渲染结果计算后回填（metadata 内的 hash 即本条内容的指纹）
    content_hash = compute_content_hash(content_md)
    trace["content_hash"] = content_hash
    metadata["trace"] = trace

    return RenderedProduct(
        title=title, content_md=content_md, metadata=metadata, content_hash=content_hash
    )


def build_chunks(content_md: str) -> List[ParsedChunk]:
    """一个产品 = 一个检索单元：单 chunk 旁路；超 6000 字符护栏才回退 TextChunker。"""
    if len(content_md) <= MAX_EMBEDDING_CHUNK_CHARS:
        return [ParsedChunk(text=content_md, metadata={"granularity": "whole"})]
    chunker = TextChunker(chunk_size=512, overlap=64)
    return [
        ParsedChunk(text=c["text"], metadata={"granularity": "overflow_chunked"})
        for c in chunker.chunk(content_md)
    ]
