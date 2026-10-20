"""导出兜底：async-export DOCX/PDF + 每 openID 每日配额闸门

触发场景：
- doc v3 读取报 400010（Unmarshal document error，实测含表格+图片的文档触发）
- 非 doc 类型（slide 等）导出 PDF 留档

配额：官方限制每 openID 每天 9 次（多租户间天然隔离）。计数走 redis_client
（跨请求共享；Redis 不可用时 redis_client 自动降级内存并告警）。计数为软闸门，
并发场景下极小概率放过 1-2 次超限调用，超限由服务端 9998/10006 兜底，可接受。
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client

from .client import TencentDocsClient, TencentDocsUnmarshalError
from .models import ExportResult, parse_v2_file_id

EXPORT_DAILY_LIMIT = 9
EXPORT_OPERATION_TTL_SECONDS = 20 * 60  # operationID 有效期
EXPORT_URL_TTL_SECONDS = 1800  # 下载 URL 有效期


class ExportQuotaExceededError(Exception):
    """当日导出配额已用尽（每 openID 每天 9 次）。"""


def export_count_key(open_id: str, day: Optional[str] = None) -> str:
    day = day or datetime.now().strftime("%Y%m%d")
    return f"{CacheKeys.TDOC_EXPORT_COUNT}:{open_id}:{day}"


def get_export_quota_used(open_id: str, day: Optional[str] = None) -> int:
    """只读查询当日已用导出次数（不消费配额）。创建批次前的配额闸门预检用。"""
    return int(redis_client.get(export_count_key(open_id, day)) or 0)


def check_and_consume_export_quota(open_id: str, limit: int = EXPORT_DAILY_LIMIT) -> int:
    """占用一次导出配额，返回占用后已用次数。超限抛 ExportQuotaExceededError。

    键 TTL 48h（跨天后自然废弃，无需主动清理）。
    """
    key = export_count_key(open_id)
    used = int(redis_client.get(key) or 0)
    if used >= limit:
        raise ExportQuotaExceededError(
            f"腾讯文档导出配额已用尽 open_id={open_id} used={used}/{limit}"
        )
    used += 1
    redis_client.set(key, used, ex=48 * 3600)
    return used


async def export_document(
    client: TencentDocsClient,
    file_id: str,
    dest_dir: str,
    export_type: Optional[str] = None,
    poll_interval: float = 2.0,
    timeout: float = 120.0,
    consume_quota: bool = False,
    daily_limit: int = EXPORT_DAILY_LIMIT,
) -> ExportResult:
    """发起导出 -> 轮询进度 -> 下载到 dest_dir。

    export_type: None = 导出原类型（doc 实测得 DOCX）；"pdf" = 转 PDF。
    consume_quota=True 时在 start_export 成功后占用一次每日配额（官方按
    发起的导出任务计配额，start 失败不应白耗次数）。
    返回 ExportResult，文件名固定 {file_id 段}.{ext}。
    """
    suffix = "pdf" if export_type == "pdf" else "docx"
    os.makedirs(dest_dir, exist_ok=True)

    operation_id = await client.start_export(file_id, export_type=export_type)
    if consume_quota:
        check_and_consume_export_quota(client.credentials.open_id, daily_limit)
    deadline = time.monotonic() + min(timeout, EXPORT_OPERATION_TTL_SECONDS)
    download_url = ""
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        progress, url = await client.get_export_progress(file_id, operation_id)
        if progress >= 100 and url:
            download_url = url
            break
        if progress and progress < 100:
            logger.debug(f"腾讯文档导出进行中 file_id={file_id} progress={progress}")
    if not download_url:
        raise TimeoutError(f"导出轮询超时 file_id={file_id} operation_id={operation_id}")

    file_path = os.path.join(dest_dir, f"tdoc_{file_id.replace('$', '_')}.{suffix}")
    size = await client.download(download_url, file_path)
    logger.info(f"腾讯文档导出完成 file_id={file_id} type={suffix} size={size}")
    return ExportResult(
        file_id=file_id,
        export_type=suffix,
        file_path=file_path,
        size_bytes=size,
        operation_id=operation_id,
    )


async def fetch_document_with_fallback(
    client: TencentDocsClient,
    file_id: str,
    dest_dir: str,
    daily_limit: int = EXPORT_DAILY_LIMIT,
) -> dict:
    """知识库导入的内容获取编排：doc v3 优先，400010 降级导出 DOCX。

    返回 {"path_type": "tree"|"docx", "document": {...}} 或
    {"path_type": "file", "export": ExportResult}。配额超限向上抛
    ExportQuotaExceededError，由任务层决定排队或失败。
    """
    try:
        data = await client.get_doc_content(file_id)
        return {"path_type": "tree", "document": data["document"]}
    except TencentDocsUnmarshalError as e:
        logger.warning(f"doc v3 解析失败降级导出 file_id={file_id}: {e}")
    export = await export_document(
        client, file_id, dest_dir, export_type=None,
        consume_quota=True, daily_limit=daily_limit,
    )
    return {"path_type": "file", "export": export}


__all__ = [
    "EXPORT_DAILY_LIMIT",
    "ExportQuotaExceededError",
    "export_document",
    "export_count_key",
    "get_export_quota_used",
    "check_and_consume_export_quota",
    "fetch_document_with_fallback",
    "parse_v2_file_id",
]
