"""
旧路径 storage/uploads/ 到新路径 storage/tenants/{tenant_id}/{scene}/ 的一次性迁移

设计要点：
- 幂等：目标已存在 + 大小相同视为已迁移，跳过；大小不同加后缀避免覆盖
- 多 worker 并发：pg_try_advisory_lock(789012) 防止 Gunicorn 多 worker 重复执行
- Redis 元数据同步：扫 uploaded_file:* 键建 path -> key 反向索引，
  迁移每个文件后更新对应 hash 的 path 字段，避免 /api/files/{file_id}/download 404
- 历史误搬修复：data_sources 场景曾因 _resolve_new_path 缺分支被误搬到
  tenants/{tid}/conversation/data_sources/，启动时自动搬到 tenants/{tid}/data_sources/
- documents.metadata 同步：data-analysis-metadata 类型记录的 source.file_path
  同步重写到新路径前缀（仅动 metadata 列，绝不碰 file_path 列）
- 渠道目录（dingtalk/wecom_kf）跳过：非租户附件，由渠道系统自管

调用入口：migrate_uploads_to_tenants()，在 FastAPI lifespan 中执行（init_database 之后）
"""

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger


# advisory lock key，避开 db_update.sql 用的 123456
_MIGRATION_LOCK_KEY = 789012

# 渠道目录跳过（非租户附件，由渠道系统自管）
_SKIP_TOP_DIRS = {"dingtalk", "wecom_kf"}


def migrate_uploads_to_tenants(project_root: Optional[Path] = None) -> Dict[str, int]:
    """将 storage/uploads/ 下的旧文件迁移到 storage/tenants/{tid}/{scene}/

    Returns:
        统计 dict：{scanned, migrated, skipped, redis_updated, errors, docs_updated}
    """
    project_root = Path(project_root or Path(__file__).resolve().parents[2])
    uploads_root = project_root / "storage" / "uploads"
    tenants_root = project_root / "storage" / "tenants"

    stats = {
        "scanned": 0,
        "migrated": 0,
        "skipped": 0,
        "redis_updated": 0,
        "errors": 0,
        "docs_updated": 0,
    }

    # 获取 advisory lock 防多 worker 并发
    # lock_conn 持有 advisory lock 的物理连接，release 时必须用同一连接 unlock
    lock_acquired, lock_conn = _acquire_advisory_lock()
    if not lock_acquired:
        logger.info("[storage_migration] 其他 worker 正在执行迁移，跳过")
        return stats

    try:
        # 建 Redis path -> key 反向索引（Redis 不可用时返回空 dict，不阻塞磁盘迁移）
        redis_index = _build_redis_path_index()
        logger.info(
            f"[storage_migration] 开始迁移，uploads_root={uploads_root}, "
            f"redis_index_size={len(redis_index)}"
        )

        # Phase A: 扫描 uploads 旧路径，搬到 tenants/{tid}/{scene}/
        if uploads_root.exists():
            for old_path in uploads_root.rglob("*"):
                if not old_path.is_file():
                    continue
                # 跳过 .migrated 标记文件（若存在）
                if old_path.name.startswith("."):
                    continue

                stats["scanned"] += 1

                new_path = _resolve_new_path(old_path, uploads_root, tenants_root)
                if new_path is None:
                    logger.debug(f"[storage_migration] 跳过非租户附件: {old_path}")
                    stats["skipped"] += 1
                    continue

                try:
                    migrated_dst, redis_key = _migrate_file(
                        old_path, new_path, redis_index
                    )
                    stats["migrated"] += 1
                    if redis_key:
                        stats["redis_updated"] += 1
                    logger.debug(
                        f"[storage_migration] 已迁移: {old_path.name} -> {migrated_dst}"
                    )
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(
                        f"[storage_migration] 迁移失败 {old_path} -> {new_path}: {e}",
                        exc_info=True,
                    )
        else:
            logger.info("[storage_migration] storage/uploads/ 不存在，跳过 uploads 扫描")

        # Phase B: 修复历史误搬到 tenants/{tid}/conversation/data_sources/ 的数据分析源文件
        _relocate_misplaced_data_sources(tenants_root, redis_index, stats)

        # Phase C: 修复 documents.metadata.source.file_path 旧路径前缀
        # 仅动 metadata 列，绝不碰 file_path 列（file_path 是知识库字段，独立）
        stats["docs_updated"] = _fix_documents_metadata_paths()

        logger.info(f"[storage_migration] 迁移完成: {stats}")
        return stats
    finally:
        _release_advisory_lock(lock_conn)


