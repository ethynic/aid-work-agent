#!/usr/bin/env python3
"""
租户数据迁移脚本

支持跨数据库迁移知识库和业务数据。
可作为 CLI 脚本运行，也可被 API 导入调用。

用法:
  python scripts/tenant_migrate_kb.py \
    --source-db aid_work_agent2 \
    --source-tenant tenant_abc123 \
    --tables kb,knowledge_categories,travel_quote \
    --mode replace \
    --dry-run
"""

import argparse
import os
import shutil
import sys
import uuid as uuid_mod
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from loguru import logger


def _get_db_config(database: str) -> dict:
    """从 DATABASE_URL 解析连接参数，替换数据库名"""
    from urllib.parse import urlparse

    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/aid_work_agent")
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": database,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
    }


def _connect(config: dict) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    return conn


def _count(conn, table: str, tenant_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s", (tenant_id,))
        return cur.fetchone()[0]


def _get_rows(conn, table: str, tenant_id: str, columns: List[str] = None) -> List[Dict]:
    cols = ", ".join(columns) if columns else "*"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {cols} FROM {table} WHERE tenant_id = %s ORDER BY id", (tenant_id,))
        return cur.fetchall()


def _delete_by_tenant(conn, table: str, tenant_id: str):
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))


def _delete_chunks_vec_by_tenant(conn, tenant_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks_vec WHERE chunk_id IN ("
            "  SELECT c.id FROM chunks c "
            "  JOIN documents d ON c.doc_id = d.id "
            "  WHERE d.tenant_id = %s"
            ")",
            (tenant_id,),
        )


def _delete_chunks_by_tenant(conn, tenant_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE doc_id IN ("
            "  SELECT id FROM documents WHERE tenant_id = %s"
            ")",
            (tenant_id,),
        )


def _batch_insert(conn, table: str, columns: List[str], rows: List[tuple], page_size: int = 500):
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    col_names = ", ".join(columns)
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows, page_size=page_size)


def _build_uuid_id_map(conn, table: str, uuids: List[str], tenant_id: str = None) -> Dict[str, int]:
    """查询 UUID -> 新生成的数字 ID 映射。tenant_id 为 None 时不过滤租户（用于 chunks 等无 tenant_id 列的表）"""
    if not uuids:
        return {}
    with conn.cursor() as cur:
        if tenant_id is not None:
            cur.execute(
                f"SELECT id, uuid FROM {table} WHERE tenant_id = %s AND uuid = ANY(%s)",
                (tenant_id, uuids),
            )
        else:
            cur.execute(
                f"SELECT id, uuid FROM {table} WHERE uuid = ANY(%s)",
                (uuids,),
            )
        return {row[1]: row[0] for row in cur.fetchall()}


def _migrate_knowledge_categories(
    source_conn, target_conn, source_tenant: str, target_tenant: str,
    mode: str, dry_run: bool,
) -> Dict[str, Any]:
    """Phase 1: 知识库分类迁移"""
    table = "knowledge_categories"
    source_count = _count(source_conn, table, source_tenant)
    target_count_before = _count(target_conn, table, target_tenant)

    if dry_run:
        return {"table": table, "source_count": source_count, "target_count_before": target_count_before,
                "inserted": 0, "skipped": 0, "deleted": 0}

    inserted = 0
    skipped = 0
    deleted = 0

    if mode == "replace":
        deleted = target_count_before
        _delete_by_tenant(target_conn, table, target_tenant)

    rows = _get_rows(source_conn, table, source_tenant)
    for row in rows:
        row_uuid = row.get("uuid")
        if not row_uuid:
            row_uuid = f"kc_{uuid_mod.uuid4().hex[:12]}"

        if mode == "merge":
            with target_conn.cursor() as cur:
                cur.execute("SELECT id FROM knowledge_categories WHERE uuid = %s", (row_uuid,))
                if cur.fetchone():
                    skipped += 1
                    continue

        with target_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO knowledge_categories (tenant_id, source_type, display_name, uuid, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (target_tenant, row["source_type"], row.get("display_name") or row["source_type"],
                 row_uuid, row.get("created_at", datetime.now()), row.get("updated_at", datetime.now())),
            )
            inserted += 1

    return {"table": table, "source_count": source_count, "target_count_before": target_count_before,
            "inserted": inserted, "skipped": skipped, "deleted": deleted}


def _build_target_knowledge_path(target_tenant: str, src_path: str, target_storage: str) -> tuple:
    """构造知识库文档迁移的目标路径（新规范）。

    知识库文档属于 `knowledge` 场景，目标路径统一落到
    `storage/tenants/{target_tenant}/knowledge/{basename}`。

    Returns:
        (tgt_rel, full_tgt) 元组：
          - tgt_rel: 相对路径，写入 `documents.file_path`
          - full_tgt: 磁盘写入路径（`{target_storage}/{target_tenant}/knowledge/{basename}`）
    """
    from src.core.storage import get_tenant_storage_path, normalize_tenant_id

    basename = os.path.basename(src_path)
    tgt_rel = get_tenant_storage_path(target_tenant, "knowledge", basename)
    # full_tgt 必须与 tgt_rel 走同一 normalize_tenant_id 剥离 tenant_ 前缀，
    # 否则 tgt_rel=tenants/b/... 与 full_tgt=tenants/tenant_b/... 不一致
    full_tgt = os.path.join(target_storage, normalize_tenant_id(target_tenant), "knowledge", basename)
    return tgt_rel, full_tgt


def _migrate_kb(
    source_conn, target_conn, source_tenant: str, target_tenant: str,
    mode: str, dry_run: bool, source_storage: str, target_storage: str,
) -> Dict[str, Any]:
    """Phase 2: 知识库三表（documents → chunks → chunks_vec）+ 文件复制"""
    doc_count = _count(source_conn, "documents", source_tenant)
    target_doc_before = _count(target_conn, "documents", target_tenant)

    # chunks 表没有 tenant_id，通过 JOIN documents 统计
    def _count_chunks_by_tenant(conn, tenant_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM chunks c JOIN documents d ON c.doc_id = d.id WHERE d.tenant_id = %s",
                (tenant_id,),
            )
            return cur.fetchone()[0]

    chunk_count = _count_chunks_by_tenant(source_conn, source_tenant)
    target_chunk_before = _count_chunks_by_tenant(target_conn, target_tenant)

    if dry_run:
        return {
            "documents": {"source_count": doc_count, "target_before": target_doc_before, "inserted": 0, "files_copied": 0},
            "chunks": {"source_count": chunk_count, "target_before": target_chunk_before, "inserted": 0},
            "chunks_vec": {"inserted": 0},
        }

    if mode == "replace":
        _delete_chunks_vec_by_tenant(target_conn, target_tenant)
        _delete_chunks_by_tenant(target_conn, target_tenant)
        _delete_by_tenant(target_conn, "documents", target_tenant)

    # 获取源文档
    source_docs = _get_rows(source_conn, "documents", source_tenant)
    if not source_docs:
        return {"documents": {"inserted": 0}, "chunks": {"inserted": 0}, "chunks_vec": {"inserted": 0}}

    # merge 模式：排除目标库已存在的 UUID
    if mode == "merge":
        source_uuids = [d["uuid"] for d in source_docs if d.get("uuid")]
        if source_uuids:
            existing_map = _build_uuid_id_map(target_conn, "documents", source_uuids, target_tenant)
            source_docs = [d for d in source_docs if d.get("uuid") not in existing_map]

    if not source_docs:
        return {"documents": {"inserted": 0}, "chunks": {"inserted": 0}, "chunks_vec": {"inserted": 0}}

    # 收集所有源文档的 chunk 数据
    source_doc_ids = [d["id"] for d in source_docs]
    with source_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM chunks WHERE doc_id = ANY(%s) ORDER BY id", (source_doc_ids,))
        all_source_chunks = cur.fetchall()

    # 插入文档
    doc_columns = [
        "user_id", "tenant_id", "title", "source_type", "file_type", "file_path",
        "file_size", "total_chunks", "embedding_model", "thumbnail_path", "duration",
        "width", "height", "mime_type", "raw_text", "metadata",
        "summary", "uuid", "created_at", "updated_at",
    ]
    doc_rows = []
    # 源 doc_id -> 实际插入的 doc_uuid（含随机 fallback）。
    # 修复既有 bug：源文档 uuid 为空时，插入阶段与后续 chunk 关联 / file_path
    # 更新阶段各自生成不同随机 uuid，导致 get() 命中 None、chunk 丢失、file_path 更新被跳过。
    # 统一用插入阶段生成的 doc_uuid 作为映射键，三处对齐。
    doc_id_to_uuid: Dict[int, str] = {}
    for d in source_docs:
        doc_uuid = d.get("uuid") or f"doc_{uuid_mod.uuid4().hex[:12]}"
        doc_id_to_uuid[d["id"]] = doc_uuid
        doc_rows.append((
            d.get("user_id"), target_tenant, d["title"], d["source_type"], d["file_type"],
            d.get("file_path"), d.get("file_size"), d.get("total_chunks"),
            d.get("embedding_model"), d.get("thumbnail_path"), d.get("duration"),
            d.get("width"), d.get("height"), d.get("mime_type"),
            d.get("raw_text"), d.get("metadata"),
            d.get("summary"), doc_uuid, d.get("created_at"), d.get("updated_at"),
        ))
    _batch_insert(target_conn, "documents", doc_columns, doc_rows)

    # 查询 UUID→new_id 映射
    actual_uuids = [row[-3] for row in doc_rows]  # uuid is 3rd from end
    doc_uuid_to_new_id = _build_uuid_id_map(target_conn, "documents", actual_uuids, target_tenant)

    # 插入 chunks
    chunk_columns = ["doc_id", "chunk_index", "text", "tokens", "metadata", "uuid", "created_at"]
    chunk_rows = []
    chunk_uuids = []
    for c in all_source_chunks:
        source_doc_id = c["doc_id"]
        new_doc_id = doc_uuid_to_new_id.get(doc_id_to_uuid.get(source_doc_id))
        if new_doc_id is None:
            continue
        chunk_uuid = c.get("uuid") or f"chunk_{uuid_mod.uuid4().hex[:12]}"
        chunk_rows.append((
            new_doc_id, c["chunk_index"], c["text"], c.get("tokens"),
            c.get("metadata"), chunk_uuid, c.get("created_at"),
        ))
        chunk_uuids.append(chunk_uuid)
    _batch_insert(target_conn, "chunks", chunk_columns, chunk_rows)

    # 查询 chunk UUID→new_id 映射（chunks 表无 tenant_id）
    chunk_uuid_to_new_id = _build_uuid_id_map(target_conn, "chunks", chunk_uuids)

    # 插入 chunks_vec
    vec_inserted = 0
    if chunk_uuids and chunk_uuid_to_new_id:
        source_chunk_ids = [c["id"] for c in all_source_chunks]
        with source_conn.cursor() as cur:
            cur.execute("SELECT chunk_id, embedding FROM chunks_vec WHERE chunk_id = ANY(%s)", (source_chunk_ids,))
            vec_rows = cur.fetchall()

        source_chunk_id_to_uuid = {c["id"]: c.get("uuid") or f"chunk_{uuid_mod.uuid4().hex[:12]}" for c in all_source_chunks}
        vec_data = []
        for v in vec_rows:
            source_cid = v[0]
            embedding = v[1]
            chunk_uuid = source_chunk_id_to_uuid.get(source_cid)
            if chunk_uuid and chunk_uuid in chunk_uuid_to_new_id:
                vec_data.append((chunk_uuid_to_new_id[chunk_uuid], embedding))

        if vec_data:
            with target_conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (%s, %s) ON CONFLICT (chunk_id) DO NOTHING",
                    vec_data,
                    page_size=500,
                )
            vec_inserted = len(vec_data)

    # 文件复制
    files_copied = 0
    for d in source_docs:
        src_path = d.get("file_path")
        if not src_path:
            continue
        # 构建源文件完整路径
        if source_storage:
            full_src = os.path.join(source_storage, src_path) if not os.path.isabs(src_path) else src_path
        else:
            full_src = src_path

        # 兼容 file_path 中包含 storage/ 前缀的情况：
        # 数据库中存的是 storage/uploads/...，source_storage 已经是 /app/source_storage
        # 直接 join 会变成 /app/source_storage/storage/uploads/...（多一层 storage/）
        if not os.path.exists(full_src) and source_storage and src_path.startswith("storage/"):
            alt_path = src_path[len("storage/"):]
            full_src = os.path.join(source_storage, alt_path) if not os.path.isabs(alt_path) else alt_path

        if not os.path.exists(full_src):
            logger.warning(f"源文件不存在: {full_src}")
            continue

        # 目标路径：新规范 storage/tenants/{target_tenant}/knowledge/{basename}
        tgt_rel, full_tgt = _build_target_knowledge_path(target_tenant, src_path, target_storage)

        os.makedirs(os.path.dirname(full_tgt), exist_ok=True)
        shutil.copy2(full_src, full_tgt)
        files_copied += 1

        # 更新目标 documents.file_path（存相对路径，与知识库 API 存储格式一致）
        new_doc_id = doc_uuid_to_new_id.get(doc_id_to_uuid.get(d["id"]))
        if new_doc_id and target_storage:
            with target_conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET file_path = %s WHERE id = %s",
                    (tgt_rel, new_doc_id),
                )

    return {
        "documents": {"source_count": doc_count, "target_before": target_doc_before, "inserted": len(doc_rows), "files_copied": files_copied},
        "chunks": {"source_count": len(all_source_chunks), "target_before": target_chunk_before, "inserted": len(chunk_rows)},
        "chunks_vec": {"inserted": vec_inserted},
    }


