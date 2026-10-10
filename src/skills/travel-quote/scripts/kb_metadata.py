# -*- coding: utf-8 -*-
"""旅游资源文档 metadata.raw_payload 读写工具（统一知识库工具前置，Phase 2）

职责：documents.metadata 顶层业务键与 raw_payload 镜像的单一同步逻辑，
供 hotel_retriever / attraction_retriever / api.travel_quote 三处写读路径复用，
保证「顶层为存储本位、raw_payload 为 LLM 镜像位」的双写一致性
（设计：docs/system/knowledge-base/unified-kb-resource-table-metadata-design.md §3）。

键位约定（与迁移脚本 scripts/migrate_travel_resource_tables_to_metadata.py 一致）：
- 表键：hotel price_table；attraction ticket_table / project_table
- 镜像：顶层业务键全量镜像进 raw_payload（排除系统键与表键本身）
"""

import json
from typing import Dict, Optional

# 系统内部键（溯源/管线痕迹）：不镜像进 raw_payload（不进 LLM 上下文），
# 与 knowledge_base_tool._parse_doc_metadata 白名单口径、迁移脚本 SYSTEM_KEYS 对齐
SYSTEM_KEYS = {
    "source_code", "external_id", "native_id", "run_id", "trace",
    "pipeline_version", "ingested_at", "content_md",
}

# 表数据键：raw_payload 内由表文本/PUT 表字段写入，镜像同步不得覆盖
TABLE_KEYS = {"price_table", "ticket_table", "project_table"}


def parse_metadata(raw) -> Dict:
    """documents.metadata 列（TEXT 存 JSON，或已解析 dict）→ dict 副本；损坏返回 {}

    dict 入参返回浅拷贝：调用方（如 apply_put_metadata）会就地修改，
    不反噬调用方持有的原 dict。
    """
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def mirror_business_keys(metadata: Dict) -> Dict:
    """顶层业务键集合（排除 raw_payload 自身与系统键）"""
    return {
        k: v for k, v in metadata.items()
        if k != "raw_payload" and k not in SYSTEM_KEYS
    }


def put_table_keys(metadata: Dict, table_updates: Dict[str, str]) -> Dict:
    """把表字段写入 raw_payload（无则创建），不动其他键。就地修改并返回。"""
    raw = metadata.get("raw_payload")
    raw = dict(raw) if isinstance(raw, dict) else {}
    raw.update(table_updates)
    metadata["raw_payload"] = raw
    return metadata


def sync_raw_payload(metadata: Dict) -> Dict:
    """顶层业务键 → raw_payload 镜像同步（顶层为准，覆盖旧镜像值）。

    表键（TABLE_KEYS）与 raw_payload 中已有的未知键不动。镜像后 raw 为空
    时不新落空键，并清除既有的损坏（非 dict）或空 payload——垃圾值不残留。
    就地修改并返回。
    """
    raw = metadata.get("raw_payload")
    raw = dict(raw) if isinstance(raw, dict) else {}
    for k, v in mirror_business_keys(metadata).items():
        if k not in TABLE_KEYS:
            raw[k] = v
    if raw:
        metadata["raw_payload"] = raw
    elif "raw_payload" in metadata:
        metadata.pop("raw_payload", None)
    return metadata


# body metadata 中禁止经 PUT 顶层合并写入的键：raw_payload 是 LLM 唯一可见
# 通道（防伪造表数据/提示注入）、images 走 PATCH 图片管线维护、系统键与表键
# 不属业务字段。剥离而非报错——前端本就不发这些键
_BODY_META_STRIP_KEYS = {"raw_payload", "images"} | SYSTEM_KEYS | TABLE_KEYS


def apply_put_metadata(existing_raw, body_meta: Optional[Dict], table_updates: Dict[str, str]) -> Dict:
    """PUT /kb/hotels|attractions 的 metadata 合并 + raw_payload 同步（D2）。

    existing_raw：DB 读到的 metadata 原值（TEXT/ dict / None）
    body_meta：PUT body 的 metadata 字段（可 None；raw_payload/images/系统键/表键
    被剥离——表数据只能经表字段写入，防止经 metadata 通道伪造 LLM 可见内容）
    table_updates：本次写入的表字段（如 {"price_table": "..."}，可空）
    返回可整体落库的新 metadata dict（表键入 raw_payload、业务键镜像同步）。
    """
    metadata = parse_metadata(existing_raw)
    if body_meta:
        metadata.update({k: v for k, v in body_meta.items() if k not in _BODY_META_STRIP_KEYS})
    if table_updates:
        put_table_keys(metadata, table_updates)
    sync_raw_payload(metadata)
    return metadata


def get_payload_table_text(metadata_raw, key: str) -> Optional[str]:
    """从 metadata.raw_payload 取表文本。

    Returns:
        str：raw_payload 有该键（含空串——表存在但无内容）
        None：raw_payload 或该键不存在 → 调用方回退读 chunk（过渡期兼容）
    """
    if key not in TABLE_KEYS:
        raise ValueError(f"非表键: {key}")
    metadata = parse_metadata(metadata_raw)
    raw = metadata.get("raw_payload")
    if isinstance(raw, dict) and key in raw:
        value = raw[key]
        return value if isinstance(value, str) else None
    return None
