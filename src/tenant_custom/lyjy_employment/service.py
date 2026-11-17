"""聊易就业同步服务（LyjyEmploymentSyncService，「目录全量 + 内容增量」双层 run）。

四步结构（研究 §7.4）：① 目录全量轻比对（下架→立即软删、恢复→restore、
消失两击兜底）；② `*_delete` 表删除事件（id 集合差，当轮软删）；③ 到期扫描
（deadline 过期在招岗位软删，expires_at 双保险下退化为账本清理）；④ 内容增量
（qs_jobs refreshtime 水位 / 文章表 addtime 水位，仅有效状态行，hash 三态落库）。

水位与已处理删除 id 集合存 source 行 sync_state（模块自管），只在 run 终态成功
后推进；失败重试自然重放（软删/重建均幂等）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tenant_custom.lyjy_employment import fetcher
from src.tenant_custom.lyjy_employment.base import (
    ERR_FETCH_FAILED,
    KbSyncServiceBase,
)
from src.tenant_custom.lyjy_employment.renderer import (
    PiiRejectedError,
    build_article_context,
    deadline_to_expires_at,
    render_article,
    render_job,
)

SYNC_TABLES = ("qs_jobs", "qs_guide", "qs_article", "qs_notice", "qs_explain")
# 有 audit 列的文章表（notice/explain 仅 is_display）
AUDITED_ARTICLE_TABLES = ("qs_guide", "qs_article")


class RunInterruptedError(RuntimeError):
    """heartbeat 失效（run 被中断/回收）：停止处理，水位不推进，重试自然重放。"""


def _int_or_0(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def jobs_catalog_valid(row: Dict[str, Any], now_seconds: int) -> bool:
    """目录行有效判定（审核通过 + 展示中 + 未关闭 + 未到期）。

    到期并入有效判定：过期岗位即使状态位仍有效也不允许 restore（防「③到期
    软删 → 下轮目录比对恢复」循环）；close_type/deadline 语义待客户确认，
    条件集中此处与 fetcher 常量一处可调。
    """
    if _int_or_0(row.get("audit")) != 1 or _int_or_0(row.get("display")) != 1:
        return False
    close_type = row.get("close_type")
    if close_type is not None and _int_or_0(close_type) != 0:
        return False
    deadline = _int_or_0(row.get("deadline"))
    if deadline > 0 and deadline < now_seconds:
        return False
    return True


def article_catalog_valid(table: str, row: Dict[str, Any]) -> bool:
    """文章目录行有效判定（guide/article 看 audit+is_display，其余仅 is_display）。"""
    if _int_or_0(row.get("is_display")) != 1:
        return False
    if table in AUDITED_ARTICLE_TABLES and _int_or_0(row.get("audit")) != 1:
        return False
    return True


class LyjyEmploymentSyncService(KbSyncServiceBase):
    MODULE = "lyjy_employment"
    DOC_ORIGIN = "lyjy_employment"
    EMBEDDING_SOURCE_TYPE = "lyjy_employment_embedding"
    CATEGORY_DISPLAY_NAME = "在招职位"  # 兜底默认分类（文档按渲染路由归属，不用此值）
    EMBEDDING_USER_MESSAGE_PREFIX = "聊易就业数据源向量化"

    async def _execute_run(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """run 执行总入口：任何阶段异常 → _fail_run 兜底，防永久 running 死锁。"""
        try:
            return await self._execute_run_inner(tenant_id, run, owner_token, limit)
        except fetcher.FetchError as e:
            logger.opt(exception=True).error(
                "lyjy_employment 拉取失败 tenant={}: {}", tenant_id, e
            )
            self._fail_run(tenant_id, run["id"], owner_token, ERR_FETCH_FAILED, e)
            return {"run_id": run["id"], "status": "failed"}
        except RunInterruptedError as e:
            logger.bind(module="lyjy_employment").warning(
                "lyjy_employment run 中断 run_id={}: {}", run["id"], e
            )
            return {"run_id": run["id"], "status": "interrupted"}
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                "lyjy_employment run 执行异常 tenant={}: {}", tenant_id, e
            )
            self._fail_run(tenant_id, run["id"], owner_token, "internal_error", e)
            return {"run_id": run["id"], "status": "failed"}

    async def _execute_run_inner(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int],
    ) -> Dict[str, Any]:
        run_id = run["id"]
        # 源行守卫（=授权记录）：无源行不执行——否则 revoke 后按空配置绕过
        source_row = await asyncio.to_thread(self._load_source_row, tenant_id)
        if not source_row:
            self._abort_run_interrupted(
                run_id, owner_token, "数据源未开通或已停用，终止执行（不拉取不计费）"
            )
            return {"run_id": run_id, "status": "interrupted"}
        sync_state = self._load_sync_state(source_row)
        selected = self._load_selection_tables(source_row)
        tables = [t for t in SYNC_TABLES if selected is None or t in selected]
        if not tables:
            self._abort_run_interrupted(run_id, owner_token, "同步范围为空（映射表行全关）")
            return {"run_id": run_id, "status": "interrupted"}

        connection = fetcher.load_connection_config(source_row)
        engine = fetcher.build_engine(connection)
        try:
            plan = await asyncio.to_thread(
                self._fetch_plan, engine, tables, sync_state, limit
            )
        finally:
            engine.dispose()

        # 记录账本刷新（目录展示字段进 payload；last_seen_at 供两击对账）
        await asyncio.to_thread(self._refresh_catalog_records, tenant_id, plan)
        module_records = await asyncio.to_thread(self._load_module_records, tenant_id)
        by_table = self._group_records_by_table(module_records)

        # ② 删除事件：id 集合差 → 当轮软删
        processed_deletes = await asyncio.to_thread(
            self._apply_delete_events, tenant_id, run, plan, sync_state, by_table
        )
        # ① 目录状态比对：下架→软删、重新上架→restore、消失两击→软删
        await asyncio.to_thread(
            self._apply_catalog_compare, tenant_id, run, plan, by_table, limit
        )
        # ③ 到期扫描：账本清理（expires_at 已让过期岗位检索不可见）
        await asyncio.to_thread(
            self._apply_expiry_scan, tenant_id, run, plan, by_table
        )
        # ④ 内容增量：渲染 → hash 三态落库
        await self._process_increments(tenant_id, run, owner_token, plan, limit)

        new_state = self._next_sync_state(plan, sync_state, processed_deletes)
        return self._finalize_run(
            tenant_id, run_id, owner_token,
            fetch_complete=plan["fetch_complete"],
            total_reported=plan["total_reported"],
            new_sync_state=new_state,
        )

    # ==================== 拉取（线程内同步执行） ====================

    def _fetch_plan(
        self, engine, tables: List[str], sync_state: Dict[str, Any],
        limit: Optional[int],
    ) -> Dict[str, Any]:
        """四步拉取一次成_plan：异常即抛（FetchError → run failed，水位不推进）。"""
        watermarks = sync_state.get("watermarks") or {}
        catalogs: Dict[str, List[dict]] = {}
        increments: Dict[str, List[dict]] = {}
        deletes: Dict[str, set] = {}
        for table in tables:
            catalogs[table] = fetcher.fetch_catalog(engine, table)
            if table in fetcher.DELETE_TABLES:
                deletes[table] = fetcher.fetch_delete_ids(engine, table)
            watermark = _int_or_0(watermarks.get(table))
            if table == "qs_jobs":
                rows = fetcher.fetch_jobs_increment(engine, watermark)
            else:
                rows = fetcher.fetch_articles_increment(engine, table, watermark)
            if limit is not None:
                rows = rows[:limit]
            increments[table] = rows
        expired_job_ids = (
            fetcher.fetch_expired_job_ids(engine) if "qs_jobs" in tables else []
        )
        dict_cache = fetcher.load_dictionary_cache(engine)
        job_rows = increments.get("qs_jobs") or []
        contacts = fetcher.fetch_contacts(engine, [int(r["id"]) for r in job_rows])
        profiles = fetcher.fetch_company_profiles(
            engine, sorted({_int_or_0(r.get("company_id")) for r in job_rows} - {0})
        )
        return {
            "tables": tables,
            "catalogs": catalogs,
            "deletes": deletes,
            "expired_job_ids": set(expired_job_ids),
            "increments": increments,
            "dict_cache": dict_cache,
            "contacts": contacts,
            "profiles": profiles,
            "fetch_complete": limit is None,
            "total_reported": sum(len(catalogs[t]) for t in tables),
            "now_seconds": int(time.time()),
        }

    # ==================== records 账本刷新 ====================

    def _refresh_catalog_records(self, tenant_id: str, plan: Dict[str, Any]) -> None:
        """目录全量 upsert 记录账本（payload 展示字段 + last_seen_at，宏陶同款）。"""

        def payload_jobs(row: dict) -> dict:
            return {
                "native_id": str(row["id"]),
                "external_prefix": "job",
                "name": str(row.get("jobs_name") or ""),
                "companyname": str(row.get("companyname") or ""),
                "audit": row.get("audit"),
                "display": row.get("display"),
                "close_type": row.get("close_type"),
                "deadline": _int_or_0(row.get("deadline")),
                "refreshtime": _int_or_0(row.get("refreshtime")),
            }

        def payload_article(table: str):
            def payload(row: dict) -> dict:
                return {
                    "native_id": str(row["id"]),
                    "external_prefix": fetcher.TABLE_PREFIXES[table],
                    "name": str(row.get("title") or ""),
                    "audit": row.get("audit"),
                    "is_display": row.get("is_display"),
                    "addtime": _int_or_0(row.get("addtime")),
                }
            return payload

        for table in plan["tables"]:
            if table == "qs_jobs":
                payload_of = payload_jobs
            else:
                payload_of = payload_article(table)
            self._refresh_records(tenant_id, plan["catalogs"][table], payload_of)

    @staticmethod
    def _group_records_by_table(
        module_records: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """module 记录按源表分组（native_id = f"{prefix}:{id}"）。"""
        prefix_to_table = {v: k for k, v in fetcher.TABLE_PREFIXES.items()}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in module_records:
            table = prefix_to_table.get(str(record["external_id"]).split(":", 1)[0])
            if table:
                grouped.setdefault(table, []).append(record)
        return grouped

    # ==================== ② 删除事件 ====================

    def _apply_delete_events(
        self, tenant_id: str, run: Dict[str, Any], plan: Dict[str, Any],
        sync_state: Dict[str, Any], by_table: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, List[int]]:
        """`*_delete` 表全量 id 与 sync_state 已处理集合做差；差集软删。

        只把「本轮存在记录行」的 id 记入已处理集合（无记录行的 id 无动作可做，
        不入集合可让集合保持最小）；集合只增不减（_delete 表只进不出）。
        """
        processed_state: Dict[str, List[int]] = {
            k: list(v) for k, v in (sync_state.get("processed_delete_ids") or {}).items()
        }
        for table in plan["tables"]:
            if table not in fetcher.DELETE_TABLES:
                continue  # 文章表无 _delete 表（测试库实测）：靠状态位 + 消失两击
            processed = set(processed_state.get(table) or [])
            new_ids = plan["deletes"].get(table, set()) - processed
            if not new_ids:
                continue
            records_by_native = {
                str(r["external_id"]): r for r in by_table.get(table, [])
            }
            hit: List[Dict[str, Any]] = []
            for delete_id in new_ids:
                native_id = f"{fetcher.TABLE_PREFIXES[table]}:{delete_id}"
                record = records_by_native.get(native_id)
                if record:
                    hit.append(record)
                    processed.add(int(delete_id))
            if hit:
                self.soft_delete_docs(tenant_id, run["id"], hit, f"{table} 删除事件")
            processed_state[table] = sorted(processed)
        return processed_state

    # ==================== ① 目录状态比对 ====================

    def _apply_catalog_compare(
        self, tenant_id: str, run: Dict[str, Any], plan: Dict[str, Any],
        by_table: Dict[str, List[Dict[str, Any]]], limit: Optional[int],
    ) -> None:
        """下架→软删；重新上架→restore；消失两击→软删（门禁：fetch_complete）。"""
        for table in plan["tables"]:
            catalog_by_native = {
                f"{fetcher.TABLE_PREFIXES[table]}:{r['id']}": r
                for r in plan["catalogs"][table]
            }
            off_shelf: List[Dict[str, Any]] = []
            maybe_deleted: List[Dict[str, Any]] = []
            for record in by_table.get(table, []):
                row = catalog_by_native.get(str(record["external_id"]))
                if row is None:
                    continue  # 消失两击单独处理
                if record.get("user_deleted") or not record.get("doc_id"):
                    continue
                valid = (
                    jobs_catalog_valid(row, plan["now_seconds"])
                    if table == "qs_jobs"
                    else article_catalog_valid(table, row)
                )
                if not valid:
                    off_shelf.append(record)
                else:
                    maybe_deleted.append(record)
            if off_shelf:
                self.soft_delete_docs(tenant_id, run["id"], off_shelf, f"{table} 目录下架")
            # 重新上架：批量查 doc 状态，deleted → restore（复用 chunks 零计费，
            # 内容变更由后续增量/每日对账重建）；岗位按目录行最新 deadline 刷新 expires_at
            doc_states = self._doc_states_bulk(
                tenant_id, [int(r["doc_id"]) for r in maybe_deleted]
            )
            for record in maybe_deleted:
                if doc_states.get(int(record["doc_id"])) == "deleted":
                    expires_at = None
                    if table == "qs_jobs":
                        expires_at = deadline_to_expires_at(
                            catalog_by_native[str(record["external_id"])].get("deadline")
                        )
                    self.restore_doc(tenant_id, run["id"], record, expires_at)
            self._reconcile_missing(
                tenant_id, run, table, by_table.get(table, []),
                set(catalog_by_native), plan["fetch_complete"],
            )

    def _reconcile_missing(
        self, tenant_id: str, run: Dict[str, Any], table: str,
        records: List[Dict[str, Any]], catalog_natives: set, gate: bool,
    ) -> None:
        """消失两击（硬删旁路兜底）：目录中消失且门禁满足才推进 miss_streak。"""
        if not gate:
            return
        missing = [
            r for r in records
            if str(r["external_id"]) not in catalog_natives and not r.get("user_deleted")
        ]
        if not missing:
            return
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for record in missing:
                native_id = str(record["external_id"])
                miss_streak = int(record.get("miss_streak") or 0) + 1
                if miss_streak >= 2 and record.get("doc_id"):
                    cursor.execute(
                        "UPDATE documents SET status = 'deleted', updated_at = now() "
                        "WHERE id = %s AND tenant_id = %s AND status = 'active'",
                        (record["doc_id"], tenant_id),
                    )
                    if cursor.rowcount:
                        self._finish_item_conn(
                            cursor, tenant_id, run["id"], native_id, action="delete"
                        )
                cursor.execute(
                    """
                    UPDATE bs_content_sync_records
                    SET miss_streak = %s, last_checked_at = now()
                    WHERE tenant_id = %s AND module = %s AND native_id = %s
                    """,
                    (miss_streak, tenant_id, self.MODULE, native_id),
                )
            conn.commit()

    # ==================== ③ 到期扫描 ====================

    def _apply_expiry_scan(
        self, tenant_id: str, run: Dict[str, Any], plan: Dict[str, Any],
        by_table: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """deadline 过期且仍处「在招状态」的岗位 → 软删账本清理（expires_at 双保险）。"""
        expired = plan.get("expired_job_ids") or set()
        if not expired:
            return
        records_by_native = {
            str(r["external_id"]): r for r in by_table.get("qs_jobs", [])
        }
        hit = [
            records_by_native[f"job:{job_id}"]
            for job_id in expired
            if f"job:{job_id}" in records_by_native
        ]
        if hit:
            self.soft_delete_docs(tenant_id, run["id"], hit, "岗位到期扫描")

    # ==================== ④ 内容增量 ====================

    async def _process_increments(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        plan: Dict[str, Any], limit: Optional[int],
    ) -> None:
        backoff = await asyncio.to_thread(self._backoff_native_ids, tenant_id)
        for table in plan["tables"]:
            rows = plan["increments"].get(table) or []
            for row in rows:
                native_id = f"{fetcher.TABLE_PREFIXES[table]}:{row['id']}"
                if native_id in backoff:
                    continue  # 失败退避期内跳过（防毒行反复烧）
                if not self._heartbeat(run["id"], owner_token):
                    raise RunInterruptedError(
                        "run 已被中断/回收，停止处理（水位未推进，重试自然重放）"
                    )
                try:
                    rendered = self._render(table, row, plan)
                    action = await self.process_rendered(tenant_id, run, rendered)
                    logger.bind(module=self.MODULE).debug(
                        "增量处理 {} {} → {}", table, native_id, action
                    )
                except PiiRejectedError as e:  # noqa: PERF203 PII 出站拦截：拒渲染记失败
                    logger.warning("lyjy_employment PII 拦截 {} {}: {}", table, native_id, e)
                    await asyncio.to_thread(
                        self._mark_record_failed, tenant_id, run["id"], native_id,
                        e, "pii_rejected",
                    )
                except Exception as e:  # noqa: BLE001 单条失败不阻断 run
                    logger.opt(exception=True).error(
                        "lyjy_employment 单条处理失败 {} {}: {}", table, native_id, e
                    )
                    await asyncio.to_thread(
                        self._mark_record_failed, tenant_id, run["id"], native_id, e,
                    )

    def _render(self, table: str, row: Dict[str, Any], plan: Dict[str, Any]):
        """按源表路由渲染（qs_jobs 职位模板；文章表共用文章模板）。"""
        if table == "qs_jobs":
            contact = plan["contacts"].get(int(row["id"]))
            profile = plan["profiles"].get(_int_or_0(row.get("company_id")))
            return render_job(row, contact, profile, plan["dict_cache"])
        category_path, region = build_article_context(table, row, plan["dict_cache"])
        return render_article(table, row, category_path, region)

    # ==================== 水位推进 ====================

    def _next_sync_state(
        self, plan: Dict[str, Any], sync_state: Dict[str, Any],
        processed_deletes: Dict[str, List[int]],
    ) -> Dict[str, Any]:
        """本轮成功后的新运行态：水位 = 本轮实际拉到的最大值（空则保持旧值）。"""
        watermarks = dict(sync_state.get("watermarks") or {})
        for table in plan["tables"]:
            rows = plan["increments"].get(table) or []
            column = "refreshtime" if table == "qs_jobs" else "addtime"
            max_seen = max((_int_or_0(r.get(column)) for r in rows), default=0)
            if max_seen > _int_or_0(watermarks.get(table)):
                watermarks[table] = max_seen
        return {
            "watermarks": watermarks,
            "processed_delete_ids": processed_deletes,
        }


lyjy_employment_service = LyjyEmploymentSyncService()
