"""宏陶商城产品知识库同步服务（HongtaoShopSyncService，设计 §5/§6 v1.4）。

**零私有表**（2026-09-21 用户架构决议）：全部账本/缓存/配置复用平台通用
`src/services/content_sync/` 五张表（module='hongtao_shop' 维度隔离）；产品数据只存
知识库（documents + metadata），论坛关联每轮内存态重算（不建关系表）。

分类：写死租户知识库顶级分类「产品」——查无则建（普通分类，同后台建法），
文档只进该分类（不建模块独立分类）。

三态判定（设计 §5.1）：
- records.user_deleted 且 hash 未变 → 静默跳过零计费（删除抑制）；
- hash 同 + doc active + pipeline 同 → skip（metadata 先比对后写，不抖 updated_at）；
- doc 软删（下架/取消勾选后重新命中）且 hash 同 → restore 复用旧 chunks 零计费；
- 否则 new/update（VL 新图 → 渲染 → embedding → 单事务落库）。

用户删除检测为**同步侧自愈**（平台 knowledge 层零侵入）：records.doc_id 有值
（曾成功入库）而 documents 行不存在（知识库硬删是行消失唯一路径）→ 置
user_deleted。对账三路径（§6）：下架立即软删（明确信号）；消失两击（门禁
fetch_complete AND total 核对 AND items 全终态）；取消勾选立即软删。

计费：embedding 复用 knowledge_service._record_knowledge_embedding_billing
（source_type=hongtao_shop_embedding，fail-open）；VL 按张 token 计费（照
wechat_mp WP13 现行模式），均落平台通用 chat_records。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from loguru import logger

from src.db.database import get_db_connection
from src.tenant_custom.hongtao_shop.fetcher import build_client, fetch_posts, fetch_products
from src.tenant_custom.hongtao_shop.joiner import build_model_index, match_post
from src.tenant_custom.hongtao_shop.renderer import PIPELINE_VERSION, build_chunks, render_product
from src.tenant_custom.hongtao_shop.vision import describe_images_cached
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.knowledge.vector_db.vector_db import get_vector_db

# ------------------------------- 常量 -------------------------------

MODULE = "hongtao_shop"
DOC_ORIGIN = "hongtao_shop"
EXTERNAL_ID_PREFIX = "product:"
# 租户知识库顶级分类：写死「产品」（查无则建普通分类，用户决议 2026-09-21）
PRODUCT_CATEGORY_NAME = "产品"
EMBEDDING_MODEL = "text-embedding-v3"
EMBEDDING_SOURCE_TYPE = "hongtao_shop_embedding"
# VL 计费倍率（用户决议 2026-09-22）：宏陶场景在公众号现行倍率（全局
# settings.billing.usage_factor）基础上 ×3；经 usage_factor_override 仅作用于
# 本模块，公众号侧不受影响
VL_USAGE_FACTOR_MULTIPLIER = 3
from src.tenant_custom.hongtao_shop.bootstrap import VL_IMAGE_PARSE_MODEL

VISION_PARSE_SOURCE_TYPE = VL_IMAGE_PARSE_MODEL  # 单一事实源在 bootstrap

# 失败退避：300s × 2^fail_count，上限 24h（防毒产品反复烧 embedding）
RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 24 * 3600
STALE_HEARTBEAT_SECONDS = 3600  # stale 回收阈值（单闸决议：需高于单 item 最坏耗时，
#                              # 含多图 VL 重试；设计 §8 v1.4 修订，见文档）

ERR_FETCH_FAILED = "fetch_failed"
ERR_CONTENT_EMPTY = "content_empty"
ERR_INTERNAL = "internal_error"


def external_id_of(native_id: Any) -> str:
    """documents.external_id 口径：f'product:{native_id}'。"""
    return f"{EXTERNAL_ID_PREFIX}{native_id}"


_METADATA_VOLATILE_TOP_KEYS = ("sync_run_id",)
_METADATA_VOLATILE_TRACE_KEYS = ("ingested_at",)


def _dump_metadata(metadata: Dict[str, Any]) -> str:
    """metadata 统一序列化（sort_keys：跳过路径「先比对后写」需与库内值逐字节可比）。"""
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def _metadata_equivalent(stored_json: str, rendered_metadata: Dict[str, Any],
                         run: Dict[str, Any]) -> bool:
    """跳过路径「先比对后写」：剔除每轮必变的易变键后逐字节比较。"""
    try:
        stored = json.loads(stored_json)
    except (TypeError, ValueError):
        return False
    fresh = dict(rendered_metadata)
    fresh["pipeline_version"] = PIPELINE_VERSION
    fresh["sync_run_id"] = run["id"]
    for meta in (stored, fresh):
        for key in _METADATA_VOLATILE_TOP_KEYS:
            meta.pop(key, None)
        # 浅拷贝 trace 后再剔除易变键，避免改写 rendered_metadata 原始对象
        trace = meta.get("trace")
        if isinstance(trace, dict):
            meta["trace"] = {
                k: v for k, v in trace.items()
                if k not in _METADATA_VOLATILE_TRACE_KEYS
            }
    return _dump_metadata(stored) == _dump_metadata(fresh)


class HongtaoShopSyncService:
    """宏陶商城同步服务（无状态，单例使用亦可）。"""

    def __init__(self, embedding_client=None, vision_parser=None):
        self._embedding_client = embedding_client
        self._vision_parser = vision_parser
        self._category_cache: Dict[str, str] = {}  # tenant_id → 产品分类 source_type

    def _get_vision_parser(self):
        """VL 解析器单例（每产品新建会泄漏 httpx client；照 wechat_mp 懒加载先例）。"""
        if self._vision_parser is None:
            from src.tenant_custom.hongtao_shop.vision import VisionParser

            self._vision_parser = VisionParser()
        return self._vision_parser

    def _get_embedding_client(self):
        if self._embedding_client is None:
            from src.config.settings import get_embedding_api_key
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client

            self._embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        return self._embedding_client

    # ==================== 产品分类（租户知识库普通分类） ====================

    def _product_category_source_type(self, tenant_id: str) -> str:
        """顶级分类「产品」的 source_type；查无则建普通分类（同后台建法）。

        身份键是 display_name；source_type 仅作文档归属键，值本身无模块语义。
        已知边缘：租户后台把「产品」改名后，本模块下次运行会新建同名「产品」
        分类而非跟随改名行（历史文档留在旧分类）——接受此行为，改名需同步
        迁移文档或改回名称。同租户 run 串行 → 无并发建重风险。
        """
        cached = self._category_cache.get(tenant_id)
        if cached:
            return cached
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_type FROM knowledge_categories "
                "WHERE tenant_id = %s AND display_name = %s AND parent_id IS NULL "
                "ORDER BY id LIMIT 1",
                (tenant_id, PRODUCT_CATEGORY_NAME),
            )
            row = cursor.fetchone()
            if row:
                self._category_cache[tenant_id] = row["source_type"]
                return row["source_type"]
            # 查无则建（source_type 自动生成，走平台 create_category 同款约定）
            source_type = f"k_{uuid.uuid4().hex[:12]}"
            cursor.execute(
                """
                INSERT INTO knowledge_categories
                    (tenant_id, source_type, display_name, parent_id, uuid)
                VALUES (%s, %s, %s, NULL, %s)
                ON CONFLICT (tenant_id, source_type) DO NOTHING
                """,
                (tenant_id, source_type, PRODUCT_CATEGORY_NAME,
                 f"kc_{uuid.uuid4().hex[:12]}"),
            )
            conn.commit()
        self._category_cache[tenant_id] = source_type
        return source_type

    # ==================== 受理与领取 ====================

    def _check_credit(self, tenant_id: str) -> Tuple[bool, str]:
        """余额预检：余额 ≤0 拦截；检查异常时不阻断（照 wechat_mp 口径）。"""
        try:
            from src.saas.db.tenant_db import TenantDB

            tenant = TenantDB.get_by_id(tenant_id)
            if not tenant:
                return False, "租户不存在"
            balance = float(tenant.get("credit_balance") or 0)
            if balance <= 0:
                return False, "积分余额已耗尽"
            return True, ""
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                "hongtao_shop 余额预检异常 tenant={}: {}", tenant_id, e
            )
            return True, ""

    def trigger_sync(self, tenant_id: str, trigger_type: str = "manual") -> Dict[str, Any]:
        """受理一次同步：余额预检 → 建 queued run（CLI/后续调度共用）。"""
        ok, reason = self._check_credit(tenant_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                if not ok:
                    cursor.execute(
                        """
                        INSERT INTO bs_content_sync_runs
                            (tenant_id, module, trigger_type, status, error_message, completed_at)
                        VALUES (%s, %s, %s, 'skipped_no_credit', %s, now())
                        RETURNING id
                        """,
                        (tenant_id, MODULE, trigger_type, reason),
                    )
                    run_id = cursor.fetchone()["id"]
                    conn.commit()
                    return {"run_id": run_id, "status": "skipped_no_credit", "reason": reason}
                # 在队去重：已有 queued/running 直接返回既有 run（防连点堆积整轮同步）
                cursor.execute(
                    """
                    SELECT id FROM bs_content_sync_runs
                    WHERE tenant_id = %s AND module = %s
                      AND status IN ('queued', 'running')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tenant_id, MODULE),
                )
                existing = cursor.fetchone()
                if existing:
                    conn.commit()
                    return {
                        "run_id": existing["id"], "status": "queued", "deduped": True,
                    }
                cursor.execute(
                    """
                    INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status)
                    VALUES (%s, %s, %s, 'queued') RETURNING id
                    """,
                    (tenant_id, MODULE, trigger_type),
                )
                run_id = cursor.fetchone()["id"]
                conn.commit()
                # best-effort 唤醒调度器（失败由 60s 兜底扫描接管）
                from src.tenant_custom.hongtao_shop.notify import notify_queued_work

                notify_queued_work()
                return {"run_id": run_id, "status": "queued"}
            except Exception:
                conn.rollback()
                raise

    def _claim_next_run(self, tenant_id: str, owner_token: str):
        """领取最早 queued run（SKIP LOCKED）；撞唯一索引（已有 running）返回 'conflict'。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE bs_content_sync_runs
                    SET status = 'running', owner_token = %s,
                        started_at = now(), heartbeat_at = now()
                    WHERE id = (
                        SELECT id FROM bs_content_sync_runs
                        WHERE tenant_id = %s AND module = %s AND status = 'queued'
                        ORDER BY created_at ASC, id ASC LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,
                    (owner_token, tenant_id, MODULE),
                )
                row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else None
            except psycopg2.errors.UniqueViolation:
                # uq_content_sync_runs_active：同租户同源已有 running，让位
                conn.rollback()
                return "conflict"
            except Exception:
                conn.rollback()
                raise

    def _heartbeat(self, run_id: int, owner_token: str) -> bool:
        """owner 守卫推进 heartbeat_at（P2.1 stale 回收依据；P1 CLI 周期调用）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_content_sync_runs SET heartbeat_at = now()
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (run_id, owner_token),
            )
            conn.commit()
            return cursor.rowcount == 1

    # ==================== 执行 ====================

    async def run_now(
        self, tenant_id: str, limit: Optional[int] = None, trigger_type: str = "manual"
    ) -> Dict[str, Any]:
        """受理 + 同进程执行（P1 CLI 路径；P2 调度器改为 claim 驱动）。"""
        accepted = self.trigger_sync(tenant_id, trigger_type=trigger_type)
        if accepted["status"] != "queued":
            return accepted
        owner_token = uuid.uuid4().hex
        run = self._claim_next_run(tenant_id, owner_token)
        if run is None or run == "conflict":
            return {"run_id": accepted["run_id"], "status": "queued"}
        return await self._execute_run(tenant_id, run, owner_token, limit=limit)

    async def claim_and_run(
        self, tenant_id: str, owner_tokens: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """调度器领取入口：循环领取该租户本模块 queued run 并逐个执行。

        owner_tokens 传入列表时收集本调用产生的 run owner（调度器 stop 时按其
        主动置 interrupted，防取消在飞 run 后卡 running 阻塞租户）。
        返回 {"executed": bool, "run_ids": [...], "reason": str}；
        conflict（已有 running）与空队列都视为无事可做。
        """
        run_ids: List[int] = []
        reason = "empty"
        while True:
            owner_token = uuid.uuid4().hex
            run = self._claim_next_run(tenant_id, owner_token)
            if run is None:
                break
            if run == "conflict":
                reason = "conflict"
                break
            run_ids.append(run["id"])
            if owner_tokens is not None:
                owner_tokens.append(owner_token)
            await self._execute_run(tenant_id, run, owner_token)
        if run_ids:
            reason = "executed"
        return {"executed": bool(run_ids), "run_ids": run_ids, "reason": reason}

    def interrupt_runs_by_owners(self, owner_tokens: List[str]) -> int:
        """按 owner 主动置 interrupted（调度器 stop 取消在飞 run 后收尾用）。

        部分唯一索引随终态化释放，租户无需等 stale 回收（最长 1h）才可再同步。
        """
        owners = [o for o in owner_tokens if o]
        if not owners:
            return 0
        interrupted = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM bs_content_sync_runs
                WHERE module = %s AND status = 'running' AND owner_token = ANY(%s)
                """,
                (MODULE, owners),
            )
            for row in cursor.fetchall():
                cursor.execute(
                    """
                    UPDATE bs_content_sync_runs
                    SET status = 'interrupted', completed_at = now(),
                        error_message = COALESCE(error_message, '') || '调度器停机中断'
                    WHERE id = %s AND status = 'running'
                    """,
                    (row["id"],),
                )
                if cursor.rowcount:
                    cursor.execute(
                        """
                        UPDATE bs_content_sync_items
                        SET status = 'interrupted', completed_at = now()
                        WHERE run_id = %s AND status IN ('pending', 'running')
                        """,
                        (row["id"],),
                    )
                    interrupted += 1
            conn.commit()
        return interrupted

    def _backoff_native_ids(self, tenant_id: str) -> set:
        """退避未到期的 native_id 集合（失败退避强制消费点：期内不再处理，防毒产品反复烧）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT native_id FROM bs_content_sync_records
                WHERE tenant_id = %s AND module = %s
                  AND next_retry_at IS NOT NULL AND next_retry_at > now()
                """,
                (tenant_id, MODULE),
            )
            return {r["native_id"] for r in cursor.fetchall()}

    def recover_stale_runs(self) -> Dict[str, Any]:
        """stale 回收：heartbeat 超 STALE_HEARTBEAT_SECONDS（3600s，单闸决议）的 running run → interrupted。

        owner 守卫：run 终态化只按 id+status='running' 更新（无需 owner——旧 worker
        若仍活着，其 heartbeat 已 >30min 未推进，后续 _finalize 的 owner 守卫会
        使其写入失效，不会双写）。items 的 pending/running 置 interrupted。
        宁晚勿错：仅当 heartbeat 确实超时才回收。
        """
        recovered = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM bs_content_sync_runs
                WHERE module = %s AND status = 'running'
                  AND COALESCE(heartbeat_at, started_at, created_at)
                      < now() - make_interval(secs => %s)
                """,
                (MODULE, STALE_HEARTBEAT_SECONDS),
            )
            stale_ids = [r["id"] for r in cursor.fetchall()]
            for run_id in stale_ids:
                cursor.execute(
                    """
                    UPDATE bs_content_sync_runs
                    SET status = 'interrupted', completed_at = now(),
                        error_message = COALESCE(error_message, '') || 'heartbeat 超时回收'
                    WHERE id = %s AND status = 'running'
                    """,
                    (run_id,),
                )
                if cursor.rowcount:
                    cursor.execute(
                        """
                        UPDATE bs_content_sync_items
                        SET status = 'interrupted', completed_at = now()
                        WHERE run_id = %s AND status IN ('pending', 'running')
                        """,
                        (run_id,),
                    )
                    recovered += 1
            conn.commit()
        if recovered:
            logger.bind(module="hongtao_shop").warning(
                "hongtao_shop 回收 stale run {} 个", recovered
            )
        return {"interrupted": recovered}

    async def _execute_run(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """run 执行总入口：任何阶段异常 → _fail_run 兜底，防永久 running 死锁
        （部分唯一索引下一条卡死的 running 会阻塞该租户该源全部后续同步）。"""
        try:
            sync_date = datetime.now().strftime("%Y-%m-%d")
            run_started = datetime.now()
            return await self._execute_run_inner(
                tenant_id, run, owner_token, limit, sync_date, run_started
            )
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                "hongtao_shop run 执行异常 tenant={}: {}", tenant_id, e
            )
            self._fail_run(tenant_id, run["id"], owner_token, ERR_INTERNAL, e)
            return {"run_id": run["id"], "status": "failed"}

    async def _execute_run_inner(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int], sync_date: str, run_started: datetime,
    ) -> Dict[str, Any]:
        run_id = run["id"]
        # 源行守卫（=授权记录）：无源行不执行——revoke 删行后 _load_selection
        # 无行按 'all' 语义会绕过白名单全量入库并计费（设计 §8.1 v1.5）
        if not await asyncio.to_thread(self._source_row_exists, tenant_id):
            self._abort_run_interrupted(
                run_id, owner_token, "数据源未开通或已停用，终止执行（不拉取不计费）"
            )
            return {"run_id": run_id, "status": "interrupted"}
        try:
            async with build_client() as client:
                products_fr = await fetch_products(client, limit=limit)
                posts_fr = await fetch_posts(client)
        except Exception as e:  # noqa: BLE001 拉取失败 → run failed
            self._fail_run(tenant_id, run_id, owner_token, ERR_FETCH_FAILED, e)
            return {"run_id": run_id, "status": "failed"}

        # 记录账本刷新（全量 upsert，目录展示字段进 payload；status 原值供对账）
        await asyncio.to_thread(self._refresh_records, tenant_id, products_fr.items)
        # 论坛关联：内存态全量重算（不建关系表，结果聚合进各产品 metadata.forum_media）
        forum_media_by_product = self._link_forum_posts(posts_fr.items, products_fr.items)
        # selection 过滤 + status=1 过滤 → 入库集
        selected = self._load_selection(tenant_id)
        backoff = self._backoff_native_ids(tenant_id)
        eligible = [
            item for item in products_fr.items
            if str(item.get("status")).strip() == "1"
            and (selected is None or str(item.get("id")) in selected)
            and str(item.get("id")) not in backoff  # 失败退避期内跳过（防毒产品反复烧）
        ]

        processed = 0
        for item in eligible:
            if not self._heartbeat(run_id, owner_token):
                return {"run_id": run_id, "status": "interrupted"}
            native_id = str(item.get("id"))
            try:
                await self._process_product(
                    tenant_id, run, item,
                    forum_media_by_product.get(native_id, []),
                    sync_date,
                )
                processed += 1
            except Exception as e:  # noqa: BLE001 单产品失败不阻断 run
                logger.opt(exception=True).error(
                    "hongtao_shop 单产品处理失败 tenant={} native_id={}: {}",
                    tenant_id, native_id, e,
                )
                self._mark_record_failed(tenant_id, run_id, native_id, e)

        # 对账三路径（下架/消失两击/取消勾选）
        await asyncio.to_thread(
            self._reconcile, tenant_id, run, products_fr, selected, run_started
        )
        summary = self._finalize_run(tenant_id, run_id, owner_token, fetch_result=products_fr)
        summary["processed"] = processed
        return summary

    # ==================== 记录账本与论坛关联 ====================

    def _refresh_records(self, tenant_id: str, items: List[Dict[str, Any]]) -> None:
        """全量 upsert 记录账本（payload 存目录展示字段；last_seen_at 对账用）。"""
        from src.tenant_custom.hongtao_shop.joiner import catalog_model

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for item in items:
                native_id = str(item.get("id"))
                payload = {
                    "name": str(item.get("name") or f"商品{native_id}"),
                    "model": catalog_model(item) or "",
                    "procode": str(item.get("procode") or ""),
                    "sellpoint": str(item.get("sellpoint") or ""),
                    "cid": str(item.get("cid") or ""),
                    "status": str(item.get("status") or ""),  # 接口原值，下架对账用
                }
                cursor.execute(
                    """
                    INSERT INTO bs_content_sync_records
                        (tenant_id, module, native_id, external_id, payload,
                         last_seen_at, miss_streak)
                    VALUES (%s, %s, %s, %s, %s, now(), 0)
                    ON CONFLICT (tenant_id, module, native_id) DO UPDATE
                    SET external_id = EXCLUDED.external_id,
                        payload = EXCLUDED.payload,
                        last_seen_at = now(), miss_streak = 0
                    """,
                    (
                        tenant_id, MODULE, native_id,
                        external_id_of(native_id),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
            conn.commit()

    def _link_forum_posts(
        self, posts: List[Dict[str, Any]], catalog: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """论坛关联（内存态，每轮全量重算）：返回 {native_id: [forum_media]}。

        帖子原文/关联证据不落关系表——聚合结果进各产品 metadata.forum_media，
        重算零成本（每轮全量拉帖）。
        """
        index = build_model_index(catalog)
        forum_media_by_product: Dict[str, List[Dict[str, Any]]] = {}
        for post in posts:
            post_id = str(post.get("id"))
            content = str(post.get("content") or "")
            result = match_post(content, index)
            if not result.linked_native_ids:
                continue
            media = {
                "post_id": post_id,
                "catename": str(post.get("catename") or ""),
                "content": content,
                "images": [u for u in (post.get("pics") or []) if isinstance(u, str)],
                "video": str(post.get("video") or ""),
            }
            for native_id in result.linked_native_ids:
                forum_media_by_product.setdefault(native_id, []).append(media)
        return forum_media_by_product

    def _load_selection(self, tenant_id: str) -> Optional[set]:
        """selection_mode='ids' → 返回选中 native_id 集合；'all' → None（不过滤）。

        selected_ids 损坏（非法 JSON/非数组）→ 抛 ValueError 使 run 失败：
        降级为空集会让对账把全部已入库产品判成「取消勾选」误软删，宁失败勿误删。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT selection_mode, selected_ids FROM bs_content_sync_sources "
                "WHERE tenant_id = %s AND module = %s",
                (tenant_id, MODULE),
            )
            row = cursor.fetchone()
        if not row or row["selection_mode"] != "ids":
            return None
        ids = row["selected_ids"]
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except (TypeError, ValueError):
                raise ValueError(
                    f"selected_ids 损坏（非法 JSON），拒绝按空集处理: tenant={tenant_id}"
                )
        if not isinstance(ids, list):
            raise ValueError(
                f"selected_ids 损坏（非数组），拒绝按空集处理: tenant={tenant_id}"
            )
        return {str(v) for v in ids}

    def _source_row_exists(self, tenant_id: str) -> bool:
        """源行（=授权记录）存在性；revoke 删行后同步执行应立即止步。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM bs_content_sync_sources "
                "WHERE tenant_id = %s AND module = %s",
                (tenant_id, MODULE),
            )
            return cursor.fetchone() is not None

    def _abort_run_interrupted(self, run_id: int, owner_token: str, message: str) -> None:
        """owner 守卫置 interrupted 终态（源行守卫兜底路径；不动 sources 行）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_content_sync_runs
                SET status = 'interrupted', error_message = %s,
                    completed_at = now(), fetch_complete = FALSE
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (message[:500], run_id, owner_token),
            )
            conn.commit()

    # ==================== 单产品处理（三态判定） ====================

    async def _process_product(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any],
        forum_media: List[Dict[str, Any]], sync_date: str,
    ) -> None:
        native_id = str(item.get("id"))
        record = self._load_record(tenant_id, native_id)
        if record is None:
            raise RuntimeError(f"content_sync 记录行缺失: {native_id}")

        # 渲染（VL 缓存判增量：未变产品全命中零费用，图变更 → hash 变 → 走 update）
        from src.tenant_custom.hongtao_shop.fetcher import extract_detail
        _, detail_imgs = extract_detail(str(item.get("detail") or ""))
        vl_map, vl_successes, vl_failures = await self._describe_images_cached(
            tenant_id, detail_imgs
        )
        rendered = render_product(
            item, vl_map, forum_media, sync_date,
            ingested_at=datetime.now(timezone.utc).isoformat(),
        )
        vl_images_new = len(vl_successes) + len(vl_failures)
        vl_billed_new = len(vl_successes)

        old_hash = record.get("content_hash")
        doc_state = self._doc_state(tenant_id, record.get("doc_id"), native_id)

        # 用户在知识库界面删除的**同步侧自愈判定**（平台 knowledge 层零侵入）：
        # records.doc_id 有值 = 曾成功入库（入库失败不会留下 doc_id）；
        # documents 行不存在 = 知识库硬删（本模块软删只改 status 不删行）。
        # → 置 user_deleted，走下方静默跳过；hash 变则视为重新发布走 ④ 重建清标记。
        if record.get("doc_id") and doc_state == "missing" and not record.get("user_deleted"):
            self._mark_user_deleted(tenant_id, native_id)
            record["user_deleted"] = True

        # ① 用户删除抑制：hash 未变 → 静默跳过零计费（不清 user_deleted）
        if record.get("user_deleted") and old_hash == rendered.content_hash:
            self._finish_item(
                tenant_id, run["id"], native_id, action="skip", status="skipped",
                vl_images=vl_images_new, vl_billed=vl_billed_new,
            )
            self._touch_record(tenant_id, native_id)
            return

        # ② 未变跳过：hash 同 + doc active + pipeline 同 → skip；metadata 先比对后写
        if (
            old_hash == rendered.content_hash
            and record.get("pipeline_version") == PIPELINE_VERSION
            and doc_state == "active"
        ):
            self._handle_skip_metadata(
                tenant_id, run, record, rendered, native_id,
                vl_images_new, vl_billed_new,
            )
            return

        # ③ restore：doc 软删（下架/取消勾选后重新命中）且 hash 未变 → 复用旧 chunks 零计费
        if doc_state == "deleted" and old_hash == rendered.content_hash:
            self._handle_restore(
                tenant_id, run, record, native_id, vl_images_new, vl_billed_new
            )
            return

        # ④ new/update（含 user_deleted 但 hash 变 = 重新发布，清标记重建；
        #    doc 行已被物理删除时按 new 计——counts 语义 = 新建文档）
        action = (
            "new"
            if not record.get("doc_id") or doc_state == "missing"
            else "update"
        )
        chunks = build_chunks(rendered.content_md)
        if not chunks:
            self._finish_item(
                tenant_id, run["id"], native_id, action="skip", status="skipped",
                error_code=ERR_CONTENT_EMPTY, error_message="分块结果为空",
                vl_images=vl_images_new, vl_billed=vl_billed_new,
            )
            return
        embedding_client = self._get_embedding_client()
        embedding_client.reset_usage()
        embeddings = await embedding_client.embed_batch([c.text for c in chunks])
        embedding_tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)

        item_row = self._open_item(
            tenant_id, run["id"], native_id, action,
            vl_images=vl_images_new, vl_billed=vl_billed_new,
        )
        doc_id = await self._persist_document_tx(
            tenant_id, run, item_row, record, native_id, rendered, chunks, embeddings, action
        )
        # 计费（业务提交后独立提交，fail-open）
        self._bill_embedding(
            tenant_id, run, item_row, doc_id, rendered.title, embedding_tokens
        )
        self._bill_vl_images(tenant_id, run, item_row, native_id, vl_successes)
        _ = vl_failures  # 失败图不入缓存，下一轮重试（观测走日志）

    async def _describe_images_cached(self, tenant_id: str, urls: List[str]):
        """图级 VL 缓存判增量（经模块级名字调用，测试可整体注入替身）。"""
        return await describe_images_cached(
            tenant_id, urls, parser=self._get_vision_parser()
        )

    def _load_record(self, tenant_id: str, native_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_content_sync_records "
                "WHERE tenant_id = %s AND module = %s AND native_id = %s",
                (tenant_id, MODULE, native_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _doc_state(self, tenant_id: str, doc_id: Optional[int], native_id: str) -> str:
        """documents 行状态（active/deleted/missing=不存在；异常按存在降级）。"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                if doc_id:
                    cursor.execute(
                        "SELECT status FROM documents WHERE id = %s AND tenant_id = %s",
                        (doc_id, tenant_id),
                    )
                    row = cursor.fetchone()
                    if row:
                        return row["status"]
                cursor.execute(
                    "SELECT status FROM documents "
                    "WHERE tenant_id = %s AND origin = %s AND external_id = %s",
                    (tenant_id, DOC_ORIGIN, external_id_of(native_id)),
                )
                row = cursor.fetchone()
                return row["status"] if row else "missing"
        except Exception:  # noqa: BLE001
            return "active"

    # ---- skip：metadata 先比对后写 ----

    def _handle_skip_metadata(
        self, tenant_id: str, run: Dict[str, Any], record: Dict[str, Any],
        rendered, native_id: str, vl_images: int, vl_billed: int,
    ) -> None:
        """零计费跳过；metadata（stock/sales/sync_date/forum_media 等）值变才一次轻量 UPDATE。"""
        doc_id = record.get("doc_id")
        if doc_id:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT metadata FROM documents WHERE id = %s AND tenant_id = %s",
                    (doc_id, tenant_id),
                )
                row = cursor.fetchone()
                if row and not _metadata_equivalent(row["metadata"], rendered.metadata, run):
                    # 值变才一次轻量 UPDATE（含本轮 sync_run_id/ingested_at 刷新）；
                    # file_type 随 v1.9 同轮修正（markdown→json），存量行迁移零额外轮次
                    fresh = dict(rendered.metadata)
                    fresh["pipeline_version"] = PIPELINE_VERSION
                    fresh["sync_run_id"] = run["id"]
                    cursor.execute(
                        "UPDATE documents SET metadata = %s, file_type = 'json', "
                        "updated_at = now() "
                        "WHERE id = %s AND tenant_id = %s",
                        (_dump_metadata(fresh), doc_id, tenant_id),
                    )
                    conn.commit()
        self._finish_item(
            tenant_id, run["id"], native_id, action="skip", status="skipped",
            vl_images=vl_images, vl_billed=vl_billed,
        )
        self._touch_record(tenant_id, native_id)

    # ---- restore：复用旧 chunks 零计费 ----

    def _handle_restore(
        self, tenant_id: str, run: Dict[str, Any], record: Dict[str, Any],
        native_id: str, vl_images: int, vl_billed: int,
    ) -> None:
        doc_id = record.get("doc_id")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE documents SET status = 'active', updated_at = now() "
                "WHERE id = %s AND tenant_id = %s AND status = 'deleted'",
                (doc_id, tenant_id),
            )
            cursor.execute(
                """
                UPDATE bs_content_sync_records
                SET processing_status = 'success', last_synced_at = now(),
                    last_checked_at = now(), fail_count = 0, next_retry_at = NULL,
                    error_message = NULL
                WHERE tenant_id = %s AND module = %s AND native_id = %s
                """,
                (tenant_id, MODULE, native_id),
            )
            conn.commit()
        self._finish_item(
            tenant_id, run["id"], native_id, action="restore", status="success",
            billing_status="not_required", vl_images=vl_images, vl_billed=vl_billed,
        )

    # ==================== 入库事务 ====================

    def _run_is_active(self, run_id: int) -> bool:
        """run 仍在 running（stale 回收后 zombie worker 的 item 写入守卫）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM bs_content_sync_runs WHERE id = %s AND status = 'running'",
                (run_id,),
            )
            return cursor.fetchone() is not None

    def _open_item(
        self, tenant_id: str, run_id: int, native_id: str, action: str,
        vl_images: int = 0, vl_billed: int = 0,
    ) -> Dict[str, Any]:
        if not self._run_is_active(run_id):
            raise RuntimeError(f"run {run_id} 已非 running（可能被 stale 回收），拒绝写入 item")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_content_sync_items
                    (tenant_id, run_id, module, native_id, action, status,
                     vl_images, vl_billed, started_at)
                VALUES (%s, %s, %s, %s, %s, 'running', %s, %s, now())
                ON CONFLICT (run_id, native_id) DO UPDATE
                SET action = EXCLUDED.action, status = 'running',
                    vl_images = EXCLUDED.vl_images, vl_billed = EXCLUDED.vl_billed,
                    started_at = now()
                RETURNING id
                """,
                (tenant_id, run_id, MODULE, native_id, action, vl_images, vl_billed),
            )
            item_id = cursor.fetchone()["id"]
            conn.commit()
            return {"id": item_id}

    def _finish_item(
        self, tenant_id: str, run_id: int, native_id: str, *,
        action: str, status: str, error_code: Optional[str] = None,
        error_message: Optional[str] = None, billing_status: Optional[str] = None,
        vl_images: int = 0, vl_billed: int = 0,
    ) -> None:
        if not self._run_is_active(run_id):
            logger.bind(module="hongtao_shop").warning(
                "run {} 已非 running，跳过 item 终态写入（防僵尸覆写回收结果）", run_id
            )
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_content_sync_items
                    (tenant_id, run_id, module, native_id, action, status, error_code,
                     error_message, billing_status, vl_images, vl_billed,
                     started_at, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (run_id, native_id) DO UPDATE
                SET action = EXCLUDED.action, status = EXCLUDED.status,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    billing_status = EXCLUDED.billing_status,
                    vl_images = EXCLUDED.vl_images, vl_billed = EXCLUDED.vl_billed,
                    completed_at = now()
                """,
                (
                    tenant_id, run_id, MODULE, native_id, action, status, error_code,
                    error_message, billing_status or "not_required",
                    vl_images, vl_billed,
                ),
            )
            conn.commit()

    def _touch_record(self, tenant_id: str, native_id: str) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_content_sync_records
                SET processing_status = 'success', last_synced_at = now(),
                    last_checked_at = now(), fail_count = 0, next_retry_at = NULL
                WHERE tenant_id = %s AND module = %s AND native_id = %s
                """,
                (tenant_id, MODULE, native_id),
            )
            conn.commit()

    def _mark_user_deleted(self, tenant_id: str, native_id: str) -> None:
        """同步侧自愈：检测到已入库文档被用户从知识库删除时置 user_deleted。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_content_sync_records SET user_deleted = TRUE, "
                "last_checked_at = now() "
                "WHERE tenant_id = %s AND module = %s AND native_id = %s "
                "AND NOT user_deleted",
                (tenant_id, MODULE, native_id),
            )
            conn.commit()

    async def _persist_document_tx(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any],
        record: Dict[str, Any], native_id: str, rendered, chunks, embeddings, action: str,
    ) -> int:
        """单事务落 documents/chunks/chunks_vec + records + item（分类走租户「产品」）。"""
        metadata = dict(rendered.metadata)
        metadata["pipeline_version"] = PIPELINE_VERSION
        metadata["sync_run_id"] = run["id"]
        category_source_type = self._product_category_source_type(tenant_id)
        external_id = external_id_of(native_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                doc_id = record.get("doc_id")
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
                        ) VALUES (NULL, %s, %s, %s, NULL, 'json', '', %s, %s, %s,
                                  %s, %s, NULL, %s, %s, %s, 'active')
                        ON CONFLICT (tenant_id, origin, external_id) WHERE external_id IS NOT NULL
                        DO NOTHING
                        RETURNING id
                        """,
                        (
                            tenant_id, rendered.title, category_source_type,
                            len(rendered.content_md.encode("utf-8")), len(chunks),
                            EMBEDDING_MODEL, rendered.content_md,
                            _dump_metadata(metadata),
                            f"doc_{uuid.uuid4().hex[:12]}",
                            DOC_ORIGIN, external_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row:
                        doc_id = row["id"]
                    else:
                        cursor.execute(
                            """
                            SELECT id FROM documents
                            WHERE tenant_id = %s AND origin = %s AND external_id = %s
                            """,
                            (tenant_id, DOC_ORIGIN, external_id),
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
                        SET title = %s, raw_text = %s, metadata = %s,
                            total_chunks = %s, embedding_model = %s, source_type = %s,
                            file_type = 'json',
                            status = 'active', expires_at = NULL, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (
                            rendered.title, rendered.content_md,
                            _dump_metadata(metadata), len(chunks),
                            EMBEDDING_MODEL, category_source_type, doc_id, tenant_id,
                        ),
                    )
                    vector_db = get_vector_db(dimension=1024, conn=conn)
                    await vector_db.delete_by_doc(doc_id)
                    cursor.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))

                chunk_ids: List[int] = []
                for idx, chunk in enumerate(chunks):
                    cursor.execute(
                        """
                        INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata, uuid)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                        """,
                        (
                            doc_id, idx, chunk.text,
                            self._estimate_tokens(chunk.text),
                            json.dumps(
                                {**chunk.metadata, "char_count": len(chunk.text)},
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
                    UPDATE bs_content_sync_records
                    SET doc_id = %s, content_hash = %s, pipeline_version = %s,
                        user_deleted = FALSE, processing_status = 'success',
                        last_synced_at = now(), last_checked_at = now(),
                        fail_count = 0, next_retry_at = NULL, error_message = NULL
                    WHERE tenant_id = %s AND module = %s AND native_id = %s
                    """,
                    (
                        doc_id, rendered.content_hash, PIPELINE_VERSION,
                        tenant_id, MODULE, native_id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE bs_content_sync_items
                    SET status = 'success', action = %s, completed_at = now()
                    WHERE id = %s AND tenant_id = %s
                      AND EXISTS (SELECT 1 FROM bs_content_sync_runs r
                                  WHERE r.id = bs_content_sync_items.run_id
                                    AND r.status = 'running')
                    """,
                    (action, item["id"], tenant_id),
                )
                conn.commit()
                return doc_id
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗估 token 数（对齐 knowledge/service 口径：中文 1 字/token，其他 4 字/token）。"""
        chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return chinese + (len(text) - chinese) // 4

    # ==================== 计费（fail-open，业务提交后独立提交） ====================

    def _bill_embedding(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any],
        doc_id: int, title: str, embedding_tokens: int,
    ) -> None:
        """embedding 计费：复用通用链路（source_type=hongtao_shop_embedding）。"""
        if embedding_tokens <= 0:
            self._merge_item_billing(tenant_id, item["id"], extra_credits=0.0,
                                      extra_reference=None, extra_unknown=False,
                                      force_not_required=True)
            return
        billing_status = "unknown"
        reference = None
        credits = 0.0
        try:
            from src.knowledge.service import knowledge_service

            record = knowledge_service._record_knowledge_embedding_billing(
                tenant_id=tenant_id,
                user_id=None,
                doc_id=doc_id,
                file_filename=title,
                embedding_tokens=embedding_tokens,
                summary_usage=None,
                source_type=EMBEDDING_SOURCE_TYPE,
                user_message_prefix="宏陶商城产品向量化",
            )
            if record and record.get("record_id"):
                billing_status = "charged"
                reference = record["record_id"]
                credits = float(record.get("credit_cost") or 0)
            else:
                credits = 0.0
        except Exception as e:  # noqa: BLE001 计费失败不影响已入库内容
            logger.opt(exception=True).error(
                "hongtao_shop embedding 计费异常（记 unknown 不重扣）: {}",
                sanitize_error_info(str(e)),
            )
        self._merge_item_billing(
            tenant_id, item["id"], extra_credits=credits, extra_reference=reference,
            extra_unknown=billing_status == "unknown",
            confirmed=billing_status == "charged",
            embedding_tokens=embedding_tokens,
        )

    def _bill_vl_images(
        self, tenant_id: str, run: Dict[str, Any], item: Dict[str, Any],
        native_id: str, successes,
    ) -> None:
        """VL 按张 token 计费（照 wechat_mp WP13 模式；unrecognized/失败张不在 successes）。

        倍率：宏陶 = 全局 usage_factor × VL_USAGE_FACTOR_MULTIPLIER（固定 ×3），
        经 usage_factor_override 传入——公众号侧仍走全局系数，互不影响。
        """
        if not successes:
            return

        from src.services.billing import calculate_credit_cost

        # 宏陶 VL 计费倍率：公众号现行倍率（全局系数）× 3（用户决议 2026-09-22）；
        # 配置读取异常按全局默认 100 兜底——计费辅助逻辑不得把已入库内容标失败
        from src.config.settings import create_settings
        try:
            base_factor = getattr(create_settings().billing, "usage_factor", 100) or 100
        except Exception:  # noqa: BLE001
            base_factor = 100
        vl_usage_factor = int(base_factor) * VL_USAGE_FACTOR_MULTIPLIER

        credits_total = 0.0
        unknown = False
        record_ids: List[str] = []
        for s in successes:
            usage = getattr(s, "usage", None) or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            try:
                credit = calculate_credit_cost(
                    prompt_tokens, completion_tokens, model=getattr(s, "model", None),
                    usage_factor_override=vl_usage_factor,
                )
                from src.db.models import ChatRecordDB

                record = ChatRecordDB.create(
                    session_id=f"{VISION_PARSE_SOURCE_TYPE}_{native_id}_{int(time.time())}",
                    tenant_id=tenant_id,
                    user_id=None,
                    user_message=f"宏陶商城产品详情图解析（{native_id}）",
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
                        "native_id": native_id,
                        # 对账标识：本行为宏陶倍率计费（全局系数 × N）
                        "usage_factor": vl_usage_factor,
                        "usage_factor_multiplier": VL_USAGE_FACTOR_MULTIPLIER,
                    },
                )
            except Exception as e:  # noqa: BLE001 单张计费失败不回滚内容
                record = None
                logger.opt(exception=True).error(
                    "hongtao_shop VL 计费异常（记 unknown）native_id={}: {}",
                    native_id, sanitize_error_info(str(e)),
                )
            if record and record.get("record_id"):
                credits_total += credit
                record_ids.append(record["record_id"])
            else:
                unknown = True
        self._merge_item_billing(
            tenant_id, item["id"], extra_credits=credits_total,
            extra_reference=",".join(record_ids) or None, extra_unknown=unknown,
            confirmed=not unknown,
        )

    def _merge_item_billing(
        self, tenant_id: str, item_id: int, *, extra_credits: float,
        extra_reference: Optional[str], extra_unknown: bool,
        confirmed: bool = False, embedding_tokens: int = 0,
        force_not_required: bool = False,
    ) -> None:
        """把计费结果合并进 item 既有计费字段（任一 unknown → 整条 unknown）。

        confirmed=True 表示本次来源已确认落账（0 积分也算 charged，不停留 pending）。
        """
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT billing_status, billing_reference, credits_charged
                    FROM bs_content_sync_items WHERE id = %s AND tenant_id = %s
                    """,
                    (item_id, tenant_id),
                )
                row = cursor.fetchone()
                if not row:
                    return
                status = row["billing_status"]
                if force_not_required and status == "pending":
                    status = "not_required"
                elif extra_unknown:
                    status = "unknown"
                elif confirmed and status in ("pending", "not_required"):
                    status = "charged"
                reference = row["billing_reference"]
                if extra_reference:
                    reference = f"{reference},{extra_reference}" if reference else extra_reference
                cursor.execute(
                    """
                    UPDATE bs_content_sync_items
                    SET billing_status = %s, billing_reference = %s,
                        credits_charged = credits_charged + %s,
                        embedding_tokens = embedding_tokens + %s
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (status, reference, extra_credits, embedding_tokens,
                     item_id, tenant_id),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("hongtao_shop item 计费合并失败: {}", type(e).__name__)

    # ==================== 失败与对账 ====================

    def _mark_record_failed(
        self, tenant_id: str, run_id: int, native_id: str, error: Exception
    ) -> None:
        message = f"{type(error).__name__}: {error}"[:500]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fail_count FROM bs_content_sync_records "
                "WHERE tenant_id = %s AND module = %s AND native_id = %s",
                (tenant_id, MODULE, native_id),
            )
            row = cursor.fetchone()
            fail_count = (row["fail_count"] if row else 0) + 1
            delay = min(RETRY_BASE_SECONDS * (2 ** fail_count), RETRY_MAX_SECONDS)
            cursor.execute(
                """
                UPDATE bs_content_sync_records
                SET processing_status = 'sync_failed', fail_count = %s,
                    next_retry_at = now() + %s * interval '1 second',
                    error_message = %s
                WHERE tenant_id = %s AND module = %s AND native_id = %s
                """,
                (fail_count, delay, message, tenant_id, MODULE, native_id),
            )
            conn.commit()
        # action 留空：失败 item 由 status='failed' 承载，不混入 skipped_count 观测口径
        self._finish_item(
            tenant_id, run_id, native_id, action=None, status="failed",
            error_code=ERR_INTERNAL, error_message=message,
        )

    def _reconcile(
        self, tenant_id: str, run: Dict[str, Any], products_fr, selected,
        run_started: datetime,
    ) -> None:
        """对账三路径（设计 §6）。门禁不满足时仅跳过「消失两击」，其余照常。

        下架信号取本轮 fetch 的接口原值 status（fetched_status），不依赖 payload。
        """
        fetched_native_ids = {str(i.get("id")) for i in products_fr.items}
        fetched_status = {
            str(i.get("id")): str(i.get("status") or "") for i in products_fr.items
        }
        gate = (
            products_fr.fetch_complete
            and products_fr.total_reported == len(products_fr.items)
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # ① 下架（明确信号，当轮立即软删）+ ② 取消勾选（显式意图，立即软删）
            cursor.execute(
                "SELECT native_id, doc_id, user_deleted FROM bs_content_sync_records "
                "WHERE tenant_id = %s AND module = %s AND doc_id IS NOT NULL",
                (tenant_id, MODULE),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                native_id = row["native_id"]
                if row["user_deleted"]:
                    continue  # 用户删除抑制：doc 已物理删，不再动
                off_shelf = (
                    native_id in fetched_status and fetched_status[native_id] != "1"
                )
                deselected = selected is not None and native_id not in selected
                if off_shelf or deselected:
                    cursor.execute(
                        "UPDATE documents SET status = 'deleted', updated_at = now() "
                        "WHERE id = %s AND tenant_id = %s AND status = 'active'",
                        (row["doc_id"], tenant_id),
                    )
                    if cursor.rowcount:
                        self._finish_item_reconcile(
                            cursor, tenant_id, run["id"], native_id, action="delete"
                        )
            # ③ 消失两击（门禁满足才推进）
            if gate:
                cursor.execute(
                    "SELECT native_id, doc_id, miss_streak, user_deleted "
                    "FROM bs_content_sync_records WHERE tenant_id = %s AND module = %s",
                    (tenant_id, MODULE),
                )
                for row in cursor.fetchall():
                    native_id = row["native_id"]
                    if native_id in fetched_native_ids or row["user_deleted"]:
                        continue
                    miss_streak = int(row["miss_streak"] or 0) + 1
                    if miss_streak >= 2 and row["doc_id"]:
                        cursor.execute(
                            "UPDATE documents SET status = 'deleted', updated_at = now() "
                            "WHERE id = %s AND tenant_id = %s AND status = 'active'",
                            (row["doc_id"], tenant_id),
                        )
                        if cursor.rowcount:
                            self._finish_item_reconcile(
                                cursor, tenant_id, run["id"], native_id, action="delete"
                            )
                            cursor.execute(
                                """
                                UPDATE bs_content_sync_records
                                SET miss_streak = %s
                                WHERE tenant_id = %s AND module = %s AND native_id = %s
                                """,
                                (miss_streak, tenant_id, MODULE, native_id),
                            )
                            continue
                    cursor.execute(
                        """
                        UPDATE bs_content_sync_records
                        SET miss_streak = %s, last_checked_at = now()
                        WHERE tenant_id = %s AND module = %s AND native_id = %s
                        """,
                        (miss_streak, tenant_id, MODULE, native_id),
                    )
            conn.commit()

    def _finish_item_reconcile(
        self, cursor, tenant_id: str, run_id: int, native_id: str, *, action: str
    ) -> None:
        cursor.execute(
            """
            INSERT INTO bs_content_sync_items
                (tenant_id, run_id, module, native_id, action, status, billing_status,
                 started_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, 'success', 'not_required', now(), now())
            ON CONFLICT (run_id, native_id) DO NOTHING
            """,
            (tenant_id, run_id, MODULE, native_id, action),
        )

    # ==================== run 终态 ====================

    def _fail_run(
        self, tenant_id: str, run_id: int, owner_token: str,
        error_code: str, error: Exception,
    ) -> None:
        message = f"{error_code}: {type(error).__name__}: {error}"[:500]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_content_sync_runs
                SET status = 'failed', error_message = %s, completed_at = now(),
                    fetch_complete = FALSE
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (message, run_id, owner_token),
            )
            cursor.execute(
                "UPDATE bs_content_sync_sources SET last_error = %s "
                "WHERE tenant_id = %s AND module = %s",
                (message, tenant_id, MODULE),
            )
            conn.commit()

    def _finalize_run(
        self, tenant_id: str, run_id: int, owner_token: str, fetch_result=None
    ) -> Dict[str, Any]:
        """按 item 终态聚合计数 → run 终态；成功才推进并清 sources.last_sync_at/last_error。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT action, status, COUNT(*) AS cnt,
                       COALESCE(SUM(credits_charged), 0) AS credits,
                       COALESCE(SUM(vl_billed), 0) AS vl_billed,
                       COALESCE(SUM(embedding_tokens), 0) AS emb_tokens
                FROM bs_content_sync_items WHERE run_id = %s GROUP BY action, status
                """,
                (run_id,),
            )
            counts = {"new": 0, "update": 0, "skip": 0, "delete": 0, "restore": 0}
            failed = 0
            credits = 0.0
            vl_billed_total = emb_tokens_total = 0
            for row in cursor.fetchall():
                action = row["action"]
                if action in counts:
                    counts[action] += int(row["cnt"])
                if row["status"] == "failed":
                    failed += int(row["cnt"])
                credits += float(row["credits"])
                vl_billed_total += int(row["vl_billed"])
                emb_tokens_total += int(row["emb_tokens"])
            cursor.execute(
                "SELECT COALESCE(SUM(vl_images), 0) AS vl_images "
                "FROM bs_content_sync_items WHERE run_id = %s",
                (run_id,),
            )
            vl_parsed = int(cursor.fetchone()["vl_images"])
            gate = bool(
                fetch_result
                and fetch_result.fetch_complete
                and fetch_result.total_reported == len(fetch_result.items)
            )
            # items 全终态判定：无 pending/running 残留
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM bs_content_sync_items "
                "WHERE run_id = %s AND status IN ('pending', 'running')",
                (run_id,),
            )
            items_all_terminal = cursor.fetchone()["cnt"] == 0
            # 截断 run（fetch_complete=False，--limit/翻页中断）不完整 → partial_failed
            run_status = "success" if (gate and items_all_terminal) else "partial_failed"
            if failed:
                run_status = "partial_failed"
            cursor.execute(
                """
                UPDATE bs_content_sync_runs
                SET status = %s, new_count = %s, updated_count = %s, skipped_count = %s,
                    deleted_count = %s, restored_count = %s, failed_count = %s,
                    vl_parsed_count = %s, vl_billed_count = %s, embedding_tokens = %s,
                    credits_charged = %s, fetch_complete = %s, total_reported = %s,
                    completed_at = now()
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (
                    run_status, counts["new"], counts["update"], counts["skip"],
                    counts["delete"], counts["restore"], failed,
                    vl_parsed, vl_billed_total, emb_tokens_total,
                    credits, gate,
                    fetch_result.total_reported if fetch_result else None,
                    run_id, owner_token,
                ),
            )
            # last_error 仅在 run 完全成功时清除（partial_failed/失败保留供观测）
            if run_status == "success":
                cursor.execute(
                    "UPDATE bs_content_sync_sources "
                    "SET last_sync_at = now(), last_error = NULL "
                    "WHERE tenant_id = %s AND module = %s",
                    (tenant_id, MODULE),
                )
            else:
                cursor.execute(
                    "UPDATE bs_content_sync_sources "
                    "SET last_sync_at = now() WHERE tenant_id = %s AND module = %s",
                    (tenant_id, MODULE),
                )
            conn.commit()
        return {"run_id": run_id, "status": run_status, "counts": counts, "failed": failed}


hongtao_shop_service = HongtaoShopSyncService()
