"""hongtao_shop renderer 单测：正文模板/hash 口径/metadata 完整性/单 chunk 回退。"""

import json

from src.tenant_custom.hongtao_shop.renderer import (
    PIPELINE_VERSION,
    RenderedProduct,
    build_chunks,
    compute_content_hash,
    render_product,
)
from src.tenant_custom.hongtao_shop.vision import CachedVision


def _item(**overrides):
    item = {
        "id": 574,
        "name": "TFZJ1890014欧典米灰",
        "procode": "",
        "sellpoint": "通体大理石",
        "cid": "12",
        "bid": "1",
        "status": "1",
        "pic": "https://oss/pic574.jpg",
        "pics": ["https://oss/p574_1.jpg", "https://oss/p574_2.jpg"],
        "detail": json.dumps(
            [
                {"content": '<img src="https://oss/d574_1.jpg"/>'},
                {"content": '<img src="https://oss/d574_2.jpg"/><p>防滑耐磨</p>'},
            ]
        ),
        "video": "",
        "stock": "1000",
        "sales": "9",
        "comment_score": "5.0",
        "comment_num": "2",
        "createtime": "1758432000",
        # 价格字段故意注入：fetcher 即弃后渲染层不得 copy-through
        "market_price": "399.00",
        "sell_price": "299.00",
    }
    item.update(overrides)
    return item


def _vl_map():
    return {
        "https://oss/d574_1.jpg": CachedVision(
            image_url="https://oss/d574_1.jpg", description="欧典米灰纹理砖效果图",
            model="GLM-5.3-Flash", status="ok", is_billed=True,
        ),
        "https://oss/d574_2.jpg": CachedVision(
            image_url="https://oss/d574_2.jpg", description=None,
            model="GLM-5.3-Flash", status="unrecognized", is_billed=False,
        ),
    }


def _forum_media():
    return [
        {
            "post_id": 419,
            "catename": "工地实景",
            "content": "TPJ157042地面铺贴实景",
            "images": ["https://oss/f1.jpg"],
            "video": "https://oss/f1.mp4",
        }
    ]


def test_render_product_basic_structure():
    r = render_product(_item(), _vl_map(), _forum_media(), "2026-09-21")
    assert r.title == "TFZJ1890014欧典米灰"
    assert r.content_md.startswith("# TFZJ1890014欧典米灰\n\n## 产品信息")
    # v1.8：去掉"是宏陶商城在售的一款…产品。"开场句（信息与字段区重复）
    assert "宏陶商城在售" not in r.content_md
    # v1.7 结构化正文：产品信息字段区
    assert "- 产品名称：TFZJ1890014欧典米灰" in r.content_md
    assert "- 型号：TFZJ1890014" in r.content_md
    assert "- 颜色：欧典米灰" in r.content_md  # 名称去型号前缀的色名
    assert "- 工艺：通体大理石" in r.content_md  # sellpoint 并入工艺
    # 旧格式 VL 描述（无字段前缀）整体落入「其他」；unrecognized 的不进
    assert "欧典米灰纹理砖效果图" in r.content_md
    # detail 富文本文字（极少）并入「其他」
    assert "详情说明：防滑耐磨" in r.content_md
    # v1.7：标识字段只进 metadata，不进正文
    for absent in ("商品ID", "上架时间", "商品编码", "商品分类ID", "基本信息",
                   "实拍素材", "效果图。"):
        assert absent not in r.content_md, f"正文不应再包含 {absent}"
    # 正文无易变数值与同步日期（设计 §3.1：只含稳定内容）
    assert "1000" not in r.content_md
    assert "库存" not in r.content_md and "销量" not in r.content_md
    assert "2026-09-21" not in r.content_md


def test_render_structured_vl_fields():
    """v1.7 字段化 VL 转述：按「字段名：内容」解析入对应栏目，尺寸进其他。"""
    vl = {
        "https://oss/d574_1.jpg": CachedVision(
            image_url="https://oss/d574_1.jpg",
            description=(
                "型号：TFZJ1890014\n"
                "尺寸：750x1500mm\n"
                "系列：通体大理石\n"
                "颜色：欧典米灰\n"
                "工艺：柔抛工艺\n"
                "卖点：防滑耐磨 温润如玉\n"
                "适用空间：家装、工装"
            ),
            model="GLM-5.3-Flash", status="ok", is_billed=True,
        )
    }
    r = render_product(_item(), vl, [], "2026-09-21")
    assert "- 型号：TFZJ1890014" in r.content_md
    assert "- 颜色：欧典米灰" in r.content_md  # 名称色名与 VL 颜色去重合一
    assert "- 工艺：通体大理石；柔抛工艺" in r.content_md  # sellpoint/VL系列/VL工艺 合并去重
    assert "- 卖点：防滑耐磨 温润如玉" in r.content_md
    assert "- 适用空间：家装、工装" in r.content_md
    assert "- 其他：尺寸 750x1500mm" in r.content_md


