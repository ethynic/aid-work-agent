"""租户物理删除服务

两类入口：
1. 管理员对「已删除」(deactivated) 租户二次删除 -> purge_tenant_core（仅核心数据）
2. 夜间定时任务（scheduler/manager.py cron=04:30）：
   - purge_expired_deleted_tenants：deactivated 且 updated_at 超 7 天 -> purge_tenant_core
   - purge_orphan_data：动态扫描所有含 tenant_id / to_tenant_id / from_tenant_id 列的表，
     分批删除租户行已不存在的孤儿数据（核心删除后的剩余历史数据由此兜底清理）
   - cleanup_orphan_storage_dirs：删除 storage/tenants/ 下租户行已不存在的附件目录

设计要点：
- 核心删除单事务完成，tenants 行最后删除且复核 status=deactivated（防恢复竞态，
  rowcount=0 时整体回滚）；无 tenant_id 列的关联表（凭据/chunks 向量）在核心删除中
  按 user_id/doc_id 子查询一并清理，否则孤儿扫描扫不到会成为永久残留
- 孤儿扫描按 ctid 分批删除（每批 5000 行），避免长事务和大范围行锁
- 孤儿判定排除 NULL、''（scheduled_tasks 等表用 '' 表示无租户上下文的遗留行）
  和 '_' 开头的占位值
- 环境可能存在仓库未声明的单列外键（如 tokens/scheduled_tasks -> users，仅按
  user_id 关联、无 tenant_id 列），残留子表行会以 FK 挡住父表孤儿删除。删除父表
  孤儿行前先动态发现此类入站外键，分批预清理引用了父表孤儿行的子表行（复合外键
  跳过，其引用侧若含 tenant_id 列会被孤儿扫描单独清理，下一轮重试收敛）
- 单表失败不中断整体清理（逐表 fail-soft，记录错误日志）
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger

from src.core.storage import get_tenants_storage_root, normalize_tenant_id
from src.db.database import get_db_connection
from src.saas.models.enums import TenantStatus
from src.core.cache_utils import invalidate_tenant_cache

# 核心数据表（管理员二次删除 / 7天过期清理时立即删除），按依赖顺序排列。
# 三类：
# 1. 无 tenant_id 列、仅靠 user_id/doc_id/chunk_id 关联的表（凭据、chunks 向量）——
#    孤儿扫描只认 tenant_id 列，这些表若不在核心删除中处理将成为永久残留
# 2. tokens 按 user_id 关联 users，必须先于 users 删除
# 3. tenants 行最后删除且带 status 复核（deactivated）：若租户在删除过程中被恢复为
#    active（编辑弹框可改 status），rowcount=0 触发回滚，全部核心删除一并撤销
CORE_PURGE_SQL: List[Tuple[str, str]] = [
    ("chunks_vec", "DELETE FROM chunks_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = %s))"),
    ("chunks", "DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = %s)"),
    ("user_email_settings", "DELETE FROM user_email_settings WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)"),
    ("remote_credentials", "DELETE FROM remote_credentials WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)"),
    ("tokens", "DELETE FROM tokens WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)"),
    # scheduled_tasks 在部分环境对 users 有单列 FK（scheduled_tasks_user_id_fkey），
    # 必须先于 users 删除；按 user_id 子查询可一并覆盖 tenant_id='' 的遗留行
    ("scheduled_tasks", "DELETE FROM scheduled_tasks WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)"),
    ("users", "DELETE FROM users WHERE tenant_id = %s"),
    ("subscriptions", "DELETE FROM subscriptions WHERE tenant_id = %s"),
    ("user_agent_permissions", "DELETE FROM user_agent_permissions WHERE tenant_id = %s"),
    ("tenant_channel_configs", "DELETE FROM tenant_channel_configs WHERE tenant_id = %s"),
]

# 孤儿扫描单批删除行数
ORPHAN_BATCH_SIZE = 5000

# 入站外键发现：指向某表的单列外键，删除行为为 NO ACTION('a') / RESTRICT('r')
# （CASCADE / SET NULL 数据库自动处理，不会阻止父行删除）。复合外键（cardinality>1）
# 跳过：其引用侧若自身含 tenant_id 列会被孤儿扫描单独清理，下一轮重试收敛
_INBOUND_FK_SQL = """
SELECT con.conrelid::regclass::text AS table_name,
       con.confrelid::regclass::text AS ref_table,
       ga.attname AS column_name,
       gb.attname AS ref_column
