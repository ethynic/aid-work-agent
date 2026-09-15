"""微信公众号内容统一入库 service（WP5，设计 §3/§5.4/§6.1/§7.4，计划 WP5 节）。

职责边界（分层规则：抓取/入库/计费只在本 service 实现，调度/API/工具为薄入口）：

- **队列 worker**：``claim_and_run(tenant_id)`` 租户串行——Redis 锁（随机 owner、
  30min 租约、逐 item 续期、仅 owner 释放；Redis 不可用拒绝执行，不无锁降级）+
  DB 事务内 queued→running（撞 ``uq_wechat_mp_runs_active`` 部分唯一索引则让位）。
  执行完领下一个 queued run 直到租户队列空。
- **处理顺序硬规则**：受理只有 URL，必须**先抓页面** → 删除多信号判定（fetcher）
  → content_hash 与已成功版本比对（相同记 check 跳过 embedding/计费）→ 不同才
  重建；pipeline_version 变化或 doc 不存在同样重建。
- **单事务落库**：documents（更新场景 doc_id 快路径先删 chunks_vec 再删 chunks）
  + chunks + chunks_vec + articles 状态 + item 状态同事务提交；HTML 提取与
  embedding 在事务外完成。
- **别名收敛（保守，三审修订）**：抓取后 extract_alias_url 给出对方形态且本租户
  已有该行时才收敛；主记录=先成功入库（否则先创建）；别名行 status='alias' +
  master_article_row_id；别名行已有 doc_id 的 documents 置 deleted +
  metadata.merged_into_doc_id；同批次重复项 item 标 skipped + duplicate_of_item_id，
  不做 item 迁移；证据不足（msg_link 回显自身形态是常态）保持独立不合并。
- **计费**：embedding 独立 source_type='wechat_mp_embedding'（复用参数化后的
  KnowledgeBaseService._record_knowledge_embedding_billing，业务先提交、计费后提交，
  fail-open）；item 记 billing_status/billing_reference/credits_charged；
  响应不可确认记 unknown，P1 禁止自动重扣；计费失败不回滚内容、不重嵌。
- **余额**：run 启动预检（不足记 skipped_no_credit 终态）+ 每付费单元（embedding）
  前复查；action='check' 的复核类 item 不受启动预检阻断（删除复核免费）。
- **图片 VL 解析（WP10，P2）**：有图且文字 < MIN_TEXT_CHARS 时先下载转存微信 CDN
  图片（image_downloader.py）→ VL 解析（vision.py，无可用多模态模型不发纯文本
  模型）→ [图片N: 描述] 插回正文入库；无模型/全部失败维持 deferred；文字充足
  的文章不触发 VL（P3 再评估全量解析）。VL 按张计费 source_type=
  'wechat_mp_image_parse'，业务提交后独立落账 fail-open，与 embedding 计费合并
  回写 item 计费字段。
- **恢复**：``recover_stale_runs()`` —— heartbeat 超时且锁已失效的 running run 标
  interrupted；queued 保持等待。

- **WP6 受理/查询（模块级函数，API 薄入口调用）**：``import_urls``（手动粘贴导入，
  批内/pending 去重 + 限流 + 同事务三件套）、``normalize_batch_urls`` /
  ``upsert_article_rows``（callback 与手动导入共用去重口径）、``list_runs`` /
  ``get_run`` / ``list_articles``（租户隔离查询）、``retry_article`` /
  ``recheck_article``（重试与存活复核入队）、``portal_list_runs`` /
  ``portal_list_articles``（平台管理跨租户查询）。

fetcher 为同步实现：异步入口一律 ``asyncio.to_thread`` 包裹抓取（backend_dev 规范）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from loguru import logger

from src.db.database import get_db_connection
from src.knowledge.chunker import TextChunker
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.knowledge.vector_db.vector_db import get_vector_db
from src.wechat_mp.content import (
    ContentExtractionError,
    ContentNode,
    ExtractedArticle,
    extract_article,
    nodes_to_text,
)
from src.wechat_mp.fetcher import (
    STATUS_DELETED,
    STATUS_OK,
    FetchResult,
    MPArticleFetcher,
)
from src.wechat_mp.identity import URLIdentity, URLIdentityError, normalize_url
from src.wechat_mp.image_downloader import MPImageDownloader
from src.wechat_mp.notify import notify_queued_work
from src.wechat_mp.vision import (
    VISION_PARSE_SOURCE_TYPE,
    VisionParseOutcome,
    VisionParser,
    calculate_image_parse_credit_cost,
)

# ------------------------------- 常量 -------------------------------

PIPELINE_VERSION = "p2"
DOC_ORIGIN = "wechat_mp"
EMBEDDING_SOURCE_TYPE = "wechat_mp_embedding"
EMBEDDING_MODEL = "text-embedding-v3"

# 分类惰性创建（D7）：稳定标识 + knowledge_categories UNIQUE(tenant_id, source_type) 兜底
CATEGORY_SOURCE_TYPE = "k_wechat_mp"
CATEGORY_DISPLAY_NAME = "公众号内容"
SUB_CATEGORY_SOURCE_TYPE = "k_wechat_mp_uncategorized"
SUB_CATEGORY_DISPLAY_NAME = "未分类"
PRE_SALES_SUBAGENT = "pre-sales"

LOCK_KEY_PREFIX = "wechat_mp_sync_lock"
LOCK_TTL_SECONDS = 1800  # 30min 租约
STALE_HEARTBEAT_SECONDS = 1800  # heartbeat 超时判定（与租约同长）

RETRY_BASE_SECONDS = 300  # 指数退避基数 5min
RETRY_MAX_SECONDS = 86400  # 退避上限 24h
RETRY_MAX_SHIFT = 8

SUMMARY_MAX_CHARS = 300  # P1 确定性截断摘要（不调 LLM）
MIN_TEXT_CHARS = 20  # 有图且文字不足 → VL 解析（P2），仍失败才 deferred（设计 §13 审阅记录）

# P2 图片 VL 解析 deferred 原因文案（error_code 仍为 deferred_image_pending）
_DEFERRED_NO_MODEL = "图片待解析：无可用多模态模型，将自动重试"
_DEFERRED_PARSE_FAILED = "图片待解析：图片下载或解析全部失败，将自动重试"

# item error_code 固定原因码（不混存记录 ID；记录 ID 走 duplicate_of_item_id/billing_reference）
ERR_NO_CREDIT = "no_credit"
ERR_ALIAS_DUPLICATE = "alias_duplicate"
ERR_ALIAS_MERGED = "alias_merged"
ERR_FETCH_FAILED = "fetch_failed"
ERR_RISK_BLOCKED = "risk_blocked"
ERR_CONTENT_EMPTY = "content_empty"
ERR_EXTRACT_FAILED = "extract_failed"
ERR_ARTICLE_MISSING = "article_missing"
ERR_INTERNAL = "internal_error"

_FRIENDLY_NO_CREDIT = "积分余额不足，已跳过本次付费处理（充值后随退避重试继续）"

# 正文占位符整段匹配（nodes_to_text 的 image_placeholder 默认格式 "[图片N]"）
_IMAGE_PLACEHOLDER_RE = re.compile(r"\[图片\d+\]")

# ------------------------------- WP6 受理/查询（API 薄入口的 service 层） -------------------------------

MAX_IMPORT_URLS = 50  # 单次手动导入合法 URL 上限
MAX_QUEUED_RUNS = 10  # 简单限流：同租户 queued run 达到该数拒绝新导入
ENQUEUE_LOCK_PREFIX = "wechat_mp_enqueue:"  # 受理限流 advisory lock 键前缀（P2：检查与插入原子化）


def _advisory_enqueue_lock(cursor, tenant_id: str) -> None:
    """受理事务级 advisory lock：使「queued 限流检查 + 插入」原子化（事务结束自动释放）。

    仅覆盖手动导入与 retry/recheck 受理；callback 受理不受此锁影响
    （微信侧有自己的重试语义，不加锁，对齐 WP6 CR P2 结论）。
    """
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"{ENQUEUE_LOCK_PREFIX}{tenant_id}",),
    )


class WeChatMPBusinessError(ValueError):
    """业务规则错误（消息面向用户可直接展示；API 层捕获转 400，不泄漏内部细节）。"""


def normalize_batch_urls(
    urls: List[str],
) -> Tuple[List[URLIdentity], List[Dict[str, str]], List[Dict[str, str]]]:
    """逐条 normalize_url 校验 + 批内按 external_id 去重（纯 Python，无 DB）。

    返回 (identities, rejected, duplicates)：
    - identities：合法且批内首个出现的身份（保序）
    - rejected：非法 URL [{url, reason}]（不拒整体，由调用方决定语义）
    - duplicates：批内重复 [{url, external_id, reason}]（首个生效，后续计入重复）
    callback（WP4 受理）与本模块 import_urls 共用，保证去重口径一致。
    """
    identities: List[URLIdentity] = []
    rejected: List[Dict[str, str]] = []
    duplicates: List[Dict[str, str]] = []
    seen: set = set()
    for url in urls or []:
        try:
            identity = normalize_url(url)
        except URLIdentityError as e:
            rejected.append({"url": url, "reason": str(e)})
            continue
        if identity.external_id in seen:
            duplicates.append(
                {
                    "url": identity.original_url,
                    "external_id": identity.external_id,
                    "reason": "批内重复 URL（同一规范身份），仅首个受理",
                }
            )
            continue
        seen.add(identity.external_id)
        identities.append(identity)
    return identities, rejected, duplicates


def upsert_article_rows(
    cursor,
    tenant_id: str,
    identities: List[URLIdentity],
    *,
    config_id: Optional[str],
    source_channel: str,
) -> List[Dict[str, Any]]:
    """批内 upsert 文章当前态行（要求调用方在事务内传入 cursor）。

    已存在 (tenant_id, external_id) 行复用不新建（original_url 等保留首受理值）；
    返回 [{external_id, original_url, fetch_url, article_row_id}]（与 identities 保序）。
    """
    accepted: List[Dict[str, Any]] = []
    for identity in identities:
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, config_id, external_id, original_url, fetch_url,
                 source_channel, status, processing_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'active', 'pending')
            ON CONFLICT (tenant_id, external_id) DO NOTHING
            RETURNING id
            """,
            (
                tenant_id,
                config_id,
                identity.external_id,
                identity.original_url,
                identity.fetch_url,
                source_channel,
            ),
        )
        row = cursor.fetchone()
        if row:
            row_id = row["id"]
        else:
            cursor.execute(
                "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
                (tenant_id, identity.external_id),
            )
            row_id = cursor.fetchone()["id"]
        accepted.append(
            {
                "external_id": identity.external_id,
                "original_url": identity.original_url,
                "fetch_url": identity.fetch_url,
                "article_row_id": row_id,
            }
        )
    return accepted


