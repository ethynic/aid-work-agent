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
- **图片 VL 解析（WP10，P2；WP13 门禁放开）**：有图即先下载转存微信 CDN 图片
  （image_downloader.py，单篇 200 张防失控硬护栏，非产品限制）→ VL 解析（vision.py，
  无可用多模态
  模型不发纯文本模型）→ 描述插回正文入库；无模型不再 deferred——有文本照常入库，
  图片行保留地址、描述留空，metadata 记 image_parse_skipped_reason='no_model'；
  仅纯图且无任何可总结内容维持 deferred。VL 按张按实际 token 计费 source_type=
  'wechat_mp_image_parse'（走 calculate_credit_cost 标准算价含价目兜底），业务
  提交后独立落账 fail-open，与 embedding 计费合并回写 item 计费字段。
- **总结入库（WP12，p3；WP13 输入改 content_md）**：VL 之后按节点顺序组装
  Markdown（content.py.build_markdown：# 标题 + 文本段落 + ![VL描述](CDN地址)），
  存 documents.metadata.content_md 并作为总结输入（图片转述与文本同等参与总结，
  指令要求图文去重）；LLM 生成 ≤500 字核心要点总结（summarize.py，主 provider
  默认文本模型）；chunk 文本 = 总结本身（无「文档标题：」标签前缀，不拼原文链接
  ——WP12 定版：原文链接存 documents.file_path 文档位置字段，与手动上传文档的
  本地路径同字段，下载边界按 origin 拦截）；raw_text 仍存 merged 纯文本供审计；
  总结失败回退 content_md 原文入库（metadata.summary_fallback/content_mode 标记
  形态）。总结调用经 record_background_llm_usage(source='wechat_mp_summary')
  独立落账 fail-open；content_hash 仍按原始节点计算，VL 描述与总结均不进指纹
  ——同文章二次处理仍走 check 零成本。
- **恢复**：``recover_stale_runs()`` —— heartbeat 超时且锁已失效的 running run 标
  interrupted；queued 保持等待。