FROM pg_constraint con
JOIN pg_attribute ga
  ON ga.attrelid = con.conrelid AND ga.attnum = con.conkey[1]
JOIN pg_attribute gb
  ON gb.attrelid = con.confrelid AND gb.attnum = con.confkey[1]
JOIN pg_class ref_cls
  ON ref_cls.oid = con.confrelid
WHERE con.contype = 'f'
  AND con.connamespace = 'public'::regnamespace
  AND ref_cls.relnamespace = 'public'::regnamespace
  AND con.confdeltype IN ('a', 'r')
  AND cardinality(con.conkey) = 1
  AND NOT ga.attisdropped AND NOT gb.attisdropped
"""

# 孤儿扫描列：exact 'tenant_id' + 共享表的双向租户列（to_tenant_id / from_tenant_id）
_ORPHAN_COLUMN_SQL = """
SELECT c.table_name, c.column_name
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = 'public'
  AND t.table_type = 'BASE TABLE'
  AND c.column_name IN ('tenant_id', 'to_tenant_id', 'from_tenant_id')
  AND c.table_name <> 'tenants'
"""


def purge_tenant_core(tenant_id: str) -> Dict[str, Any]:
    """物理删除租户核心数据（单事务）+ 缓存失效 + 附件目录。

    Returns: {"success": bool, "deleted": {table: rows}, "error": str?}
    """
    deleted: Dict[str, int] = {}
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for table, sql in CORE_PURGE_SQL:
                cursor.execute(sql, (tenant_id,))
                deleted[table] = cursor.rowcount
            # tenants 行最后删除，带 status 复核防止删除过程中租户被恢复：
            # rowcount=0 说明租户不存在或已非 deactivated，回滚全部核心删除
            cursor.execute(
                "DELETE FROM tenants WHERE tenant_id = %s AND status = %s",
                (tenant_id, TenantStatus.DEACTIVATED.value),
            )
            if cursor.rowcount == 0:
                raise RuntimeError(f"租户不存在或状态已非 deactivated，已回滚: tenant={tenant_id}")
            deleted["tenants"] = cursor.rowcount
            conn.commit()
    except Exception as e:
        logger.opt(exception=True).error(f"租户核心数据物理删除失败: tenant={tenant_id}: {e}")
        return {"success": False, "deleted": deleted, "error": str(e)}

    try:
        invalidate_tenant_cache(tenant_id)
    except Exception as e:
        logger.warning(f"租户核心删除后缓存清理失败（不影响删除结果）: tenant={tenant_id}: {e}")

    _remove_tenant_storage_dir(tenant_id)

    logger.info(f"租户核心数据物理删除完成: tenant={tenant_id}, deleted={deleted}")
    return {"success": True, "deleted": deleted}


def purge_expired_deleted_tenants(days: int = 7) -> List[str]:
    """物理删除「已删除」超过 days 天的租户（核心数据）。返回已清理的 tenant_id 列表。"""
    tenant_ids: List[str] = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # updated_at 为软删除时写入的时间戳；deactivated 租户不会再被更新（登录/授权均已拦截），
            # 以它作为删除时间基准。updated_at 为无时区 TIMESTAMP，用 LOCALTIMESTAMP 对齐
            cursor.execute(
                """
                SELECT tenant_id FROM tenants
                WHERE status = %s
                  AND updated_at IS NOT NULL
                  AND updated_at < LOCALTIMESTAMP - (%s || ' days')::interval
                """,
                (TenantStatus.DEACTIVATED.value, str(days)),
            )
            tenant_ids = [row["tenant_id"] for row in cursor.fetchall()]
    except Exception as e:
        logger.opt(exception=True).error(f"扫描过期已删除租户失败: {e}")
        return []

    purged_ids: List[str] = []
    for tid in tenant_ids:
        result = purge_tenant_core(tid)
        if result["success"]:
            purged_ids.append(tid)
        else:
            logger.error(f"过期租户核心清理失败，跳过: tenant={tid}")
    if purged_ids:
        logger.info(f"夜间清理：物理删除 {len(purged_ids)} 个超 {days} 天已删除租户: {purged_ids}")
    return purged_ids


def _ident(name: str) -> str:
    """标识符安全加引号（表/列名来自 pg_catalog，仍做转义防意外）"""
    return '"' + name.replace('"', '""') + '"'


def _list_orphan_scan_targets() -> List[Tuple[str, str]]:
    """返回待扫描的 (table, column) 列表（information_schema 动态发现，覆盖 bs_* 业务表）"""
    targets: List[Tuple[str, str]] = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_ORPHAN_COLUMN_SQL)
        for row in cursor.fetchall():
            targets.append((row["table_name"], row["column_name"]))
    return targets


def _fetch_inbound_fks() -> Dict[str, List[Tuple[str, str, str]]]:
    """返回 {被引用表: [(引用表, 引用列, 被引用列), ...]}，仅含会阻止父行删除的单列 FK"""
    fks: Dict[str, List[Tuple[str, str, str]]] = {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_INBOUND_FK_SQL)
        for row in cursor.fetchall():
            fks.setdefault(row["ref_table"], []).append(
                (row["table_name"], row["column_name"], row["ref_column"])
            )
    return fks


def _purge_orphan_fk_dependents(
    cursor, conn, parent_table: str, parent_col: str,
    fk: Tuple[str, str, str], batch_size: int,
) -> int:
    """分批删除引用「父表孤儿行」的子表行，返回删除总数。

    子表（如 tokens 仅按 user_id 关联 users，无 tenant_id 列）可能不在孤儿扫描目标中，
    其残留行会以 FK 挡住父表孤儿删除。子表删除条件 = 引用值落在父表孤儿集合内
    （JOIN 父表并套用同一孤儿谓词），而非简单的「父行已不存在」——后者在父表删除
    前置阶段拿不到即将被删的行。
    """
    fk_table, fk_col, ref_col = fk
    orphan_pred = (
        f"t.{_ident(parent_col)} IS NOT NULL AND t.{_ident(parent_col)} <> '' "
        f"AND t.{_ident(parent_col)} NOT LIKE '\\_%' "
        f"AND t.{_ident(parent_col)} NOT IN (SELECT tenant_id FROM tenants)"
    )
    total = 0
    while True:
        cursor.execute(
            f"DELETE FROM {_ident(fk_table)} WHERE ctid IN ("
            f"  SELECT r.ctid FROM {_ident(fk_table)} r"
            f"  JOIN {_ident(parent_table)} t ON t.{_ident(ref_col)} = r.{_ident(fk_col)}"
            f"  WHERE {orphan_pred}"
            f"  LIMIT {int(batch_size)})"
        )
        deleted = cursor.rowcount
        conn.commit()
        total += deleted
        if deleted < batch_size:
            break
    return total


def purge_orphan_data(batch_size: int = ORPHAN_BATCH_SIZE) -> Dict[str, int]:
    """扫描所有含 tenant_id / to_tenant_id 列的表，分批删除租户行已不存在的孤儿数据。

    Returns: {table: 总删除行数}（仅含删除数 > 0 的表）
    """
    total_deleted: Dict[str, int] = {}
    try:
        targets = _list_orphan_scan_targets()
    except Exception as e:
        logger.opt(exception=True).error(f"孤儿数据扫描目标发现失败: {e}")
        return total_deleted

    try:
        inbound_fks = _fetch_inbound_fks()
    except Exception as e:
        # FK 预清理是可选增强，发现失败时退化为原行为（父表删除可能被 FK 挡住，下轮重试）
        logger.opt(exception=True).warning(f"入站外键发现失败，本轮跳过子表预清理: {e}")
        inbound_fks = {}

    for table, column in targets:
        table_ident = f'"{table}"'
        column_ident = f'"{column}"'
        deleted_total = 0
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # 先清理引用了本表孤儿行的子表行（环境级单列 FK），否则子表残留行
                # 会以 FK 挡住本表孤儿删除（如 tokens_user_id_fkey 挡住 users）
                for fk in inbound_fks.get(table, []):
                    fk_deleted = _purge_orphan_fk_dependents(
                        cursor, conn, table, column, fk, batch_size)
                    if fk_deleted > 0:
                        fk_key = f"{fk[0]}.{fk[1]}"
                        total_deleted[fk_key] = total_deleted.get(fk_key, 0) + fk_deleted
                        logger.info(
                            f"孤儿数据清理（FK 预清理）: {fk[0]}.{fk[1]} 引用 {table} 孤儿行，删除 {fk_deleted} 行")
                while True:
                    # ctid 分批删除，避免单条 DELETE 长事务；'' 为遗留哨兵值、
                    # '_' 开头为占位值（如 _anonymous），均不视为孤儿
                    cursor.execute(
                        f"DELETE FROM {table_ident} WHERE ctid IN ("
                        f"  SELECT ctid FROM {table_ident}"
                        f"  WHERE {column_ident} IS NOT NULL AND {column_ident} <> ''"
                        f"    AND {column_ident} NOT LIKE '\\_%'"
                        f"    AND {column_ident} NOT IN (SELECT tenant_id FROM tenants)"
                        f"  LIMIT {int(batch_size)})"
                    )
                    deleted = cursor.rowcount
                    conn.commit()
                    deleted_total += deleted
                    if deleted < batch_size:
                        break
            if deleted_total > 0:
                total_deleted[f"{table}.{column}"] = deleted_total
                logger.info(f"孤儿数据清理: {table}.{column} 删除 {deleted_total} 行")
        except Exception as e:
            # 单表失败不中断整体（如异构类型列）；下一轮夜间任务可重试
            # 事务由 get_db_connection 上下文管理器统一回滚/关闭
            logger.opt(exception=True).error(f"孤儿数据清理失败（跳过该表）: {table}.{column}: {e}")

    if total_deleted:
        logger.info(f"夜间孤儿数据清理完成: {total_deleted}")
    else:
        logger.debug("夜间孤儿数据清理完成: 无孤儿数据")
    return total_deleted


def cleanup_orphan_storage_dirs() -> List[str]:
    """删除 storage/tenants/ 下租户行已不存在的附件目录。返回已删除的目录名列表。"""
    removed: List[str] = []
    root = Path(get_tenants_storage_root())
    if not root.is_dir():
        return removed

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tenant_id FROM tenants")
            valid_ids = {row["tenant_id"] for row in cursor.fetchall()}
    except Exception as e:
        logger.opt(exception=True).error(f"孤儿存储目录清理：查询租户列表失败: {e}")
        return removed

    # 空集保护：租户列表为空（连错库/清库）时中止，防止把所有租户目录判为孤儿
    if not valid_ids:
        logger.warning("孤儿存储目录清理：租户列表为空，中止本次清理")
        return removed

    def _is_valid_dir(name: str) -> bool:
        # 存储目录名是剥离 tenant_ 前缀的规范化 ID（normalize_tenant_id），与 DB
        # tenant_id 不同名，两种命名都视为有效；下划线开头为 _anonymous 等特殊占位目录
        if name.startswith("_"):
            return True
        return name in valid_ids or name in valid_dir_names or f"tenant_{name}" in valid_ids

    valid_dir_names = {normalize_tenant_id(tid) for tid in valid_ids}
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if _is_valid_dir(entry.name):
            continue
        try:
            # 删除前逐目录复核租户表：清理扫描窗口内新注册的租户目录不误删
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM tenants WHERE tenant_id = ANY(%s) LIMIT 1",
                    ([entry.name, f"tenant_{entry.name}", normalize_tenant_id(entry.name)],),
                )
                if cursor.fetchone():
                    logger.info(f"孤儿存储目录复核时租户已存在，跳过: {entry.name}")
                    continue
        except Exception as e:
            logger.opt(exception=True).error(f"孤儿存储目录复核失败（跳过该目录）: {entry}: {e}")
            continue
        try:
            from src.core.simulation import skip_disk_delete

            if skip_disk_delete(entry, context="tenant_purge_orphan_dir"):
                continue
            shutil.rmtree(entry)
            removed.append(entry.name)
            logger.info(f"孤儿存储目录已删除: {entry}")
        except Exception as e:
            logger.opt(exception=True).error(f"孤儿存储目录删除失败: {entry}: {e}")

    return removed


def _remove_tenant_storage_dir(tenant_id: str) -> None:
    """删除租户附件目录（核心删除的一部分；失败不阻断删除结果）"""
    try:
        # 存储目录名使用规范化 ID（剥离 tenant_ 前缀），见 src/core/storage.py normalize_tenant_id
        target = Path(get_tenants_storage_root()) / normalize_tenant_id(tenant_id)
        if target.is_dir():
            from src.core.simulation import skip_disk_delete

            if skip_disk_delete(target, context="tenant_purge_storage_dir"):
                return
            shutil.rmtree(target)
            logger.info(f"租户附件目录已删除: {target}")
    except Exception as e:
        logger.opt(exception=True).error(f"租户附件目录删除失败: tenant={tenant_id}: {e}")