def import_urls(tenant_id: str, user_id: Optional[str], urls: List[str]) -> Dict[str, Any]:
    """手动粘贴 URL 导入（WP6，计划 WP6 节 + 设计 §13 硬规则）。

    - 非法 URL 不拒整体，进 rejected 列表；合法 URL 批内去重 + 与既有 pending item 去重
    - 超过 MAX_IMPORT_URLS 条合法 URL → 业务错误；同租户 queued run ≥ MAX_QUEUED_RUNS → 业务错误
    - 同事务：advisory lock（限流原子化，WP6 CR P2）→ upsert articles（config_id=NULL,
      source_channel='manual'）→ queued run（trigger_type='manual'）→ pending items
      （action=NULL，worker 视为 'new'）
    - **不因刚同步过/hash 相同跳过受理**（刷新是明确请求，worker 抓取后按 hash 判定）；
      仅当该 URL 已有待处理 item（尚未被 worker 领取）时去重，避免重复排队
    - 受理成功提交后 best-effort 唤醒 worker（WP7 notify；失败由 60s 兜底扫描接管）
    """
    identities, rejected, duplicates = normalize_batch_urls(urls)
    if len(identities) > MAX_IMPORT_URLS:
        raise WeChatMPBusinessError(
            f"单次最多导入 {MAX_IMPORT_URLS} 条合法 URL（去重后 {len(identities)} 条），请分批提交"
        )
    if not identities:
        return {
            "run_id": None,
            "accepted": 0,
            "rejected": rejected,
            "duplicates": duplicates,
            "message": "没有可导入的合法 URL",
        }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # 限流原子化：advisory lock 使「queued 计数检查 + 插入」串行（WP6 CR P2）
            _advisory_enqueue_lock(cursor, tenant_id)
            cursor.execute(
                """
                SELECT count(*) AS c FROM bs_wechat_mp_sync_runs
                WHERE tenant_id = %s AND status = 'queued'
                """,
                (tenant_id,),
            )
            queued_count = int(cursor.fetchone()["c"])
            if queued_count >= MAX_QUEUED_RUNS:
                raise WeChatMPBusinessError(
                    f"待处理任务已达 {MAX_QUEUED_RUNS} 个，请等待队列消化后再提交导入"
                )

            # 与既有 pending item 去重：URL 已在活跃队列（queued/running run）中则不重复建
            # item（该 item 处理时仍会重抓页面，刷新语义不丢失）。只认活跃 run：worker
            # 失联回收后的 interrupted run 会残留永不处理的 pending item，若据此去重，
            # 明确的刷新请求将被永久静默跳过（违背设计 §14 硬规则）
            external_ids = [i.external_id for i in identities]
            cursor.execute(
                """
                SELECT DISTINCT a.external_id AS external_id
                FROM bs_wechat_mp_sync_items it
                JOIN bs_wechat_mp_sync_runs r
                  ON r.id = it.run_id AND r.tenant_id = it.tenant_id
                JOIN bs_wechat_mp_articles a
                  ON a.id = it.article_row_id AND a.tenant_id = it.tenant_id
                WHERE it.tenant_id = %s AND it.status = 'pending'
                  AND r.status IN ('queued', 'running')
                  AND a.external_id = ANY(%s)
                """,
                (tenant_id, external_ids),
            )
            pending_ids = {r["external_id"] for r in cursor.fetchall()}
            fresh = [i for i in identities if i.external_id not in pending_ids]
            for identity in identities:
                if identity.external_id in pending_ids:
                    duplicates.append(
                        {
                            "url": identity.original_url,
                            "external_id": identity.external_id,
                            "reason": "该 URL 已在待处理队列中，不重复排队",
                        }
                    )
            if not fresh:
                conn.rollback()
                return {
                    "run_id": None,
                    "accepted": 0,
                    "rejected": rejected,
                    "duplicates": duplicates,
                    "message": "提交的 URL 均已在待处理队列中，未重复排队",
                }

            rows = upsert_article_rows(
                cursor, tenant_id, fresh, config_id=None, source_channel="manual"
            )
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, user_id, trigger_type, status, total_count)
                VALUES (%s, NULL, %s, 'manual', 'queued', %s)
                RETURNING id
                """,
                (tenant_id, user_id, len(rows)),
            )
            run_id = cursor.fetchone()["id"]
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_items
                        (tenant_id, user_id, run_id, article_row_id, action, status)
                    VALUES (%s, %s, %s, %s, NULL, 'pending')
                    """,
                    (tenant_id, user_id, run_id, row["article_row_id"]),
                )
            conn.commit()
        except WeChatMPBusinessError:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    logger.bind(module="wechat_mp").info(
        "wechat_mp 手动导入受理 tenant_id={} run_id={} accepted={} rejected={} duplicates={}",
        tenant_id, run_id, len(rows), len(rejected), len(duplicates),
    )
    try:
        notify_queued_work()  # best-effort 唤醒 worker；失败由 60s 兜底扫描接管
    except Exception as e:  # noqa: BLE001 唤醒失败不影响受理结果
        logger.bind(module="wechat_mp").debug("wechat_mp 队列唤醒通知异常（忽略）: {}", e)
    return {
        "run_id": run_id,
        "accepted": len(rows),
        "rejected": rejected,
        "duplicates": duplicates,
        "message": (
            f"已受理 {len(rows)} 条 URL 并加入队列（同租户按顺序串行处理），"
            f"可用 run_id={run_id} 查询进度"
        ),
    }


# run 列表返回列（租户端与 portal 端共用；error_message 写库前已脱敏，回传不新增来源）
_RUN_COLUMNS = (
    "id, tenant_id, config_id, user_id, trigger_type, status, total_count, new_count, "
    "updated_count, deleted_count, skipped_count, failed_count, credits_charged, "
    "error_message, created_at, started_at, completed_at"
)

_ARTICLE_COLUMNS = (
    "id, tenant_id, external_id, original_url, title, source_channel, status, "
    "processing_status, doc_id, error_message, next_retry_at, "
    "last_synced_at, last_checked_at, created_at"
)