def _migrate_travel_quote_table(
    source_conn, target_conn, table: str, source_tenant: str, target_tenant: str,
    mode: str, dry_run: bool,
) -> Dict[str, Any]:
    """Phase 3: 单张旅游报价表迁移"""
    source_count = _count(source_conn, table, source_tenant)
    target_before = _count(target_conn, table, target_tenant)

    if dry_run:
        return {"table": table, "source_count": source_count, "target_before": target_before,
                "inserted": 0, "skipped": 0, "deleted": 0}

    inserted = 0
    skipped = 0
    deleted = 0

    if mode == "replace":
        deleted = target_before
        _delete_by_tenant(target_conn, table, target_tenant)

    rows = _get_rows(source_conn, table, source_tenant)
    if not rows:
        return {"table": table, "source_count": source_count, "target_before": target_before,
                "inserted": 0, "skipped": 0, "deleted": deleted}

    if mode == "merge":
        uuids = [r.get("uuid") for r in rows if r.get("uuid")]
        if uuids:
            existing_map = _build_uuid_id_map(target_conn, table, uuids, target_tenant)
            existing_uuids = set(existing_map.keys())
            rows_to_insert = [r for r in rows if r.get("uuid") not in existing_uuids]
            skipped = len(rows) - len(rows_to_insert)
            rows = rows_to_insert

    if rows:
        # 排除 id 列
        sample = rows[0]
        skip_cols = {"id"}
        columns = [k for k in sample.keys() if k not in skip_cols]
        row_tuples = []
        for r in rows:
            row_uuid = r.get("uuid")
            if not row_uuid:
                row_uuid = f"{_UUID_PREFIX_MAP.get(table, 'bs')}_{uuid_mod.uuid4().hex[:12]}"
            vals = []
            for col in columns:
                if col == "tenant_id":
                    vals.append(target_tenant)
                elif col == "uuid":
                    vals.append(row_uuid)
                else:
                    vals.append(r.get(col))
            row_tuples.append(tuple(vals))
        _batch_insert(target_conn, table, columns, row_tuples)
        inserted = len(row_tuples)

    return {"table": table, "source_count": source_count, "target_before": target_before,
            "inserted": inserted, "skipped": skipped, "deleted": deleted}


