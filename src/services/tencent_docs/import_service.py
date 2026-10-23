"""导入任务框架：批次创建（预检 + 配额闸门 + 无变化跳过）+ item 执行编排

创建批次（API 进程，同步快速返回）：
- 逐篇 get_doc_content 预检：成功 -> tree 路径；400010 -> needs_export；其他错误 -> 直接 failed
- needs_export 总数超当日剩余导出配额（每 openID 9 次/天）-> 拒绝整批（ImportPreflightError）
- 已导入且 last_modify_time 未变 -> item 直接 skipped（用户确认的覆盖更新语义下，
  无变化文档不重复入库、不删旧版）

执行 item（background runner 调度器进程）：
- fetch_document_with_fallback（400010 自动降级导出 DOCX，exporter.py）
- tree -> Markdown 写 .md；file -> 直接用导出的 DOCX
- KnowledgeService.upload_document 全管线入库（source_type=tencent_docs）
- 覆盖更新：先建新文档，成功后删旧文档并 UPSERT 映射（中途失败保留旧版，不丢内容）
- 频控错误退避重试（300s 起 x2，上限 1h，最多 5 次）；单篇失败不阻塞整批
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.storage import get_tenant_storage_dir, get_tenant_storage_path
from src.db.tdoc_import import (
    TdocImportBatchDB,
    TdocImportItemDB,
    TdocImportedDocDB,
)
from src.knowledge.service import knowledge_service

from .client import TencentDocsClient, TencentDocsError, TencentDocsRateLimitError, TencentDocsUnmarshalError
from .converter import document_tree_to_markdown
from .credentials import get_credentials
from .exporter import (
    EXPORT_DAILY_LIMIT,
    ExportQuotaExceededError,
    check_and_consume_export_quota,
    fetch_document_with_fallback,
    get_export_quota_used,
)
from .models import TencentDocsCredentials

TDOC_SOURCE_TYPE = "tencent_docs"
TDOC_CATEGORY_NAME = "腾讯文档导入"
# Phase 1 仅支持 Word 类在线文档（表格类/幻灯片见计划 §8 决策点 3）
SUPPORTED_DOC_TYPES = {"doc"}

RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 3600
MAX_RETRIES = 5

# 腾讯文档 API 全局节流（单副本调度器 + API 预检共用；0.5s 间隔 = 120 次/分，
# 远低于单用户 300 次/分频控上限）
_TDOC_MIN_CALL_INTERVAL = 0.5
_last_call_ts = 0.0
_throttle_lock = asyncio.Lock()


class ImportPreflightError(Exception):
    """创建批次被拒绝（如导出配额不足），整批不创建。"""


async def throttle_tdoc_call() -> None:
    """跨协程串行化腾讯文档 API 调用间隔（内存节流，仅限单进程单副本语义）。"""
    global _last_call_ts
    async with _throttle_lock:
        wait = _last_call_ts + _TDOC_MIN_CALL_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_ts = time.monotonic()


# ------------------------------- 分类登记 -------------------------------

def ensure_tdoc_category(tenant_id: str) -> None:
    """幂等登记租户顶级分类 tencent_docs（入库前调用；失败仅告警不阻塞导入）。"""
    try:
        categories = knowledge_service.list_categories(tenant_id)
        if any(c.get("source_type") == TDOC_SOURCE_TYPE for c in categories):
            return
        result = knowledge_service.create_category(
            tenant_id=tenant_id, source_type=TDOC_SOURCE_TYPE, display_name=TDOC_CATEGORY_NAME
        )
        if result.get("success"):
            logger.info(f"腾讯文档导入分类已登记 tenant_id={tenant_id}")
        else:
            # 409（并发已建）等情况视为成功
            logger.warning(f"腾讯文档导入分类登记未成功（可接受）: {result.get('error')}")
    except Exception as e:
        logger.opt(exception=True).warning(f"腾讯文档导入分类登记异常（不阻塞导入）: {e}")


# ------------------------------- 创建批次 -------------------------------

async def create_batch(
    tenant_id: str, user_id: str,
    files: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """创建导入批次。

    files 每项：file_id（内容操作 ID，快捷方式文档传 shortcut_id）/ title /
    doc_type / last_modify_time（前端选择器从 drive 列表取得）。

    Returns: {"success": True, "batch_id": ...} 或
             {"success": False, "error": ...}（整批拒绝/无有效项）。
    """
    if not files:
        return {"success": False, "error": "未选择任何文档"}

    credentials = get_credentials(tenant_id, user_id)

    prechecked: List[Dict[str, Any]] = []
    needs_export_count = 0
    preflight_fail_count = 0

    for f in files:
        file_id = (f.get("file_id") or "").strip()
        title = (f.get("title") or file_id or "未命名").strip()
        doc_type = (f.get("doc_type") or "").strip()
        last_modify = f.get("last_modify_time")
        item = {
            "tdoc_file_id": file_id,
            "title": title,
            "doc_type": doc_type,
            "last_modify_time": int(last_modify) if last_modify else None,
            "status": "pending",
            "error_message": None,
        }

        if not file_id:
            item.update(status="failed", error_message="缺少文档 ID")
            preflight_fail_count += 1
        elif doc_type not in SUPPORTED_DOC_TYPES:
            item.update(status="failed", error_message=f"暂不支持该文档类型（{doc_type or '未知'}），Phase 1 仅支持在线文档")
            preflight_fail_count += 1
        else:
            # 无变化跳过：已导入且 last_modify_time 未变
            mapped = TdocImportedDocDB.get(tenant_id, file_id)
            if (
                mapped
                and item["last_modify_time"]
                and mapped.get("last_modify_time") is not None
                and int(mapped["last_modify_time"]) == item["last_modify_time"]
            ):
                item.update(status="skipped", error_message="文档自上次导入后无变化，已跳过")
            else:
                try:
                    await throttle_tdoc_call()
                    async with TencentDocsClient(credentials) as client:
                        await client.get_doc_content(file_id)
                    item["path_hint"] = "tree"
                except TencentDocsUnmarshalError:
                    item["path_hint"] = "export"
                    needs_export_count += 1
                except TencentDocsError as e:
                    item.update(status="failed", error_message=f"预检失败：{e.message}")
                    preflight_fail_count += 1

        prechecked.append(item)

    # 导出配额闸门：needs_export 总数不得超过当日剩余配额（预检只读不消费）
    if needs_export_count > 0:
        used = get_export_quota_used(credentials.open_id)
        remaining = max(0, EXPORT_DAILY_LIMIT - used)
        if needs_export_count > remaining:
            return {
                "success": False,
                "error": (
                    f"所选文档中有 {needs_export_count} 篇需走导出兜底（doc v3 无法直接读取），"
                    f"今日导出配额仅剩 {remaining}/{EXPORT_DAILY_LIMIT} 次，"
                    "请减少选择或明日再试"
                ),
            }

    queueable = [i for i in prechecked if i["status"] == "pending"]
    skipped_count = sum(1 for i in prechecked if i["status"] == "skipped")
    if not queueable and not skipped_count:
        errors = "；".join(
            f"《{i['title']}》{i['error_message']}" for i in prechecked if i["status"] == "failed"
        )
        return {"success": False, "error": f"没有可导入的文档：{errors}"}

    batch_id = f"tdocb_{uuid.uuid4().hex[:12]}"
    error_summary_parts = []
    if preflight_fail_count:
        error_summary_parts.append(f"预检失败 {preflight_fail_count} 篇")
    if skipped_count:
        error_summary_parts.append(f"无变化跳过 {skipped_count} 篇")
    TdocImportBatchDB.create(
        batch_id, tenant_id, user_id, prechecked,
        error_message="；".join(error_summary_parts),
    )
    return {
        "success": True,
        "batch_id": batch_id,
        "total": len(prechecked),
        "queued": len(queueable),
        "skipped": skipped_count,
        "failed_preflight": preflight_fail_count,
    }


# ------------------------------- 执行 item -------------------------------

def _retry_backoff(retry_count: int) -> int:
    return min(RETRY_BASE_SECONDS * (2 ** retry_count), RETRY_MAX_SECONDS)


async def execute_item(item: Dict[str, Any], credentials: TencentDocsCredentials) -> None:
    """执行单个导入 item。异常全部在内部消化（落 item 状态），不向上抛。"""
    item_id = item["id"]
    file_id = item["tdoc_file_id"]
    tenant_id = item["tenant_id"]
    user_id = item.get("user_id") or ""
    title = item.get("title") or file_id

    try:
        async with TencentDocsClient(credentials) as client:
            await throttle_tdoc_call()
            dest_dir = get_tenant_storage_dir(tenant_id, "knowledge")
            fetched = await fetch_document_with_fallback(client, file_id, dest_dir)

        if fetched["path_type"] == "tree":
            markdown = document_tree_to_markdown(fetched["document"])
            if not markdown.strip():
                raise TencentDocsError(-1, "文档内容为空（树转 Markdown 后无文本）")
            filename = f"tdoc_{uuid.uuid4().hex[:12]}.md"
            file_path = get_tenant_storage_path(tenant_id, "knowledge", filename)
            await asyncio.to_thread(_write_text, file_path, markdown)
            upload_name = f"{title}.md"
        else:
            export = fetched["export"]
            file_path = export.file_path
            upload_name = f"{title}.{export.export_type}"

        ensure_tdoc_category(tenant_id)

        last_modify = item.get("last_modify_time")
        result = await knowledge_service.upload_document(
            file_path=file_path,
            file_filename=upload_name,
            user_id=user_id or None,
            tenant_id=tenant_id,
            source_type=TDOC_SOURCE_TYPE,
        )
        if not result.get("success") or not result.get("document_id"):
            raise TencentDocsError(-1, f"知识库入库失败：{result.get('error') or '未知错误'}")
        document_id = int(result["document_id"])

        # 覆盖更新：先建新（上面已成功）再删旧，中途失败保留旧版本
        mapped = TdocImportedDocDB.get(tenant_id, file_id)
        if mapped and mapped.get("document_id"):
            try:
                del_result = await knowledge_service.delete_document(
                    int(mapped["document_id"]), tenant_id
                )
                if not del_result.get("success"):
                    logger.warning(
                        f"腾讯文档覆盖更新：旧文档删除未成功（保留旧版） doc_id={mapped['document_id']}: "
                        f"{del_result.get('error')}"
                    )
            except Exception as e:
                logger.opt(exception=True).warning(
                    f"腾讯文档覆盖更新：旧文档删除异常（保留旧版） doc_id={mapped['document_id']}: {e}"
                )
        TdocImportedDocDB.upsert(tenant_id, file_id, document_id, title, last_modify)

        TdocImportItemDB.mark_succeeded(item_id, document_id)
        logger.info(f"腾讯文档导入成功 item_id={item_id} title={title} document_id={document_id}")

    except TencentDocsRateLimitError as e:
        retry_count = int(item.get("retry_count") or 0)
        if retry_count + 1 <= MAX_RETRIES:
            delay = _retry_backoff(retry_count)
            TdocImportItemDB.mark_failed(item_id, f"频控限制，将自动重试：{e.message}", retry_delay_seconds=delay)
            logger.warning(f"腾讯文档导入频控退避 item_id={item_id} retry={retry_count + 1} delay={delay}s")
        else:
            TdocImportItemDB.mark_failed(item_id, f"频控限制，重试 {MAX_RETRIES} 次后放弃：{e.message}")
            logger.warning(f"腾讯文档导入频控放弃 item_id={item_id}")
    except ExportQuotaExceededError as e:
        TdocImportItemDB.mark_failed(item_id, "今日导出配额已用尽（每账号每天 9 次），请明日重试")
        logger.warning(f"腾讯文档导入导出配额不足 item_id={item_id}: {e}")
    except Exception as e:
        logger.opt(exception=True).error(f"腾讯文档导入失败 item_id={item_id} title={title}: {e}")
        TdocImportItemDB.mark_failed(item_id, f"导入失败：{e}")
    finally:
        TdocImportBatchDB.refresh_progress(item["batch_id"])


def _write_text(path: str, text: str) -> None:
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