def _query_runs(
    *, tenant_id: Optional[str], limit: int, offset: int
) -> Dict[str, Any]:
    """runs 列表查询（tenant_id=None 时跨租户，仅 portal 端点使用）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS} FROM bs_wechat_mp_sync_runs
            WHERE %s::text IS NULL OR tenant_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, tenant_id, limit, offset),
        )
        runs = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            """
            SELECT count(*) AS c FROM bs_wechat_mp_sync_runs
            WHERE %s::text IS NULL OR tenant_id = %s
            """,
            (tenant_id, tenant_id),
        )
        total = int(cursor.fetchone()["c"])
    return {"runs": runs, "total": total}


def list_runs(tenant_id: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """本租户运行记录列表（created_at DESC，租户隔离由 tenant_id 过滤保证）。"""
    return _query_runs(tenant_id=tenant_id, limit=limit, offset=offset)


def portal_list_runs(
    tenant_id: Optional[str] = None, limit: int = 20, offset: int = 0
) -> Dict[str, Any]:
    """portal 跨租户运行记录列表（仅 platform_admin 端点调用，tenant_id 可选过滤）。"""
    return _query_runs(tenant_id=tenant_id, limit=limit, offset=offset)


def get_run(tenant_id: str, run_id: int) -> Optional[Dict[str, Any]]:
    """run 详情 + items 列表；非本租户或不存在返回 None（API 层 404，防跨租户探测）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS} FROM bs_wechat_mp_sync_runs
            WHERE id = %s AND tenant_id = %s
            """,
            (run_id, tenant_id),
        )
        run = cursor.fetchone()
        if not run:
            return None
        cursor.execute(
            """
            SELECT id, article_row_id, action, status, error_code, duplicate_of_item_id,
                   error_message, billing_status, billing_reference, credits_charged,
                   created_at, started_at, completed_at
            FROM bs_wechat_mp_sync_items
            WHERE tenant_id = %s AND run_id = %s
            ORDER BY id ASC
            """,
            (tenant_id, run_id),
        )
        items = [dict(i) for i in cursor.fetchall()]
    return {"run": dict(run), "items": items}


def _query_articles(
    *,
    tenant_id: Optional[str],
    status: Optional[str],
    processing_status: Optional[str],
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """文章当前态列表查询（tenant_id=None 时跨租户，仅 portal 端点使用）。"""
    clauses = ["%s::text IS NULL OR tenant_id = %s"]
    params: List[Any] = [tenant_id, tenant_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if processing_status:
        clauses.append("processing_status = %s")
        params.append(processing_status)
    where = " AND ".join(f"({c})" for c in clauses)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_ARTICLE_COLUMNS} FROM bs_wechat_mp_articles
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        articles = [dict(a) for a in cursor.fetchall()]
        cursor.execute(
            f"SELECT count(*) AS c FROM bs_wechat_mp_articles WHERE {where}",
            tuple(params),
        )
        total = int(cursor.fetchone()["c"])
    return {"articles": articles, "total": total}


def list_articles(
    tenant_id: str,
    status: Optional[str] = None,
    processing_status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """本租户文章当前态列表（状态/处理状态可选过滤）。"""
    return _query_articles(
        tenant_id=tenant_id,
        status=status,
        processing_status=processing_status,
        limit=limit,
        offset=offset,
    )


def portal_list_articles(
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    processing_status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """portal 跨租户文章列表（仅 platform_admin 端点调用，tenant_id 可选过滤）。"""
    return _query_articles(
        tenant_id=tenant_id,
        status=status,
        processing_status=processing_status,
        limit=limit,
        offset=offset,
    )


def retry_article(
    tenant_id: str, user_id: Optional[str], article_row_id: int
) -> Optional[Dict[str, Any]]:
    """失败/可重试文章重入队：新 queued run（trigger_type='retry'）+ 单 item（action=NULL）。

    - 文章行同步回 processing_status='pending'、next_retry_at=NULL
    - alias 行 / status='deleted' 拒绝（业务错误）；非本租户返回 None（API 404）
    - 设计 §13 硬规则同样适用：重试也必须先抓页面再按 hash 判定，不做受理前跳过
    """
    return _enqueue_single_article(
        tenant_id, user_id, article_row_id, trigger_type="retry", action=None
    )


def recheck_article(
    tenant_id: str, user_id: Optional[str], article_row_id: int
) -> Optional[Dict[str, Any]]:
    """对 active 文章发起存活复核：新 queued run（trigger_type='recheck'）+ 单 item（action='check'）。

    check 类 item 不受 run 启动余额预检阻断（删除复核免费，service 已实现该语义）。
    """
    return _enqueue_single_article(
        tenant_id, user_id, article_row_id, trigger_type="recheck", action="check"
    )


def _enqueue_single_article(
    tenant_id: str,
    user_id: Optional[str],
    article_row_id: int,
    *,
    trigger_type: str,
    action: Optional[str],
) -> Optional[Dict[str, Any]]:
    """retry/recheck 共用：校验文章行 → 同事务建 queued run + 单 pending item。

    WP6 CR P2 修复：与 import-urls 同口径——事务开头取 advisory lock，且同租户
    queued run ≥ MAX_QUEUED_RUNS 时抛业务错误；受理成功提交后 best-effort 唤醒 worker。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # 限流原子化：advisory lock 使「queued 计数检查 + 插入」串行（WP6 CR P2）
            _advisory_enqueue_lock(cursor, tenant_id)
            cursor.execute(
                """
                SELECT count(*) AS c FROM bs_wechat_mp_sync_runs
                WHERE tenant_id = %s AND status = 'queued'
                """,
                (tenant_id,),
            )
            queued_count = int(cursor.fetchone()["c"])
            if queued_count >= MAX_QUEUED_RUNS:
                raise WeChatMPBusinessError(
                    f"待处理任务已达 {MAX_QUEUED_RUNS} 个，请等待队列消化后再提交"
                )

            cursor.execute(
                "SELECT id, status FROM bs_wechat_mp_articles WHERE id = %s AND tenant_id = %s",
                (article_row_id, tenant_id),
            )
            article = cursor.fetchone()
            if article is None:
                return None
            if article["status"] == "alias":
                raise WeChatMPBusinessError("别名行已收敛到主记录，请对主记录操作")
            if article["status"] == "deleted":
                raise WeChatMPBusinessError("文章已删除（原文在公众号侧不存在），无法重试")
            if trigger_type == "recheck" and article["status"] != "active":
                raise WeChatMPBusinessError("仅 active 状态的文章支持存活复核")

            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, user_id, trigger_type, status, total_count)
                VALUES (%s, NULL, %s, %s, 'queued', 1)
                RETURNING id
                """,
                (tenant_id, user_id, trigger_type),
            )
            run_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, user_id, run_id, article_row_id, action, status)
                VALUES (%s, %s, %s, %s, %s, 'pending')
                """,
                (tenant_id, user_id, run_id, article_row_id, action),
            )
            if trigger_type == "retry":
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_articles
                    SET processing_status = 'pending', next_retry_at = NULL
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (article_row_id, tenant_id),
                )
            conn.commit()
        except WeChatMPBusinessError:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    verb = "重试" if trigger_type == "retry" else "复核"
    logger.bind(module="wechat_mp").info(
        "wechat_mp 文章{}入队 tenant_id={} article_row_id={} run_id={}",
        verb, tenant_id, article_row_id, run_id,
    )
    try:
        notify_queued_work()  # best-effort 唤醒 worker；失败由 60s 兜底扫描接管
    except Exception as e:  # noqa: BLE001 唤醒失败不影响受理结果
        logger.bind(module="wechat_mp").debug("wechat_mp 队列唤醒通知异常（忽略）: {}", e)
    message = (
        f"已重新入队（run_id={run_id}），同租户按顺序串行处理"
        if trigger_type == "retry"
        else f"复核任务已入队（run_id={run_id}），完成后可在运行记录中查看结果"
    )
    return {"run_id": run_id, "trigger_type": trigger_type, "message": message}


# ------------------------------- service -------------------------------


