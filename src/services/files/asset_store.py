"""文件元数据持久化（file_assets 表）。

Redis `uploaded_file:{file_id}` 元数据只有 24h TTL，过期后磁盘自愈重建会丢失
原始文件名（下载 Content-Disposition 退化为 file_xxx.ext）。本模块把元数据
持久化到 DB，Redis 降级为缓存；所有函数内部容错，DB 故障不阻断上传/下载主流程。

name 保护约定（与 download_registry.STORAGE_FILE_NAME_RE 口径一致）：
存量 name 是存储名形态（file_{12位hex}[.ext]）或为空时允许覆盖；
已有用户可见名（如 "04_标准客房卫生间_洁具与五金.xlsx"）不被磁盘自愈名冲掉。
"""

import json
from typing import Any, Dict, Optional

from loguru import logger

# 与 download_registry.STORAGE_FILE_NAME_RE 保持一致（SQL 端独立内嵌）
_SQL_STORAGE_NAME_RE = r'^file_[0-9a-f]{12}(\.[^.]+)?$'

_UPSERT_SQL = """
INSERT INTO file_assets
    (file_id, tenant_id, user_id, name, path, size, mime_type, type, source, extra)
VALUES (
    %(file_id)s, %(tenant_id)s, %(user_id)s, %(name)s,
    %(path)s, %(size)s, %(mime_type)s, %(type)s, %(source)s, COALESCE(%(extra)s, '{}'::jsonb))
ON CONFLICT (file_id) DO UPDATE SET
    path = COALESCE(EXCLUDED.path, file_assets.path),
    size = COALESCE(EXCLUDED.size, file_assets.size),
    mime_type = COALESCE(EXCLUDED.mime_type, file_assets.mime_type),
    type = COALESCE(EXCLUDED.type, file_assets.type),
    source = EXCLUDED.source,
    -- 未传 extra（序列化为 '{}' 无信息量）时保留旧值（如 ImageRegistry 写入的宽高等）
    extra = COALESCE(NULLIF(EXCLUDED.extra, '{}'::jsonb), file_assets.extra),
    -- tenant/user 新值优先：磁盘自愈/补录晚于上传时能回填归属（P1 修复）；
    -- 新值为 NULL 时保留旧值，不会清空
    tenant_id = COALESCE(EXCLUDED.tenant_id, file_assets.tenant_id),
    user_id = COALESCE(EXCLUDED.user_id, file_assets.user_id),
    name = CASE
        WHEN file_assets.name IS NULL OR file_assets.name = '' THEN EXCLUDED.name
        WHEN file_assets.name ~ %(storage_name_re)s THEN EXCLUDED.name
        ELSE file_assets.name
    END,
    updated_at = CURRENT_TIMESTAMP
"""


def _safe_db(handler, *, context: str) -> Any:
    """执行 DB 操作，异常仅告警不抛（DB 故障不能阻断上传/下载主流程）。"""
    from src.db.database import get_pooled_connection, return_pooled_connection

    conn = None
    try:
        conn = get_pooled_connection()
        return handler(conn)
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.opt(exception=True).warning(f"file_assets {context} 失败（不影响主流程）: {e}")
        return None
    finally:
        if conn is not None:
            try:
                return_pooled_connection(conn)
            except Exception:
                pass


def upsert_file_asset(
    meta: Dict[str, Any],
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    source: str = '',
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """按 file_id upsert 文件元数据。

    已存在的用户可见 name 不会被覆盖（见模块 docstring 的 name 保护约定）。
    """
    if not meta or not str(meta.get("file_id") or "").strip():
        return

    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_SQL,
                {
                    "file_id": str(meta["file_id"]),
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "name": str(meta.get("name") or "") or None,
                    "path": meta.get("path"),
                    "size": meta.get("size"),
                    "mime_type": meta.get("mime_type"),
                    "type": meta.get("type"),
                    "source": source or None,
                    "extra": psycopg2_json(extra),
                    "storage_name_re": _SQL_STORAGE_NAME_RE,
                },
            )
        conn.commit()

    _safe_db(_run, context=f"upsert {meta['file_id']}")


def psycopg2_json(extra: Optional[Dict[str, Any]]):
    """extra dict -> jsonb 参数；None 传 SQL NULL（配合 upsert 的 COALESCE 保留旧值）。"""
    if extra is None:
        return None
    return json.dumps(extra, ensure_ascii=False)


def get_file_asset(file_id: str) -> Optional[Dict[str, Any]]:
    """按 file_id 读取持久化元数据，转为 uploaded_file meta 形态的 dict。"""
    if not file_id:
        return None

    def _run(conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_id, name, path, size, mime_type, type "
                "FROM file_assets WHERE file_id = %s",
                (file_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        info = {
            "file_id": row[0],
            "name": row[1],
            "path": row[2],
            "size": int(row[3]) if row[3] is not None else 0,
            "mime_type": row[4],
            "type": row[5] or "file",
        }
        conn.commit()
        return info

    return _safe_db(_run, context=f"get {file_id}")


def delete_file_asset(file_id: str) -> None:
    """按 file_id 删除持久化元数据（文件删除入口同步调用）。"""
    if not file_id:
        return

    def _run(conn):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM file_assets WHERE file_id = %s", (file_id,))
        conn.commit()

    _safe_db(_run, context=f"delete {file_id}")