def _resolve_new_path(
    old_path: Path, uploads_root: Path, tenants_root: Path
) -> Optional[Path]:
    """根据旧路径推算新路径。返回 None 表示跳过（渠道目录等）。

    迁移规则：
        storage/uploads/conversation/{file}                 -> storage/tenants/_anonymous/conversation/{file}
        storage/uploads/knowledge/{file}                    -> storage/tenants/_anonymous/knowledge/{file}
        storage/uploads/tenant_{tid}/user_{uid}/{file}      -> storage/tenants/{tid}/conversation/{file}
        storage/uploads/tenant_{tid}/knowledge/{file}       -> storage/tenants/{tid}/knowledge/{file}
        storage/uploads/tenant_{tid}/data_sources/{file}    -> storage/tenants/{tid}/data_sources/{file}
        storage/uploads/tenant_{tid}/{file}                 -> storage/tenants/{tid}/conversation/{file}
        storage/uploads/{tid}/knowledge/{file}              -> storage/tenants/{tid}/knowledge/{file}
        storage/uploads/{tid}/data_sources/{file}           -> storage/tenants/{tid}/data_sources/{file}
        storage/uploads/_global/data_sources/{file}         -> storage/tenants/_anonymous/data_sources/{file}
        storage/uploads/{tid}/templates/{file}              -> storage/tenants/{tid}/templates/{file}
        storage/uploads/dingtalk/、wecom_kf/                -> None（跳过）
        其他                                                -> None（跳过 + warning）
    """
    try:
        rel = old_path.relative_to(uploads_root)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None

    top = parts[0]

    # 渠道目录跳过
    if top in _SKIP_TOP_DIRS:
        return None

    # 旧的全局 conversation 目录 -> 匿名租户
    if top == "conversation":
        if len(parts) < 2:
            return None
        return tenants_root / "_anonymous" / "conversation" / Path(*parts[1:])

    # 旧的全局 knowledge 目录 -> 匿名租户（c3f749b 之前的无租户知识库路径）
    if top == "knowledge":
        if len(parts) < 2:
            return None
        return tenants_root / "_anonymous" / "knowledge" / Path(*parts[1:])

    # tenant_{tid}/... 系列
    if top.startswith("tenant_"):
        tid = top[len("tenant_"):]
        if not tid or len(parts) < 2:
            return None

        # tenant_{tid}/user_{uid}/{file} -> conversation
        if parts[1].startswith("user_"):
            if len(parts) < 3:
                return None
            return tenants_root / tid / "conversation" / Path(*parts[2:])

        # tenant_{tid}/knowledge/{file} -> knowledge
        if parts[1] == "knowledge":
            if len(parts) < 3:
                return None
            return tenants_root / tid / "knowledge" / Path(*parts[2:])

        # tenant_{tid}/data_sources/{file} -> data_sources（数据分析源文件）
        if parts[1] == "data_sources":
            if len(parts) < 3:
                return None
            return tenants_root / tid / "data_sources" / Path(*parts[2:])

        # tenant_{tid}/{file} -> conversation
        return tenants_root / tid / "conversation" / Path(*parts[1:])

    # {tid}/knowledge/{file} -> knowledge（c3f749b 风格的租户路径，tid 不带 tenant_ 前缀）
    # parts[1]=="knowledge" 是强约束，避免误识别其他顶层目录
    if len(parts) >= 2 and parts[1] == "knowledge":
        return tenants_root / top / "knowledge" / Path(*parts[2:])

    # {tid}/data_sources/{file} -> data_sources（数据分析源文件，tid 不带 tenant_ 前缀）
    # _global/data_sources/{file} -> _anonymous/data_sources/{file}（无租户回退统一为 _anonymous）
    if len(parts) >= 2 and parts[1] == "data_sources":
        if top == "_global":
            return tenants_root / "_anonymous" / "data_sources" / Path(*parts[2:])
        return tenants_root / top / "data_sources" / Path(*parts[2:])

    # {tid}/templates/{file} -> templates（子智能体模板文件，tid 不带 tenant_ 前缀）
    if len(parts) >= 2 and parts[1] == "templates":
        return tenants_root / top / "templates" / Path(*parts[2:])

    # 其他无法识别
    logger.warning(
        f"[storage_migration] 无法识别的旧路径结构，跳过: {old_path}"
    )
    return None


