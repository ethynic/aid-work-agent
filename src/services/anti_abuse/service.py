"""AntiAbuseService：绑定加载 / 匹配 / 预算评估 / 事件落库。

Phase 1 观察闭环的数据层。绑定匹配优先级（设计 §3.2/§5.2）：
user 绑定 > channel 绑定（按 users.source 渠道类型降级匹配，Phase 2 请求
上下文精确到 config_id）> tenant 兜底行 > configs/config.yaml 平台默认。
"""
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from .rules import SESSION_SOURCE_TYPES, validate_policy_params

# 滚动窗口时间窗（PostgreSQL interval 字符串）
_PERIOD_INTERVALS = {"hour": "1 hour", "day": "1 day"}


class AntiAbuseService:
    """防滥用策略服务。方法内部自行开连接，无跨请求内存状态。"""

    # ============== 绑定加载与匹配 ==============

    @staticmethod
    def load_policy_row(
        tenant_id: str, scope_type: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        """读一条绑定行；无行返回 None"""
        with get_db_connection() as cursor:
            cursor.execute(
                """SELECT tenant_id, scope_type, scope_id, mode, policies, updated_by,
                          created_at, updated_at
                   FROM abuse_policies
                   WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s""",
                (tenant_id, scope_type, scope_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @classmethod
    def resolve_binding(
        cls, tenant_id: str, user_id: str, source: Optional[str] = None
    ) -> Dict[str, Any]:
        """解析用户生效的绑定（不并集不合并，取第一条命中）。

        - 内部用户（source 为空）：user 绑定 -> tenant 兜底 -> 平台默认
        - 渠道用户（source 非空）：channel 绑定（按 channel_type 降级）-> tenant 兜底 -> 平台默认

        返回 {scope_type, scope_id, mode, policies, is_platform_default}
        """
        if source:
            row = cls.load_channel_policy_row(tenant_id, source)
            if row:
                return row
        else:
            row = cls.load_policy_row(tenant_id, "user", user_id)
            if row:
                return row
        row = cls.load_policy_row(tenant_id, "tenant", tenant_id)
        if row:
            return row
        default = settings.anti_abuse.tenant_default
        return {
            "tenant_id": tenant_id,
            "scope_type": "tenant",
            "scope_id": tenant_id,
            "mode": default.mode,
            "policies": default.policies,
            "is_platform_default": True,
        }

    @staticmethod
    def load_channel_policy_row(tenant_id: str, channel_type: str) -> Optional[Dict[str, Any]]:
        """按 (tenant_id, channel_type) 降级匹配渠道绑定行。

        Phase 1 扫描器无请求上下文，无法从 user_id 精确反查 config_id
        （users.source 只有渠道类型），同渠道类型多份配置时取第一份。
        """
        with get_db_connection() as cursor:
            cursor.execute(
                """SELECT p.tenant_id, p.scope_type, p.scope_id, p.mode, p.policies,
                          p.updated_by, p.created_at, p.updated_at
                   FROM abuse_policies p
                   JOIN tenant_channel_configs c ON c.config_id = p.scope_id
                   WHERE p.tenant_id = %s AND p.scope_type = 'channel'
                     AND c.channel_type = %s
                   ORDER BY p.updated_at DESC
                   LIMIT 1""",
                (tenant_id, channel_type),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ============== 预算评估 ==============

    @staticmethod
    def evaluate_credit_budget(tenant_id: str, user_id: str, period: str) -> float:
        """滚动窗口内会话执行类积分消耗合计（设计 §5.2，数据源 chat_records）"""
        interval = _PERIOD_INTERVALS.get(period)
        if interval is None:
            raise ValueError(f"credit_budget 非法 period: {period}")
        with get_db_connection() as cursor:
            cursor.execute(
                f"""SELECT COALESCE(SUM(credit_cost), 0) AS total FROM chat_records
                    WHERE tenant_id = %s AND user_id = %s AND credit_cost > 0
                      AND source_type = ANY(%s)
                      AND created_at >= NOW() - interval '{interval}'""",
                (tenant_id, user_id, list(SESSION_SOURCE_TYPES)),
            )
            return float(cursor.fetchone()["total"] or 0)

    # ============== 事件落库 ==============

    @staticmethod
    def record_event(
        cursor,
        *,
        tenant_id: str,
        scope_type: str,
        scope_id: str,
        rule_id: str,
        action: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """写触发事件，同键 (tenant_id, user_id, rule_id, action) 1 小时内去重。

        传入外部 cursor（调用方事务），返回新事件 id；1 小时内已有同键返回 None。
        """
        cursor.execute(
            """INSERT INTO abuse_events
                   (tenant_id, scope_type, scope_id, user_id, rule_id, action,
                    session_id, channel_type, detail)
               SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
               WHERE NOT EXISTS (
                   SELECT 1 FROM abuse_events
                   WHERE tenant_id = %s AND rule_id = %s AND action = %s
                     AND user_id IS NOT DISTINCT FROM %s
                     AND created_at > NOW() - interval '1 hour'
               )
               RETURNING id""",
            (
                tenant_id, scope_type, scope_id, user_id, rule_id, action,
                session_id, channel_type,
                json.dumps(detail, ensure_ascii=False) if detail else None,
                tenant_id, rule_id, action, user_id,
            ),
        )
        row = cursor.fetchone()
        return int(row["id"]) if row else None

    # ============== 绑定写入（API 用） ==============

    @staticmethod
    def upsert_policy(
        tenant_id: str,
        scope_type: str,
        scope_id: str,
        mode: str,
        policies: List[Dict[str, Any]],
        updated_by: str,
    ) -> None:
        """整存整取绑定（mode + policies 数组）；参数在调用前已校验"""
        for policy in policies:
            ok, err = validate_policy_params(policy.get("rule_id", ""), policy.get("params"))
            if not ok:
                raise ValueError(err)
        with get_db_connection() as cursor:
            cursor.execute(
                """INSERT INTO abuse_policies
                       (tenant_id, scope_type, scope_id, mode, policies, updated_by)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                   ON CONFLICT (tenant_id, scope_type, scope_id)
                   DO UPDATE SET mode = EXCLUDED.mode, policies = EXCLUDED.policies,
                                 updated_by = EXCLUDED.updated_by,
                                 updated_at = CURRENT_TIMESTAMP""",
                (
                    tenant_id, scope_type, scope_id, mode,
                    json.dumps(policies, ensure_ascii=False), updated_by,
                ),
            )
            cursor.commit()
        logger.info(
            f"后端日志：防滥用绑定已保存 tenant={tenant_id} scope={scope_type}:{scope_id} "
            f"mode={mode} rules={[p.get('rule_id') for p in policies]}"
        )
