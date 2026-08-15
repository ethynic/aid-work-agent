"""
续费提醒核心服务

根据租户过去 n 天日均积分消耗，推算积分余额不足 7 天用量时判定为待续费。

统计口径：
- 日均消耗 = (chat_records + client_usage_logs 两表 credit_cost 之和) / n 天
- 统计窗口 n：租户开通距今天数 d，d > 30 取 30；否则取 max(d, 1)（开通到今天的实际天数，
  避免统计窗口超出开通期，此时"过去 n 天"== 全量数据）；created_at 缺失时默认 7
- 预估可用天数：int(credit_balance / daily_avg_cost)；日均 0 时余额 > 0 返回 -1（前端显示"暂无数据"），
  余额 <= 0 返回 0
- 待续费判定 renewal_pending：credit_balance <= 0（耗尽）或（日均 > 0 且预估可用天数 < 7）
"""

from datetime import datetime, timedelta
from typing import Optional

from src.db.database import get_db_connection

# 预估可用天数低于该阈值判定为待续费
RENEWAL_THRESHOLD_DAYS = 7
# 老租户日均统计窗口（天）
LONG_WINDOW_DAYS = 30
# created_at 缺失时的默认统计窗口（天）
DEFAULT_WINDOW_DAYS = 7


def usage_window_days(tenant: dict) -> int:
    """根据租户开通时间决定日均统计窗口 n 天。

    开通距今天数 d：d > 30 取 30；否则取 max(d, 1)；created_at 缺失默认 7。
    """
    created_at = tenant.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("T", " "))
            except ValueError:
                created_at = None
        if created_at:
            elapsed_days = (datetime.now() - created_at).days
            if elapsed_days > LONG_WINDOW_DAYS:
                return LONG_WINDOW_DAYS
            return max(elapsed_days, 1)
    return DEFAULT_WINDOW_DAYS


def _query_cost_totals(cursor, table: str, tenant_ids: list, start: Optional[str]) -> dict:
    """查询一批租户在某时间起点之后的积分消耗总和（按租户分组）。

    table 仅允许 chat_records / client_usage_logs 两个固定值；start=None 查全量。
    """
    if start:
        cursor.execute(
            f"SELECT tenant_id, COALESCE(SUM(credit_cost), 0) AS total FROM {table} "
            "WHERE tenant_id = ANY(%s) AND created_at >= %s GROUP BY tenant_id",
            (tenant_ids, start),
        )
    else:
        cursor.execute(
            f"SELECT tenant_id, COALESCE(SUM(credit_cost), 0) AS total FROM {table} "
            "WHERE tenant_id = ANY(%s) GROUP BY tenant_id",
            (tenant_ids,),
        )
    return {row["tenant_id"]: float(row["total"] or 0) for row in cursor.fetchall()}


def _fetch_tenant_cost_totals(cursor, tenant_ids: list, window_days: Optional[int]) -> dict:
    """批量统计租户积分消耗（chat_records + client_usage_logs 合并）。

    window_days=None 表示查全量（新租户"过去 n 天"== 全量数据，无需时间过滤）。
    """
    start = (
        (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d 00:00:00")
        if window_days else None
    )
    totals = _query_cost_totals(cursor, "chat_records", tenant_ids, start)
    for tenant_id, value in _query_cost_totals(cursor, "client_usage_logs", tenant_ids, start).items():
        totals[tenant_id] = totals.get(tenant_id, 0) + value
    return totals


def _calc_renewal(tenant: dict, total_cost: float, days: int) -> tuple:
    """根据窗口内总消耗计算单租户续费状态。

    Returns: (daily_avg_cost, estimated_days_left, renewal_pending)
    """
    balance = float(tenant.get("credit_balance") or 0)
    daily_avg = round(total_cost / days, 2) if total_cost > 0 else 0.0
    if daily_avg > 0:
        estimated_days_left = max(0, int(balance / daily_avg))
    else:
        estimated_days_left = -1 if balance > 0 else 0
    renewal_pending = balance <= 0 or (daily_avg > 0 and estimated_days_left < RENEWAL_THRESHOLD_DAYS)
    return daily_avg, estimated_days_left, renewal_pending


def compute_renewal_status(tenant: dict) -> dict:
    """计算单个租户续费状态。

    Returns: {"daily_avg_cost": float, "estimated_days_left": int, "renewal_pending": bool}
    """
    days = usage_window_days(tenant)
    window = LONG_WINDOW_DAYS if days >= LONG_WINDOW_DAYS else None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        totals = _fetch_tenant_cost_totals(cursor, [tenant["tenant_id"]], window)
    total = totals.get(tenant["tenant_id"], 0)
    daily_avg, estimated_days_left, renewal_pending = _calc_renewal(tenant, total, days)
    return {
        "daily_avg_cost": daily_avg,
        "estimated_days_left": estimated_days_left,
        "renewal_pending": renewal_pending,
    }


def enrich_tenants_with_renewal(tenants: list[dict]) -> None:
    """就地给租户列表每行补充续费状态字段。

    批量实现：老租户（窗口 30 天）一次 SQL，新租户（窗口为其开通天数，查全量）一次 SQL，
    每类至多 2 条聚合查询（chat_records + client_usage_logs），避免逐租户 N 次查询。
    """
    if not tenants:
        return
    old_group = []
    new_group = []
    for t in tenants:
        days = usage_window_days(t)
        t["_window_days"] = days
        (old_group if days >= LONG_WINDOW_DAYS else new_group).append(t)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for group, window in ((old_group, LONG_WINDOW_DAYS), (new_group, None)):
            if not group:
                continue
            totals = _fetch_tenant_cost_totals(cursor, [t["tenant_id"] for t in group], window)
            for t in group:
                days = t.pop("_window_days")
                daily_avg, estimated_days_left, renewal_pending = _calc_renewal(
                    t, totals.get(t["tenant_id"], 0), days
                )
                t["daily_avg_cost"] = daily_avg
                t["estimated_days_left"] = estimated_days_left
                t["renewal_pending"] = renewal_pending