def _migrate_file(
    src: Path, dst: Path, redis_index: Dict[str, str]
) -> Tuple[Path, Optional[str]]:
    """迁移单个文件，返回 (最终目标路径, 命中的 redis_key 或 None)"""
    # 幂等：目标已存在 + 大小相同 -> 跳过磁盘移动，但仍尝试更新 Redis（可能磁盘已迁但 Redis 没更新）
    final_dst = dst
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size:
            logger.debug(f"[storage_migration] 目标已存在且大小相同，跳过移动: {dst}")
        else:
            # 大小不同：加后缀避免覆盖
            suffix = f"_migrated_{uuid.uuid4().hex[:8]}"
            final_dst = dst.with_name(f"{dst.stem}{suffix}{dst.suffix}")
            shutil.move(str(src), str(final_dst))
    else:
        final_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(final_dst))

    # 同步 Redis 元数据：从反向索引查 src 路径对应的 redis_key
    src_abs = str(src.absolute())
    redis_key = redis_index.get(src_abs) or redis_index.get(str(src))
    if redis_key:
        _update_redis_path(redis_key, str(final_dst.absolute()))

    return final_dst, redis_key


# 数据分析源文件旧路径重写规则（4 条，与 _resolve_new_path 对齐）
# 用正则匹配前缀通配，保留前缀（绝对/相对），仅替换 storage/uploads/... 或
# storage/tenants/{tid}/conversation/data_sources/... 部分，使输出路径与输入路径
# 同格式（绝对路径输入 -> 绝对路径输出，相对路径输入 -> 相对路径输出）
#
# **规则顺序敏感**：规则1（tenant_ 前缀）和规则2（_global）必须排在规则3
# （通用 [^/]+）之前，否则 tenant_t1 的 tid 会被截断为 _t1，_global 也会被规则3
# 误匹配。规则4（误搬路径）独立，与其他规则无重叠。
_LEGACY_DATA_SOURCE_PATTERNS = [
    # uploads/tenant_{tid}/data_sources/{file} -> tenants/{tid}/data_sources/{file}
    (
        re.compile(r"^(.*)storage/uploads/tenant_([^/]+)/data_sources/(.+)$"),
        lambda m: f"{m.group(1)}storage/tenants/{m.group(2)}/data_sources/{m.group(3)}",
    ),
    # uploads/_global/data_sources/{file} -> tenants/_anonymous/data_sources/{file}
    # 必须在通用 {tid} 规则前，因为 _global 是特殊标识
    (
        re.compile(r"^(.*)storage/uploads/_global/data_sources/(.+)$"),
        lambda m: f"{m.group(1)}storage/tenants/_anonymous/data_sources/{m.group(2)}",
    ),
    # uploads/{tid}/data_sources/{file} -> tenants/{tid}/data_sources/{file}（tid 不含 tenant_ 前缀）
    (
        re.compile(r"^(.*)storage/uploads/([^/]+)/data_sources/(.+)$"),
        lambda m: f"{m.group(1)}storage/tenants/{m.group(2)}/data_sources/{m.group(3)}",
    ),
    # tenants/{tid}/conversation/data_sources/{file} -> tenants/{tid}/data_sources/{file}
    # （_resolve_new_path 缺 data_sources 分支时历史误搬的位置）
    (
        re.compile(r"^(.*)storage/tenants/([^/]+)/conversation/data_sources/(.+)$"),
        lambda m: f"{m.group(1)}storage/tenants/{m.group(2)}/data_sources/{m.group(3)}",
    ),
]


