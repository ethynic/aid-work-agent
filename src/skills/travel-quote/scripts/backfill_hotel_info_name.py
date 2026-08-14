# -*- coding: utf-8 -*-
"""
回填酒店知识库 info_text 缺失的"酒店名称：xxx"行

背景：旧版 parser 的 LLM 偶尔漏输出"酒店名称"行，导致 chunk0（info_text，
唯一被向量化 + ILIKE 名称检索的字段）不含酒店名，按酒店名搜索搜不到
（如 doc 973 "西江天合盛景民宿" 搜不到）。

本脚本遍历 hotel_resource 老数据：
1. 从 documents.title（"酒店：xxx"）提取酒店名
2. 若 chunk0 info_text 不含"酒店名称"行，前置补上
3. 重新 embedding，更新 chunks.text / chunks_vec.embedding / documents.summary

用法：
    venv/Scripts/python.exe src/skills/travel-quote/scripts/backfill_hotel_info_name.py --dry-run
    venv/Scripts/python.exe src/skills/travel-quote/scripts/backfill_hotel_info_name.py --tenant-id tenant_xxx
    venv/Scripts/python.exe src/skills/travel-quote/scripts/backfill_hotel_info_name.py
"""
import argparse
import sys
from pathlib import Path
from typing import List, Optional

# 支持直接 python 运行：把项目根加入 sys.path 以 import src.* / 读 .env
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在任何 src.* import 之前加载 .env，否则 DB_CONFIG 等模块级常量
# 会读到默认值（localhost:5432）而非 .env 的 DATABASE_URL
from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from loguru import logger  # noqa: E402

import hotel_retriever  # noqa: E402


def _extract_hotel_name(title: str) -> str:
    """从 documents.title（"酒店：xxx"）提取酒店名"""
    if "：" in title:
        return title.split("：", 1)[-1].strip()
    return title.strip()


def _has_name_line(info_text: str) -> bool:
    """info_text 是否已含以"酒店名称"开头的行"""
    return any(line.strip().startswith("酒店名称") for line in (info_text or "").splitlines())


def backfill(tenant_id: Optional[str] = None, dry_run: bool = False) -> None:
    # 脚本独立运行需先初始化 PG 连接池（import database 会触发配置加载）
    from src.db.database import init_postgres_pool
    init_postgres_pool()

    retriever = hotel_retriever.HotelRetriever()
    source_type = hotel_retriever.HotelRetriever.SOURCE_TYPE

    with retriever._get_conn() as conn:
        if tenant_id:
            conn.execute("""
                SELECT d.id AS doc_id, d.title, c.id AS chunk_id, c.text
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.source_type = %s AND d.tenant_id = %s
                ORDER BY d.id
            """, (source_type, tenant_id))
        else:
            conn.execute("""
                SELECT d.id AS doc_id, d.title, c.id AS chunk_id, c.text
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
                WHERE d.source_type = %s
                ORDER BY d.id
            """, (source_type,))
        rows = conn.fetchall()

    logger.info(f"[Backfill] 待检查 {len(rows)} 条酒店文档 (tenant={tenant_id or 'ALL'})")

    updated = 0
    skipped = 0
    failed: List[str] = []

    for row in rows:
        doc_id = row["doc_id"]
        title = row["title"] or ""
        info_text = row["text"] or ""
        chunk_id = row["chunk_id"]

        if _has_name_line(info_text):
            skipped += 1
            continue

        hotel_name = _extract_hotel_name(title)
        if not hotel_name:
            failed.append(f"doc_id={doc_id}: 无法从 title 提取酒店名 ({title!r})")
            continue

        new_text = f"酒店名称：{hotel_name}\n{info_text}"

        if dry_run:
            logger.info(f"[Backfill][DRY] doc_id={doc_id} 将补名称行: {hotel_name}")
            updated += 1
            continue

        try:
            client = retriever._get_embedding_client()
            client.reset_usage()
            embedding = retriever._embed(new_text)

            # 补计费：回填脚本的 embedding 消耗（独立落库，归属租户便于审计）
            if client.last_usage_tokens > 0:
                try:
                    from src.services.session_record import record_admin_embedding_usage
                    record_admin_embedding_usage(
                        client,
                        tenant_id=tenant_id,
                        source_label="backfill_hotel_info_name",
                        source_type="background_embedding",
                    )
                except Exception:
                    logger.debug("Failed to record backfill embedding usage", exc_info=True)

            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            with retriever._get_conn() as conn:
                conn.execute(
                    "UPDATE chunks SET text = %s, tokens = %s WHERE id = %s",
                    (new_text, len(new_text), chunk_id),
                )
                conn.execute(
                    "UPDATE chunks_vec SET embedding = %s::vector WHERE chunk_id = %s",
                    (embedding_str, chunk_id),
                )
                conn.execute(
                    "UPDATE documents SET summary = %s WHERE id = %s",
                    (new_text, doc_id),
                )
                conn.commit()
            updated += 1
            logger.info(f"[Backfill] doc_id={doc_id} 已补名称行: {hotel_name}")
        except Exception as e:
            logger.error(f"[Backfill] doc_id={doc_id} 更新失败: {e}", exc_info=True)
            failed.append(f"doc_id={doc_id}: {e}")

    logger.info(f"[Backfill] 完成: updated={updated} skipped={skipped} failed={len(failed)}")
    if failed:
        logger.warning("[Backfill] 失败明细:\n" + "\n".join(failed[:30]))


def main():
    parser = argparse.ArgumentParser(description="回填酒店知识库 info_text 缺失的酒店名称行")
    parser.add_argument("--tenant-id", default=None, help="指定租户，默认全部 hotel_resource")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    backfill(tenant_id=args.tenant_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
