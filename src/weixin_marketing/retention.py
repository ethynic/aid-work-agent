"""weixin_marketing 保留期清理（P5 R59③：payloads/occurrences）

- payloads：weixin_marketing_event_payloads 过期且不再被任何**未 processed** 事件
  引用的行（匹配 worker 的 condition 判定仍能加载在途事件 payload）；
  候选锁定与接纳侧 FOR SHARE 构成统一锁协议（P5 复审 P1-1，见 event_sources.
  store_event_payload_on）：清理 FOR UPDATE SKIP LOCKED 跳过接纳中行，不等待；
- occurrences：desktop_automation_occurrences 本场景过期且**无 runs 引用**的行
  （run 明细/逐条账本的引用不悬空；时间槽防重放由 schedule 自身状态保证——
  once consumed/finished、interval next_fire_at 前移——不依赖历史 occurrence 行）；
- enabled 门控（R42）+ data_retention_days 可配（默认 30 天）；批量有界，
  删除条件恒带租户列（表族租户隔离规范；occurrences 按本场景过滤不动他场景）。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.weixin_marketing.config import WeixinMarketingConfig, get_weixin_marketing_config
from src.weixin_marketing.constants import (
    DEFAULT_RETENTION_CLEANUP_BATCH,
    SCENARIO_KEY,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_config(config: Optional[WeixinMarketingConfig]) -> WeixinMarketingConfig:
    return config if config is not None else get_weixin_marketing_config()


def cleanup_expired(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """过期 payloads/occurrences 批量清理（enabled 门控；幂等可重放）。

    返回 {enabled, payloads_deleted, occurrences_deleted}。批量上限防长事务长锁，
    未清完的行下轮 tick 继续。"""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "payloads_deleted": 0, "occurrences_deleted": 0}
    now = _aware(now or _utcnow())
    batch = int(batch or DEFAULT_RETENTION_CLEANUP_BATCH)
    cutoff = now - timedelta(days=cfg.data_retention_days)
    with get_db_connection() as conn:
        cur = conn.cursor()
        # payloads：候选 FOR UPDATE SKIP LOCKED（P5 复审 P1-1 统一锁协议）——
        # 接纳事务持 FOR SHARE 的复用行直接跳过本轮（不等待）：接纳提交后下一轮
        # NOT EXISTS 看到新事件引用、永不清除被引用行；被跳过的行不阻塞批次
        cur.execute(
            """
            WITH candidates AS (
                SELECT id FROM weixin_marketing_event_payloads
                WHERE created_at < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM desktop_automation_events e
                      WHERE e.tenant_id = weixin_marketing_event_payloads.tenant_id
                        AND e.payload_hash = weixin_marketing_event_payloads.payload_hash
                        AND e.state <> 'processed'
                  )
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM weixin_marketing_event_payloads p
            USING candidates c WHERE p.id = c.id
            """,
            (cutoff, batch),
        )
        payloads_deleted = cur.rowcount
        # occurrences：同样 FOR UPDATE SKIP LOCKED（P2-① 加固）——多进程并发清理
        # 同批次行时跳过已被锁定的候选，消除理论死锁面（无 FOR SHARE 对端，纯防加固）
        cur.execute(
            """
            WITH occ_candidates AS (
                SELECT id FROM desktop_automation_occurrences
                WHERE scenario_key = %s AND created_at < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM desktop_automation_runs r
                      WHERE r.tenant_id = desktop_automation_occurrences.tenant_id
                        AND r.occurrence_id = desktop_automation_occurrences.id
                  )
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            DELETE FROM desktop_automation_occurrences o
            USING occ_candidates c WHERE o.id = c.id
            """,
            (SCENARIO_KEY, cutoff, batch),
        )
        occurrences_deleted = cur.rowcount
        conn.commit()
    if payloads_deleted or occurrences_deleted:
        logger.info(
            f"后端日志：weixin_marketing 保留期清理（{cfg.data_retention_days} 天）"
            f"payloads={payloads_deleted} occurrences={occurrences_deleted}"
        )
    return {
        "enabled": True,
        "payloads_deleted": payloads_deleted,
        "occurrences_deleted": occurrences_deleted,
    }
