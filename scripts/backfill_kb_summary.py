#!/usr/bin/env python3
"""知识库文档摘要回填脚本

为 documents 表中 summary 为空但有 raw_text 的文档补生成摘要。
复用 KnowledgeBaseService.generate_summary（chat_lite 通道，已关思考），
计费经 record_background_llm_usage 显式传 lite 模型名（按 lite 单价正确计价）。

用法（生产容器内执行）:
  docker exec aid-agent-api python scripts/backfill_kb_summary.py --dry-run
  docker exec aid-agent-api python scripts/backfill_kb_summary.py
  docker exec aid-agent-api python scripts/backfill_kb_summary.py --doc-id 2264 --doc-id 2268
  docker exec aid-agent-api python scripts/backfill_kb_summary.py --tenant-id tenant_c148f4efb4dc
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
from loguru import logger

# 保证 scripts/ 目录直接运行时能 import src.*
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _get_db_config() -> dict:
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/aid_work_agent")
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "").lstrip("/") or "aid_work_agent",
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
    }


def _fetch_targets(args) -> list:
    """查询摘要为空但有正文的文档"""
    sql = """
        SELECT id, tenant_id, user_id, title, file_type, raw_text, created_at
        FROM documents
        WHERE (summary IS NULL OR summary = '')
          AND raw_text IS NOT NULL AND length(btrim(raw_text)) > 0
    """
    params: list = []
    if args.doc_id:
        sql += " AND id = ANY(%s)"
        params.append(args.doc_id)
    if args.tenant_id:
        sql += " AND tenant_id = %s"
        params.append(args.tenant_id)
    sql += " ORDER BY created_at ASC"
    if args.limit:
        sql += " LIMIT %s"
        params.append(args.limit)

    conn = psycopg2.connect(**_get_db_config())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


async def _backfill_one(doc: dict, svc) -> bool:
    """为单个文档生成并回写摘要，返回是否成功"""
    from src.services.session_record import record_background_llm_usage
    from src.config import settings

    summary = await svc.generate_summary(text=doc["raw_text"], title=doc["title"])

    # 计费：显式传 lite 模型名，走兜底路径按 lite 单价独立落库
    usage = getattr(svc, "_last_summary_usage", None)
    try:
        lite_model = settings.llm.get_lite_target()[1]
    except Exception:
        lite_model = None
    record_background_llm_usage(usage, source="kb_summary_backfill", model=lite_model)

    if not summary or not summary.strip():
        logger.warning(f"[backfill] doc_id={doc['id']} 摘要生成失败（空），跳过回写: {doc['title']}")
        return False

    conn = psycopg2.connect(**_get_db_config())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET summary = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
                (summary.strip(), doc["id"], doc["tenant_id"]),
            )
        conn.commit()
        logger.info(
            f"[backfill] doc_id={doc['id']} tenant={doc['tenant_id']} 已回写摘要"
            f"（{len(summary.strip())} 字）: {doc['title']}"
        )
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _main(args) -> int:
    from src.knowledge.service import knowledge_service

    targets = _fetch_targets(args)
    logger.info(f"[backfill] 命中 {len(targets)} 个摘要为空的文档")
    if not targets:
        return 0

    for doc in targets:
        logger.info(
            f"[backfill] 待处理 doc_id={doc['id']} tenant={doc['tenant_id']} "
            f"file_type={doc['file_type']} raw_len={len(doc['raw_text'])} created={doc['created_at']} "
            f"title={doc['title']}"
        )

    if args.dry_run:
        logger.info("[backfill] dry-run 模式，不实际生成摘要")
        return 0

    ok, fail = 0, 0
    for doc in targets:
        try:
            if await _backfill_one(doc, knowledge_service):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            logger.opt(exception=True).error(f"[backfill] doc_id={doc['id']} 回填失败: {e}")

    logger.info(f"[backfill] 完成: 成功 {ok} / 失败 {fail} / 共 {len(targets)}")
    return 0 if fail == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="知识库文档摘要回填")
    parser.add_argument("--doc-id", type=int, action="append", help="指定文档 id，可多次传入")
    parser.add_argument("--tenant-id", help="限定租户")
    parser.add_argument("--limit", type=int, help="最多处理 N 条")
    parser.add_argument("--dry-run", action="store_true", help="只列出待处理文档，不实际生成")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