def rewrite_legacy_data_source_path(file_path: str) -> Optional[str]:
    """把数据分析源文件的旧路径前缀重写为新规范路径

    用于：
    - 迁移脚本主动修复 documents.metadata.source.file_path（_fix_documents_metadata_paths）
    - data_analyzer._load_excel 被动兜底（运行时遇到旧路径自动重写加载）

    规则与 _resolve_new_path 对齐（4 条），保留路径前缀（绝对/相对），仅替换
    storage/uploads/... 或 storage/tenants/{tid}/conversation/data_sources/... 部分：

        storage/uploads/tenant_{tid}/data_sources/{file}       -> storage/tenants/{tid}/data_sources/{file}
        storage/uploads/_global/data_sources/{file}             -> storage/tenants/_anonymous/data_sources/{file}
        storage/uploads/{tid}/data_sources/{file}               -> storage/tenants/{tid}/data_sources/{file}
        storage/tenants/{tid}/conversation/data_sources/{file}  -> storage/tenants/{tid}/data_sources/{file}

    Args:
        file_path: 任意路径字符串（绝对或相对）

    Returns:
        命中规则 -> 新路径字符串（与输入同格式，绝对路径输入返回绝对路径，
                   相对路径输入返回相对路径）；不命中 -> None
    """
    if not file_path:
        return None
    for pattern, replacer in _LEGACY_DATA_SOURCE_PATTERNS:
        m = pattern.match(file_path)
        if m:
            return replacer(m)
    return None


def _relocate_misplaced_data_sources(
    tenants_root: Path, redis_index: Dict[str, str], stats: Dict[str, int]
) -> None:
    """修复历史误搬到 tenants/{tid}/conversation/data_sources/ 的数据分析源文件

    扫描 tenants_root/{tid}/conversation/data_sources/*，搬到
    tenants/{tid}/data_sources/{file}，复用 _migrate_file 的幂等规则
    （同大小跳过、不同加后缀），同步 Redis path 字段。

    二次运行时 conversation/data_sources/ 已空，天然幂等。
    """
    if not tenants_root.exists():
        return

    for tid_dir in tenants_root.iterdir():
        if not tid_dir.is_dir():
            continue
        misplaced_dir = tid_dir / "conversation" / "data_sources"
        if not misplaced_dir.exists():
            continue

        for old_path in list(misplaced_dir.rglob("*")):
            if not old_path.is_file():
                continue
            try:
                rel = old_path.relative_to(misplaced_dir)
            except ValueError:
                continue
            new_path = tenants_root / tid_dir.name / "data_sources" / rel

            try:
                migrated_dst, redis_key = _migrate_file(
                    old_path, new_path, redis_index
                )
                stats["migrated"] += 1
                if redis_key:
                    stats["redis_updated"] += 1
                logger.debug(
                    f"[storage_migration] relocate 误搬文件: {old_path} -> {migrated_dst}"
                )
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"[storage_migration] relocate 失败 {old_path} -> {new_path}: {e}",
                    exc_info=True,
                )

        # 清理空目录（best-effort，非空则保留）
        try:
            misplaced_dir.rmdir()
        except OSError:
            pass


def _fix_documents_metadata_paths() -> int:
    """修复 documents.metadata.source.file_path 旧路径前缀

    遍历 source_type='data-analysis-metadata' 的 documents 行，用
    rewrite_legacy_data_source_path 重写 metadata.source.file_path，
    命中则 UPDATE documents SET metadata=%s WHERE id=%s。

    **仅动 metadata 列，绝不碰 file_path 列**（file_path 是知识库字段，独立）。

    Returns:
        更新的行数；DB 不可用或异常时返回 0（不阻塞迁移）
    """
    try:
        from src.db.database import get_db_connection
    except ImportError:
        logger.warning("[storage_migration] 数据库模块不可用，跳过 documents metadata 修复")
        return 0

    updated = 0
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, metadata FROM documents "
                "WHERE source_type = 'data-analysis-metadata'"
            )
            rows = cursor.fetchall()

            for row in rows:
                # 兼容 dict cursor 和 tuple cursor
                if isinstance(row, dict):
                    doc_id = row["id"]
                    meta_raw = row["metadata"]
                else:
                    doc_id = row[0]
                    meta_raw = row[1]

                if isinstance(meta_raw, str):
                    try:
                        meta = json.loads(meta_raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                elif isinstance(meta_raw, dict):
                    meta = meta_raw
                else:
                    continue

                source = meta.get("source")
                if not isinstance(source, dict):
                    continue

                file_path = source.get("file_path")
                if not file_path:
                    continue

                new_path = rewrite_legacy_data_source_path(file_path)
                if not new_path or new_path == file_path:
                    continue

                source["file_path"] = new_path
                cursor.execute(
                    "UPDATE documents SET metadata = %s WHERE id = %s",
                    (json.dumps(meta, ensure_ascii=False), doc_id),
                )
                updated += 1
                logger.info(
                    f"[storage_migration] documents#{doc_id} source.file_path 重写: "
                    f"{file_path} -> {new_path}"
                )

            conn.commit()
    except Exception as e:
        logger.warning(
            f"[storage_migration] 修复 documents metadata 失败（不阻塞迁移）: {e}",
            exc_info=True,
        )
        return 0

    return updated


def _build_redis_path_index() -> Dict[str, str]:
    """扫所有 uploaded_file:* 键，建 path -> redis_key 反向索引

    Redis 不可用或无键时返回空 dict，不阻塞磁盘迁移
    """
    try:
        from src.core.redis_client import redis_client

        index: Dict[str, str] = {}
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match="*uploaded_file:*", count=200)
            for key in keys:
                info = redis_client.hgetall(key)
                if not info:
                    continue
                path = info.get("path")
                if path:
                    index[path] = key
            if cursor == 0:
                break
        return index
    except Exception as e:
        logger.warning(
            f"[storage_migration] 构建 Redis 反向索引失败，跳过 Redis 元数据同步: {e}"
        )
        return {}


