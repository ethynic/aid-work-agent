"""两数据源共用落库底层（base，照 hongtao_shop/service.py 同构自写）。

零私有表：runs/items/records/sources 复用 `src/services/content_sync/` 通用五表
（module 维度隔离）。三态判定与对账语义（§7.4）：hash 未变跳过（metadata 先比
对后写轻量更新，不抖 updated_at）；变更删旧 chunks/chunks_vec 重建；新建全链
路；下架/删除/到期 → documents.status='deleted' 软删；重新上架 hash 未变走
restore（恢复 active 复用 chunks 零计费）；user_deleted 同步侧自愈。

水位/删除 id 集合由各模块自管（source 行 sync_state JSONB，平台层不解读），
只在 run 终态成功后推进。embedding 计费复用 knowledge_service 通用链路
（无 VL，无需模块计费种子；单价行走平台 text-embedding-v3 既有配置）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from loguru import logger

from src.db.database import get_db_connection
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.tenant_custom.lyjy_employment.renderer import (
    PIPELINE_VERSION,
    RenderedDoc,
    build_chunks,
    compute_content_hash,
)

# 失败退避：300s × 2^fail_count，上限 24h（防毒行反复烧 embedding，宏陶同款）
RETRY_BASE_SECONDS = 300
RETRY_MAX_SECONDS = 24 * 3600
# stale 回收集阈：heartbeat 超 1h 的 running run 视为中断残留（宏陶单闸决议同款）
STALE_HEARTBEAT_SECONDS = 3600

ERR_FETCH_FAILED = "fetch_failed"
ERR_CONTENT_EMPTY = "content_empty"
ERR_PII_REJECTED = "pii_rejected"
ERR_INTERNAL = "internal_error"

_METADATA_VOLATILE_TOP_KEYS = ("sync_run_id",)


def dump_metadata(metadata: Dict[str, Any]) -> str:
    """metadata 统一序列化（sort_keys：跳过路径「先比对后写」需与库内值逐字节可比）。"""
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def metadata_equivalent(stored_json: str, rendered_metadata: Dict[str, Any],
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
    return dump_metadata(stored) == dump_metadata(fresh)


class KbSyncServiceBase:
    """客户数据库源同步服务基类（无状态，单例使用亦可；子类提供模块常量）。"""

    MODULE: str = ""
    DOC_ORIGIN: str = ""
    EMBEDDING_SOURCE_TYPE: str = ""
    CATEGORY_DISPLAY_NAME: str = ""  # 默认顶级分类（分类路由未命中时的兜底，不使用也必须可解析）
    EMBEDDING_USER_MESSAGE_PREFIX: str = "客户数据源文档向量化"

    def __init__(self, embedding_client=None):
        self._embedding_client = embedding_client
        self._category_cache: Dict[str, str] = {}  # tenant_id → 分类 source_type

    def _get_embedding_client(self):
        if self._embedding_client is None:
            from src.config.settings import get_embedding_api_key
            from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client

            self._embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        return self._embedding_client

    # ==================== 分类自举（查无则建普通分类，宏陶同款） ====================

    def _category_source_type(self, tenant_id: str, display_name: Optional[str] = None) -> str:
        """按 display_name 查找租户顶级分类；查无则建普通分类（同后台建法）。

        身份键是 display_name；source_type 仅作文档归属键。六分类分属两个 module
        各自自举（qs_jobs→在招职位…zc_article→创业政策）。同租户 run 串行 →
        无并发建重风险。
        """
        display_name = display_name or self.CATEGORY_DISPLAY_NAME
        cache_key = f"{tenant_id}::{display_name}"
        cached = self._category_cache.get(cache_key)
        if cached:
            return cached
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_type FROM knowledge_categories "
                "WHERE tenant_id = %s AND display_name = %s AND parent_id IS NULL "
                "ORDER BY id LIMIT 1",
                (tenant_id, display_name),
            )
            row = cursor.fetchone()
            if row:
                self._category_cache[cache_key] = row["source_type"]
                return row["source_type"]
            source_type = f"k_{uuid.uuid4().hex[:12]}"
            cursor.execute(
                """
                INSERT INTO knowledge_categories
                    (tenant_id, source_type, display_name, parent_id, uuid)
                VALUES (%s, %s, %s, NULL, %s)
                ON CONFLICT (tenant_id, source_type) DO NOTHING
                """,
                (tenant_id, source_type, display_name, f"kc_{uuid.uuid4().hex[:12]}"),
            )
            conn.commit()
        self._category_cache[cache_key] = source_type
        return source_type

    # ==================== 源行与受理 ====================

    def _load_source_row(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """源行（=授权记录）整行；revoke 删行后同步执行应立即止步。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_content_sync_sources "
                "WHERE tenant_id = %s AND module = %s",
                (tenant_id, self.MODULE),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _load_selection_tables(self, source_row: Dict[str, Any]) -> Optional[set]:
        """selection_mode='ids' → 选中源表集合（§7.5 映射表行开关）；'all' → None。

        selected_ids 损坏（非法 JSON/非数组）→ 抛 ValueError 使 run 失败：
        降级为空集会把已入库内容全部判成「取消勾选」误软删，宁失败勿误删（宏陶同款）。
        """
        if source_row.get("selection_mode") != "ids":
            return None
        ids = source_row.get("selected_ids")
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except (TypeError, ValueError):
                raise ValueError(
                    f"selected_ids 损坏（非法 JSON），拒绝按空集处理: tenant_module={self.MODULE}"
                )
        if not isinstance(ids, list):
            raise ValueError(
                f"selected_ids 损坏（非数组），拒绝按空集处理: tenant_module={self.MODULE}"
            )
        return {str(v) for v in ids}

    def _load_sync_state(self, source_row: Dict[str, Any]) -> Dict[str, Any]:
        """读模块自管运行态（sync_state JSONB；平台层不解读）。"""
        state = source_row.get("sync_state")
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except (TypeError, ValueError):
                state = None
        return state if isinstance(state, dict) else {}

    def _save_sync_state(self, tenant_id: str, state: Dict[str, Any]) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_content_sync_sources SET sync_state = %s, updated_at = now() "
                "WHERE tenant_id = %s AND module = %s",
                (json.dumps(state, ensure_ascii=False), tenant_id, self.MODULE),
            )
            conn.commit()

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
                "{} 余额预检异常 tenant={}: {}", self.MODULE, tenant_id, e
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
                        (tenant_id, self.MODULE, trigger_type, reason),
                    )
                    run_id = cursor.fetchone()["id"]
                    conn.commit()
                    return {"run_id": run_id, "status": "skipped_no_credit", "reason": reason}
                # 在队去重：已有 queued/running 直接返回既有 run
                cursor.execute(
                    """
                    SELECT id FROM bs_content_sync_runs
                    WHERE tenant_id = %s AND module = %s
                      AND status IN ('queued', 'running')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tenant_id, self.MODULE),
                )
                existing = cursor.fetchone()
                if existing:
                    conn.commit()
                    return {"run_id": existing["id"], "status": "queued", "deduped": True}
                cursor.execute(
                    """
                    INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status)
                    VALUES (%s, %s, %s, 'queued') RETURNING id
                    """,
                    (tenant_id, self.MODULE, trigger_type),
                )
                run_id = cursor.fetchone()["id"]
                conn.commit()
                # best-effort 唤醒调度器（失败由 60s 兜底扫描接管）
                from src.tenant_custom.lyjy_employment.notify import notify_queued_work

                notify_queued_work(self.MODULE)
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
                    (owner_token, tenant_id, self.MODULE),
                )
                row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else None
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                return "conflict"
            except Exception:
                conn.rollback()
                raise

    def _heartbeat(self, run_id: int, owner_token: str) -> bool:
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
        """调度器领取入口：循环领取该租户本模块 queued run 并逐个执行（宏陶同款）。"""
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
        """按 owner 主动置 interrupted（调度器 stop 取消在飞 run 后收尾用）。"""
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
                (self.MODULE, owners),
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

    def recover_stale_runs(self) -> Dict[str, Any]:
        """stale 回收：heartbeat 超 STALE_HEARTBEAT_SECONDS 的 running run → interrupted。

        owner 守卫：run 终态化只按 id+status='running' 更新（旧 worker 若仍活着，
        后续 _finalize/_heartbeat 的 owner 守卫会使其写入失效，不会双写）。
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
                (self.MODULE, STALE_HEARTBEAT_SECONDS),
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
            logger.bind(module=self.MODULE).warning(
                "{} 回收 stale run {} 个（heartbeat 超时）", self.MODULE, recovered
            )
        return {"interrupted": recovered}

    # ==================== 账本与退避 ====================

    def _refresh_records(self, tenant_id: str, rows: List[Dict[str, Any]],
                         payload_of) -> None:
        """全量 upsert 记录账本（payload 目录展示字段 + last_seen_at 对账）。

        payload_of(row) → dict（目录展示字段，含 native_id/external_prefix）。
        native_id 与 external_id 同为 f"{prefix}:{id}"：本模块多表共账本，
        UNIQUE(tenant_id, module, native_id) 必须带表前缀防主键撞号。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for row in rows:
                payload = payload_of(row)
                native_id = f"{payload['external_prefix']}:{payload['native_id']}"
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
                        tenant_id, self.MODULE, native_id, native_id,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
            conn.commit()

    def _load_module_records(self, tenant_id: str) -> List[Dict[str, Any]]:
        """模块全部记录行（目录比对/删除事件用；payload 不取，数万行账本轻查）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT native_id, external_id, content_hash, pipeline_version, doc_id, "
                "user_deleted, miss_streak "
                "FROM bs_content_sync_records WHERE tenant_id = %s AND module = %s",
                (tenant_id, self.MODULE),
            )
            return [dict(r) for r in cursor.fetchall()]

    def _backoff_native_ids(self, tenant_id: str) -> set:
        """退避未到期的 native_id 集合（期内不再处理，防毒行反复烧）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT native_id FROM bs_content_sync_records
                WHERE tenant_id = %s AND module = %s
                  AND next_retry_at IS NOT NULL AND next_retry_at > now()
                """,
                (tenant_id, self.MODULE),
            )
            return {r["native_id"] for r in cursor.fetchall()}

    def _run_is_active(self, run_id: int) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM bs_content_sync_runs WHERE id = %s AND status = 'running'",
                (run_id,),
            )
            return cursor.fetchone() is not None

    def _finish_item(
        self, tenant_id: str, run_id: int, native_id: str, *,
        action: Optional[str], status: str, error_code: Optional[str] = None,
        error_message: Optional[str] = None, billing_status: Optional[str] = None,
        embedding_tokens: int = 0,
    ) -> None:
        if not self._run_is_active(run_id):
            logger.bind(module=self.MODULE).warning(
                "run {} 已非 running，跳过 item 终态写入（防僵尸覆写回收结果）", run_id
            )
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_content_sync_items
                    (tenant_id, run_id, module, native_id, action, status, error_code,
                     error_message, billing_status, embedding_tokens,
                     started_at, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (run_id, native_id) DO UPDATE
                SET action = EXCLUDED.action, status = EXCLUDED.status,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    billing_status = EXCLUDED.billing_status,
                    embedding_tokens = EXCLUDED.embedding_tokens,
                    completed_at = now()
                """,
                (
                    tenant_id, run_id, self.MODULE, native_id, action, status,
                    error_code, error_message, billing_status or "not_required",
                    embedding_tokens,
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
                (tenant_id, self.MODULE, native_id),
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
                (tenant_id, self.MODULE, native_id),
            )
            conn.commit()

    def _mark_record_failed(
        self, tenant_id: str, run_id: int, native_id: str, error: Exception,
        error_code: str = ERR_INTERNAL,
    ) -> None:
        message = f"{type(error).__name__}: {error}"[:500]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fail_count FROM bs_content_sync_records "
                "WHERE tenant_id = %s AND module = %s AND native_id = %s",
                (tenant_id, self.MODULE, native_id),
            )
            row = cursor.fetchone()
            fail_count = ((row["fail_count"] if row else 0) or 0) + 1
            delay = min(RETRY_BASE_SECONDS * (2 ** fail_count), RETRY_MAX_SECONDS)
            cursor.execute(
                """
                UPDATE bs_content_sync_records
                SET processing_status = 'sync_failed', fail_count = %s,
                    next_retry_at = now() + %s * interval '1 second',
                    error_message = %s
                WHERE tenant_id = %s AND module = %s AND native_id = %s
                """,
                (fail_count, delay, message, tenant_id, self.MODULE, native_id),
            )
            conn.commit()
        self._finish_item(
            tenant_id, run_id, native_id, action=None, status="failed",
            error_code=error_code, error_message=message,
        )

    # ==================== 三态处理核心 ====================

    def _load_record(self, tenant_id: str, native_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_content_sync_records "
                "WHERE tenant_id = %s AND module = %s AND native_id = %s",
                (tenant_id, self.MODULE, native_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def _doc_state(self, tenant_id: str, doc_id: Optional[int],
                   external_id: str) -> str:
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
                    (tenant_id, self.DOC_ORIGIN, external_id),
                )
                row = cursor.fetchone()
                return row["status"] if row else "missing"
        except Exception:  # noqa: BLE001
            return "active"

    def _doc_states_bulk(self, tenant_id: str, doc_ids: List[int]) -> Dict[int, str]:
        """批量查 documents 行状态（目录比对用；分片 IN，缺行 = 用户已硬删）。"""
        states: Dict[int, str] = {}
        if not doc_ids:
            return states
        unique = sorted({int(d) for d in doc_ids if d})
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for start in range(0, len(unique), 200):
                chunk = unique[start:start + 200]
                cursor.execute(
                    "SELECT id, status FROM documents "
                    f"WHERE tenant_id = %s AND id IN ({', '.join(str(d) for d in chunk)})",
                    (tenant_id,),
                )
                for row in cursor.fetchall():
                    states[int(row["id"])] = row["status"]
        return states

    async def process_rendered(
        self, tenant_id: str, run: Dict[str, Any], rendered: RenderedDoc,
    ) -> str:
        """单条渲染结果的三态处理（②跳过/③restore/④重建 + user_deleted 自愈）。

        返回 action（skip/restore/new/update）；渲染层异常（PII 等）由调用方捕获。
        记录键统一用 rendered.external_id（带表前缀，与 records.native_id 同形）。
        """
        native_id = rendered.external_id
        record = self._load_record(tenant_id, native_id)
        if record is None:
            raise RuntimeError(f"content_sync 记录行缺失: {native_id}")

        doc_state = self._doc_state(tenant_id, record.get("doc_id"), rendered.external_id)
        old_hash = record.get("content_hash")

        # 用户删除自愈：records.doc_id 有值（曾成功入库）而 documents 行不存在
        # （知识库硬删是行消失唯一路径）→ 置 user_deleted；hash 变视为重新发布
        if record.get("doc_id") and doc_state == "missing" and not record.get("user_deleted"):
            self._mark_user_deleted(tenant_id, native_id)
            record["user_deleted"] = True

        # ① 用户删除抑制：hash 未变 → 静默跳过零计费
        if record.get("user_deleted") and old_hash == rendered.content_hash:
            self._finish_item(
                tenant_id, run["id"], native_id, action="skip", status="skipped"
            )
            self._touch_record(tenant_id, native_id)
            return "skip"

        # ② 未变跳过：hash 同 + doc active + pipeline 同 → skip；metadata 先比对后写
        if (
            old_hash == rendered.content_hash
            and record.get("pipeline_version") == PIPELINE_VERSION
            and doc_state == "active"
        ):
            self._handle_skip_metadata(tenant_id, run, record, rendered)
            return "skip"

        # ③ restore：doc 软删（下架/删除后重新命中）且 hash 未变 → 复用旧 chunks 零计费
        if doc_state == "deleted" and old_hash == rendered.content_hash:
            self._restore_document(tenant_id, run, record, rendered)
            return "restore"

        # ④ new/update（含 user_deleted 但 hash 变 = 重新发布，清标记重建）
        action = "new" if not record.get("doc_id") or doc_state == "missing" else "update"
        chunks = build_chunks(rendered.content_md)
        if not chunks:
            self._finish_item(
                tenant_id, run["id"], native_id, action="skip", status="skipped",
                error_code=ERR_CONTENT_EMPTY, error_message="分块结果为空",
            )
            return "skip"
        embedding_client = self._get_embedding_client()
        embedding_client.reset_usage()
        embeddings = await embedding_client.embed_batch([c.text for c in chunks])
        embedding_tokens = int(getattr(embedding_client, "last_usage_tokens", 0) or 0)

        doc_id = await self._persist_document_tx(
            tenant_id, run, record, rendered, chunks, embeddings, action,
            rendered.expires_at,
        )
        self._finish_item(
            tenant_id, run["id"], native_id, action=action, status="success",
        )
        # 计费（业务提交后独立提交，fail-open）
        self._bill_embedding(tenant_id, run["id"], native_id, doc_id,
                             rendered.title, embedding_tokens)
        return action

    def _handle_skip_metadata(self, tenant_id: str, run: Dict[str, Any],
                              record: Dict[str, Any], rendered: RenderedDoc) -> None:
        """零计费跳过；metadata 值变才一次轻量 UPDATE（顺带刷新 expires_at：
        deadline 变更不进 hash，走本路径保持文档有效期与源一致）。"""
        doc_id = record.get("doc_id")
        if doc_id:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT metadata FROM documents WHERE id = %s AND tenant_id = %s",
                    (doc_id, tenant_id),
                )
                row = cursor.fetchone()
                if row and not metadata_equivalent(row["metadata"], rendered.metadata, run):
                    fresh = dict(rendered.metadata)
                    fresh["pipeline_version"] = PIPELINE_VERSION
                    fresh["sync_run_id"] = run["id"]
                    cursor.execute(
                        "UPDATE documents SET metadata = %s, file_type = 'json', "
                        "expires_at = %s, updated_at = now() "
                        "WHERE id = %s AND tenant_id = %s",
                        (dump_metadata(fresh), rendered.expires_at, doc_id, tenant_id),
                    )
                    conn.commit()
        self._finish_item(
            tenant_id, run["id"], rendered.external_id, action="skip", status="skipped"
        )
        self._touch_record(tenant_id, rendered.external_id)

    def _restore_document(self, tenant_id: str, run: Dict[str, Any],
                          record: Dict[str, Any], rendered: RenderedDoc) -> None:
        """③ restore：恢复 active 复用旧 chunks 零计费；行消失（用户删）→ 自愈标记。"""
        doc_id = record.get("doc_id")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE documents SET status = 'active', updated_at = now() "
                "WHERE id = %s AND tenant_id = %s AND status = 'deleted'",
                (doc_id, tenant_id),
            )
            restored = cursor.rowcount == 1
            cursor.execute(
                """
                UPDATE bs_content_sync_records
                SET processing_status = 'success', last_synced_at = now(),
                    last_checked_at = now(), fail_count = 0, next_retry_at = NULL,
                    error_message = NULL
                WHERE tenant_id = %s AND module = %s AND native_id = %s
                """,
                (tenant_id, self.MODULE, rendered.external_id),
            )
            conn.commit()
        if restored:
            self._finish_item(
                tenant_id, run["id"], rendered.external_id, action="restore",
                status="success", billing_status="not_required",
            )
        else:
            # rowcount=0：区分 doc 行消失（用户删，自愈）与状态已变化（幂等无动作）
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM documents WHERE id = %s AND tenant_id = %s",
                    (doc_id, tenant_id),
                )
                missing = cursor.fetchone() is None
                conn.commit()
            if missing:
                self._mark_user_deleted(tenant_id, rendered.external_id)
            self._finish_item(
                tenant_id, run["id"], rendered.external_id, action="skip", status="skipped",
                error_code=None if not missing else "user_deleted",
                error_message=None if not missing else "恢复时文档行已消失，置 user_deleted",
            )

    async def _persist_document_tx(
        self, tenant_id: str, run: Dict[str, Any], record: Dict[str, Any],
        rendered: RenderedDoc, chunks, embeddings, action: str,
        expires_at: Optional[Any],
    ) -> int:
        """单事务落 documents/chunks/chunks_vec + records（origin+external_id upsert）。"""
        metadata = dict(rendered.metadata)
        metadata["pipeline_version"] = PIPELINE_VERSION
        metadata["sync_run_id"] = run["id"]
        category_source_type = self._category_source_type(
            tenant_id, rendered.category_display_name
        )
        external_id = rendered.external_id

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
                            origin, external_id, status, expires_at
                        ) VALUES (NULL, %s, %s, %s, NULL, 'json', '', %s, %s, %s,
                                  %s, %s, NULL, %s, %s, %s, 'active', %s)
                        ON CONFLICT (tenant_id, origin, external_id) WHERE external_id IS NOT NULL
                        DO NOTHING
                        RETURNING id
                        """,
                        (
                            tenant_id, rendered.title, category_source_type,
                            len(rendered.content_md.encode("utf-8")), len(chunks),
                            "text-embedding-v3", rendered.content_md,
                            dump_metadata(metadata), f"doc_{uuid.uuid4().hex[:12]}",
                            self.DOC_ORIGIN, external_id, expires_at,
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
                            (tenant_id, self.DOC_ORIGIN, external_id),
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
                            file_type = 'json', status = 'active', expires_at = %s,
                            updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (
                            rendered.title, rendered.content_md,
                            dump_metadata(metadata), len(chunks),
                            "text-embedding-v3", category_source_type, expires_at,
                            doc_id, tenant_id,
                        ),
                    )
                    from src.knowledge.vector_db.vector_db import get_vector_db

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
                from src.knowledge.vector_db.vector_db import get_vector_db

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
                        tenant_id, self.MODULE, rendered.external_id,
                    ),
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

    # ==================== 计费（fail-open） ====================

    def _bill_embedding(self, tenant_id: str, run_id: int, native_id: str,
                        doc_id: int, title: str, embedding_tokens: int) -> None:
        """embedding 计费：复用通用 _record_knowledge_embedding_billing（模块 source_type）。"""
        billing_status = "not_required"
        reference = None
        credits = 0.0
        if embedding_tokens > 0:
            billing_status = "unknown"
            try:
                from src.knowledge.service import knowledge_service

                record = knowledge_service._record_knowledge_embedding_billing(
                    tenant_id=tenant_id,
                    user_id=None,
                    doc_id=doc_id,
                    file_filename=title,
                    embedding_tokens=embedding_tokens,
                    summary_usage=None,
                    source_type=self.EMBEDDING_SOURCE_TYPE,
                    user_message_prefix=self.EMBEDDING_USER_MESSAGE_PREFIX,
                )
                if record and record.get("record_id"):
                    billing_status = "charged"
                    reference = record["record_id"]
                    credits = float(record.get("credit_cost") or 0)
                else:
                    credits = 0.0
            except Exception as e:  # noqa: BLE001 计费失败不影响已入库内容
                logger.opt(exception=True).error(
                    "{} embedding 计费异常（记 unknown 不重扣）: {}",
                    self.MODULE, sanitize_error_info(str(e)),
                )
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE bs_content_sync_items
                    SET billing_status = %s,
                        billing_reference = COALESCE(billing_reference, %s),
                        credits_charged = credits_charged + %s,
                        embedding_tokens = embedding_tokens + %s
                    WHERE run_id = %s AND native_id = %s AND tenant_id = %s
                    """,
                    (
                        billing_status, reference, credits, embedding_tokens,
                        run_id, native_id, tenant_id,
                    ),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("{} item 计费合并失败: {}", self.MODULE, type(e).__name__)

    # ==================== 软删（下架/删除事件/到期）与对账 ====================

    def soft_delete_docs(
        self, tenant_id: str, run_id: int, records: List[Dict[str, Any]],
        reason: str,
    ) -> int:
        """批量软删：documents.status='deleted'（仅 active 行），出 delete item。

        rowcount=0 有两种情形，必须区分：doc 行已消失（知识库硬删是行消失唯一
        路径）→ user_deleted 自愈标记；doc 已是 deleted（同轮其他路径先删过）
        → 幂等无动作，绝不能误标 user_deleted。返回实际软删行数。
        """
        deleted = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for record in records:
                if not record.get("doc_id") or record.get("user_deleted"):
                    continue
                cursor.execute(
                    "UPDATE documents SET status = 'deleted', updated_at = now() "
                    "WHERE id = %s AND tenant_id = %s AND status = 'active'",
                    (record["doc_id"], tenant_id),
                )
                if cursor.rowcount:
                    deleted += 1
                    self._finish_item_conn(
                        cursor, tenant_id, run_id, record["native_id"], action="delete"
                    )
                    continue
                cursor.execute(
                    "SELECT status FROM documents WHERE id = %s AND tenant_id = %s",
                    (record["doc_id"], tenant_id),
                )
                row = cursor.fetchone()
                if row is None:
                    # doc 行已消失：用户删除自愈
                    cursor.execute(
                        "UPDATE bs_content_sync_records SET user_deleted = TRUE, "
                        "last_checked_at = now() "
                        "WHERE tenant_id = %s AND module = %s AND native_id = %s "
                        "AND NOT user_deleted",
                        (tenant_id, self.MODULE, record["native_id"]),
                    )
            conn.commit()
        if deleted:
            logger.bind(module=self.MODULE).info(
                "{} 软删 {} 条文档（{}）", self.MODULE, deleted, reason
            )
        return deleted

    def _finish_item_conn(self, cursor, tenant_id: str, run_id: int,
                          native_id: str, *, action: str) -> None:
        cursor.execute(
            """
            INSERT INTO bs_content_sync_items
                (tenant_id, run_id, module, native_id, action, status, billing_status,
                 started_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, 'success', 'not_required', now(), now())
            ON CONFLICT (run_id, native_id) DO NOTHING
            """,
            (tenant_id, run_id, self.MODULE, native_id, action),
        )

    def restore_doc(self, tenant_id: str, run_id: int, record: Dict[str, Any],
                    expires_at: Optional[Any] = None) -> bool:
        """直接恢复 active（目录比对发现重新上架；内容变更由后续内容增量/对账重建）。

        expires_at 一并按源侧最新 deadline 刷新（过期→延期场景软删期间 deadline
        已变，恢复时不能沿用旧行的过期时间）；返回是否实际恢复。
        """
        if not record.get("doc_id") or record.get("user_deleted"):
            return False
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE documents SET status = 'active', expires_at = %s, "
                "updated_at = now() "
                "WHERE id = %s AND tenant_id = %s AND status = 'deleted'",
                (expires_at, record["doc_id"], tenant_id),
            )
            restored = cursor.rowcount == 1
            if restored:
                cursor.execute(
                    """
                    UPDATE bs_content_sync_records
                    SET processing_status = 'success', last_checked_at = now(),
                        fail_count = 0, next_retry_at = NULL, error_message = NULL
                    WHERE tenant_id = %s AND module = %s AND native_id = %s
                    """,
                    (tenant_id, self.MODULE, record["native_id"]),
                )
            conn.commit()
        if restored and run_id:
            self._finish_item(
                tenant_id, run_id, record["native_id"], action="restore",
                status="success", billing_status="not_required",
            )
        return restored

    # ==================== run 终态 ====================

    def _fail_run(self, tenant_id: str, run_id: int, owner_token: str,
                  error_code: str, error: Exception) -> None:
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
                (message, tenant_id, self.MODULE),
            )
            conn.commit()

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

    def _finalize_run(
        self, tenant_id: str, run_id: int, owner_token: str, *,
        fetch_complete: bool, total_reported: Optional[int],
        new_sync_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按 item 终态聚合计数 → run 终态；成功才推进 sources 与 sync_state 水位。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT action, status, COUNT(*) AS cnt,
                       COALESCE(SUM(credits_charged), 0) AS credits,
                       COALESCE(SUM(embedding_tokens), 0) AS emb_tokens
                FROM bs_content_sync_items WHERE run_id = %s GROUP BY action, status
                """,
                (run_id,),
            )
            counts = {"new": 0, "update": 0, "skip": 0, "delete": 0, "restore": 0}
            failed = 0
            credits = 0.0
            emb_tokens_total = 0
            for row in cursor.fetchall():
                action = row["action"]
                if action in counts:
                    counts[action] += int(row["cnt"])
                if row["status"] == "failed":
                    failed += int(row["cnt"])
                credits += float(row["credits"])
                emb_tokens_total += int(row["emb_tokens"])
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM bs_content_sync_items "
                "WHERE run_id = %s AND status IN ('pending', 'running')",
                (run_id,),
            )
            items_all_terminal = cursor.fetchone()["cnt"] == 0
            run_status = "success" if (fetch_complete and items_all_terminal) else "partial_failed"
            if failed:
                run_status = "partial_failed"
            cursor.execute(
                """
                UPDATE bs_content_sync_runs
                SET status = %s, new_count = %s, updated_count = %s, skipped_count = %s,
                    deleted_count = %s, restored_count = %s, failed_count = %s,
                    embedding_tokens = %s, credits_charged = %s,
                    fetch_complete = %s, total_reported = %s, completed_at = now()
                WHERE id = %s AND owner_token = %s AND status = 'running'
                """,
                (
                    run_status, counts["new"], counts["update"], counts["skip"],
                    counts["delete"], counts["restore"], failed,
                    emb_tokens_total, credits, fetch_complete, total_reported,
                    run_id, owner_token,
                ),
            )
            # 水位只在 run 终态成功后推进（失败重试自然重放）；sync_state 为 None
            # 的模块（sccy 全量即对账）不触碰该列
            if run_status == "success":
                if new_sync_state is not None:
                    cursor.execute(
                        "UPDATE bs_content_sync_sources "
                        "SET last_sync_at = now(), last_error = NULL, sync_state = %s, "
                        "    updated_at = now() "
                        "WHERE tenant_id = %s AND module = %s",
                        (
                            json.dumps(new_sync_state, ensure_ascii=False),
                            tenant_id, self.MODULE,
                        ),
                    )
                else:
                    cursor.execute(
                        "UPDATE bs_content_sync_sources "
                        "SET last_sync_at = now(), last_error = NULL "
                        "WHERE tenant_id = %s AND module = %s",
                        (tenant_id, self.MODULE),
                    )
            else:
                cursor.execute(
                    "UPDATE bs_content_sync_sources "
                    "SET last_sync_at = now() WHERE tenant_id = %s AND module = %s",
                    (tenant_id, self.MODULE),
                )
            conn.commit()
        return {"run_id": run_id, "status": run_status, "counts": counts, "failed": failed}


def compute_content_hash_of(content_md: str) -> str:
    """供模块外（对账脚本等）复算 hash 的入口（与渲染层同口径）。"""
    return compute_content_hash(content_md)
