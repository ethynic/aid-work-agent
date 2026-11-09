# -*- coding: utf-8 -*-
"""渠道端 Agent 回图发送（所有第三方渠道共用）

渠道单条消息只能单一类型（text / image / file 互斥），UnifiedResponse 中的
ImageRef（file_id 引用）需要各渠道先解析成本地文件、再走各平台上传 API。

本模块负责「ImageRef -> 本地路径」这一段公共逻辑；各渠道 adapter 负责
「本地路径 -> 平台 media_id/image_key/photoURL -> 发送」平台差异段。

发送顺序与降级口径（对齐 wecom_kf 基准实现）：
    文本（含图片占位符）-> images -> downloadable_files
    单图失败记 warning 不阻断后续发送。
"""
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


async def resolve_image_ref_local_path(ref: Any) -> Optional[Path]:
    """从 ImageRef（dict 形式，含 file_id）解析出本地路径。

    通过 ImageRegistry 从 Redis 查 file_id 拿本地绝对路径。
    ref 只要有 file_id 字段即可（不需要 ImageRef 全部字段）。

    Returns:
        本地路径 Path 对象，失败返回 None
    """
    from src.core.image_asset import get_image_registry

    file_id = ref.get("file_id") if isinstance(ref, dict) else None
    if not file_id:
        logger.warning("[image_reply] resolve image path failed: ref 缺少 file_id, ref=%r", ref)
        return None
    try:
        registry = get_image_registry()
        ref_obj = await registry.get_ref_by_file_id(file_id)
        if ref_obj is None:
            logger.warning("[image_reply] image not found in registry: file_id=%s", file_id)
            return None
        return await registry.resolve_local_path(ref_obj)
    except Exception as e:
        logger.warning("[image_reply] resolve image path failed file_id=%s: %s", file_id, e)
        return None


def resolve_downloadable_file_local_path(file_info: Any, tenant_id: Optional[str]) -> Optional[str]:
    """从 DownloadableFileInfo（file_id 引用）解析图片文件的本地路径。

    对齐 wecom_kf _send_image_file_as_image 口径：Redis uploaded_file:{file_id}
    元数据取本地路径 + 租户归属校验（防止构造 file_id 读取其他租户文件）。
    调用方按需做平台素材大小限制（如企微临时素材 image 2MB）。

    Returns:
        本地路径字符串，失败返回 None
    """
    import os

    from src.core.redis_client import redis_client
    from src.core.storage import is_tenant_owned_file

    file_id = getattr(file_info, "file_id", None)
    if not file_id or not tenant_id:
        return None
    file_meta = redis_client.hgetall(redis_client.make_key("uploaded_file", file_id))
    if not file_meta:
        return None
    file_path = file_meta.get("path")
    if not file_path or not os.path.exists(file_path):
        return None
    if not is_tenant_owned_file(file_path, tenant_id):
        logger.warning(
            f"[image_reply] 图片不在本租户存储目录内，跳过直发: file_id={file_id}"
        )
        return None
    return file_path