class WeChatMPSyncService:
    """公众号内容入知识库统一同步服务（全部抓取/入库/计费逻辑的唯一实现处）。"""

    def __init__(
        self,
        fetcher: Optional[MPArticleFetcher] = None,
        redis: Any = None,
        embedding_client: Any = None,
        chunker: Optional[TextChunker] = None,
        image_downloader: Optional[MPImageDownloader] = None,
        vision_parser: Optional[VisionParser] = None,
    ):
        self._fetcher = fetcher or MPArticleFetcher()
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._embedding_client = embedding_client  # 测试注入；None 时懒加载真实 client
        self._image_downloader = image_downloader  # WP10 图片下载转存（测试可注入）
        self._vision_parser = vision_parser  # WP10 VL 解析（测试可注入）
        if chunker is None:
            from src.config.settings import settings

            chunker = TextChunker(
                chunk_size=getattr(settings, "knowledge_chunk_size", 512),
                overlap=getattr(settings, "knowledge_chunk_overlap", 64),
            )
        self._chunker = chunker

    # ==================== 公共入口 ====================

    async def claim_and_run(self, tenant_id: str) -> Dict[str, Any]:
        """领取并串行执行该租户全部 queued run，直到队列空。

        返回 {"executed": bool, "reason": str|None, "run_ids": [..]}：
        - redis_unavailable：Redis 不可用，拒绝执行（安全关键锁不无锁降级）
        - locked：同租户已有持有者
        - db_running_conflict：撞 running 唯一约束，让位给其他 worker
        """
        if not self._redis.is_available():
            logger.warning(
                "后端日志：wechat_mp 同步拒绝执行：Redis 不可用 tenant_id={}", tenant_id
            )
            return {"executed": False, "reason": "redis_unavailable", "run_ids": []}

        owner = uuid.uuid4().hex
        lock_key = self._redis.make_key(LOCK_KEY_PREFIX, tenant_id)
        if not self._redis.acquire_lock(lock_key, self._lock_value(owner), ex=LOCK_TTL_SECONDS):
            return {"executed": False, "reason": "locked", "run_ids": []}

        run_ids: List[int] = []
        try:
            while True:
                claim = self._claim_next_run(tenant_id, owner)
                if claim is None:
                    break
                if claim == "conflict":
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp 领取撞 running 唯一约束，让位 tenant_id={}", tenant_id
                    )
                    return {
                        "executed": bool(run_ids),
                        "reason": "db_running_conflict",
                        "run_ids": run_ids,
                    }
                run_ids.append(claim["id"])
                lock_kept = await self._execute_run(tenant_id, claim, lock_key, owner)
                if not lock_kept:
                    break
        finally:
            self._redis.release_lock(lock_key, self._lock_value(owner))
        return {"executed": True, "reason": None, "run_ids": run_ids}

    def recover_stale_runs(self) -> Dict[str, int]:
        """回收 stale running run：heartbeat 超时且 Redis 锁已失效 → interrupted。

        queued run 保持等待（队列事实来源在 DB）。Redis 不可用时无法确认锁状态，
        跳过回收（宁晚勿错）。
        """
        if not self._redis.is_available():
            logger.warning("后端日志：wechat_mp stale 回收跳过：Redis 不可用")
            return {"interrupted": 0}
        interrupted = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, owner_token FROM bs_wechat_mp_sync_runs
                WHERE status = 'running'
                  AND COALESCE(heartbeat_at, started_at, created_at)
                      < now() - make_interval(secs => %s)
                """,
                (STALE_HEARTBEAT_SECONDS,),
            )
            stale = cursor.fetchall()
        for run in stale:
            lock_key = self._redis.make_key(LOCK_KEY_PREFIX, run["tenant_id"])
            # 锁值按 JSON 存储（_lock_value），redis_client.get 反序列化后为 owner 原文；
            # 锁仍被原 owner 持有说明 worker 活着，不回收
            current = self._redis.get(lock_key)
            if current is not None and run["owner_token"] and current == run["owner_token"]:
                continue
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_runs
                    SET status = 'interrupted',
                        error_message = 'worker 失联（heartbeat 超时且锁已失效）',
                        completed_at = now()
                    WHERE id = %s AND status = 'running'
                    """,
                    (run["id"],),
                )
                if cursor.rowcount == 0:
                    continue
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items SET status = 'interrupted', completed_at = now()
                    WHERE run_id = %s AND status = 'running'
                    """,
                    (run["id"],),
                )
                conn.commit()
            interrupted += 1
            logger.bind(module="wechat_mp").warning(
                "wechat_mp stale run 回收 run_id={} tenant_id={}", run["id"], run["tenant_id"]
            )
        return {"interrupted": interrupted}

    # ==================== 锁与领取 ====================

    @staticmethod
    def _lock_value(owner: str) -> str:
        # redis_client.get 按 JSON 反序列化；锁值存 JSON 字符串以便 recover 读回比对
        return json.dumps(owner)

    def _claim_next_run(self, tenant_id: str, owner: str):
        """事务内 queued→running 领取下一条；撞 running 唯一索引返回 'conflict'。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_runs
                    SET status = 'running', owner_token = %s, heartbeat_at = now(),
                        started_at = COALESCE(started_at, now())
                    WHERE id = (
                        SELECT id FROM bs_wechat_mp_sync_runs
                        WHERE tenant_id = %s AND status = 'queued'
                        ORDER BY created_at ASC, id ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, config_id, user_id, agent_id, session_id, trigger_type
                    """,
                    (owner, tenant_id),
                )
                row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else None
            except psycopg2.errors.UniqueViolation:
                # uq_wechat_mp_runs_active：他 worker 已有 running，让位
                conn.rollback()
                return "conflict"

    def _heartbeat(self, run_id: int, owner: str) -> bool:
        """推进 heartbeat（owner 守卫，防旧 worker 续写）。返回是否仍持有。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_runs SET heartbeat_at = now()
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (run_id, owner),
            )
            conn.commit()
            return cursor.rowcount == 1

    # ==================== run 执行 ====================

    async def _execute_run(
        self, tenant_id: str, run: Dict[str, Any], lock_key: str, owner: str
    ) -> bool:
        """执行一个 run 的全部 pending item。返回 False 表示锁已失（run 留待回收）。"""
        run_id = run["id"]
        logger.bind(module="wechat_mp").info(
            "wechat_mp run 开始 tenant_id={} run_id={} trigger={}",
            tenant_id, run_id, run.get("trigger_type"),
        )

        # ---- 启动余额预检：不足则付费类 item 记 skipped_no_credit；复核类不阻断 ----
        balance_ok, balance_reason = self._check_credit(tenant_id)

        items = self._load_pending_items(tenant_id, run_id)
        for item in items:
            # 逐 item 续租；失锁即停止后续调用与写入（设计 §5.1）
            if not self._redis.renew_lock(lock_key, self._lock_value(owner), LOCK_TTL_SECONDS):
                logger.bind(module="wechat_mp").error(
                    "wechat_mp 锁续期失败，停止执行 tenant_id={} run_id={}", tenant_id, run_id
                )
                return False
            if not self._heartbeat(run_id, owner):
                logger.bind(module="wechat_mp").error(
                    "wechat_mp heartbeat 失去 owner，停止执行 run_id={}", run_id
                )
                return False

            if not balance_ok and (item.get("action") or "new") != "check":
                self._mark_item_no_credit(tenant_id, item)
                self._mark_article_no_credit_retry(tenant_id, item["article_row_id"])
                continue
            # 逐 item 复核状态：同批次别名收敛可能已把本 item 标 skipped
            if self._get_item_status(tenant_id, item["id"]) != "pending":
                continue
            try:
                await self._process_item(tenant_id, run, item)
            except Exception as e:  # noqa: BLE001 单 item 失败不阻断 run
                logger.opt(exception=True).error(
                    "后端日志：wechat_mp item 处理异常 tenant_id={} run_id={} item_id={}: {}",
                    tenant_id, run_id, item["id"], sanitize_error_info(str(e)),
                )
                self._mark_item_failed(tenant_id, item, ERR_INTERNAL, str(e))

        self._finalize_run(tenant_id, run_id, owner)
        return True

    def _load_pending_items(self, tenant_id: str, run_id: int) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, article_row_id, action, user_id FROM bs_wechat_mp_sync_items
                WHERE tenant_id = %s AND run_id = %s AND status = 'pending'
                ORDER BY id ASC
                """,
                (tenant_id, run_id),
            )
            return [dict(r) for r in cursor.fetchall()]

    # ==================== 单 item 管道 ====================

    async def _process_item(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any]
    ) -> None:
        item_id = item["id"]
        article = self._load_article(tenant_id, item["article_row_id"])
        if article is None:
            self._mark_item_failed(tenant_id, item, ERR_ARTICLE_MISSING, "文章行不存在")
            return
        if article["status"] == "alias":
            # 别名行不参与复核与处理（设计 §5.4）
            self._mark_item_skipped(
                tenant_id, item_id, ERR_ALIAS_MERGED, "别名行已收敛到主记录，不重复处理"
            )
            return

        self._mark_item_running(tenant_id, item_id)

        # ---- 1. 先抓页面（受理只有 URL，不存在抓取前按 hash 跳过）----
        fetch: FetchResult = await asyncio.to_thread(
            self._fetcher.fetch, article["fetch_url"] or article["original_url"]
        )

        # ---- 2. 删除多信号判定（免费，不受余额阻断）----
        if fetch.status == STATUS_DELETED:
            self._handle_deleted(tenant_id, item, article)
            return
        if fetch.status != STATUS_OK:
            code = ERR_RISK_BLOCKED if fetch.status == "risk_blocked" else ERR_FETCH_FAILED
            self._mark_item_failed(
                tenant_id, item, code, f"抓取未成功: {fetch.error or fetch.status}"
            )
            self._mark_article_retry(
                tenant_id, article["id"], f"fetch:{fetch.error or fetch.status}"
            )
            return

        # ---- 3. 正文提取 ----
        try:
            extracted = extract_article(fetch.html or "")
        except ContentExtractionError as e:
            msg = str(e)
            if "内容为空" in msg:
                # 空内容跳过并记原因（非技术失败，不进退避）
                self._mark_item_skipped(tenant_id, item_id, ERR_CONTENT_EMPTY, msg)
                self._touch_article_checked(tenant_id, article["id"], processing_status=None)
            else:
                self._mark_item_failed(tenant_id, item, ERR_EXTRACT_FAILED, msg)
                self._mark_article_retry(tenant_id, article["id"], f"extract:{type(e).__name__}")
            return

        # ---- 4. 别名收敛（保守：仅页面给出对方形态且本租户已有该行时）----
        if self._converge_alias(tenant_id, run["id"], item, article, extracted.alias):
            return  # 当前 item 已被收敛为 skipped

        # ---- 5. 内容指纹与已成功版本比对 ----
        title = extracted.title or article["title"] or "未命名文章"
        body_text = nodes_to_text(extracted.nodes)
        content_hash = self._content_hash(title, extracted.nodes)

        # check 类 item 豁免 processing_status=='success' 条件（WP7 CR 遗留 A）：
        # content_hash 只在成功入库事务内与 documents 内容原子写入，hash 相同+
        # pipeline 相同即证明页面内容就是已入库版本，当前 processing_status 只反映
        # 其后的瞬时状态（retry 受理置 pending / 其后 sync_failed、deferred），
        # 不改变「无需重建」的事实——不豁免会把复核打到付费重建产生冗余计费。
        # doc 行缺失（doc_state=None）与 pipeline 变更仍自然落到重建分支。
        is_check_item = (item.get("action") or "new") == "check"
        if (
            article["content_hash"] == content_hash
            and article["pipeline_version"] == PIPELINE_VERSION
            and (is_check_item or article["processing_status"] == "success")
        ):
            doc_state = self._get_doc_state(tenant_id, article["doc_id"])
            if doc_state == "active":
                # hash 未变且文档在：记 check，零 embedding 零计费
                self._handle_check_unchanged(tenant_id, item, article)
                return
            if doc_state == "deleted":
                # 重现且 hash 未变：复用旧 chunks 恢复 active（设计 §5.2）
                self._handle_restore(tenant_id, item, article, extracted, title)
                return
            # doc 行不存在 → 继续重建

        # ---- 6. 纯图/短文本门禁（P2：图片 VL 解析提前，设计 §6.1/§6.2）----
        # 占位符必须整段剔除：只删 "[图片" 前缀会残留 "N]"，图片多时残留字符
        # 累积越过阈值，纯图文章漏判 deferred 并对空内容计费（CR P1 修复）
        plain_text = _IMAGE_PLACEHOLDER_RE.sub("", body_text).strip()
        image_billing_successes: List[Any] = []
        image_meta: Dict[str, Any] = {}
        if extracted.image_count > 0 and len(plain_text) < MIN_TEXT_CHARS:
            # P2 范围决策（计划 WP10 节）：仅「文字不足且有图」触发 VL 解析（负责人
            # 痛点：纯图/图文文章被 deferred 无法入库）；文字充足的文章维持 [图片N]
            # 占位不解析，图片描述全量插回留待 P3 分类阶段再评估
            parse_result = await self._parse_images_or_defer(
                tenant_id, item, article, extracted
            )
            if parse_result is None:
                return  # 已置 deferred（无可用模型/全部失败）或 no_credit
            body_text, image_meta, image_billing_successes = parse_result

        # ---- 7. 付费单元前余额复查 ----
        balance_ok, _ = self._check_credit(tenant_id)
        if not balance_ok:
            self._mark_item_no_credit(tenant_id, item)
            self._mark_article_no_credit_retry(tenant_id, article["id"])
            return

        # ---- 8. 拼正文 → 分块 → embedding（事务外）----
        doc_title = f"[公众号] {title}"[:255]
        text_with_title = f"文档标题：{doc_title}\n\n{body_text}"
        chunks = self._chunker.chunk(text_with_title)
        if not chunks:
            self._mark_item_skipped(tenant_id, item_id, ERR_CONTENT_EMPTY, "分块结果为空")
            return
        embedding_client = self._get_embedding_client()
        embedding_client.reset_usage()
        embeddings = await embedding_client.embed_batch([c["text"] for c in chunks])
        embedding_tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)

        # ---- 9. 单事务落库（documents/chunks/chunks_vec/articles/item）----
        action = "new" if not article["doc_id"] else "update"
        doc_id = await self._persist_document_tx(
            tenant_id=tenant_id,
            run=run,
            item=item,
            article=article,
            extracted=extracted,
            doc_title=doc_title,
            body_text=body_text,
            content_hash=content_hash,
            chunks=chunks,
            embeddings=embeddings,
            action=action,
            image_meta=image_meta or None,
        )

        # ---- 10. 售前挂接 + 计费（业务已提交，fail-open）----
        self._attach_presales_and_record(tenant_id, doc_id)
        self._bill_embedding(
            tenant_id=tenant_id,
            user_id=item.get("user_id") or run.get("user_id"),
            doc_id=doc_id,
            title=doc_title,
            embedding_tokens=embedding_tokens,
            item_id=item_id,
        )
        if image_billing_successes:
            # VL 按张计费（独立提交 + 合并回写 item 计费字段，fail-open）
            self._bill_image_parses(
                tenant_id=tenant_id,
                user_id=item.get("user_id") or run.get("user_id"),
                article_row_id=article["id"],
                item_id=item_id,
                successes=image_billing_successes,
            )

    # ==================== 图片 VL 解析（WP10，P2） ====================

    async def _parse_images_or_defer(
        self,
        tenant_id: str,
        item: Dict[str, Any],
        article: Dict[str, Any],
        extracted: ExtractedArticle,
    ) -> Optional[Tuple[str, Dict[str, Any], List[Any]]]:
        """图片下载 + VL 解析 + 正文插回。返回 (增强正文, image metadata, 计费清单)。

        返回 None 表示 item 已置终态（deferred / no_credit），调用方直接返回：
        - 无可用多模态模型 → deferred（不发纯文本模型，设计 §6.2）
        - 余额不足 → 复用 no_credit 语义（付费单元=按张 VL）
        - 全部图片下载/解析失败 → deferred（退避重试机制自然接管）
        """
        vision = self._get_vision_parser()
        if not vision.available():
            self._mark_item_deferred(tenant_id, item["id"], _DEFERRED_NO_MODEL)
            self._mark_article_deferred(
                tenant_id, article["id"], extracted, _DEFERRED_NO_MODEL
            )
            return None

        # 文章级余额预检：不足整篇跳过解析（免费动作不预扣，只拦截付费单元）
        ok, _ = self._check_credit(tenant_id)
        if not ok:
            self._mark_item_no_credit(tenant_id, item)
            self._mark_article_no_credit_retry(tenant_id, article["id"])
            return None

        # 下载转存（同步 httpx + Pillow，线程内执行；逐张独立不中断）
        # 编号必须与 _merge_image_descriptions/nodes_to_text 的图片序数一致
        # （第 N 个 image 节点即图片 N）——用全节点索引会在混排（短文字+图）文章
        # 中错位：描述张冠李戴、跨图丢描述（WP10 测试期修复）
        srcs: List[Tuple[int, str]] = []
        img_seq = 0
        for node in extracted.nodes:
            if node.type == "image" and node.src:
                img_seq += 1
                srcs.append((img_seq, node.src))
        outcome = VisionParseOutcome()
        dl = await asyncio.to_thread(
            self._get_image_downloader().download, tenant_id, article["id"], srcs
        )
        if dl.images:
            outcome = await vision.describe_images(
                [(img.n, img.local_path) for img in dl.images], tenant_id
            )

        if not outcome.descriptions:
            message = _DEFERRED_NO_MODEL if outcome.no_model else _DEFERRED_PARSE_FAILED
            self._mark_item_deferred(tenant_id, item["id"], message)
            self._mark_article_deferred(tenant_id, article["id"], extracted, message)
            return None

        # [图片N: 描述] 插回原文位置；失败图片保留 [图片N] 占位照常入库
        enhanced_nodes = self._merge_image_descriptions(
            extracted.nodes, outcome.descriptions
        )
        enhanced_text = nodes_to_text(enhanced_nodes)
        image_meta = {
            "image_local_paths": [img.local_path for img in dl.images],
            "image_parse_failed_count": len(outcome.failures),
            "image_skipped_count": dl.skipped_over_limit,
        }
        return enhanced_text, image_meta, list(outcome.successes)

    @staticmethod
    def _merge_image_descriptions(
        nodes: List[ContentNode], descriptions: Dict[int, str]
    ) -> List[ContentNode]:
        """把 VL 描述以 [图片N: 描述] 文本节点替换对应 image 节点（保序）。

        失败图片同样替换为 [图片N] 文本节点并保留**原始编号**——若保留 image 节点，
        nodes_to_text 会按剩余图片重新编号，导致占位序号与转存文件/描述错位。
        注意：不动 extracted.nodes 原列表——content_hash 在替换前已按原始节点
        计算完毕，VL 输出的不确定性不得影响内容指纹（否则 LLM 措辞变化会造成
        hash 漂移触发无谓重建）。
        """
        merged: List[ContentNode] = []
        img_idx = 0
        for node in nodes:
            if node.type == "image":
                img_idx += 1
                desc = descriptions.get(img_idx)
                if desc:
                    merged.append(
                        ContentNode(type="text", text=f"[图片{img_idx}: {desc}]")
                    )
                else:
                    merged.append(ContentNode(type="text", text=f"[图片{img_idx}]"))
            else:
                merged.append(node)
        return merged

    # ==================== 终态分支 ====================

    def _handle_deleted(
        self, tenant_id: str, item: Dict[str, Any], article: Dict[str, Any]
    ) -> None:
        """删除页多信号命中：软删除 documents + 文章状态 + item，单事务。"""
        doc_id = article["doc_id"]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if doc_id:
                cursor.execute(
                    """
                    UPDATE documents SET status = 'deleted', updated_at = now()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (doc_id, tenant_id),
                )
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET status = 'deleted', processing_status = 'success',
                    last_checked_at = now(), next_retry_at = NULL, error_message = NULL
                WHERE id = %s AND tenant_id = %s
                """,
                (article["id"], tenant_id),
            )
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'success', action = 'delete',
                    billing_status = 'not_required', credits_charged = 0,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (item["id"], tenant_id),
            )
            conn.commit()
        logger.bind(module="wechat_mp").info(
            "wechat_mp 文章软删除 tenant_id={} article_id={} doc_id={}",
            tenant_id, article["id"], doc_id,
        )

    def _handle_check_unchanged(
        self, tenant_id: str, item: Dict[str, Any], article: Dict[str, Any]
    ) -> None:
        """hash 未变且文档在：仅更新检查信息，零 embedding 零计费。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET last_checked_at = now(), processing_status = 'success',
                    next_retry_at = NULL, error_message = NULL
                WHERE id = %s AND tenant_id = %s
                """,
                (article["id"], tenant_id),
            )
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'success', action = 'check',
                    billing_status = 'not_required', credits_charged = 0,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (item["id"], tenant_id),
            )
            conn.commit()

    def _handle_restore(
        self,
        tenant_id: str,
        item: Dict[str, Any],
        article: Dict[str, Any],
        extracted: ExtractedArticle,
        title: str,
    ) -> None:
        """hash 未变但文档软删除：复用旧 chunks 恢复 active，零 embedding 零计费。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE documents SET status = 'active', updated_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (article["doc_id"], tenant_id),
            )
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET status = 'active', processing_status = 'success',
                    last_synced_at = now(), last_checked_at = now(),
                    next_retry_at = NULL, error_message = NULL
                WHERE id = %s AND tenant_id = %s
                """,
                (article["id"], tenant_id),
            )
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'success', action = 'restore',
                    billing_status = 'not_required', credits_charged = 0,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (item["id"], tenant_id),
            )
            conn.commit()
        logger.bind(module="wechat_mp").info(
            "wechat_mp 文章恢复 active tenant_id={} article_id={} doc_id={}",
            tenant_id, article["id"], article["doc_id"],
        )

    # ==================== 别名收敛 ====================

    def _converge_alias(
        self,
        tenant_id: str,
        run_id: int,
        item: Dict[str, Any],
        article: Dict[str, Any],
        alias: Optional[URLIdentity],
    ) -> bool:
        """短↔长链同文收敛。返回 True 表示当前 item 已置 skipped（调用方直接返回）。

        保守规则（设计 §5.4 三审修订）：
        - 页面未给出对方形态（msg_link 回显自身是常态）→ 不合并
        - 对方形态在本租户无文章行 → 不合并（各自独立）
        - 主记录 = 先成功入库（doc_id 非空且 success），否则先创建（id 小）
        - 同批次冲突不做 item 迁移：别名侧的同 run item 标 skipped + duplicate_of_item_id
        - 别名行已有 doc_id：documents 置 deleted + metadata.merged_into_doc_id
        """
        if alias is None or alias.external_id == article["external_id"]:
            return False

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, external_id, doc_id, processing_status, status,
                       master_article_row_id, created_at
                FROM bs_wechat_mp_articles
                WHERE tenant_id = %s AND external_id = %s
                """,
                (tenant_id, alias.external_id),
            )
            other = cursor.fetchone()
            if other is None:
                return False  # 证据指向的形态未受理过，保持独立

            # 对方已是别名 → 其主记录才是主记录
            if other["status"] == "alias" and other["master_article_row_id"]:
                master_row_id = other["master_article_row_id"]
                if master_row_id == article["id"]:
                    return False  # 当前即主记录，无需收敛
                alias_row, master_id = dict(article), master_row_id
                current_is_alias = True
            else:
                # 主记录判定：先成功入库优先，否则先创建
                cur_succeeded = bool(article["doc_id"]) and article["processing_status"] == "success"
                other_succeeded = bool(other["doc_id"]) and other["processing_status"] == "success"
                if other_succeeded and not cur_succeeded:
                    current_is_alias = True
                elif cur_succeeded and not other_succeeded:
                    current_is_alias = False
                else:
                    current_is_alias = article["id"] > other["id"]
                if current_is_alias:
                    alias_row, master_id = dict(article), other["id"]
                else:
                    alias_row, master_id = dict(other), article["id"]

            # 同批次主 item（供 duplicate_of_item_id 关联；跨批次为 NULL）
            cursor.execute(
                """
                SELECT id FROM bs_wechat_mp_sync_items
                WHERE tenant_id = %s AND run_id = %s AND article_row_id = %s
                """,
                (tenant_id, run_id, master_id),
            )
            master_item = cursor.fetchone()
            master_item_id = master_item["id"] if master_item else None

            # 别名行收敛
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET status = 'alias', master_article_row_id = %s,
                    next_retry_at = NULL, error_message = NULL
                WHERE id = %s AND tenant_id = %s
                """,
                (master_id, alias_row["id"], tenant_id),
            )
            # 别名行已有 doc：documents 置 deleted + merged_into_doc_id（检索自动隐藏）
            if alias_row.get("doc_id"):
                cursor.execute(
                    "SELECT metadata FROM documents WHERE id = %s AND tenant_id = %s",
                    (alias_row["doc_id"], tenant_id),
                )
                doc_row = cursor.fetchone()
                if doc_row:
                    try:
                        metadata = json.loads(doc_row["metadata"]) if doc_row["metadata"] else {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                    metadata["merged_into_doc_id"] = self._get_doc_id_of_article(
                        cursor, tenant_id, master_id
                    )
                    cursor.execute(
                        """
                        UPDATE documents
                        SET status = 'deleted', metadata = %s, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (json.dumps(metadata, ensure_ascii=False), alias_row["doc_id"], tenant_id),
                    )

            if current_is_alias:
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items
                    SET status = 'skipped', error_code = %s, duplicate_of_item_id = %s,
                        error_message = '识别为同文别名（另一形态链接），收敛到主记录',
                        billing_status = 'not_required', credits_charged = 0,
                        completed_at = now()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (ERR_ALIAS_DUPLICATE, master_item_id, item["id"], tenant_id),
                )
            else:
                # 当前继续处理；同批次别名侧 item 标 skipped
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items
                    SET status = 'skipped', error_code = %s, duplicate_of_item_id = %s,
                        error_message = '识别为同文别名（另一形态链接），收敛到主记录',
                        billing_status = 'not_required', credits_charged = 0,
                        completed_at = now()
                    WHERE tenant_id = %s AND run_id = %s AND article_row_id = %s
                      AND status = 'pending'
                    """,
                    (ERR_ALIAS_DUPLICATE, item["id"], tenant_id, run_id, alias_row["id"]),
                )
            conn.commit()

        logger.bind(module="wechat_mp").info(
            "wechat_mp 别名收敛 tenant_id={} alias_row={} master_row={}",
            tenant_id, alias_row["id"], master_id,
        )
        return current_is_alias

    @staticmethod
    def _get_doc_id_of_article(cursor, tenant_id: str, article_row_id: int) -> Optional[int]:
        cursor.execute(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s AND tenant_id = %s",
            (article_row_id, tenant_id),
        )
        row = cursor.fetchone()
        return row["doc_id"] if row else None

    # ==================== 入库事务 ====================

    async def _persist_document_tx(
        self,
        *,
        tenant_id: str,
        run: Dict[str, Any],
        item: Dict[str, Any],
        article: Dict[str, Any],
        extracted: ExtractedArticle,
        doc_title: str,
        body_text: str,
        content_hash: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        action: str,
        image_meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """单事务落 documents/chunks/chunks_vec + articles + item（+惰性分类）。

        更新场景走 doc_id 快路径：先删 chunks_vec 再删 chunks 后重建。
        新文档撞 uq_documents_origin_external（并发/历史遗留）时转更新路径。
        """
        metadata = {
            "original_url": article["original_url"],
            "fetch_url": article["fetch_url"],
            "source_channel": article["source_channel"],
            "account_name": extracted.account_name,
            "publish_time": extracted.publish_time.isoformat() if extracted.publish_time else None,
            "article_row_id": article["id"],
            "pipeline_version": PIPELINE_VERSION,
            "sync_run_id": run["id"],
            "image_count": extracted.image_count,
        }
        if image_meta:
            # WP10：图片转存路径 / VL 解析失败数 / 超上限跳过数（供前端详情与对账）
            metadata.update(image_meta)
        summary = body_text[:SUMMARY_MAX_CHARS] if body_text else None
        raw_text = body_text
        # 时间统一 UTC：列类型 TIMESTAMP（无时区），写前去 tz 防会话时区偏移
        publish_time_naive = (
            extracted.publish_time.astimezone(timezone.utc).replace(tzinfo=None)
            if extracted.publish_time else None
        )

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                self._ensure_categories_tx(cursor, tenant_id)

                doc_id = article["doc_id"]
                doc_exists = False
                if doc_id:
                    cursor.execute(
                        "SELECT 1 FROM documents WHERE id = %s AND tenant_id = %s FOR UPDATE",
                        (doc_id, tenant_id),
                    )
                    doc_exists = cursor.fetchone() is not None

                if not doc_exists:
                    doc_id = None
                    cursor.execute(
                        """
                        INSERT INTO documents (
                            user_id, tenant_id, title, source_type, sub_category,
                            file_type, file_path, file_size, total_chunks, embedding_model,
                            raw_text, metadata, summary, uuid,
                            origin, external_id, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                        ON CONFLICT (tenant_id, origin, external_id) WHERE external_id IS NOT NULL
                        DO NOTHING
                        RETURNING id
                        """,
                        (
                            item.get("user_id") or run.get("user_id"),
                            tenant_id,
                            doc_title,
                            CATEGORY_SOURCE_TYPE,
                            SUB_CATEGORY_SOURCE_TYPE,
                            "html",
                            None,
                            len(raw_text.encode("utf-8")),
                            len(chunks),
                            EMBEDDING_MODEL,
                            raw_text,
                            json.dumps(metadata, ensure_ascii=False),
                            summary,
                            f"doc_{uuid.uuid4().hex[:12]}",
                            DOC_ORIGIN,
                            article["external_id"],
                        ),
                    )
                    row = cursor.fetchone()
                    if row:
                        doc_id = row["id"]
                    else:
                        # 并发撞唯一索引：转更新既有文档
                        cursor.execute(
                            """
                            SELECT id FROM documents
                            WHERE tenant_id = %s AND origin = %s AND external_id = %s
                            """,
                            (tenant_id, DOC_ORIGIN, article["external_id"]),
                        )
                        found = cursor.fetchone()
                        if not found:
                            raise RuntimeError("documents 唯一索引冲突但回查无行")
                        doc_id = found["id"]
                        doc_exists = True

                if doc_exists:
                    cursor.execute(
                        """
                        UPDATE documents
                        SET title = %s, raw_text = %s, summary = %s, metadata = %s,
                            total_chunks = %s, embedding_model = %s,
                            source_type = %s, sub_category = %s,
                            status = 'active', expires_at = NULL, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (
                            doc_title, raw_text, summary,
                            json.dumps(metadata, ensure_ascii=False),
                            len(chunks), EMBEDDING_MODEL,
                            CATEGORY_SOURCE_TYPE, SUB_CATEGORY_SOURCE_TYPE,
                            doc_id, tenant_id,
                        ),
                    )
                    # 快路径：先删 chunks_vec 再删 chunks（同一连接，避免锁冲突）
                    vector_db = get_vector_db(dimension=1024, conn=conn)
                    await vector_db.delete_by_doc(doc_id)
                    cursor.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))

                chunk_ids: List[int] = []
                for chunk in chunks:
                    cursor.execute(
                        """
                        INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata, uuid)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            doc_id,
                            chunk["index"],
                            chunk["text"],
                            chunk["tokens"],
                            json.dumps(
                                {**chunk.get("metadata", {}), "char_count": len(chunk["text"])},
                                ensure_ascii=False,
                            ),
                            f"chunk_{uuid.uuid4().hex[:12]}",
                        ),
                    )
                    chunk_ids.append(cursor.fetchone()["id"])

                vector_db = get_vector_db(dimension=1024, conn=conn)
                await vector_db.insert(chunk_ids, embeddings)

                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_articles
                    SET title = %s, publish_time = COALESCE(%s, publish_time),
                        content_hash = %s, doc_id = %s,
                        status = 'active', processing_status = 'success',
                        pipeline_version = %s, image_count = %s,
                        last_synced_at = now(), last_checked_at = now(),
                        next_retry_at = NULL, error_message = NULL
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (
                        extracted.title or article["title"],
                        publish_time_naive,
                        content_hash, doc_id, PIPELINE_VERSION,
                        extracted.image_count, article["id"], tenant_id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items
                    SET status = 'success', action = %s, completed_at = now()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (action, item["id"], tenant_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        logger.bind(module="wechat_mp").info(
            "wechat_mp 文档入库 tenant_id={} run_id={} article_id={} doc_id={} action={} chunks={}",
            tenant_id, run["id"], article["id"], doc_id, action, len(chunks),
        )
        return doc_id

    def _ensure_categories_tx(self, cursor, tenant_id: str) -> None:
        """惰性创建「公众号内容 / 未分类」分类（文档入库事务内，失败不遗留空分类）。

        幂等：稳定 source_type + UNIQUE(tenant_id, source_type) ON CONFLICT 兜底。
        """
        cursor.execute(
            """
            INSERT INTO knowledge_categories (tenant_id, source_type, display_name, parent_id, uuid)
            VALUES (%s, %s, %s, NULL, %s)
            ON CONFLICT (tenant_id, source_type) DO NOTHING
            RETURNING id
            """,
            (tenant_id, CATEGORY_SOURCE_TYPE, CATEGORY_DISPLAY_NAME,
             f"kc_{uuid.uuid4().hex[:12]}"),
        )
        row = cursor.fetchone()
        if row:
            top_id = row["id"]
        else:
            cursor.execute(
                "SELECT id FROM knowledge_categories WHERE tenant_id = %s AND source_type = %s",
                (tenant_id, CATEGORY_SOURCE_TYPE),
            )
            top_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO knowledge_categories (tenant_id, source_type, display_name, parent_id, uuid)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_type) DO NOTHING
            """,
            (tenant_id, SUB_CATEGORY_SOURCE_TYPE, SUB_CATEGORY_DISPLAY_NAME, top_id,
             f"kc_{uuid.uuid4().hex[:12]}"),
        )

    # ==================== 售前挂接 ====================

    def _attach_presales_and_record(self, tenant_id: str, doc_id: int) -> None:
        """首篇有效文档入库后挂接本租户售前智能体（幂等、只增不删、失败不阻断）。

        挂接结果（含无售前实例的待挂接原因）合并写入 documents.metadata
        .presales_attach，供管理端/后续补挂接流程查询。
        """
        status = "failed"
        reason = ""
        try:
            from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
            from src.saas.db.subscription_db import SubscriptionDB

            with get_db_connection() as conn:
                allowed = SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)
            if PRE_SALES_SUBAGENT not in allowed:
                status = "no_instance"
                reason = "租户无售前智能体实例，待实例创建后可补挂接"
            else:
                sources = SubagentKnowledgeSourceDB.get(tenant_id, PRE_SALES_SUBAGENT) or []
                if any(
                    s.get("source_type") == CATEGORY_SOURCE_TYPE and not s.get("owner_tenant_id")
                    for s in sources
                ):
                    status = "skipped"
                elif SubagentKnowledgeSourceDB.append_own_source(
                    tenant_id, PRE_SALES_SUBAGENT, CATEGORY_SOURCE_TYPE, CATEGORY_DISPLAY_NAME
                ):
                    status = "attached"
                else:
                    reason = "append_own_source 返回失败"
        except Exception as e:  # noqa: BLE001 挂接失败不阻断入库结果
            logger.opt(exception=True).warning(
                "后端日志：wechat_mp 售前挂接异常 tenant_id={}: {}", tenant_id, e
            )
            reason = f"attach_exception:{type(e).__name__}"

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT metadata FROM documents WHERE id = %s AND tenant_id = %s",
                    (doc_id, tenant_id),
                )
                row = cursor.fetchone()
                if not row:
                    return
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
                metadata["presales_attach"] = {
                    "status": status,
                    "reason": reason,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                cursor.execute(
                    "UPDATE documents SET metadata = %s WHERE id = %s AND tenant_id = %s",
                    (json.dumps(metadata, ensure_ascii=False), doc_id, tenant_id),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "后端日志：wechat_mp 挂接结果记录失败 tenant_id={} doc_id={}: {}",
                tenant_id, doc_id, e,
            )

    # ==================== 计费 ====================

    def _bill_embedding(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        doc_id: int,
        title: str,
        embedding_tokens: int,
        item_id: int,
    ) -> None:
        """embedding 计费（业务提交后、独立提交，fail-open）。

        ChatRecordDB.create 内部异常返回 None 且无法区分失败阶段（可能已扣也可能
        未扣），保守记 unknown 并禁止自动重扣（设计 §7.4）；记录落库成功记 charged。
        """
        billing_status = "unknown"
        billing_reference: Optional[str] = None
        credits = 0.0
        if embedding_tokens <= 0:
            billing_status = "not_required"
        else:
            try:
                from src.knowledge.service import knowledge_service

                record = knowledge_service._record_knowledge_embedding_billing(
                    tenant_id=tenant_id,
                    user_id=str(user_id) if user_id is not None else None,
                    doc_id=doc_id,
                    file_filename=title,
                    embedding_tokens=embedding_tokens,
                    summary_usage=None,
                    source_type=EMBEDDING_SOURCE_TYPE,
                    user_message_prefix="公众号文章向量化",
                )
                if record and record.get("record_id"):
                    billing_status = "charged"
                    billing_reference = record["record_id"]
                    credits = float(record.get("credit_cost") or 0)
                else:
                    # 响应不可确认：记 unknown，P1 禁止自动补扣/重试扣款
                    billing_status = "unknown"
                    logger.bind(module="wechat_mp").error(
                        "wechat_mp 计费结果不可确认（记 unknown，不重扣）tenant_id={} doc_id={}",
                        tenant_id, doc_id,
                    )
            except Exception as e:  # noqa: BLE001 计费失败不回滚内容、不触发重嵌
                billing_status = "unknown"
                logger.opt(exception=True).error(
                    "后端日志：wechat_mp embedding 计费异常（记 unknown）tenant_id={} doc_id={}: {}",
                    tenant_id, doc_id, sanitize_error_info(str(e)),
                )
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items
                    SET billing_status = %s, billing_reference = %s, credits_charged = %s
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (billing_status, billing_reference, credits, item_id, tenant_id),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 计费状态回写失败 item_id={}: {}", item_id, e
            )

    # ==================== 图片 VL 按张计费（WP10，设计 §6.2/D2） ====================

    @staticmethod
    def _get_image_parse_price() -> Tuple[float, int]:
        """读取按张单价（token_cost_prices.price_per_call，model_code=wechat_mp_image_parse）
        与 usage_factor；缺配置返回 (0, factor)（不扣费不阻断，对齐 ASR 单价缺失口径）。"""
        from src.config.settings import create_settings

        factor = int(getattr(create_settings().billing, "usage_factor", 100) or 100)
        try:
            from src.db.models import TokenCostPriceDB

            tcp = TokenCostPriceDB.get_by_model_name(VISION_PARSE_SOURCE_TYPE)
            if not tcp:
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 图片按张单价未配置（token_cost_prices 无 "
                    "model_code=wechat_mp_image_parse 行），credit_cost=0"
                )
                return 0.0, factor
            return float(tcp.get("price_per_call") or 0), factor
        except Exception as e:  # noqa: BLE001 单价读取失败按 0 计，不阻断入库
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 图片按张单价读取异常（按 0 计）: {}", e
            )
            return 0.0, factor

    def _bill_image_parses(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        article_row_id: int,
        item_id: int,
        successes: List[Any],
    ) -> None:
        """VL 按张计费（业务提交后独立提交，fail-open，对齐 _bill_embedding 口径）。

        - 每成功 1 张写一条 chat_records(source_type='wechat_mp_image_parse')，
          credit_cost=ceil(price_per_call × usage_factor × 100)/100（与 ASR 按次公式
          同源）；VL 实际 token 用量记 usage_breakdown 供对账，收费按张不按 token
        - gateway.chat() 只做观测性 usage 记录不落账，本方法为唯一计费写入点（无双重扣费）
        - ChatRecordDB.create 内部异常返回 None 且无法区分失败阶段（可能已扣也可能
          未扣）→ 该张记 unknown，禁止自动重扣（设计 §7.4）
        - item.billing_status/billing_reference/credits_charged 与 embedding 计费合并
          回写（两笔独立提交，任一 unknown 即整条标 unknown 防自动补扣误判）
        """
        if not successes:
            return
        price_per_call, usage_factor = self._get_image_parse_price()
        per_image_credit = calculate_image_parse_credit_cost(price_per_call, usage_factor)

        image_credits = 0.0
        image_unknown = False
        record_ids: List[str] = []
        import time as _time

        for s in successes:
            try:
                from src.db.models import ChatRecordDB

                record = ChatRecordDB.create(
                    session_id=(
                        f"{VISION_PARSE_SOURCE_TYPE}_{article_row_id}_"
                        f"{int(_time.time())}"
                    ),
                    tenant_id=tenant_id,
                    user_id=str(user_id) if user_id is not None else None,
                    user_message=f"公众号文章图片解析（第{s.n}张）",
                    assistant_message=(s.description or "")[:500],
                    total_token_count=int(s.usage.get("total_tokens") or 0),
                    prompt_tokens=int(s.usage.get("prompt_tokens") or 0),
                    completion_tokens=int(s.usage.get("completion_tokens") or 0),
                    model=s.model,
                    provider=s.provider,
                    status="completed",
                    source_type=VISION_PARSE_SOURCE_TYPE,
                    credit_cost=per_image_credit,
                    usage_breakdown={
                        "billing_mode": "per_call",
                        "price_per_call": price_per_call,
                        "usage_factor": usage_factor,
                        "image_n": s.n,
                        "article_row_id": article_row_id,
                        "vl_usage": s.usage,
                    },
                )
            except Exception as e:  # noqa: BLE001 单张计费失败不回滚内容
                record = None
                logger.opt(exception=True).error(
                    "后端日志：wechat_mp 图片计费异常（记 unknown）tenant_id={} "
                    "article_id={} image_n={}: {}",
                    tenant_id, article_row_id, s.n, sanitize_error_info(str(e)),
                )
            if record and record.get("record_id"):
                image_credits += per_image_credit
                record_ids.append(record["record_id"])
            else:
                image_unknown = True
                logger.bind(module="wechat_mp").error(
                    "wechat_mp 图片计费结果不可确认（记 unknown，不重扣）"
                    "tenant_id={} article_id={} image_n={}",
                    tenant_id, article_row_id, s.n,
                )

        logger.bind(module="wechat_mp").info(
            "wechat_mp 图片按张计费 tenant_id={} article_id={} images={} "
            "credits={} unknown={}",
            tenant_id, article_row_id, len(successes), image_credits, image_unknown,
        )
        self._merge_item_billing(
            tenant_id, item_id,
            extra_credits=image_credits,
            extra_reference=",".join(record_ids) or None,
            extra_unknown=image_unknown,
        )

    def _merge_item_billing(
        self,
        tenant_id: str,
        item_id: int,
        *,
        extra_credits: float,
        extra_reference: Optional[str],
        extra_unknown: bool,
    ) -> None:
        """把图片计费结果合并进 item 既有计费字段（embedding 计费可能已写入）。

        合并语义：credits 累加；reference 以逗号拼接；任一来源 unknown → 整条
        记 unknown（存在未确认扣款时禁止自动补扣，宁保守勿漏记）。
        """
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT billing_status, billing_reference, credits_charged
                    FROM bs_wechat_mp_sync_items
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (item_id, tenant_id),
                )
                row = cursor.fetchone()
                if not row:
                    return
                credits = float(row["credits_charged"] or 0) + extra_credits
                refs = [
                    r for r in (row["billing_reference"], extra_reference) if r
                ]
                reference = ",".join(refs) or None
                statuses = {row["billing_status"] or "not_required"}
                if extra_unknown:
                    statuses.add("unknown")
                if "unknown" in statuses:
                    status = "unknown"
                elif "charged" in statuses:
                    status = "charged"
                else:
                    status = "not_required"
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items
                    SET billing_status = %s, billing_reference = %s, credits_charged = %s
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (status, reference, credits, item_id, tenant_id),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001 合并失败不影响已落账记录
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 图片计费合并回写失败 item_id={}: {}", item_id, e
            )

    # ==================== 余额 ====================

    def _check_credit(self, tenant_id: str) -> Tuple[bool, str]:
        """余额检查：余额 <= 0 或租户不存在拦截；检查异常时不阻断（对齐既有策略）。"""
        try:
            from src.saas.db.tenant_db import TenantDB

            tenant = TenantDB.get_by_id(tenant_id)
            if not tenant:
                return False, "租户不存在"
            balance = float(tenant.get("credit_balance") or 0)
            if balance <= 0:
                return False, "积分余额已耗尽"
            return True, ""
        except Exception as e:
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 余额预检异常 tenant={}: {}", tenant_id, e
            )
            return True, ""

    # ==================== 状态落库小件 ====================

    def _load_article(self, tenant_id: str, article_row_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_wechat_mp_articles WHERE id = %s AND tenant_id = %s",
                (article_row_id, tenant_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _get_doc_state(self, tenant_id: str, doc_id: Optional[int]) -> Optional[str]:
        """documents 行状态（active/deleted/None=不存在）；异常按存在降级（对齐 crawler）。"""
        if not doc_id:
            return None
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT status FROM documents WHERE id = %s AND tenant_id = %s",
                    (doc_id, tenant_id),
                )
                row = cursor.fetchone()
                return row["status"] if row else None
        except Exception as e:
            logger.warning(
                "后端日志：wechat_mp 文档状态校验异常（按 active 降级）doc_id={}: {}", doc_id, e
            )
            return "active"

    def _retry_after(self, tenant_id: str, article_row_id: int) -> datetime:
        """指数退避：按该文章累计失败（含 no_credit 跳过）次数 300s×2^n，上限 24h。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS c FROM bs_wechat_mp_sync_items
                WHERE tenant_id = %s AND article_row_id = %s
                  AND (status = 'failed' OR error_code = %s)
                """,
                (tenant_id, article_row_id, ERR_NO_CREDIT),
            )
            attempts = int(cursor.fetchone()["c"])
        delay = min(RETRY_BASE_SECONDS * (2 ** min(attempts, RETRY_MAX_SHIFT)), RETRY_MAX_SECONDS)
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=delay)

    def _get_item_status(self, tenant_id: str, item_id: int) -> Optional[str]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s AND tenant_id = %s",
                (item_id, tenant_id),
            )
            row = cursor.fetchone()
            return row["status"] if row else None

    def _mark_article_no_credit_retry(self, tenant_id: str, article_id: int) -> None:
        """余额不足退避：保持 pending（非 sync_failed），WP7 到期重试入队。"""
        next_retry = self._retry_after(tenant_id, article_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET processing_status = 'pending', next_retry_at = %s,
                    error_message = %s, last_checked_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (next_retry, _FRIENDLY_NO_CREDIT, article_id, tenant_id),
            )
            conn.commit()

    def _mark_item_running(self, tenant_id: str, item_id: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items SET status = 'running', started_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (item_id, tenant_id),
            )
            conn.commit()

    def _mark_item_skipped(
        self, tenant_id: str, item_id: int, error_code: str, message: str
    ) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'skipped', error_code = %s, error_message = %s,
                    billing_status = 'not_required', credits_charged = 0,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (error_code, message, item_id, tenant_id),
            )
            conn.commit()

    def _mark_item_no_credit(self, tenant_id: str, item: Dict[str, Any]) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'skipped', error_code = %s, error_message = %s,
                    billing_status = 'not_required', credits_charged = 0,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (ERR_NO_CREDIT, _FRIENDLY_NO_CREDIT, item["id"], tenant_id),
            )
            conn.commit()
        logger.bind(module="wechat_mp").warning(
            "wechat_mp 余额不足跳过 item_id={} tenant_id={}", item["id"], tenant_id
        )

    def _mark_item_deferred(self, tenant_id: str, item_id: int, message: str) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'deferred', error_code = 'deferred_image_pending',
                    error_message = %s, billing_status = 'not_required',
                    credits_charged = 0, completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (message, item_id, tenant_id),
            )
            conn.commit()

    def _mark_item_failed(
        self, tenant_id: str, item: Dict[str, Any], error_code: str, message: str
    ) -> None:
        safe_message = sanitize_error_info(message)[:500]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_items
                SET status = 'failed', error_code = %s, error_message = %s,
                    completed_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (error_code, safe_message, item["id"], tenant_id),
            )
            conn.commit()

    def _mark_article_retry(self, tenant_id: str, article_id: int, reason: str) -> None:
        next_retry = self._retry_after(tenant_id, article_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET processing_status = 'sync_failed', next_retry_at = %s,
                    error_message = %s, last_checked_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (next_retry, sanitize_error_info(reason)[:500], article_id, tenant_id),
            )
            conn.commit()

    def _touch_article_checked(
        self, tenant_id: str, article_id: int, processing_status: Optional[str]
    ) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if processing_status is None:
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_articles SET last_checked_at = now()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (article_id, tenant_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_articles
                    SET last_checked_at = now(), processing_status = %s
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (processing_status, article_id, tenant_id),
                )
            conn.commit()

    def _mark_article_deferred(
        self,
        tenant_id: str,
        article_id: int,
        extracted: ExtractedArticle,
        message: str = "有图片且文字不足，待图片解析版本处理",
    ) -> None:
        """文章行置 deferred（P2：图片 VL 解析不可用/失败）。

        deferred 不进失败退避（next_retry_at=NULL，非技术失败）；自动重试由
        scheduler 24h 存活复核通道承接（processing_status='deferred' 在复核到期
        条件内，error_message 向用户承诺「将自动重试」）。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET processing_status = 'deferred', image_count = %s,
                    last_checked_at = now(), error_message = %s
                WHERE id = %s AND tenant_id = %s
                """,
                (extracted.image_count, message, article_id, tenant_id),
            )
            conn.commit()

    # ==================== run 收尾 ====================

    def _finalize_run(self, tenant_id: str, run_id: int, owner: str) -> None:
        """按 item 终态汇总 run；事件在该 run 全部 item 终态后置 done。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT status, action, error_code, credits_charged
                FROM bs_wechat_mp_sync_items
                WHERE tenant_id = %s AND run_id = %s
                """,
                (tenant_id, run_id),
            )
            items = cursor.fetchall()

        total = len(items)
        new_count = updated_count = deleted_count = skipped_count = failed_count = 0
        no_credit_count = 0
        credits = 0.0
        for it in items:
            credits += float(it["credits_charged"] or 0)
            if it["status"] == "failed":
                failed_count += 1
            elif it["status"] in ("skipped", "deferred"):
                skipped_count += 1
                if it["error_code"] == ERR_NO_CREDIT:
                    no_credit_count += 1
            elif it["status"] == "success":
                if it["action"] == "new":
                    new_count += 1
                elif it["action"] == "update":
                    updated_count += 1
                elif it["action"] == "delete":
                    deleted_count += 1
                else:  # check / restore 无内容变更，计入 skipped
                    skipped_count += 1

        if failed_count == 0 and no_credit_count == 0:
            run_status = "success"
        elif failed_count == 0 and new_count == updated_count == deleted_count == 0:
            run_status = "skipped_no_credit"
        elif new_count + updated_count + deleted_count > 0:
            run_status = "partial_failed"
        else:
            run_status = "failed"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_runs
                SET status = %s, total_count = %s, new_count = %s, updated_count = %s,
                    deleted_count = %s, skipped_count = %s, failed_count = %s,
                    credits_charged = %s, completed_at = now(), heartbeat_at = now()
                WHERE id = %s AND tenant_id = %s AND owner_token = %s AND status = 'running'
                """,
                (
                    run_status, total, new_count, updated_count, deleted_count,
                    skipped_count, failed_count, credits, run_id, tenant_id, owner,
                ),
            )
            finalized = cursor.rowcount == 1
            if finalized:
                # 事件在其 run 全部 item 终态后置 done（按设计 §8 附带 tenant_id）
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_events
                    SET status = 'done', processed_at = now()
                    WHERE run_id = %s AND tenant_id = %s AND status = 'pending'
                    """,
                    (run_id, tenant_id),
                )
            conn.commit()
        if not finalized:
            logger.bind(module="wechat_mp").error(
                "wechat_mp run 收尾未命中（owner/状态已变）run_id={}", run_id
            )
            return
        logger.bind(module="wechat_mp").info(
            "wechat_mp run 收尾 tenant_id={} run_id={} status={} new={} updated={} "
            "deleted={} skipped={} failed={} credits={}",
            tenant_id, run_id, run_status, new_count, updated_count,
            deleted_count, skipped_count, failed_count, credits,
        )

    # ==================== 工具 ====================

    def _get_embedding_client(self):
        if self._embedding_client is None:
            from src.config.settings import get_embedding_api_key
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client

            self._embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        return self._embedding_client

    def _get_image_downloader(self) -> MPImageDownloader:
        """图片下载转存器（测试可注入；默认真实实现）。"""
        if self._image_downloader is None:
            self._image_downloader = MPImageDownloader()
        return self._image_downloader

    def _get_vision_parser(self) -> VisionParser:
        """VL 解析器（测试可注入；默认真实实现，模型清单实时查库）。"""
        if self._vision_parser is None:
            self._vision_parser = VisionParser()
        return self._vision_parser

    @staticmethod
    def _content_hash(title: str, nodes: List[Any]) -> str:
        """内容指纹：结构化序列编码（保序，避免拼接歧义）。"""
        payload = {
            "title": title or "",
            "nodes": [
                {"t": n.type, "x": n.text if n.type == "text" else (n.src or "")}
                for n in nodes
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