_UUID_PREFIX_MAP = {
    "bs_travel_quote_vehicles": "tqv",
    "bs_travel_quote_meals": "tqm",
    "bs_travel_quote_guides": "tqg",
    "bs_travel_quote_fees": "tqf",
    "bs_travel_quote_seasons": "tqs",
}

_TRAVEL_QUOTE_TABLES = list(_UUID_PREFIX_MAP.keys())


def run_migration(
    source_db: str,
    target_db: str = None,
    source_tenant: str = None,
    target_tenant: str = None,
    tables: List[str] = None,
    mode: str = "replace",
    source_storage: str = None,
    target_storage: str = None,
    dry_run: bool = False,
) -> dict:
    """
    执行租户数据迁移。

    参数:
        source_db: 源数据库名
        target_db: 目标数据库名（默认从 DATABASE_URL 读取）
        source_tenant: 源租户 ID
        target_tenant: 目标租户 ID（默认同 source_tenant）
        tables: 要迁移的表组列表，可选: kb, knowledge_categories, travel_quote
        mode: "replace" 或 "merge"
        source_storage: 源存储根路径
        target_storage: 目标存储根路径
        dry_run: 仅统计不执行

    返回:
        {success, summary: {...}, errors: [...]}
    """
    if not source_tenant:
        return {"success": False, "error": "source_tenant 不能为空"}

    if target_db is None:
        target_db = os.getenv("DATABASE_URL", "").split("/")[-1] if "/" in os.getenv("DATABASE_URL", "") else "aid_work_agent"
    if target_tenant is None:
        target_tenant = source_tenant
    if tables is None:
        tables = ["kb", "knowledge_categories", "travel_quote"]
    if source_storage is None:
        source_storage = os.getenv("SOURCE_STORAGE", "/app/source_storage")
    if target_storage is None:
        from src.core.storage import get_tenants_storage_root
        target_storage = get_tenants_storage_root()

    source_config = _get_db_config(source_db)
    target_config = _get_db_config(target_db)

    logger.info(f"迁移配置: {source_db} -> {target_db}, 租户 {source_tenant} -> {target_tenant}, 模式: {mode}, dry_run: {dry_run}")

    source_conn = None
    target_conn = None
    summary = {}
    errors = []

    try:
        source_conn = _connect(source_config)
        target_conn = _connect(target_config)
        logger.info("数据库连接成功")

        # knowledge_categories
        if "knowledge_categories" in tables:
            try:
                summary["knowledge_categories"] = _migrate_knowledge_categories(
                    source_conn, target_conn, source_tenant, target_tenant, mode, dry_run,
                )
            except Exception as e:
                errors.append(f"knowledge_categories: {e}")
                logger.error(f"迁移 knowledge_categories 失败: {e}", exc_info=True)

        # kb (documents → chunks → chunks_vec)
        if "kb" in tables:
            try:
                summary["kb"] = _migrate_kb(
                    source_conn, target_conn, source_tenant, target_tenant,
                    mode, dry_run, source_storage, target_storage,
                )
            except Exception as e:
                errors.append(f"kb: {e}")
                logger.error(f"迁移 kb 失败: {e}", exc_info=True)

        # travel_quote
        if "travel_quote" in tables:
            tq_summary = {}
            for table in _TRAVEL_QUOTE_TABLES:
                try:
                    tq_summary[table] = _migrate_travel_quote_table(
                        source_conn, target_conn, table, source_tenant, target_tenant, mode, dry_run,
                    )
                except Exception as e:
                    errors.append(f"{table}: {e}")
                    logger.error(f"迁移 {table} 失败: {e}", exc_info=True)
            summary["travel_quote"] = tq_summary

        if not dry_run and not errors:
            target_conn.commit()
            logger.info("迁移事务已提交")
        elif dry_run:
            logger.info("dry_run 模式，不执行实际操作")

        return {"success": len(errors) == 0, "summary": summary, "errors": errors}

    except Exception as e:
        logger.error(f"迁移失败: {e}", exc_info=True)
        return {"success": False, "error": str(e), "summary": summary, "errors": errors}
    finally:
        if source_conn:
            try:
                source_conn.close()
            except Exception:
                pass
        if target_conn:
            try:
                target_conn.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="租户数据迁移工具")
    parser.add_argument("--source-db", required=True, help="源数据库名")
    parser.add_argument("--target-db", default=None, help="目标数据库名（默认从 DATABASE_URL 读取）")
    parser.add_argument("--source-tenant", required=True, help="源租户 ID")
    parser.add_argument("--target-tenant", default=None, help="目标租户 ID（默认同 source_tenant）")
    parser.add_argument("--tables", default="kb,knowledge_categories,travel_quote",
                        help="要迁移的表组，逗号分隔")
    parser.add_argument("--mode", default="replace", choices=["replace", "merge"],
                        help="迁移模式: replace(先删后插) / merge(按UUID去重)")
    parser.add_argument("--source-storage", default=None, help="源存储根路径")
    parser.add_argument("--target-storage", default=None, help="目标存储根路径")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不执行")

    args = parser.parse_args()

    # 确保 settings 可导入
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    tables = [t.strip() for t in args.tables.split(",")]

    result = run_migration(
        source_db=args.source_db,
        target_db=args.target_db,
        source_tenant=args.source_tenant,
        target_tenant=args.target_tenant,
        tables=tables,
        mode=args.mode,
        source_storage=args.source_storage,
        target_storage=args.target_storage,
        dry_run=args.dry_run,
    )

    import json
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
