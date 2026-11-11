"""AI 获客邮件打开记录合并任务（ai-lead-gen 技能）。

像素端点 /api/track/p.gif 匿名直写 bs_ai_lead_gen_opens（同库）；本任务
周期性把新增打开事件按归一化 message_id 找出，并对候选 message_id 按
opens 表全量聚合（累计次数 + 最近打开时间）合并回
bs_ai_lead_gen_emails.open_count / opens_at。

替代技能侧 fetch_opens.py 的手动 HTTP 回拉：同一数据库无需走
/api/track/opens 鉴权端点，也无需 tracking_key。租户归属以 emails 表
（真实 tenant_id）为准，不信任 opens.tenant_id（像素 t 参数为尽力而为字段）。

水位（ailg_opens_sync:watermark）：记录已扫描的 opens.id，Redis 持久键；
Redis 降级内存时仅当前进程有效——调度器由分布式锁保证单实例运行，不影响
正确性。聚合口径与 fetch_opens.py 一致（norm_message_id 去 <>)，幂等可重复跑。
"""

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.db.database import get_db_connection

# 单轮扫描上限：超过则下轮继续（水位只推进到本批末尾），防止打开风暴下单轮过重
SCAN_BATCH = 20000

_WATERMARK_KEY = redis_client.make_key(CacheKeys.AI_LEAD_GEN_OPENS_SYNC, "watermark")


def _load_watermark() -> int:
    try:
        value = redis_client.get(_WATERMARK_KEY)
        return int(value) if value else 0
    except Exception as e:  # noqa: BLE001 Redis 不可用降级：从 0 重扫（聚合幂等，只是多扫）
        logger.warning(f"后端日志：AI获客打开合并水位读取失败（从0重扫）: {e}")
        return 0


def _save_watermark(value: int) -> None:
    try:
        redis_client.set(_WATERMARK_KEY, value)
    except Exception as e:  # noqa: BLE001 水位写失败只影响下轮起点（从0重扫，结果幂等）
        logger.warning(f"后端日志：AI获客打开合并水位写入失败: {e}")


def _norm_message_id(value) -> str:
    """Message-ID 归一化：去尖括号与空白（与 fetch_opens / reply_monitor 同口径）。"""
    return (value or "").strip().strip("<>").strip()


def sync_opens_once() -> dict:
    """扫描 opens 表新增记录并合并进 emails 表，返回统计供日志汇报。"""
    watermark = _load_watermark()
    candidates: set = set()
    max_id = watermark
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, message_id FROM bs_ai_lead_gen_opens "
            "WHERE id > %s ORDER BY id LIMIT %s",
            (watermark, SCAN_BATCH + 1),
        )
        rows = cur.fetchall()
        has_more = len(rows) > SCAN_BATCH
        rows = rows[:SCAN_BATCH]
        for row in rows:
            max_id = max(max_id, row["id"])
            mid = _norm_message_id(row["message_id"])
            if mid:
                candidates.add(mid)

        updated = 0
        for mid in candidates:
            # 候选按 opens 全量聚合（累计口径），单条聚合命中 opens 主键范围扫描，
            # 候选量级为"本轮新增事件涉及的 message_id 数"，量级很小
            cur.execute(
                "SELECT COUNT(*) AS cnt, MAX(opened_at) AS last_at "
                "FROM bs_ai_lead_gen_opens WHERE btrim(message_id, '<>') = %s",
                (mid,),
            )
            agg = cur.fetchone()
            cur.execute(
                "UPDATE bs_ai_lead_gen_emails "
                "SET opens_at = %s, open_count = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE btrim(message_id, '<>') = %s "
                "AND (opens_at IS DISTINCT FROM %s OR open_count IS DISTINCT FROM %s)",
                (agg["last_at"], agg["cnt"], mid, agg["last_at"], agg["cnt"]),
            )
            updated += cur.rowcount

        # get_db_connection 退出时无条件 rollback，UPDATE 必须显式提交
        if updated:
            conn.commit()

        # 仅在已扫完本批（无溢出）时推进水位到当前最大 id；溢出时下轮从本批末尾续扫
        if not has_more and rows:
            _save_watermark(max_id)

    return {"scanned": len(rows), "candidates": len(candidates), "updated": updated,
            "has_more": has_more}