def _update_redis_path(redis_key: str, new_path: str) -> None:
    """更新单个 uploaded_file:* hash 的 path 字段"""
    try:
        from src.core.redis_client import redis_client

        redis_client.hset(redis_key, "path", new_path)
    except Exception as e:
        logger.warning(
            f"[storage_migration] 更新 Redis path 失败 [{redis_key}]: {e}"
        )


def _acquire_advisory_lock(
    max_retries: int = 3, interval: float = 1.0
) -> Tuple[bool, Optional[Any]]:
    """获取 pg_try_advisory_lock，防止 Gunicorn 多 worker 并发执行迁移

    Returns:
        (True, conn): 获取锁成功，conn 为持有 lock 的物理连接，调用方必须
                      在 finally 中调用 _release_advisory_lock(conn) 释放
        (False, None): 未获取锁（其他 worker 持有或重试耗尽）
        (True, None): 数据库模块不可用，降级单 worker 执行（无需 release）
    """
    try:
        from src.db.database import get_pooled_connection, return_pooled_connection
    except ImportError:
        logger.warning("[storage_migration] 数据库模块不可用，跳过 advisory lock")
        return True, None  # 无 DB 时降级单 worker 执行

    import time
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_pooled_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (_MIGRATION_LOCK_KEY,)
            )
            row = cursor.fetchone()
            # 默认 cursor 返回 tuple，用 row[0] 访问第一列（AS locked）
            locked = bool(row[0]) if row else False
            cursor.close()
            conn.commit()
            if locked:
                # 关键：持有 lock 的连接不归还，由调用方在 release 时用同一连接 unlock
                return True, conn
            # 未拿到 lock，归还连接后重试
            return_pooled_connection(conn)
            logger.info(
                f"[storage_migration] advisory lock 被其他 worker 持有，"
                f"等待重试 (attempt {attempt+1}/{max_retries})"
            )
        except Exception as e:
            logger.warning(
                f"[storage_migration] 获取 advisory lock 异常 (attempt {attempt+1}): {e}"
            )
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return_pooled_connection(conn)
        time.sleep(interval)

    return False, None


def _release_advisory_lock(conn: Optional[Any]) -> None:
    """释放 pg_advisory_unlock 并归还连接

    必须传入 _acquire_advisory_lock 返回的同一 conn，确保 unlock 在持有 lock
    的 session 上执行（PostgreSQL advisory lock 是 session-level）。
    conn 为 None 时无操作（无 DB 降级场景）。
    """
    if conn is None:
        return
    try:
        from src.db.database import return_pooled_connection

        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT pg_advisory_unlock(%s) AS unlocked", (_MIGRATION_LOCK_KEY,)
            )
            cursor.close()
            conn.commit()
        except Exception as e:
            logger.warning(f"[storage_migration] 释放 advisory lock 失败: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            return_pooled_connection(conn)
    except ImportError:
        pass