- **WP9 接口通道（freepublish 定时对账）**：scheduled run 先跑
  ``_reconcile_config``——client.batchget_all(no_content=1) 全分页 → 消息级 diff：
  新消息建 articles 行 + pending item；wx_update_time/sync_failed/pipeline 变化建
  item（无活跃 item 门禁）；未变且 success 且 pipeline 相同仅推进 last_synced_at
  （不重拉正文）；missing 重现恢复 active。整条缺失防护：仅完整可靠扫描迁移——
  首次缺失置 missing，连续两轮缺失 + getarticle 53600 复核才软删（不完整扫描不迁移）。
  单 item 走 ``_fetch_freepublish_article``：多图文按 §6.1 P3 粒度合并（排除
  is_deleted 子篇、未删子篇「标题→正文」保序拼接），空数组/结构异常失败保留旧版
  绝不判删除，全删走 _handle_deleted；之后与 URL 通道在 hash 比对处汇合；
  入库后跨来源互标 related_doc_ids（§5.4，fail-open）。

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
from src.core.text_sanitizer import sanitize_text
from src.knowledge.chunker import TextChunker
from src.wechat_mp import config_codec as wechat_mp_codec
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.knowledge.vector_db.vector_db import get_vector_db
from src.wechat_mp.client import (
    ArticleNotFoundError,
    WeChatMPAPIClient,
    WeChatMPAPIError,
    friendly_api_error,
    is_truthy_flag,
)
from src.wechat_mp.content import (
    ContentExtractionError,
    ContentNode,
    ExtractedArticle,
    build_markdown,
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
from src.wechat_mp.summarize import (
    SUMMARY_MAX_CHARS,
    SUMMARY_SOURCE_TYPE,
    ArticleSummarizer,
)
from src.wechat_mp.vision import (
    VISION_PARSE_SOURCE_TYPE,
    VisionParseOutcome,
    VisionParser,
)
from src.services.billing import calculate_credit_cost

# ------------------------------- 常量 -------------------------------

# WP13：p3→p4（图文 Markdown 化——门禁放开有图即解析、metadata.content_md、
# 总结输入改 content_md、图片计费改按实际 token）。
# WP13-r2：p4→p5（VL 指令放宽——无文字图改一句话客观白描，纯纹理/装饰条图不再
# 计失败；单篇图片 30 张产品上限移除，改 200 张防失控硬护栏；image_parsed_count
# 成功路径回写 articles 列）。存量 p4 文章在复核/pipeline 不匹配时自动重建：
# 图片重新解析（补付解析费，运营知会）。
PIPELINE_VERSION = "p5"
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

# SUMMARY_MAX_CHARS（500 字总结上限）迁至 summarize.py（WP12：总结由 LLM 生成，
# 不再是 P1 的确定性 300 字截断）；此处 import 以维持既有引用口径。

# WP12 总结正文形态（documents.metadata.content_mode）
CONTENT_MODE_SUMMARY = "summary"
CONTENT_MODE_RAW_FALLBACK = "raw_fallback"

# WP9 接口通道（freepublish）：身份定稿（设计 §5.2.1/§6.1 P3 粒度）
# 一条消息（article_id）= 一篇合并文档；external_id = {appid}:{article_id}:combined，
# 不以数组下标做持久身份。appid/article_id 均不含冒号，解析安全。
FREEPUBLISH_CHANNEL = "freepublish"
FREEPUBLISH_COMBINED_SUFFIX = ":combined"

# WP13 自有号清单源（设计 wechat-mp-list-source-design.md §3.4）：
# - source_channel='list'；子篇短链经 normalize_url 撞键，与 URL/freepublish 通道天然去重
# - processing_status 新增 'pending_manual'（manual 模式：仅建行不入队，待用户勾选）
LIST_CHANNEL = "list"
PENDING_MANUAL = "pending_manual"


def freepublish_external_id(appid: str, article_id: str) -> str:
    """接口通道消息身份：{appid}:{article_id}:combined（一条消息一篇合并文档）。"""
    return f"{appid}:{article_id}{FREEPUBLISH_COMBINED_SUFFIX}"


def parse_freepublish_article_id(external_id: str) -> Optional[str]:
    """从 freepublish 身份解出 article_id；格式不符返回 None（调用方记失败）。"""
    parts = (external_id or "").split(":")
    if len(parts) != 3 or parts[2] != "combined" or not parts[0] or not parts[1]:
        return None
    return parts[1]


def _wrap_freepublish_content(content_html: str) -> str:
    """getarticle 的 content 是正文片段（无页面骨架、无 #js_content 容器——主控者
    2026-09-15 实测），包装容器后复用 content.extract_article 的清洗与保序解析。"""
    return f'<div id="js_content">{content_html}</div>'


def _parse_unix_seconds(value: Optional[int]) -> Optional[datetime]:
    """unix 秒 → tz-aware UTC datetime（getarticle 顶层 create_time/update_time）。"""
    if not isinstance(value, int):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None

# 图片 VL 解析 deferred 原因文案（WP13 起仅「纯图且无任何可总结内容」使用；
# error_code 仍为 deferred_image_pending）
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


def switch_list_sync_mode(tenant_id: str, mode: str) -> Dict[str, Any]:
    """切换清单源同步模式（WP13，设计 §3.4：auto_all 全部自动 / manual 手动挑选）。

    - manual→auto_all：自动补齐 pending_manual 积压文章入队（设计 §3.4 定版）
    - 未绑定清单源 / 模式非法 → 业务错误
    返回 {"config_id", "mode", "enqueued"}（enqueued=本次补齐入队篇数）。
    """
    if mode not in ("auto_all", "manual"):
        raise WeChatMPBusinessError("同步模式仅支持 auto_all / manual")
    from src.wechat_mp import list_session as list_session_mod

    config_id = None
    bound = list_session_mod.find_bound_list_config(tenant_id)
    if not bound:
        raise WeChatMPBusinessError("尚未绑定公众号清单源，请先扫码授权")
    config_id = bound["config_id"]

    # 先补齐积压、后落模式（两步非原子）：若先写模式后入队，入队失败（如队列满）
    # 会留下「auto_all + 积压滞留 pending_manual」的不一致态——对账对 pending_manual
    # 行不建 item，积压将无人接管。先入队失败则模式保持 manual，用户可重试。
    enqueued = 0
    if mode == "auto_all":
        pending_ids = _list_pending_manual_ids(tenant_id)
        if pending_ids:
            result = enqueue_manual_articles(tenant_id, None, pending_ids)
            enqueued = int(result.get("enqueued") or 0)
    ok = list_session_mod.write_list_config_fields(
        config_id, fields={"list_sync_mode": mode}
    )
    if not ok:
        raise WeChatMPBusinessError("渠道配置不存在或已删除，无法切换模式")
    logger.bind(module="wechat_mp").info(
        "wechat_mp 清单同步模式切换 tenant_id={} config_id={} mode={} enqueued={}",
        tenant_id, config_id, mode, enqueued,
    )
    return {"config_id": config_id, "mode": mode, "enqueued": enqueued}


def _list_pending_manual_ids(tenant_id: str) -> List[int]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM bs_wechat_mp_articles
            WHERE tenant_id = %s AND source_channel = %s AND processing_status = %s
              AND status = 'active'
            ORDER BY id ASC
            """,
            (tenant_id, LIST_CHANNEL, PENDING_MANUAL),
        )
        return [r["id"] for r in cursor.fetchall()]


def enqueue_manual_articles(
    tenant_id: str, user_id: Optional[str], article_row_ids: List[int]
) -> Dict[str, Any]:
    """manual 模式勾选同步（WP13）：pending_manual 文章单篇/批量入队走 URL 直采。

    - 仅受理本租户 active 且 processing_status='pending_manual' 的行（其余跳过计数）
    - 一个 queued run（trigger_type='manual'，config_id=绑定配置）+ 每篇一条
      pending item（action=NULL）；文章行回 processing_status='pending'
    - 与 retry/recheck 同口径：advisory lock 限流原子化 + queued run 上限 + 受理
      成功后 best-effort 唤醒 worker
    """
    if not article_row_ids:
        raise WeChatMPBusinessError("请至少选择一篇文章")
    bound_config_id = None
    try:
        from src.wechat_mp import list_session as list_session_mod

        bound = list_session_mod.find_bound_list_config(tenant_id)
        bound_config_id = bound["config_id"] if bound else None
    except Exception:  # noqa: BLE001 绑定查询失败不阻断入队（config_id 仅追溯用）
        bound_config_id = None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
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
                """
                SELECT id FROM bs_wechat_mp_articles
                WHERE tenant_id = %s AND id = ANY(%s)
                  AND status = 'active' AND processing_status = %s
                """,
                (tenant_id, list(article_row_ids), PENDING_MANUAL),
            )
            valid_ids = [r["id"] for r in cursor.fetchall()]
            skipped = len(article_row_ids) - len(valid_ids)
            if not valid_ids:
                return {"run_id": None, "enqueued": 0, "skipped": skipped,
                        "message": "所选文章均不在待挑选状态"}
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, user_id, trigger_type, status, total_count)
                VALUES (%s, %s, %s, 'manual', 'queued', %s)
                RETURNING id
                """,
                (tenant_id, bound_config_id, user_id, len(valid_ids)),
            )
            run_id = cursor.fetchone()["id"]
            for article_row_id in valid_ids:
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_items
                        (tenant_id, user_id, run_id, article_row_id, action, status)
                    VALUES (%s, %s, %s, %s, NULL, 'pending')
                    """,
                    (tenant_id, user_id, run_id, article_row_id),
                )
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
    logger.bind(module="wechat_mp").info(
        "wechat_mp 手动挑选同步入队 tenant_id={} run_id={} enqueued={} skipped={}",
        tenant_id, run_id, len(valid_ids), skipped,
    )
    try:
        notify_queued_work()
    except Exception as e:  # noqa: BLE001 唤醒失败不影响受理结果
        logger.bind(module="wechat_mp").debug("wechat_mp 队列唤醒通知异常（忽略）: {}", e)
    return {
        "run_id": run_id,
        "enqueued": len(valid_ids),
        "skipped": skipped,
        "message": f"已受理 {len(valid_ids)} 篇并加入队列（run_id={run_id}），同租户按顺序串行处理",
    }


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
        summarizer: Optional[Any] = None,
    ):
        self._fetcher = fetcher or MPArticleFetcher()
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._embedding_client = embedding_client  # 测试注入；None 时懒加载真实 client
        self._image_downloader = image_downloader  # WP10 图片下载转存（测试可注入）
        self._vision_parser = vision_parser  # WP10 VL 解析（测试可注入）
        self._summarizer = summarizer  # WP12 文章总结（测试可注入）
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

        # ---- WP9：scheduled run 先跑 freepublish 对账（batchget diff → 同事务建 items）----
        count_overrides: Optional[Dict[str, Any]] = None
        if run.get("trigger_type") == "scheduled":
            stats = await asyncio.to_thread(self._reconcile_scheduled_run, tenant_id, run, owner)
            if stats is None:
                # 对账失败已在 _reconcile_scheduled_run 内记 failed（脱敏 error_message），
                # 不得把拉取失败/结构异常误报 success（设计 §5.2.6）
                return True
            count_overrides = stats

        # ---- WP13：list_sync run 先跑自有号清单对账（三阶段同 WP9 模式）----
        elif run.get("trigger_type") == "list_sync":
            stats = await asyncio.to_thread(self._reconcile_list_sync_run, tenant_id, run, owner)
            if stats is None:
                # 拉取失败/结构异常已在 _reconcile_list_sync_run 内记 failed，不得误报 success
                return True
            count_overrides = stats

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

        self._finalize_run(tenant_id, run_id, owner, count_overrides=count_overrides)
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

    # ==================== WP9 接口通道：scheduled run 对账 ====================

    def _load_mp_config(self, tenant_id: str, config_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """加载本租户 wechat_mp 配置的明文 appid/secret（接口通道专用）。

        任何一项不满足（config_id 缺失 / 配置不存在 / 租户不符 / 渠道类型不符 /
        appid 或 secret 为空）返回 None——调用方将 run 记 failed，不误报 success。
        """
        if not config_id:
            return None
        from src.saas.db.channel_config_db import ChannelConfigDB

        try:
            config = ChannelConfigDB.get_by_id_decrypted(config_id)
        except Exception:  # noqa: BLE001 配置读取失败按不可用处理（上层记 failed）
            return None
        if not config or config.get("tenant_id") != tenant_id:
            return None
        if config.get("channel_type") != "wechat_mp":
            return None
        data = config.get("config") or {}
        appid = str(data.get("appid") or "").strip()
        secret = str(data.get("secret") or "").strip()
        if not appid or not secret:
            return None
        return {"appid": appid, "secret": secret}

    def _build_api_client(
        self, tenant_id: str, config_id: Optional[str], config: Dict[str, Any]
    ) -> WeChatMPAPIClient:
        """构造接口客户端（测试经 monkeypatch svc_mod.WeChatMPAPIClient 注入替身）。"""
        return WeChatMPAPIClient(
            tenant_id=tenant_id, config_id=config_id or "", appid=config["appid"], secret=config["secret"]
        )

    def _reconcile_scheduled_run(
        self, tenant_id: str, run: Dict[str, Any], owner: str
    ) -> Optional[Dict[str, int]]:
        """scheduled run 的对账入口（同步，asyncio.to_thread 调用）。

        返回 {"total_count": 本轮源消息数, "extra_skipped": 未变跳过数} 供 run 收尾
        补写计数；对账失败（配置不可用 / API 失败 / 结构异常）置 run=failed 并返回
        None——不得把拉取失败或空结果误报 success（设计 §5.2.6）。
        """
        config = self._load_mp_config(tenant_id, run.get("config_id"))
        if config is None:
            self._fail_run(tenant_id, run["id"], owner, "接口通道配置不可用（缺 AppID/AppSecret 或配置不属于本租户）")
            return None
        try:
            total_count, unchanged = self._reconcile_config(tenant_id, run, config)
        except Exception as e:  # noqa: BLE001 对账异常 → run failed（错误消息脱敏）
            logger.opt(exception=True).error(
                "后端日志：wechat_mp scheduled 对账失败 tenant_id={} run_id={}: {}",
                tenant_id, run["id"], sanitize_error_info(str(e)),
            )
            self._fail_run(tenant_id, run["id"], owner, friendly_api_error(e))
            return None
        logger.bind(module="wechat_mp").info(
            "wechat_mp scheduled 对账完成 tenant_id={} run_id={} config_id={} "
            "total={} unchanged={}",
            tenant_id, run["id"], run.get("config_id"), total_count, unchanged,
        )
        return {"total_count": total_count, "extra_skipped": unchanged}

    def _reconcile_config(
        self, tenant_id: str, run: Dict[str, Any], config: Dict[str, Any]
    ) -> Tuple[int, int]:
        """batchget 全分页对账（消息级 diff + 整条缺失防护，设计 §5.2.4）。

        - 库中无该 (tenant, external_id) 行 → 建 articles 行 + pending item（新增）
        - wx_update_time != 源 update_time / sync_failed / pipeline 变更 → 建 item
          （前提：无挂在 queued/running run 上的 pending/running item，同 retry/recheck
          的 NOT EXISTS 口径；本 run 自身刚建的 item 由 UNIQUE 约束防重）
        - 未变且 success 且 pipeline 相同 → 跳过，仅 last_synced_at（不重拉正文）
        - missing 行重新出现 → 置回 active 并建 item（重现恢复语义）
        - deleted 行重新出现且源时间变化 → 置回 active 并建 item（重新发布）；
          源时间未变 → 保持 deleted 跳过
        - 整条缺失防护：仅本轮完整可靠（分页无失败/结构无异常/无重复页/总数一致）
          才迁移——active 首次缺失置 missing；已 missing 且仍缺失 → getarticle 详情
          复核，errcode 53600（不存在）才同事务软删；其他 errcode/异常保留 missing。
          本轮不完整可靠 → 不做任何 missing 迁移（宁可不删不可误删）。

        返回 (本轮消息总数, 未变跳过数)。
        """
        client = self._build_api_client(tenant_id, run.get("config_id"), config)
        scan = client.batchget_all(no_content=1)
        appid = config["appid"]
        unchanged_skipped = 0
        missing_recheck: List[Dict[str, Any]] = []

        # ---- 阶段 1：消息级 diff（单事务）----
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id, external_id, wx_update_time, status, processing_status,
                           pipeline_version, doc_id
                    FROM bs_wechat_mp_articles
                    WHERE tenant_id = %s AND source_channel = %s AND config_id = %s
                    """,
                    (tenant_id, FREEPUBLISH_CHANNEL, run.get("config_id")),
                )
                existing = {r["external_id"]: dict(r) for r in cursor.fetchall()}

                present_ids: set = set()
                for msg in scan.messages:
                    external_id = freepublish_external_id(appid, msg["article_id"])
                    present_ids.add(external_id)
                    source_time = datetime.fromtimestamp(
                        msg["update_time"], tz=timezone.utc
                    ).replace(tzinfo=None)
                    row = existing.get(external_id)
                    if row is None:
                        # 新消息：建 articles 行（batchget 元数据先行，正文由 item 拉取）
                        article_row_id = self._insert_freepublish_article(
                            cursor, tenant_id, run.get("config_id"), external_id,
                            appid, msg,
                        )
                        self._insert_pending_item(cursor, tenant_id, run, article_row_id)
                        continue

                    if row["status"] in ("missing", "deleted"):
                        if row["status"] == "missing":
                            # 重现恢复：置回 active 并按变更建 item（不依赖时间比较）
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles SET status = 'active'
                                WHERE id = %s AND tenant_id = %s AND status = 'missing'
                                """,
                                (row["id"], tenant_id),
                            )
                            self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                            continue
                        # deleted 行重现：仅源时间变化（可能重新发布）才恢复
                        if row["wx_update_time"] is not None and row["wx_update_time"] != source_time:
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles SET status = 'active'
                                WHERE id = %s AND tenant_id = %s AND status = 'deleted'
                                """,
                                (row["id"], tenant_id),
                            )
                            self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                        else:
                            unchanged_skipped += 1  # 已确认删除且源未变：保持删除，静默跳过
                        continue

                    need_refresh = (
                        row["wx_update_time"] is None
                        or row["wx_update_time"] != source_time
                        or row["processing_status"] == "sync_failed"
                        or (row["pipeline_version"] or "") != PIPELINE_VERSION
                    )
                    if need_refresh:
                        self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                    else:
                        # 未变且 success 且 pipeline 相同：不重拉正文，仅推进 last_synced_at
                        unchanged_skipped += 1
                        cursor.execute(
                            """
                            UPDATE bs_wechat_mp_articles SET last_synced_at = now()
                            WHERE id = %s AND tenant_id = %s
                            """,
                            (row["id"], tenant_id),
                        )

                # ---- 整条缺失防护（设计 §5.2.4）：仅完整可靠扫描执行 ----
                if scan.reliable:
                    for external_id, row in existing.items():
                        if external_id in present_ids:
                            continue
                        if row["status"] == "active":
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles SET status = 'missing'
                                WHERE id = %s AND tenant_id = %s AND status = 'active'
                                """,
                                (row["id"], tenant_id),
                            )
                        elif row["status"] == "missing":
                            # 连续两轮完整扫描仍缺失：进入详情复核（阶段 2）
                            missing_recheck.append(row)
                else:
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp 对账本轮不完整可靠，缺失迁移整体跳过 tenant_id={} "
                        "run_id={} total={} fetched={}",
                        tenant_id, run["id"], scan.total_count, scan.fetched,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # ---- 阶段 2：missing 详情复核（事务外网络调用，逐条独立）----
        to_delete: List[Dict[str, Any]] = []
        for row in missing_recheck:
            article_id = parse_freepublish_article_id(row["external_id"])
            if not article_id:
                continue  # 身份异常：保留 missing 待审计
            try:
                client.getarticle(article_id)
            except ArticleNotFoundError:
                to_delete.append(row)  # 唯一可靠的删除判据（errcode 53600）
            except Exception as e:  # noqa: BLE001 其他 errcode/异常保留 missing 不删
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp missing 复核未确认删除（保留 missing）tenant_id={} "
                    "article_id={} err={}",
                    tenant_id, article_id, type(e).__name__,
                )
        close = getattr(client, "close", None)
        if callable(close):
            close()  # 阶段 2 后不再使用：释放 httpx 连接池（getattr 兼容测试替身）

        # ---- 阶段 3：确认不存在 → 同事务软删（复用 _handle_deleted 落库语义）----
        for row in to_delete:
            self._soft_delete_article_and_doc(tenant_id, row["id"], row.get("doc_id"))

        return len(scan.messages), unchanged_skipped

    @staticmethod
    def _insert_freepublish_article(
        cursor,
        tenant_id: str,
        config_id: Optional[str],
        external_id: str,
        appid: str,
        msg: Dict[str, Any],
    ) -> int:
        """按 batchget 消息元数据建 articles 行（正文 pending，item 拉取时回填）。

        original_url/title 取首个 news_item 的 url/title（no_content=1 保留元数据，
        主控者 2026-09-15 实测，client 已做类型规整）；publish_time 取
        content.create_time 兜底 update_time；时间统一 UTC naive（列类型 TIMESTAMP，
        对齐既有写入口径）。
        """
        publish_ts = msg.get("create_time") or msg["update_time"]
        publish_time = datetime.fromtimestamp(publish_ts, tz=timezone.utc).replace(tzinfo=None)
        update_time = datetime.fromtimestamp(msg["update_time"], tz=timezone.utc).replace(tzinfo=None)
        first_title = msg.get("first_title")
        first_title = sanitize_text(first_title, source="wechat_mp_ingest")[:255] if first_title else None
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, config_id, external_id, original_url, fetch_url,
                 source_channel, title, publish_time, wx_update_time,
                 status, processing_status)
            VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, 'active', 'pending')
            ON CONFLICT (tenant_id, external_id) DO NOTHING
            RETURNING id
            """,
            (
                tenant_id, config_id, external_id, msg.get("first_url"),
                FREEPUBLISH_CHANNEL, first_title, publish_time, update_time,
            ),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
            (tenant_id, external_id),
        )
        return cursor.fetchone()["id"]

    @staticmethod
    def _insert_pending_item(cursor, tenant_id: str, run: Dict[str, Any], article_row_id: int) -> None:
        """对账新建/变更文章的 pending item（action=NULL，worker 视为 'new'/'update'）。"""
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_items
                (tenant_id, user_id, run_id, article_row_id, action, status)
            VALUES (%s, NULL, %s, %s, NULL, 'pending')
            """,
            (tenant_id, run["id"], article_row_id),
        )

    @staticmethod
    def _insert_item_if_idle(cursor, tenant_id: str, run: Dict[str, Any], article_row_id: int) -> None:
        """无活跃 item（queued/running run 上的 pending/running）时建 pending item。

        与 scheduler retry/recheck 生成器同口径的 NOT EXISTS 门禁：文章已被其他
        待执行任务覆盖时不重复排队。本 run 刚建的 item 亦被门禁识别（同 run 内
        消息级去重 + UNIQUE(tenant, run_id, article_row_id) 双保险）。
        """
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_items
                (tenant_id, user_id, run_id, article_row_id, action, status)
            SELECT %s, NULL, %s, %s, NULL, 'pending'
            WHERE NOT EXISTS (
                SELECT 1 FROM bs_wechat_mp_sync_items it
                JOIN bs_wechat_mp_sync_runs r
                  ON r.id = it.run_id AND r.tenant_id = it.tenant_id
                WHERE it.tenant_id = %s
                  AND it.article_row_id = %s
                  AND it.status IN ('pending', 'running')
                  AND r.status IN ('queued', 'running')
            )
            """,
            (tenant_id, run["id"], article_row_id, tenant_id, article_row_id),
        )

    def _soft_delete_article_and_doc(
        self, tenant_id: str, article_row_id: int, doc_id: Optional[int]
    ) -> None:
        """missing 复核确认源不存在：软删 articles + documents（单事务，同 _handle_deleted）。"""
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
                WHERE id = %s AND tenant_id = %s AND status = 'missing'
                """,
                (article_row_id, tenant_id),
            )
            conn.commit()
        logger.bind(module="wechat_mp").info(
            "wechat_mp missing 复核软删除 tenant_id={} article_id={} doc_id={}",
            tenant_id, article_row_id, doc_id,
        )

    def _fail_run(self, tenant_id: str, run_id: int, owner: str, message: str) -> None:
        """run 级失败（对账异常等，items 尚未/无需执行）：终态 failed + 脱敏原因。

        owner/状态守卫对齐 _finalize_run；防御性把该 run 遗留 pending item 置
        interrupted（正常路径对账失败时 items 尚未创建）。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_sync_runs
                SET status = 'failed', error_message = %s,
                    completed_at = now(), heartbeat_at = now()
                WHERE id = %s AND tenant_id = %s AND owner_token = %s AND status = 'running'
                """,
                (sanitize_error_info(message)[:500], run_id, tenant_id, owner),
            )
            finalized = cursor.rowcount == 1
            if finalized:
                cursor.execute(
                    """
                    UPDATE bs_wechat_mp_sync_items SET status = 'interrupted', completed_at = now()
                    WHERE tenant_id = %s AND run_id = %s AND status IN ('pending', 'running')
                    """,
                    (tenant_id, run_id),
                )
            conn.commit()
        if finalized:
            logger.bind(module="wechat_mp").error(
                "wechat_mp scheduled run 失败 tenant_id={} run_id={}", tenant_id, run_id
            )
        else:
            logger.bind(module="wechat_mp").error(
                "wechat_mp run 失败收尾未命中（owner/状态已变）run_id={}", run_id
            )

    # ==================== WP13 清单源：list_sync run 对账 ====================

    def _reconcile_list_sync_run(
        self, tenant_id: str, run: Dict[str, Any], owner: str
    ) -> Optional[Dict[str, int]]:
        """list_sync run 的对账入口（同步，asyncio.to_thread 调用，设计 §3.4）。

        三阶段同 WP9 模式：①拉清单+diff（单事务）→ ②无跨阶段网络调用
        （删除只认清单显式 is_deleted 信号，无 missing 详情复核）→ ③同事务软删
        已在 ① 内完成。失败（会话缺失/失效/账号异常/结构异常）置 run=failed 并
        返回 None——不得误报 success；session_expired→list_sync_status='expired'，
        account_error→'account_error'（设计 §3.4）。

        过期通知（设计 §3.5）：仓库现有通知设施（notification_service webhook/
        email、wecom_bot）均为平台级或按功能独立配置，无「租户管理员」寻址能力，
        v1 以状态置位 + run failed 记录 + 前端横幅兜底，站外推送登记后续。
        """
        from src.wechat_mp import list_session as list_session_mod
        from src.wechat_mp.list_source import (
            ListAccountError,
            ListFreqControlError,
            ListSessionExpiredError,
            ListSourceError,
            OwnListClient,
        )

        config_id = run.get("config_id")
        session = list_session_mod.load_list_session(config_id) if config_id else None
        if session is None:
            self._fail_run(tenant_id, run["id"], owner, "清单源会话不存在（可能已解绑），请重新扫码绑定")
            if config_id:
                list_session_mod.set_list_sync_status(config_id, "expired")
            return None

        # expiring 预警置位（best-effort）：active 且进入 expire_at-24h 窗口
        fields = session.get("fields") or {}
        if fields.get("list_sync_status") == "active" and (
            list_session_mod.effective_list_sync_status(fields) == "expiring"
        ):
            list_session_mod.set_list_sync_status(config_id, "expiring")

        mode = fields.get("list_sync_mode") or "auto_all"
        # WP13-r1（设计 §3.4）：首次回填上限 + 回填完成标记。
        # list_backfill_done=false → 首次回填：按子篇计数只取最新 N 篇（fetch 层
        # 达到上限即停止翻页，边界消息整条计入，超硬顶 500 截断）；
        # true → 增量：走到重叠即停（整页全部已知且 update_time 一致才停）。
        backfill_done = bool(fields.get("list_backfill_done"))
        max_articles = wechat_mp_codec.clamp_list_sync_max_articles(
            fields.get("list_sync_max_articles")
        )
        client = OwnListClient(token=session["token"], cookie=session["cookie"])
        try:
            if backfill_done:
                known_times = self._load_list_known_times(tenant_id)
                scan = client.fetch_sync_scan(
                    page_all_known=lambda page: self._page_all_known(known_times, page)
                )
            else:
                scan = client.fetch_sync_scan(max_articles=max_articles)
        except ListSessionExpiredError as e:
            # 会话失效：置 expired（前端横幅+徽标提示重新扫码；站外通知 v1 无设施，见上）
            self._fail_run(tenant_id, run["id"], owner, str(e))
            list_session_mod.set_list_sync_status(config_id, "expired")
            return None
        except ListAccountError as e:
            self._fail_run(tenant_id, run["id"], owner, str(e))
            list_session_mod.set_list_sync_status(config_id, "account_error")
            return None
        except (ListFreqControlError, ListSourceError) as e:
            # 频控/结构异常/网络：run=failed 不改绑定状态（下轮 tick 随周期自然重试）
            self._fail_run(tenant_id, run["id"], owner, str(e))
            return None
        except Exception as e:  # noqa: BLE001 未预期异常同样不误报 success
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 清单拉取未预期异常 tenant_id={} run_id={}: {}",
                tenant_id, run["id"], sanitize_error_info(str(e)),
            )
            self._fail_run(tenant_id, run["id"], owner, "清单拉取失败（未预期异常）")
            return None
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        try:
            total_sub, unchanged, deleted = self._reconcile_list_articles(
                tenant_id, run, scan, mode
            )
        except Exception as e:  # noqa: BLE001 diff 落库异常 → run failed
            logger.opt(exception=True).error(
                "后端日志：wechat_mp 清单对账落库失败 tenant_id={} run_id={}: {}",
                tenant_id, run["id"], sanitize_error_info(str(e)),
            )
            self._fail_run(tenant_id, run["id"], owner, "清单对账落库失败")
            return None

        # 成功推进最近同步时间（best-effort，前端「上次同步时间」展示用）；
        # 首次回填且本轮扫描 complete（翻完或达上限）才置 list_backfill_done——
        # 不完整扫描不置，下轮继续按上限回填（设计 §3.4）
        success_fields: Dict[str, Any] = {
            "list_last_sync_at": datetime.now(timezone.utc).isoformat(),
        }
        if not backfill_done and scan.complete:
            success_fields["list_backfill_done"] = True
        try:
            list_session_mod.write_list_config_fields(config_id, fields=success_fields)
        except Exception as e:  # noqa: BLE001
            logger.bind(module="wechat_mp").debug(
                "wechat_mp list_last_sync_at 写入失败（忽略）: {}", e
            )
        logger.bind(module="wechat_mp").info(
            "wechat_mp 清单对账完成 tenant_id={} run_id={} config_id={} mode={} "
            "messages={} sub_articles={} unchanged={} deleted={} backfill_done={} "
            "max_articles={} complete={}",
            tenant_id, run["id"], config_id, mode,
            scan.total_count, total_sub, unchanged, deleted,
            success_fields.get("list_backfill_done", backfill_done),
            max_articles, scan.complete,
        )
        return {
            "total_count": total_sub,
            "extra_skipped": unchanged,
            "extra_deleted": deleted,
        }

    @staticmethod
    def _list_source_time(art: Any) -> datetime:
        """清单子篇 update_time（unix 秒）→ UTC naive（对齐既有 wx_update_time 口径）。"""
        return datetime.fromtimestamp(art.update_time, tz=timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _load_list_known_times(tenant_id: str) -> Dict[str, Any]:
        """租户全部 articles 行的 external_id → wx_update_time（增量重叠判定用）。

        撞键范围=本租户全部行（不限 source_channel），与 diff 撞键口径一致：
        他通道已入库的同身份行同样算「已知」，避免增量轮永远停不下翻页。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT external_id, wx_update_time
                FROM bs_wechat_mp_articles
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            return {r["external_id"]: r["wx_update_time"] for r in cursor.fetchall()}

    def _page_all_known(self, known_times: Dict[str, Any], page: List[Any]) -> bool:
        """增量「走到重叠即停」整页判定（设计 §3.4）：整页子篇全部已知且
        update_time 与库内一致才返回 True。

        任何一条不满足（身份未知 / 链接不可规范 / 时间缺失或不一致）→ False
        继续翻页——早停漏拉由下一轮对账兜底，宁多翻不漏新。
        """
        for art in page:
            try:
                external_id = normalize_url(art.link).external_id
            except URLIdentityError:
                return False
            row_time = known_times.get(external_id)
            if row_time is None:
                return False
            if isinstance(row_time, datetime) and row_time.tzinfo is not None:
                row_time = row_time.replace(tzinfo=None)  # 防 timestamptz 形态不一致
            if row_time != self._list_source_time(art):
                return False
        return True

    def _reconcile_list_articles(
        self,
        tenant_id: str,
        run: Dict[str, Any],
        scan: Any,
        mode: str,
    ) -> Tuple[int, int, int]:
        """清单子篇 diff（单事务，设计 §3.4）。

        - 撞键范围=本租户全部文章行（不限 source_channel）：跨通道天然去重——
          manual/callback/freepublish 通道已入库的文章不再重复建行/重拉
        - 新行：auto_all 建 articles 行（pending）+ pending item；manual 仅建行
          processing_status='pending_manual'（不建 item 不计费不入库，待勾选）
        - list 行 update_time 变更 / sync_failed / pipeline 变更 → 建 item
          （活跃 item 门禁同 WP9）；未变仅推进 last_synced_at
        - manual 行仍在 pending_manual（未被勾选）：源时间变化仅刷新 wx_update_time，
          不入队（未获用户授权不入库）
        - 删除：清单 is_deleted=true 是源侧明确信号（管理员在公众号后台删除），
          直接软删 articles 行 + documents——优先级高于 URL 通道多信号推断
          （设计 §3.4 定版）；alias 行不软删（无独立正文，主记录另行处理）

        返回 (本轮源子篇数, 未变跳过数, 软删数)。
        """
        from src.wechat_mp.list_source import OwnArticle

        unchanged = 0
        deleted_count = 0
        manual_mode = mode == "manual"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id, external_id, source_channel, wx_update_time, status,
                           processing_status, pipeline_version, doc_id
                    FROM bs_wechat_mp_articles
                    WHERE tenant_id = %s
                    """,
                    (tenant_id,),
                )
                existing = {r["external_id"]: dict(r) for r in cursor.fetchall()}

                for art in scan.articles:
                    if not isinstance(art, OwnArticle):
                        raise RuntimeError("清单子篇类型异常")
                    try:
                        identity = normalize_url(art.link)
                    except URLIdentityError:
                        # 单条链接非法（非 mp 域/参数缺失）：跳过该条不致命（不误报
                        # success 的主体是拉取失败与结构异常），记日志留审计
                        logger.bind(module="wechat_mp").warning(
                            "wechat_mp 清单子篇链接不可规范（跳过）tenant_id={} run_id={}",
                            tenant_id, run["id"],
                        )
                        continue
                    row = existing.get(identity.external_id)

                    # ---- 源侧显式删除信号：直接软删（任何通道的对应文档）----
                    if art.is_deleted:
                        if row and row["status"] not in ("deleted", "alias"):
                            self._soft_delete_list_article(cursor, tenant_id, row)
                            deleted_count += 1
                        continue

                    if row is None:
                        article_row_id = self._insert_list_article(
                            cursor, tenant_id, run, identity, art, manual_mode
                        )
                        if not manual_mode:
                            self._insert_pending_item(cursor, tenant_id, run, article_row_id)
                        else:
                            # manual 新行：无 item，计入 skipped（待用户挑选，非失败）
                            unchanged += 1
                        continue

                    if row["status"] == "alias":
                        unchanged += 1  # 别名行由主记录管理
                        continue
                    if row["status"] == "deleted":
                        # 删除行重现且 is_deleted=false：仅源时间变化（可能重新发布）才恢复
                        source_time = self._list_source_time(art)
                        if (
                            row["wx_update_time"] is not None
                            and row["wx_update_time"] != source_time
                        ):
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles SET status = 'active'
                                WHERE id = %s AND tenant_id = %s AND status = 'deleted'
                                """,
                                (row["id"], tenant_id),
                            )
                            self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                        else:
                            unchanged += 1
                        continue
                    if row["status"] == "missing":
                        # 重现恢复（对齐 WP9 语义）：置回 active 并按变更建 item
                        cursor.execute(
                            """
                            UPDATE bs_wechat_mp_articles SET status = 'active'
                            WHERE id = %s AND tenant_id = %s AND status = 'missing'
                            """,
                            (row["id"], tenant_id),
                        )
                        self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                        continue

                    if row["source_channel"] != LIST_CHANNEL:
                        # 他通道行：增量归各自通道管理，清单仅提供删除信号（上方）。
                        # 跨通道去重=不重复建行不重复拉取（设计 §3.4「记录位置语义
                        # 与既有通道完全一致」）
                        unchanged += 1
                        continue

                    source_time = self._list_source_time(art)
                    if row["processing_status"] == PENDING_MANUAL:
                        # manual 模式未勾选文章：源更新仅刷新基准时间，保持待挑选
                        if row["wx_update_time"] is None or row["wx_update_time"] != source_time:
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles
                                SET wx_update_time = %s, last_synced_at = now()
                                WHERE id = %s AND tenant_id = %s
                                """,
                                (source_time, row["id"], tenant_id),
                            )
                        unchanged += 1
                        continue

                    need_refresh = (
                        row["wx_update_time"] is None
                        or row["wx_update_time"] != source_time
                        or row["processing_status"] == "sync_failed"
                        or (row["pipeline_version"] or "") != PIPELINE_VERSION
                    )
                    if need_refresh:
                        # 源时间推进在 diff 阶段落库（URL 通道不走 freepublish 的
                        # fp_ctx 回填路径，若等 item 处理成功再推进会导致每轮对账
                        # 都因时间落后重复建 item）
                        if row["wx_update_time"] != source_time:
                            cursor.execute(
                                """
                                UPDATE bs_wechat_mp_articles
                                SET wx_update_time = %s
                                WHERE id = %s AND tenant_id = %s
                                """,
                                (source_time, row["id"], tenant_id),
                            )
                        self._insert_item_if_idle(cursor, tenant_id, run, row["id"])
                    else:
                        unchanged += 1
                        cursor.execute(
                            """
                            UPDATE bs_wechat_mp_articles SET last_synced_at = now()
                            WHERE id = %s AND tenant_id = %s
                            """,
                            (row["id"], tenant_id),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(scan.articles), unchanged, deleted_count

    @staticmethod
    def _insert_list_article(
        cursor,
        tenant_id: str,
        run: Dict[str, Any],
        identity: URLIdentity,
        art: Any,
        manual_mode: bool,
    ) -> int:
        """按清单子篇建 articles 行（元数据存 tags JSONB，入库时并入 documents.metadata）。

        - source_channel='list'；original_url=短链原文，fetch_url=规范抓取链
        - wx_update_time=清单 update_time（增量对账基准）；publish_time 取 create_time
        - manual 模式落 pending_manual（不建 item），auto_all 落 pending
        """
        publish_time = (
            datetime.fromtimestamp(art.create_time, tz=timezone.utc).replace(tzinfo=None)
            if art.create_time
            else None
        )
        wx_update_time = (
            datetime.fromtimestamp(art.update_time, tz=timezone.utc).replace(tzinfo=None)
        )
        title = sanitize_text(art.title, source="wechat_mp_ingest")[:255] if art.title else None
        list_meta = {
            "aid": art.aid or None,
            "msgid": art.msgid,
            "itemidx": art.itemidx,
            "publish_type": art.publish_type,
        }
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, config_id, external_id, original_url, fetch_url,
                 source_channel, title, publish_time, wx_update_time, tags,
                 status, processing_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            ON CONFLICT (tenant_id, external_id) DO NOTHING
            RETURNING id
            """,
            (
                tenant_id, run.get("config_id"), identity.external_id, art.link,
                identity.fetch_url, LIST_CHANNEL, title, publish_time, wx_update_time,
                json.dumps(list_meta, ensure_ascii=False),
                PENDING_MANUAL if manual_mode else "pending",
            ),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
            (tenant_id, identity.external_id),
        )
        return cursor.fetchone()["id"]

    @staticmethod
    def _soft_delete_list_article(cursor, tenant_id: str, row: Dict[str, Any]) -> None:
        """清单源侧删除信号（is_deleted=true）：软删 documents + articles 行（事务内）。

        语义（设计 §3.4 定版）：清单 is_deleted 是管理员在公众号后台删除的**明确
        信号**，优先级高于 URL 通道的多信号推断——不再走 missing 两轮防护。
        """
        if row.get("doc_id"):
            cursor.execute(
                """
                UPDATE documents SET status = 'deleted', updated_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (row["doc_id"], tenant_id),
            )
        cursor.execute(
            """
            UPDATE bs_wechat_mp_articles
            SET status = 'deleted', processing_status = 'success',
                last_checked_at = now(), next_retry_at = NULL, error_message = NULL
            WHERE id = %s AND tenant_id = %s AND status <> 'deleted'
            """,
            (row["id"], tenant_id),
        )

    # ==================== WP13 清单源：模式切换与勾选入队 ====================

    def _get_list_bound_config_id(self, tenant_id: str) -> Optional[str]:
        from src.wechat_mp.list_session import find_bound_list_config

        bound = find_bound_list_config(tenant_id)
        return bound["config_id"] if bound else None

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

        # ---- 1. 取正文：URL 通道先抓页面（受理只有 URL，不存在抓取前按 hash 跳过）；
        # freepublish 通道（WP9）走 getarticle 详情 + 多图文合并，不走 URL fetcher ----
        fp_ctx: Optional[Dict[str, Any]] = None
        if article["source_channel"] == FREEPUBLISH_CHANNEL:
            prepared = await self._fetch_freepublish_article(tenant_id, run, item, article)
        else:
            prepared = await self._fetch_url_article(tenant_id, run, item, article)
        if prepared is None:
            return  # item 已置终态（删除软删 / 失败保留旧版 / skipped）
        extracted, fp_ctx = prepared

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
                if fp_ctx:
                    # freepublish：check 快路径同样算处理成功，刷新源更新时间——
                    # 否则 wx_update_time 恒落后于源，每轮对账都会重复建 item 拉 getarticle
                    self._refresh_wx_update_time(
                        tenant_id, article["id"], fp_ctx.get("wx_update_time")
                    )
                return
            if doc_state == "deleted":
                # 重现且 hash 未变：复用旧 chunks 恢复 active（设计 §5.2）
                self._handle_restore(tenant_id, item, article, extracted, title)
                return
            # doc 行不存在 → 继续重建

        # ---- 6. 图片解析（WP13 门禁放开：有图即下载→VL；WP13-r2 移除单篇 30 张
        # 产品上限，下载侧保留 200 张防失控硬护栏）----
        # 「文字 < 20 且有图」门禁移除（负责人定稿：公众号文章基本是图文，图片
        # 内容与文本同等重要，必须进总结）；无多模态模型不再 deferred——有文本
        # 照常入库，图片行保留地址、描述留空（metadata 记缺失原因）；仅纯图且
        # 无任何可总结内容维持 deferred（无可总结内容）
        image_billing_successes: List[Any] = []
        image_meta: Dict[str, Any] = {}
        image_descriptions: Dict[int, str] = {}
        if extracted.image_count > 0:
            parse_result = await self._parse_images_or_defer(
                tenant_id, item, article, extracted
            )
            if parse_result is None:
                return  # 已置 deferred（纯图无可总结内容）或 no_credit
            body_text = parse_result["merged_text"]
            image_meta = parse_result["image_meta"]
            image_descriptions = parse_result["descriptions"]
            image_billing_successes = parse_result["successes"]

        # ---- 7. 付费单元前余额复查 ----
        balance_ok, _ = self._check_credit(tenant_id)
        if not balance_ok:
            self._mark_item_no_credit(tenant_id, item)
            self._mark_article_no_credit_retry(tenant_id, article["id"])
            return

        # ---- 8. 组装 content_md + 核心要点总结（WP12/WP13）----
        # 按节点顺序忠实组装 Markdown（# 标题 + 文本段落 + ![VL描述](CDN地址)，
        # VL 失败/超限/无模型的图片 alt 用「图片N」），存 metadata.content_md 并
        # 作为总结输入（图片转述与文本同等参与总结）；两次尝试均失败回退 content_md
        # 原文入库（不丢数据），形态记 metadata.content_mode
        doc_title = f"[公众号] {title}"[:255]
        original_url = article["original_url"]
        content_md = build_markdown(title, extracted.nodes, image_descriptions)
        summary_result = await self._summarize_article(content_md, title)
        content_mode = CONTENT_MODE_RAW_FALLBACK
        summary_column: Optional[str] = None
        if summary_result is not None:
            summary_text, summary_model, summary_usage = summary_result
            doc_content = summary_text
            summary_column = summary_text
            content_mode = CONTENT_MODE_SUMMARY
        else:
            # 回退：content_md 即文档正文（已是忠实内容，回退语义不变）；总结列沿用截断口径
            doc_content = content_md
            summary_column = content_md[:SUMMARY_MAX_CHARS] if content_md else None
            summary_model = None
            summary_usage = None

        # ---- 9. 分块 → embedding（事务外）----
        # chunk 文本 = 总结（或回退正文）本身；不加「文档标题：」标签前缀，
        # 也不拼原文链接（WP12 定版：链接存 documents.file_path 即文档位置字段，
        # 避免链接挤占/独占 chunk、向量化噪声；检索与详情可见性见 knowledge api）
        chunks = self._chunker.chunk(doc_content)
        if not chunks:
            self._mark_item_skipped(tenant_id, item_id, ERR_CONTENT_EMPTY, "分块结果为空")
            return
        embedding_client = self._get_embedding_client()
        embedding_client.reset_usage()
        embeddings = await embedding_client.embed_batch([c["text"] for c in chunks])
        embedding_tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)

        # ---- 10. 单事务落库（documents/chunks/chunks_vec/articles/item）----
        action = "new" if not article["doc_id"] else "update"
        doc_id = await self._persist_document_tx(
            tenant_id=tenant_id,
            run=run,
            item=item,
            article=article,
            extracted=extracted,
            doc_title=doc_title,
            body_text=body_text,
            summary_text=summary_column,
            content_mode=content_mode,
            content_hash=content_hash,
            chunks=chunks,
            embeddings=embeddings,
            action=action,
            image_meta=image_meta or None,
            content_md=content_md,
            location_url=(fp_ctx or {}).get("location_url"),
            wx_update_time=(fp_ctx or {}).get("wx_update_time"),
            extra_metadata=(
                {"sub_articles": fp_ctx["sub_articles"]} if fp_ctx else None
            ),
        )

        # ---- 11. 售前挂接 + 计费（业务已提交，fail-open）----
        self._attach_presales_and_record(tenant_id, doc_id)
        self._bill_embedding(
            tenant_id=tenant_id,
            user_id=item.get("user_id") or run.get("user_id"),
            doc_id=doc_id,
            title=doc_title,
            embedding_tokens=embedding_tokens,
            item_id=item_id,
        )
        if summary_usage:
            # 总结调用计费（record_background_llm_usage 独立落账，fail-open）；
            # 与 embedding/图片计费同序——业务提交后再落账，落库失败不再白扣
            # 总结费（WP12 遗留 A1，p4 存量重建放大暴露面，2026-09-16 移序）
            self._bill_summary(
                tenant_id=tenant_id,
                user_id=item.get("user_id") or run.get("user_id"),
                usage=summary_usage,
                model=summary_model,
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
        if fp_ctx:
            # ---- 12. 跨来源重叠互标（设计 §5.4，fail-open，不物理合并）----
            try:
                await asyncio.to_thread(
                    self._link_related_docs,
                    tenant_id,
                    doc_id,
                    article["id"],
                    fp_ctx.get("sub_urls") or [],
                )
            except Exception as e:  # noqa: BLE001 互标失败不影响已入库文档
                logger.opt(exception=True).warning(
                    "后端日志：wechat_mp 跨来源互标失败 tenant_id={} doc_id={}: {}",
                    tenant_id, doc_id, sanitize_error_info(str(e)),
                )

    # ==================== 正文获取分支（URL 直采 / freepublish 接口） ====================

    async def _fetch_url_article(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any], article: Dict[str, Any]
    ) -> Optional[Tuple[ExtractedArticle, None]]:
        """URL 通道：抓页面 → 删除多信号 → 提取 → 别名收敛（既有管道原样搬运）。

        item 已置终态时返回 None；正常返回 (extracted, None)（无 fp_ctx）。
        """
        fetch: FetchResult = await asyncio.to_thread(
            self._fetcher.fetch, article["fetch_url"] or article["original_url"]
        )

        # 删除多信号判定（免费，不受余额阻断）
        if fetch.status == STATUS_DELETED:
            self._handle_deleted(tenant_id, item, article)
            return None
        if fetch.status != STATUS_OK:
            code = ERR_RISK_BLOCKED if fetch.status == "risk_blocked" else ERR_FETCH_FAILED
            self._mark_item_failed(
                tenant_id, item, code, f"抓取未成功: {fetch.error or fetch.status}"
            )
            self._mark_article_retry(
                tenant_id, article["id"], f"fetch:{fetch.error or fetch.status}"
            )
            return None

        # 正文提取
        try:
            extracted = extract_article(fetch.html or "")
        except ContentExtractionError as e:
            msg = str(e)
            if "内容为空" in msg:
                # 空内容跳过并记原因（非技术失败，不进退避）
                self._mark_item_skipped(tenant_id, item["id"], ERR_CONTENT_EMPTY, msg)
                self._touch_article_checked(tenant_id, article["id"], processing_status=None)
            else:
                self._mark_item_failed(tenant_id, item, ERR_EXTRACT_FAILED, msg)
                self._mark_article_retry(tenant_id, article["id"], f"extract:{type(e).__name__}")
            return None

        # 别名收敛（保守：仅页面给出对方形态且本租户已有该行时）
        if self._converge_alias(tenant_id, run["id"], item, article, extracted.alias):
            return None  # 当前 item 已被收敛为 skipped
        return extracted, None

    async def _fetch_freepublish_article(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any], article: Dict[str, Any]
    ) -> Optional[Tuple[ExtractedArticle, Dict[str, Any]]]:
        """freepublish 通道（WP9）：getarticle 详情 → 多图文合并（设计 §6.1 P3 粒度）。

        - 排除 is_deleted=true 的子篇；未删子篇按返回顺序拼「子篇标题 → 正文」
        - 一条消息合并一篇文档（external_id 已定身份），alias=None 跳过别名收敛
        - 删除语义：news_item 空数组/字段缺失/类型异常 → item 失败保留旧版（绝不判
          删除）；全部子篇明确 is_deleted=true → 复用 _handle_deleted 软删整篇
        - 返回 (merged ExtractedArticle, fp_ctx)；item 已置终态返回 None。
          fp_ctx: article_id / wx_update_time(UTC naive) / location_url（首个未删
          子篇 url）/ sub_articles（全子篇 顺序/标题/url/规范身份/删除标记）/
          sub_urls（未删子篇 url 列表，跨来源互标用）
        """
        article_id = parse_freepublish_article_id(article["external_id"])
        if article_id is None:
            self._mark_item_failed(tenant_id, item, ERR_INTERNAL, "freepublish 身份解析失败")
            return None
        config = self._load_mp_config(tenant_id, article.get("config_id"))
        if config is None:
            self._mark_item_failed(
                tenant_id, item, ERR_FETCH_FAILED, "接口通道配置不可用（缺 AppID/AppSecret）"
            )
            self._mark_article_retry(tenant_id, article["id"], "freepublish:config_missing")
            return None
        client = self._build_api_client(tenant_id, article.get("config_id"), config)
        try:
            detail = await asyncio.to_thread(client.getarticle, article_id)
        except WeChatMPAPIError as e:
            # 53600 亦按失败保留旧版：整条删除唯一入口是对账缺失复核（双重确认）
            self._mark_item_failed(tenant_id, item, ERR_FETCH_FAILED, friendly_api_error(e))
            self._mark_article_retry(tenant_id, article["id"], f"freepublish:{e.errcode}")
            return None
        except Exception as e:  # noqa: BLE001
            self._mark_item_failed(tenant_id, item, ERR_INTERNAL, str(e))
            self._mark_article_retry(tenant_id, article["id"], "freepublish:internal")
            return None
        finally:
            # 客户端自持 httpx 连接池：单 item 即用即弃，及时释放（成功/失败路径均覆盖；
            # getattr 兼容测试注入的无 close 替身）
            close = getattr(client, "close", None)
            if callable(close):
                close()

        news_item = detail.get("news_item")
        if not isinstance(news_item, list) or not news_item:
            # 空数组/字段缺失/类型异常 ≠ 删除：失败保留旧版（设计 §5.2.4）
            self._mark_item_failed(
                tenant_id, item, ERR_FETCH_FAILED,
                "getarticle 返回 news_item 为空或结构异常，保留旧版本",
            )
            self._mark_article_retry(tenant_id, article["id"], "freepublish:news_item_empty")
            return None

        merged_nodes: List[ContentNode] = []
        sub_meta: List[Dict[str, Any]] = []
        sub_urls: List[str] = []
        first_title: Optional[str] = None
        first_author: Optional[str] = None
        for idx, sub in enumerate(news_item):
            if not isinstance(sub, dict):
                self._mark_item_failed(
                    tenant_id, item, ERR_FETCH_FAILED,
                    f"news_item[{idx}] 结构异常（非对象），保留旧版本",
                )
                self._mark_article_retry(tenant_id, article["id"], "freepublish:sub_structure")
                return None
            deleted_flag = is_truthy_flag(sub.get("is_deleted"))
            title = sanitize_text(str(sub.get("title")), source="wechat_mp_ingest") if sub.get("title") else None
            url = sub.get("url") if isinstance(sub.get("url"), str) and sub.get("url") else None
            alias_identity: Optional[URLIdentity] = None
            if url:
                try:
                    alias_identity = normalize_url(url)
                except URLIdentityError:
                    alias_identity = None  # url 不可规范：保留原值，互标阶段自然跳过
            sub_meta.append(
                {
                    "order": idx,
                    "title": title,
                    "url": url,
                    "external_id": alias_identity.external_id if alias_identity else None,
                    "is_deleted": deleted_flag,
                }
            )
            if deleted_flag:
                continue  # 已删子篇不参与合并（设计 §5.2.4）
            content_html = sub.get("content")
            if not isinstance(content_html, str) or not content_html.strip():
                # 未删子篇正文缺失属结构异常：整条失败保留旧版，绝不按删除处理
                self._mark_item_failed(
                    tenant_id, item, ERR_EXTRACT_FAILED,
                    f"news_item[{idx}] 未删除子篇正文缺失（结构异常），保留旧版本",
                )
                self._mark_article_retry(tenant_id, article["id"], "freepublish:sub_content_missing")
                return None
            try:
                sub_extracted = extract_article(_wrap_freepublish_content(content_html))
            except ContentExtractionError as e:
                self._mark_item_failed(
                    tenant_id, item, ERR_EXTRACT_FAILED,
                    f"news_item[{idx}] 子篇正文提取失败：{e}",
                )
                self._mark_article_retry(tenant_id, article["id"], "freepublish:extract")
                return None
            if first_title is None:
                first_title = title
                first_author = (
                    sanitize_text(str(sub.get("author")), source="wechat_mp_ingest") if sub.get("author") else None
                )
            # 前置子篇标题 text 节点（保序合并，跨子篇可检索边界）
            if title:
                merged_nodes.append(ContentNode(type="text", text=title))
            merged_nodes.extend(sub_extracted.nodes)
            if url:
                sub_urls.append(url)

        if not merged_nodes:
            # 全部子篇明确 is_deleted=true → 软删除整篇（复用既有删除落库语义）
            self._handle_deleted(tenant_id, item, article)
            return None

        create_time = detail.get("create_time")
        update_time = detail.get("update_time")
        wx_dt = _parse_unix_seconds(update_time if isinstance(update_time, int) else None)
        extracted = ExtractedArticle(
            title=first_title,
            account_name=first_author,
            publish_time=_parse_unix_seconds(create_time if isinstance(create_time, int) else None),
            nodes=merged_nodes,
            alias=None,  # 接口通道身份按 article_id 定稿，不做 URL 形态别名收敛
        )
        fp_ctx = {
            "article_id": article_id,
            "wx_update_time": wx_dt.astimezone(timezone.utc).replace(tzinfo=None) if wx_dt else None,
            "location_url": sub_urls[0] if sub_urls else article["original_url"],
            "sub_articles": sub_meta,
            "sub_urls": sub_urls,
        }
        return extracted, fp_ctx

    def _refresh_wx_update_time(
        self, tenant_id: str, article_row_id: int, wx_update_time: Optional[datetime]
    ) -> None:
        """freepublish 处理成功（含 check 快路径）后推进源更新时间与 last_synced_at。"""
        if wx_update_time is None:
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_wechat_mp_articles
                SET wx_update_time = %s, last_synced_at = now(), last_checked_at = now()
                WHERE id = %s AND tenant_id = %s
                """,
                (wx_update_time, article_row_id, tenant_id),
            )
            conn.commit()

    def _link_related_docs(
        self,
        tenant_id: str,
        doc_id: int,
        article_row_id: int,
        sub_urls: List[str],
    ) -> None:
        """跨来源重叠互标（设计 §5.4）：子篇 url 规范身份命中本租户已有文档时，
        双方 documents.metadata.related_doc_ids 互标（fail-open，不物理合并）。"""
        matched_doc_ids: set = set()
        for url in sub_urls or []:
            try:
                identity = normalize_url(url)
            except URLIdentityError:
                continue
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT a.doc_id FROM bs_wechat_mp_articles a
                        WHERE a.tenant_id = %s AND a.external_id = %s
                          AND a.doc_id IS NOT NULL AND a.id <> %s
                        """,
                        (tenant_id, identity.external_id, article_row_id),
                    )
                    row = cursor.fetchone()
                    if row and row["doc_id"] and row["doc_id"] != doc_id:
                        matched_doc_ids.add(int(row["doc_id"]))
            except Exception as e:  # noqa: BLE001 单条查询失败不中断互标
                logger.opt(exception=True).warning(
                    "后端日志：wechat_mp 跨来源查询失败 tenant_id={}: {}",
                    tenant_id, sanitize_error_info(str(e)),
                )
        if not matched_doc_ids:
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for other_doc_id in sorted(matched_doc_ids):
                # 当前文档 ← 对方
                self._append_related_doc_id(cursor, tenant_id, doc_id, other_doc_id)
                # 对方 ← 当前文档（互标）
                self._append_related_doc_id(cursor, tenant_id, other_doc_id, doc_id)
            conn.commit()
        logger.bind(module="wechat_mp").info(
            "wechat_mp 跨来源互标 tenant_id={} doc_id={} related={}",
            tenant_id, doc_id, sorted(matched_doc_ids),
        )

    @staticmethod
    def _append_related_doc_id(cursor, tenant_id: str, doc_id: int, related_doc_id: int) -> None:
        """documents.metadata.related_doc_ids 追加（读-改-写，幂等去重；异常上抛由调用方兜底）。"""
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
        related = metadata.get("related_doc_ids")
        if not isinstance(related, list):
            related = []
        if related_doc_id in related:
            return
        related.append(related_doc_id)
        metadata["related_doc_ids"] = related
        cursor.execute(
            "UPDATE documents SET metadata = %s, updated_at = now() WHERE id = %s AND tenant_id = %s",
            (json.dumps(metadata, ensure_ascii=False), doc_id, tenant_id),
        )

    # ==================== 图片 VL 解析（WP10，P2） ====================

    async def _parse_images_or_defer(
        self,
        tenant_id: str,
        item: Dict[str, Any],
        article: Dict[str, Any],
        extracted: ExtractedArticle,
    ) -> Optional[Dict[str, Any]]:
        """图片下载 + VL 解析（WP13 门禁放开：有图即解析；WP13-r2 移除单篇 30 张
        产品上限，单篇仅保留下载侧 200 张防失控硬护栏）。

        返回 dict：
        - merged_text：[图片N: 描述] 插回后的 merged 纯文本（raw_text 审计口径）
        - descriptions：{n: cleaned 描述}（content_md 的 alt 注释来源）
        - successes：VL 成功清单（按张按 token 计费）
        - image_meta：image_parsed_count / image_failed_count / image_skipped_count
          （+ image_local_paths；无模型时另记 image_parse_skipped_reason='no_model'）；
          image_parsed_count 由 _persist_document_tx 在成功路径回写 articles 列；
          skipped 只会来自 200 防失控护栏（非产品限制）

        返回 None 表示 item 已置终态，调用方直接返回：
        - 余额不足 → 复用 no_credit 语义（付费单元=按张 VL）
        - 纯图（剔除占位后无任何文本）且无任何描述（无模型/下载解析全失败）→
          deferred（无可总结内容，退避重试机制自然接管）；有文本不再 deferred
        """
        vision = self._get_vision_parser()
        has_model = vision.available()

        # 纯图判定：占位符必须整段剔除（只删 "[图片" 前缀会残留 "N]"，图片多时
        # 残留字符累积会误判为有文本——CR P1 修复口径保留）
        plain_text = _IMAGE_PLACEHOLDER_RE.sub("", nodes_to_text(extracted.nodes)).strip()

        if not has_model:
            if not plain_text:
                # 纯图且无文本且无模型：无可总结内容 → 维持 deferred（自动重试）
                self._mark_item_deferred(tenant_id, item["id"], _DEFERRED_NO_MODEL)
                self._mark_article_deferred(
                    tenant_id, article["id"], extracted, _DEFERRED_NO_MODEL
                )
                return None
            # WP13：有文本 → 照常入库，图片行保留地址、描述留空（不下载不解析）
            return {
                "merged_text": nodes_to_text(extracted.nodes),
                "descriptions": {},
                "successes": [],
                "image_meta": {
                    "image_parsed_count": 0,
                    "image_failed_count": 0,
                    "image_skipped_count": 0,
                    "image_parse_skipped_reason": "no_model",
                },
            }

        # 文章级余额预检：不足整篇跳过解析（免费动作不预扣，只拦截付费单元）
        ok, _ = self._check_credit(tenant_id)
        if not ok:
            self._mark_item_no_credit(tenant_id, item)
            self._mark_article_no_credit_retry(tenant_id, article["id"])
            return None

        # 下载转存（同步 httpx + Pillow，线程内执行；逐张独立不中断）
        # 编号必须与 build_markdown/nodes_to_text 的图片序数一致（第 N 个有 src 的
        # image 节点即图片 N）——用全节点索引会在混排（短文字+图）文章中错位：
        # 描述张冠李戴、跨图丢描述（WP10 测试期修复）
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

        if not outcome.descriptions and not plain_text:
            # 纯图且全部失败：无可总结内容 → deferred（有文本则照常入库）
            self._mark_item_deferred(tenant_id, item["id"], _DEFERRED_PARSE_FAILED)
            self._mark_article_deferred(
                tenant_id, article["id"], extracted, _DEFERRED_PARSE_FAILED
            )
            return None

        image_meta: Dict[str, Any] = {
            "image_parsed_count": len(outcome.successes),
            # 解析失败张 + 下载失败张（均无描述，不产生计费）
            "image_failed_count": len(outcome.failures) + len(dl.failures),
            # WP13-r2：skipped 只会来自 200 防失控护栏（超出部分未发起请求）
            "image_skipped_count": dl.skipped_over_limit,
        }
        if dl.images:
            image_meta["image_local_paths"] = [img.local_path for img in dl.images]
        return {
            # [图片N: 描述] 插回原文位置；失败图片保留 [图片N] 占位照常入库
            "merged_text": nodes_to_text(
                self._merge_image_descriptions(extracted.nodes, outcome.descriptions)
            ),
            "descriptions": outcome.descriptions,
            "successes": list(outcome.successes),
            "image_meta": image_meta,
        }

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

    # ==================== 核心要点总结（WP12，p3；WP13 输入改 content_md） ====================

    async def _summarize_article(
        self, merged_text: str, title: str
    ) -> Optional[Tuple[str, str, Dict[str, int]]]:
        """生成 ≤500 字核心要点总结（WP12；WP13 起输入为 content_md 全文）。

        返回 (summary, model_name, usage)；输入为空或两次尝试均失败返回 None，
        调用方回退 content_md 原文入库（不丢数据）。总结器测试可注入，默认
        ArticleSummarizer（LLMGateway() 主 provider 默认文本模型，不 pin VL 模型）。
        """
        if not (merged_text or "").strip():
            return None  # 空内容无从总结（也无谓计费），直接走回退口径
        return await self._get_summarizer().summarize(merged_text, title)

    @staticmethod
    def _bill_summary(
        *,
        tenant_id: str,
        user_id: Optional[str],
        usage: Dict[str, int],
        model: str,
    ) -> None:
        """总结 LLM 调用计费（record_background_llm_usage，fail-open，WP12）。

        worker 线程无 SessionRecord，函数内自动降级独立 ChatRecordDB.create 落账
        （source='wechat_mp_summary' 拼进 session_id 供追溯）；空 usage 直接返回、
        落账异常只告警，均不阻断入库（对齐既有 fail-open 口径）。
        """
        if not usage:
            return
        try:
            from src.services.session_record import record_background_llm_usage

            record_background_llm_usage(
                usage,
                tenant_id=tenant_id,
                user_id=str(user_id) if user_id is not None else None,
                source=SUMMARY_SOURCE_TYPE,
                user_message="公众号文章总结",
                model=model,
            )
        except Exception as e:  # noqa: BLE001 计费失败不回滚内容
            logger.opt(exception=True).warning(
                "后端日志：wechat_mp 总结计费异常（忽略）tenant_id={}: {}",
                tenant_id, sanitize_error_info(str(e)),
            )

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
        summary_text: Optional[str],
        content_mode: str,
        content_hash: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        action: str,
        image_meta: Optional[Dict[str, Any]] = None,
        content_md: str = "",
        location_url: Optional[str] = None,
        wx_update_time: Optional[datetime] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """单事务落 documents/chunks/chunks_vec + articles + item（+惰性分类）。

        WP12 口径：body_text = merged 全文 → documents.raw_text（审计）；
        summary_text = LLM 总结（或回退截断）→ documents.summary；
        content_mode（'summary'|'raw_fallback'）记 metadata.content_mode，
        回退时另记 metadata.summary_fallback=true。
        chunk 文本由调用方以总结（或回退正文）预先生成（无「文档标题：」前缀）；
        file_path = 原文链接（文档位置字段）。

        WP13 增补（图文 Markdown 化）：content_md 存 metadata.content_md（# 标题 +
        文本段落 + ![VL描述](CDN地址)）；metadata.ingested_at = 入库时间（UTC ISO）；
        image_meta 携带 image_parsed_count / image_failed_count / image_skipped_count，
        其中 image_parsed_count 同时回写 articles 列（WP13-r2 补齐成功路径落库）。

        WP9 freepublish 可选参数（URL 通道不传即维持既有行为）：
        - location_url：文档位置字段与 articles.original_url 回填值（首个未删子篇 url）
        - wx_update_time：处理成功时推进源更新时间（增量去重基准）
        - extra_metadata：合并进 documents.metadata（sub_articles 子篇清单）

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
            # WP12：正文形态（总结版 / 总结失败回退原文版）
            "content_mode": content_mode,
            # WP13：图文 Markdown（# 标题 + 文本段落 + ![VL描述](CDN地址)）与入库时间
            "content_md": content_md,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        if content_mode == CONTENT_MODE_RAW_FALLBACK:
            metadata["summary_fallback"] = True
        if image_meta:
            # WP10/WP13：图片转存路径 / 解析计数（parsed/failed/skipped）/ 无模型
            # 缺失原因（供前端详情与对账）
            metadata.update(image_meta)
        if extra_metadata:
            # WP9：freepublish 子篇清单（顺序/标题/url/规范身份/删除标记）
            metadata.update(extra_metadata)
        if article.get("source_channel") == LIST_CHANNEL and article.get("tags"):
            # WP13：清单源元数据（建行时存 articles.tags JSONB）并入 documents.metadata
            # 供审计（aid/msgid/itemidx/publish_type）；psycopg2 已把 JSONB 解为 dict
            list_meta = article["tags"]
            if isinstance(list_meta, str):
                try:
                    list_meta = json.loads(list_meta)
                except (json.JSONDecodeError, TypeError):
                    list_meta = None
            if isinstance(list_meta, dict):
                metadata["list_source"] = list_meta
        # 文档位置（WP12：URL 即外部文档位置字段）；freepublish 回填首个未删子篇 url
        doc_location_url = location_url or article["original_url"]
        # wx_update_time（WP9 freepublish 增量基准）：None 时 COALESCE 保留现值
        original_url_write = location_url
        summary = summary_text
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
                            # 文档位置 = 原文链接（WP12 定版：URL 即外部文档的"位置"，
                            # 与手动上传文档的本地路径同字段；下载边界按 origin 拦截，
                            # 不会把 URL 当本地文件打开）。freepublish 为首个未删子篇 url
                            doc_location_url,
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
                            file_path = %s,
                            status = 'active', expires_at = NULL, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (
                            doc_title, raw_text, summary,
                            json.dumps(metadata, ensure_ascii=False),
                            len(chunks), EMBEDDING_MODEL,
                            CATEGORY_SOURCE_TYPE, SUB_CATEGORY_SOURCE_TYPE,
                            # 重建场景同步位置字段（存量行可能为 NULL）
                            doc_location_url,
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
                        image_parsed_count = %s,
                        original_url = COALESCE(%s, original_url),
                        wx_update_time = COALESCE(%s, wx_update_time),
                        last_synced_at = now(), last_checked_at = now(),
                        next_retry_at = NULL, error_message = NULL
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (
                        extracted.title or article["title"],
                        publish_time_naive,
                        content_hash, doc_id, PIPELINE_VERSION,
                        extracted.image_count,
                        # WP13-r2：解析计数回写文章列（口径=metadata.image_parsed_count；
                        # 无图/无模型场景 image_meta 为空或缺省 → 0）
                        (image_meta or {}).get("image_parsed_count", 0),
                        # WP9：freepublish 回填首个未删子篇 url 与源更新时间；URL 通道传 None 保持现值
                        original_url_write, wx_update_time,
                        article["id"], tenant_id,
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

    # ==================== 图片 VL 按张计费（WP13：按实际 token，标准算价路径） ====================

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

        WP13 起废弃 price_per_call 固定价（价目种子行保留不用）：每成功 1 张按该张
        实际 usage × 所用模型单价走标准 text 算价路径（src/services/billing.py
        calculate_credit_cost，模型无价目行时按 BILLING_FALLBACK_MODEL 兜底）；
        每张一条 chat_records(source_type='wechat_mp_image_parse')，
        usage_breakdown 记 billing_mode='token' 与实际模型供对账。
        - gateway.chat() 只做观测性 usage 记录不落账，本方法为唯一计费写入点（无双重扣费）
        - ChatRecordDB.create 内部异常返回 None 且无法区分失败阶段（可能已扣也可能
          未扣）→ 该张记 unknown，禁止自动重扣（设计 §7.4）
        - item.billing_status/billing_reference/credits_charged 与 embedding 计费合并
          回写（两笔独立提交，任一 unknown 即整条标 unknown 防自动补扣误判）
        - 「图片无法识别」/失败张不在 successes（不计费口径不变）
        """
        if not successes:
            return

        image_credits = 0.0
        image_unknown = False
        record_ids: List[str] = []
        import time as _time

        for s in successes:
            usage = getattr(s, "usage", None) or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            try:
                # 按该张实际 token × 实际模型单价计费（含价目兜底，单价缺失极端
                # 场景返回 0.0 不阻断）
                credit = calculate_credit_cost(
                    prompt_tokens, completion_tokens, model=getattr(s, "model", None)
                )

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
                    total_token_count=int(usage.get("total_tokens") or 0),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model=s.model,
                    provider=s.provider,
                    status="completed",
                    source_type=VISION_PARSE_SOURCE_TYPE,
                    credit_cost=credit,
                    usage_breakdown={
                        "billing_mode": "token",
                        "model": s.model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "image_n": s.n,
                        "article_row_id": article_row_id,
                        "vl_usage": usage,
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
                image_credits += credit
                record_ids.append(record["record_id"])
            else:
                image_unknown = True
                logger.bind(module="wechat_mp").error(
                    "wechat_mp 图片计费结果不可确认（记 unknown，不重扣）"
                    "tenant_id={} article_id={} image_n={}",
                    tenant_id, article_row_id, s.n,
                )

        logger.bind(module="wechat_mp").info(
            "wechat_mp 图片按 token 计费 tenant_id={} article_id={} images={} "
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
        条件内，error_message 向用户承诺「将自动重试」）。复核后 pipeline_version
        与当前 PIPELINE_VERSION（p5）不匹配，deferred 存量自动走重建分支
        （VL → 总结 → 入库），无需单独迁移。
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

    def _finalize_run(
        self,
        tenant_id: str,
        run_id: int,
        owner: str,
        count_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        """按 item 终态汇总 run；事件在该 run 全部 item 终态后置 done。

        count_overrides（WP9 scheduled run 专用）：对账 run 的 total_count=本轮源
        消息数（item 只覆盖有变化的文章），未变跳过数并入 skipped_count——聚合
        item 字段照旧，缺的计数显式补写。
        """
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

        if count_overrides:
            total = int(count_overrides.get("total_count", total))
            skipped_count += int(count_overrides.get("extra_skipped", 0) or 0)
            # WP13：清单对账的 is_deleted 软删不建 item，计数显式补写
            deleted_count += int(count_overrides.get("extra_deleted", 0) or 0)

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

    def _get_summarizer(self):
        """文章总结器（测试可注入；默认真实实现，主 provider 默认文本模型）。"""
        if self._summarizer is None:
            self._summarizer = ArticleSummarizer()
        return self._summarizer

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
