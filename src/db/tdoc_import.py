"""腾讯文档知识库导入：凭据绑定 + 导入批次/批次项 + 租户级导入映射（数据库访问）

表：tdoc_credentials / tdoc_import_batches / tdoc_import_items / tdoc_imported_docs
（deploy/init-postgres.sql 知识库表段，增量见 db_update.yaml 2026-10-20 批次）

敏感字段 access_token/refresh_token 加密存储由调用方（credentials.py）完成，
本模块只做透明 CRUD，不接触明文语义。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection

# item 终态（调度器据此判断批次是否收敛）
ITEM_TERMINAL_STATUSES = ("succeeded", "failed", "skipped")


class TdocCredentialDB:
    """凭据绑定 CRUD（加密字段原样存取）"""

    @staticmethod
    def upsert(tenant_id: str, user_id: str, record: Dict[str, Any]) -> bool:
        """按 (tenant_id, user_id) UPSERT 凭据绑定。

        record 需含：client_id / access_token_enc / open_id，可选 refresh_token_enc。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tdoc_credentials (
                    tenant_id, user_id, client_id, access_token_enc,
                    refresh_token_enc, open_id, status
                ) VALUES (%s, %s, %s, %s, %s, %s, 'active')
                ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                    client_id = EXCLUDED.client_id,
                    access_token_enc = EXCLUDED.access_token_enc,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    open_id = EXCLUDED.open_id,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    tenant_id,
                    user_id,
                    record["client_id"],
                    record["access_token_enc"],
                    record.get("refresh_token_enc"),
                    record["open_id"],
                ),
            )
            conn.commit()
            return True

    @staticmethod
    def get(tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tdoc_credentials WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def delete(tenant_id: str, user_id: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tdoc_credentials WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def mark_expired(tenant_id: str, user_id: str) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_credentials SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = %s AND user_id = %s
                """,
                (tenant_id, user_id),
            )
            conn.commit()


class TdocImportBatchDB:
    """导入批次 CRUD"""

    @staticmethod
    def create(
        batch_id: str,
        tenant_id: str,
        user_id: str,
        items: List[Dict[str, Any]],
        error_message: str = "",
    ) -> str:
        """创建批次 + items（单事务）。items 每项含：
        tdoc_file_id / title / doc_type / status / last_modify_time / error_message。

        skip_count 在创建时即落定（无变化跳过在预检阶段判定）；其余计数由
        _refresh_batch_progress 在执行期滚动更新。
        """
        skip_count = sum(1 for i in items if i["status"] == "skipped")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tdoc_import_batches (
                    batch_id, tenant_id, user_id, status,
                    total_count, skip_count, error_message
                ) VALUES (%s, %s, %s, 'queued', %s, %s, %s)
                """,
                (batch_id, tenant_id, user_id, len(items), skip_count, error_message),
            )
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO tdoc_import_items (
                        batch_id, tenant_id, tdoc_file_id, title, doc_type,
                        status, last_modify_time, error_message
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        batch_id,
                        tenant_id,
                        item["tdoc_file_id"],
                        item.get("title"),
                        item.get("doc_type"),
                        item["status"],
                        item.get("last_modify_time"),
                        item.get("error_message"),
                    ),
                )
            conn.commit()
            logger.info(f"腾讯文档导入批次已创建 batch_id={batch_id} total={len(items)} skip={skip_count}")
            return batch_id

    @staticmethod
    def get(batch_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """tenant_id 传 None 供后台驱动按 batch_id 直查（调度器无租户上下文）；
        API 侧必须传 tenant_id 严格隔离。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is None:
                cursor.execute(
                    "SELECT * FROM tdoc_import_batches WHERE batch_id = %s",
                    (batch_id,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tdoc_import_batches WHERE batch_id = %s AND tenant_id = %s",
                    (batch_id, tenant_id),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_tenant(
        tenant_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[List[Dict[str, Any]], int]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS c FROM tdoc_import_batches WHERE tenant_id = %s",
                (tenant_id,),
            )
            total = int(cursor.fetchone()["c"])
            offset = (page - 1) * page_size
            cursor.execute(
                """
                SELECT * FROM tdoc_import_batches WHERE tenant_id = %s
                ORDER BY created_at DESC LIMIT %s OFFSET %s
                """,
                (tenant_id, page_size, offset),
            )
            return [dict(r) for r in cursor.fetchall()], total

    @staticmethod
    def claim_queued(limit: int = 10) -> List[Dict[str, Any]]:
        """调度器领取 queued 批次：原子置 running，返回被领取的批次。跨租户（后台驱动）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_import_batches SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE batch_id IN (
                    SELECT batch_id FROM tdoc_import_batches
                    WHERE status = 'queued'
                    ORDER BY created_at ASC LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (limit,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            conn.commit()
            return rows

    @staticmethod
    def refresh_progress(batch_id: str) -> Optional[Dict[str, Any]]:
        """按 items 终态滚动汇总批次计数，items 全终态时收敛批次状态。返回更新后的批次。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_import_batches b SET
                    success_count = s.n_success,
                    fail_count = s.n_fail,
                    skip_count = s.n_skip,
                    status = CASE
                        WHEN s.n_open = 0 AND s.n_fail = 0 AND s.n_success > 0 THEN 'succeeded'
                        WHEN s.n_open = 0 AND s.n_success = 0 AND s.n_fail > 0 THEN 'failed'
                        WHEN s.n_open = 0 THEN 'partial'
                        ELSE 'running'
                    END,
                    updated_at = CURRENT_TIMESTAMP
                FROM (
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'succeeded') AS n_success,
                        COUNT(*) FILTER (WHERE status = 'failed') AS n_fail,
                        COUNT(*) FILTER (WHERE status = 'skipped') AS n_skip,
                        COUNT(*) FILTER (WHERE status NOT IN ('succeeded', 'failed', 'skipped')) AS n_open
                    FROM tdoc_import_items WHERE batch_id = %s
                ) s
                WHERE b.batch_id = %s
                RETURNING b.*
                """,
                (batch_id, batch_id),
            )
            row = cursor.fetchone()
            conn.commit()
            return dict(row) if row else None


class TdocImportItemDB:
    """批次项 CRUD"""

    @staticmethod
    def list_by_batch(batch_id: str) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tdoc_import_items WHERE batch_id = %s ORDER BY id ASC",
                (batch_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def claim_due(limit: int = 10) -> List[Dict[str, Any]]:
        """调度器领取到期 item（pending 且无延迟或退避到期），原子置 running。

        只领取所属批次已处于 running 状态的 item（claim_queued 先行）。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_import_items SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE id IN (
                    SELECT i.id FROM tdoc_import_items i
                    JOIN tdoc_import_batches b ON b.batch_id = i.batch_id
                    WHERE i.status = 'pending'
                      AND b.status = 'running'
                      AND (i.next_retry_at IS NULL OR i.next_retry_at <= CURRENT_TIMESTAMP)
                    ORDER BY i.id ASC LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                (limit,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            conn.commit()
            return rows

    @staticmethod
    def mark_succeeded(item_id: int, document_id: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_import_items
                SET status = 'succeeded', document_id = %s, error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (document_id, item_id),
            )
            conn.commit()

    @staticmethod
    def mark_failed(item_id: int, error_message: str, retry_delay_seconds: Optional[int] = None) -> None:
        """标记失败。retry_delay_seconds 非空时保持 pending 并安排退避重试（超最大次数则终态失败）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if retry_delay_seconds is not None:
                cursor.execute(
                    """
                    UPDATE tdoc_import_items
                    SET status = 'pending',
                        error_message = %s,
                        retry_count = retry_count + 1,
                        next_retry_at = CURRENT_TIMESTAMP + make_interval(secs => %s),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND retry_count < %s
                    """,
                    (error_message, retry_delay_seconds, item_id, _MAX_RETRIES),
                )
                if cursor.rowcount > 0:
                    conn.commit()
                    return
                # 达到最大重试次数：终态失败，保留最后一次错误信息
            cursor.execute(
                """
                UPDATE tdoc_import_items
                SET status = 'failed', error_message = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (error_message, item_id),
            )
            conn.commit()

    @staticmethod
    def reset_failed_for_retry(batch_id: str) -> int:
        """重试批次内全部 failed items：重置 pending、清退避时间与错误（retry_count 保留）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE tdoc_import_items
                SET status = 'pending', error_message = NULL, next_retry_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE batch_id = %s AND status = 'failed'
                """,
                (batch_id,),
            )
            reset = cursor.rowcount
            cursor.execute(
                """
                UPDATE tdoc_import_batches
                SET status = CASE WHEN status IN ('failed', 'partial') THEN 'running' ELSE status END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE batch_id = %s
                """,
                (batch_id,),
            )
            conn.commit()
            return reset


_MAX_RETRIES = 5


class TdocImportedDocDB:
    """租户级导入映射（覆盖更新/跳过判断的唯一依据）"""

    @staticmethod
    def get(tenant_id: str, tdoc_file_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tdoc_imported_docs WHERE tenant_id = %s AND tdoc_file_id = %s",
                (tenant_id, tdoc_file_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_all(tenant_id: str) -> Dict[str, Dict[str, Any]]:
        """租户全量映射（文件选择器标注 imported 状态用）。key = tdoc_file_id。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tdoc_imported_docs WHERE tenant_id = %s",
                (tenant_id,),
            )
            return {r["tdoc_file_id"]: dict(r) for r in cursor.fetchall()}

    @staticmethod
    def upsert(
        tenant_id: str, tdoc_file_id: str, document_id: int,
        title: str, last_modify_time: Optional[int],
    ) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tdoc_imported_docs (
                    tenant_id, tdoc_file_id, document_id, title, last_modify_time
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, tdoc_file_id) DO UPDATE SET
                    document_id = EXCLUDED.document_id,
                    title = EXCLUDED.title,
                    last_modify_time = EXCLUDED.last_modify_time,
                    imported_at = CURRENT_TIMESTAMP
                """,
                (tenant_id, tdoc_file_id, document_id, title, last_modify_time),
            )
            conn.commit()

    @staticmethod
    def list_document_ids(tenant_id: str) -> List[int]:
        """租户映射的全部 document_id（级联删除校验用）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT document_id FROM tdoc_imported_docs WHERE tenant_id = %s",
                (tenant_id,),
            )
            return [int(r["document_id"]) for r in cursor.fetchall()]
