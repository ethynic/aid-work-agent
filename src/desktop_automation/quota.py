"""desktop_automation 额度（R9：固定 scope 顺序逐层预留，条件 UPDATE 防超限）

模型：desktop_automation_quota_buckets，scope_type ∈ tenant/task/target/account/resource，
opaque scope_id，bucket_start = floor(now/window)*window 窗口对齐。

预留（许可事务内）：
- 按 R9 固定顺序 (tenant < task < target < account < resource) 排序后逐层
  SELECT ... FOR UPDATE + 条件 UPDATE reserved_count+1 WHERE reserved_count + used_count
  < limit_count（R19：已使用＋未决预留共同占用额度，settle 后同窗口不再放行）；
- 任一层不足抛 QuotaExhausted，由许可事务整体回滚（不产生半预留）；
- 并发下固定顺序加锁规避死锁（与 scope_id 升序组成全序）。

落账（结果同事务）：reserved→used（和不变，继续占用额度）；释放（R22）：仅可信未执行
证据（operation-result effect=none 且 phase=prepared）reserved-1——许可过期清扫不释放，
彻底遗弃的预留随 quota 窗口翻页自然失效。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from src.db.database import get_db_connection
from src.desktop_automation.constants import QUOTA_SCOPE_RANK


@dataclass(frozen=True)
class QuotaScope:
    """一次预留的额度层级（由适配器 authorize_decision.quota_scopes 转换而来）"""

    scope_type: str
    scope_id: str
    limit_count: int
    window_seconds: int


@dataclass(frozen=True)
class QuotaReservation:
    """一次成功预留的账本定位（许可行 quota_reservation JSON 持久化，落账/释放凭据）"""

    scope_type: str
    scope_id: str
    bucket_start: str  # ISO8601 UTC（TIMESTAMPTZ 精确回定位）

    def as_dict(self) -> dict:
        return {
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "bucket_start": self.bucket_start,
        }


class QuotaExhausted(Exception):
    """额度不足（许可事务整体回滚；scope 描述第一层失败的层级）"""

    def __init__(self, scope: QuotaScope):
        self.scope = scope
        super().__init__(
            f"quota exhausted: scope_type={scope.scope_type} scope_id={scope.scope_id} "
            f"limit={scope.limit_count}"
        )


def bucket_start_for(window_seconds: int, now: datetime) -> datetime:
    """窗口对齐：bucket_start = floor(now/window)*window（UTC epoch 对齐，R9）"""
    if window_seconds <= 0:
        raise ValueError("window_seconds 必须为正")
    ts = now.timestamp()
    aligned = math.floor(ts / window_seconds) * window_seconds
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def sort_scopes(scopes: Iterable[QuotaScope]) -> List[QuotaScope]:
    """按 R9 固定顺序 + scope_id 升序排序（许可事务的确定性加锁顺序）"""
    return sorted(
        scopes,
        key=lambda s: (QUOTA_SCOPE_RANK.get(s.scope_type, len(QUOTA_SCOPE_RANK)), s.scope_id),
    )


def reserve_quota(
    cursor, tenant_id: str, scopes: Sequence[QuotaScope], now: datetime
) -> List[QuotaReservation]:
    """逐层预留（须在许可事务内调用）。全部成功返回凭据列表；任一不足抛 QuotaExhausted。

    每层：INSERT ON CONFLICT DO NOTHING 建行（幂等）→ SELECT FOR UPDATE →
    条件 UPDATE reserved_count+1 WHERE reserved_count + used_count < limit_count
    （R19：used 计入占用，rowcount=0 即不足）。
    """
    if not scopes:
        return []
    reservations: List[QuotaReservation] = []
    for scope in sort_scopes(scopes):
        bucket_start = bucket_start_for(scope.window_seconds, now)
        cursor.execute(
            """
            INSERT INTO desktop_automation_quota_buckets
                (tenant_id, scope_type, scope_id, bucket_start, window_seconds, limit_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, scope_type, scope_id, bucket_start) DO NOTHING
            """,
            (
                tenant_id, scope.scope_type, scope.scope_id,
                bucket_start, scope.window_seconds, scope.limit_count,
            ),
        )
        cursor.execute(
            """
            SELECT id, limit_count FROM desktop_automation_quota_buckets
            WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s AND bucket_start = %s
            FOR UPDATE
            """,
            (tenant_id, scope.scope_type, scope.scope_id, bucket_start),
        )
        row = cursor.fetchone()
        if row is None:
            # 理论不可达（刚 INSERT 或已存在）；防御性视为不足
            raise QuotaExhausted(scope)
        cursor.execute(
            """
            UPDATE desktop_automation_quota_buckets
            SET reserved_count = reserved_count + 1, updated_at = NOW()
            WHERE id = %s AND reserved_count + used_count < %s
            """,
            (row["id"], row["limit_count"]),
        )
        if cursor.rowcount != 1:
            raise QuotaExhausted(scope)
        reservations.append(
            QuotaReservation(
                scope_type=scope.scope_type,
                scope_id=scope.scope_id,
                bucket_start=bucket_start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            )
        )
    return reservations


def settle_quota(cursor, tenant_id: str, reservations: Sequence[dict]) -> None:
    """结果落账：reserved→used（与结果同事务；幂等由调用方状态机保证只结算一次）"""
    for r in reservations:
        cursor.execute(
            """
            UPDATE desktop_automation_quota_buckets
            SET reserved_count = GREATEST(reserved_count - 1, 0),
                used_count = used_count + 1,
                updated_at = NOW()
            WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s AND bucket_start = %s
            """,
            (tenant_id, r["scope_type"], r["scope_id"], r["bucket_start"]),
        )


def release_quota(cursor, tenant_id: str, reservations: Sequence[dict]) -> None:
    """释放预留（R22：仅可信未执行证据 effect=none+prepared）：reserved-1（下限 0）"""
    for r in reservations:
        cursor.execute(
            """
            UPDATE desktop_automation_quota_buckets
            SET reserved_count = GREATEST(reserved_count - 1, 0),
                updated_at = NOW()
            WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s AND bucket_start = %s
            """,
            (tenant_id, r["scope_type"], r["scope_id"], r["bucket_start"]),
        )


def get_bucket(
    tenant_id: str,
    scope_type: str,
    scope_id: str,
    bucket_start: datetime,
) -> Optional[dict]:
    """查询桶现状（测试/对账；带租户过滤）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tenant_id, scope_type, scope_id, bucket_start, window_seconds,
                   limit_count, reserved_count, used_count
            FROM desktop_automation_quota_buckets
            WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s AND bucket_start = %s
            """,
            (tenant_id, scope_type, scope_id, bucket_start),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