def test_parse_vl_fields_prefix_and_fallback():
    from src.tenant_custom.hongtao_shop.renderer import _parse_vl_fields
    parsed = _parse_vl_fields(
        "型号:TFG157013\r\n  尺寸：900x1800mm\n无前缀的转述行\n\n卖点：石中贵族"
    )
    assert parsed["型号"] == ["TFG157013"]  # 半角冒号容错
    assert parsed["尺寸"] == ["900x1800mm"]
    assert parsed["卖点"] == ["石中贵族"]
    assert parsed["其他"] == ["无前缀的转述行"]


def test_render_product_metadata_complete():
    r = render_product(_item(), _vl_map(), _forum_media(), "2026-09-21", ingested_at="2026-09-21T12:00:00")
    m = r.metadata
    # v1.9：业务数据整包嵌套在 raw_payload（与通用 api-ingest 契约对齐），
    # 顶层只留 trace 等系统痕迹
    rp = m["raw_payload"]
    assert rp["name"] == "TFZJ1890014欧典米灰"
    assert rp["model"] == "TFZJ1890014"
    assert rp["stock"] == "1000" and rp["sales"] == "9"  # 易变数值只在 metadata
    assert rp["pics"] == ["https://oss/pic574.jpg", "https://oss/p574_1.jpg", "https://oss/p574_2.jpg"]
    assert rp["detail_images"] == ["https://oss/d574_1.jpg", "https://oss/d574_2.jpg"]
    assert len(rp["forum_media"]) == 1
    # v1.7：标识字段从正文移入 metadata（正文不再含商品ID/上架时间）
    assert rp["cid"] == "12"
    assert rp["listing_date"] == "2025-09-21"  # createtime 1758432000 @UTC+8
    assert m["trace"]["source"] == "hongtao_shop"
    assert m["trace"]["native_id"] == "574"
    assert m["trace"]["sync_date"] == "2026-09-21"
    assert m["trace"]["content_hash"] == r.content_hash
    # 价格字段彻底排除：正文与 metadata 双查（fixture 已注入 market_price/sell_price）
    assert "399" not in r.content_md and "299" not in r.content_md
    assert "market_price" not in r.content_md and "sell_price" not in r.content_md
    serialized = json.dumps(m, ensure_ascii=False)
    assert "market_price" not in serialized and "sell_price" not in serialized
    assert "399" not in serialized and "299" not in serialized


def test_render_placeholder_product_gets_suspected_model():
    """占位名商品：VL 描述提取疑似型号补全（标题保留原名）。"""
    # URL 必须与 fixture 的 detail_imgs 一致（渲染按 detail 图序取描述）
    vl = {
        "https://oss/d574_1.jpg": CachedVision(
            image_url="https://oss/d574_1.jpg", description="TPJ157042 通体砖面",
            model="GLM-5.3-Flash", status="ok", is_billed=True,
        )
    }
    r = render_product(_item(name="编号:888"), vl, [], "2026-09-21")
    assert r.title == "编号:888"  # 标题始终保留原名
    assert "疑似型号/系列：TPJ157042" in r.content_md


def test_hash_idempotent_and_sensitive():
    """同输入同 hash；VL 内容变 → hash 变；论坛关联变化不进正文（v1.7：只走
    metadata 轻量更新，不再触发重嵌）；pipeline 进 hash。"""
    r1 = render_product(_item(), _vl_map(), _forum_media(), "2026-09-21")
    r2 = render_product(_item(), _vl_map(), _forum_media(), "2026-12-31")
    assert r1.content_hash == r2.content_hash  # sync_date 不进正文 → 不影响 hash

    r3 = render_product(_item(), _vl_map(), _forum_media() + _forum_media(), "2026-09-21")
    assert r3.content_hash == r1.content_hash  # v1.7：实拍素材计数不再进正文

    vl_changed = {
        "https://oss/d574_1.jpg": CachedVision(
            image_url="https://oss/d574_1.jpg", description="完全不同的描述",
            model="GLM-5.3-Flash", status="ok", is_billed=True,
        )
    }
    r4 = render_product(_item(), vl_changed, _forum_media(), "2026-09-21")
    assert r4.content_hash != r1.content_hash  # VL 内容变化触发重判

    import hashlib
    manual = hashlib.sha256((PIPELINE_VERSION + "\n" + r1.content_md).encode()).hexdigest()
    assert manual == r1.content_hash


def test_build_chunks_whole_and_overflow():
    short = render_product(_item(), _vl_map(), [], "2026-09-21")
    chunks = build_chunks(short.content_md)
    assert len(chunks) == 1
    assert chunks[0].metadata["granularity"] == "whole"
    assert chunks[0].text == short.content_md

    long_md = "# 名称\n\n" + "长文内容。" * 2000  # >6000 字符
    chunks2 = build_chunks(long_md)
    assert len(chunks2) > 1
    assert all(c.metadata["granularity"] == "overflow_chunked" for c in chunks2)


def test_compute_content_hash_stable():
    assert compute_content_hash("abc") == compute_content_hash("abc")
    assert compute_content_hash("abc") != compute_content_hash("abd")
