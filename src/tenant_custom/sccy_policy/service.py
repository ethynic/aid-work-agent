"""水城创业网同步服务（SccyPolicySyncService，每日全量即对账）。

单表千级行：全量拉 zc_article（含内容列），有效行（audit=1 AND closed=0 且
分类未隐藏）全量走 hash 三态（未变跳过零计费）；无效行 → 软删（sccy 删除/
下架都是状态位翻转，研究 §2.2）；重新上架 → restore。无水位（sync_state
不使用）。

渲染/落库/账本复用 lyjy_employment 共用底层（base + 文章模板），来源标注
「水城创业网」，分类路由「创业政策」。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tenant_custom.lyjy_employment.base import ERR_FETCH_FAILED, KbSyncServiceBase
from src.tenant_custom.lyjy_employment.fetcher import (
    FetchError,
    build_engine,
    load_connection_config,
)
from src.tenant_custom.lyjy_employment.renderer import (
    PiiRejectedError,
    SOURCE_NAME_SCCY,
    render_article,
)
from src.tenant_custom.lyjy_employment.service import RunInterruptedError
from src.tenant_custom.sccy_policy import fetcher


class SccyPolicySyncService(KbSyncServiceBase):
    MODULE = "sccy_policy"
    DOC_ORIGIN = "sccy_policy"
    EMBEDDING_SOURCE_TYPE = "sccy_policy_embedding"
    CATEGORY_DISPLAY_NAME = "创业政策"
    EMBEDDING_USER_MESSAGE_PREFIX = "水城创业网政策向量化"

    async def _execute_run(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """run 执行总入口：异常 → _fail_run 兜底，防永久 running 死锁。"""
        try:
            return await self._execute_run_inner(tenant_id, run, owner_token, limit)
        except RunInterruptedError as e:
            logger.bind(module=self.MODULE).warning(
                "sccy_policy run 中断 run_id={}: {}", run["id"], e
            )
            return {"run_id": run["id"], "status": "interrupted"}
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(
                "sccy_policy run 执行异常 tenant={}: {}", tenant_id, e
            )
            self._fail_run(
                tenant_id, run["id"], owner_token,
                ERR_FETCH_FAILED if isinstance(e, FetchError) else "internal_error", e,
            )
            return {"run_id": run["id"], "status": "failed"}

    async def _execute_run_inner(
        self, tenant_id: str, run: Dict[str, Any], owner_token: str,
        limit: Optional[int],
    ) -> Dict[str, Any]:
        run_id = run["id"]
        # 源行守卫（=授权记录）：无源行不执行
        source_row = await asyncio.to_thread(self._load_source_row, tenant_id)
        if not source_row:
            self._abort_run_interrupted(
                run_id, owner_token, "数据源未开通或已停用，终止执行（不拉取不计费）"
            )
            return {"run_id": run_id, "status": "interrupted"}
        connection = load_connection_config(source_row)
        engine = build_engine(connection)
        try:
            cate_rows, article_rows = await asyncio.to_thread(
                lambda: (fetcher.fetch_cate_rows(engine), fetcher.fetch_articles(engine))
            )
        finally:
            engine.dispose()
        if limit is not None:
            article_rows = article_rows[:limit]

        cate_maps = fetcher.build_cate_maps(cate_rows)
        # 记录账本刷新（全量 upsert；payload 存目录展示字段与状态原值）
        await asyncio.to_thread(self._refresh_records, tenant_id, article_rows, lambda r: {
            "native_id": str(r["article_id"]),
            "external_prefix": "zcarticle",
            "name": str(r.get("title") or ""),
            "audit": r.get("audit"),
            "closed": r.get("closed"),
            "cate_id": str(r.get("cate_id") or ""),
            "dateline": int(r.get("dateline") or 0),
        })
        module_records = await asyncio.to_thread(self._load_module_records, tenant_id)
        backoff = await asyncio.to_thread(self._backoff_native_ids, tenant_id)

        valid_natives: set = set()
        valid_rows: List[dict] = []
        for row in article_rows:
            if fetcher.article_valid(row, cate_maps["hidden_ids"]):
                valid_natives.add(f"zcarticle:{row['article_id']}")
                valid_rows.append(row)
        # 对账（全量即对账）：不在有效集的记录 → 软删（状态位翻转/隐藏分类，含硬删旁路）。
        # 截断 run（--limit 冒烟）有效集不完整，跳过软删防误删
        if limit is None:
            off_shelf = [
                r for r in module_records
                if str(r["external_id"]) not in valid_natives
                and r.get("doc_id") and not r.get("user_deleted")
            ]
            if off_shelf:
                self.soft_delete_docs(tenant_id, run_id, off_shelf, "zc_article 状态位/隐藏分类")
        # 有效集内 doc 已软删的恢复不在此处理：下方全量 hash 比对的三态机制
        # 自带 restore 路径（deleted + hash 同 → 复用 chunks 零计费；hash 变 → 重建），
        # 预先直接恢复会让 item 被 content pass 的 skip 覆盖

        # 内容全量 hash 比对（未变跳过零计费）
        for row in valid_rows:
            native_id = f"zcarticle:{row['article_id']}"
            if native_id in backoff:
                continue
            if not self._heartbeat(run_id, owner_token):
                raise RunInterruptedError("run 已被中断/回收，停止处理")
            try:
                rendered = render_article(
                    "zc_article", row,
                    category_path=cate_maps["path_of"].get(str(row.get("cate_id")), ""),
                    region="",
                    source_name=SOURCE_NAME_SCCY,
                    id_column="article_id",
                )
                await self.process_rendered(tenant_id, run, rendered)
            except PiiRejectedError as e:
                logger.warning("sccy_policy PII 拦截 {}: {}", native_id, e)
                await asyncio.to_thread(
                    self._mark_record_failed, tenant_id, run_id, native_id,
                    e, "pii_rejected",
                )
            except Exception as e:  # noqa: BLE001 单条失败不阻断 run
                logger.opt(exception=True).error(
                    "sccy_policy 单条处理失败 {}: {}", native_id, e
                )
                await asyncio.to_thread(
                    self._mark_record_failed, tenant_id, run_id, native_id, e,
                )

        return self._finalize_run(
            tenant_id, run_id, owner_token,
            fetch_complete=limit is None,
            total_reported=len(article_rows),
            new_sync_state=None,  # 全量即对账，无水位
        )


sccy_policy_service = SccyPolicySyncService()
