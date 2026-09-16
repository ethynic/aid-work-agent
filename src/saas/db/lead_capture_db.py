"""客户留资线索表数据访问层

对应表 `bs_lead_capture_leads`（售前咨询留资，能力级中性命名）。
手机号按项目安全原则加密落库（复用 src/db/encryption.py，与渠道配置敏感字段同款），
列表/详情接口在权限内解密返回，日志/统计不打印明文。

设计文档：docs/subagent/pre-sales/lead-capture-design.md
开发计划：docs/subagent/pre-sales/lead-capture-dev-plan.md
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager
from src.saas.models.enums import LeadIntentLevel

# 线索阶段枚举（前后端保持一致）
LEAD_STAGES = ("new", "contacting", "converted", "abandoned")

# 客户意向度枚举（lead_refresh 判定回写，非法值拒绝入库）
LEAD_INTENT_LEVELS = LeadIntentLevel.all_values()


def _encrypt_phone(phone: Optional[str]) -> Optional[str]:
    """手机号加密落库；空值原样返回。"""
    if not phone:
        return None
    try:
        return encryption_manager.encrypt(str(phone).strip())
    except Exception as e:
        logger.error(f"后端日志：留资手机号加密失败: {e}")
        raise


def _decrypt_phone(encrypted: Optional[str]) -> Optional[str]:
    """解密手机号；空值/非密文原样返回。"""
    if not encrypted:
        return None
    try:
        return encryption_manager.decrypt(str(encrypted))
    except Exception:
        # 兼容历史明文数据（加密功能上线前写入的）
        return encrypted


class LeadCaptureDB:
    """客户留资线索表数据访问类"""

    @staticmethod
    def create(
        *,
        tenant_id: str,
        lead_id: str,
        user_id: Optional[str] = None,
        customer_user_id: Optional[str] = None,
        channel_chat_id: Optional[str] = None,
        kf_account_name: Optional[str] = None,
        contact_method: str,
        phone: Optional[str] = None,
        contact_name: Optional[str] = None,
        demand_summary: Optional[str] = None,
        assigned_to: Optional[str] = None,
        assignee_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """写入一条留资线索，返回新记录（phone 已解密）。"""
        encrypted_phone = _encrypt_phone(phone) if contact_method == "phone" else None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO bs_lead_capture_leads
                    (lead_id, tenant_id, user_id, customer_user_id, channel_chat_id,
                     kf_account_name, contact_method, phone, contact_name, demand_summary,
                     assigned_to, assignee_name, session_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        lead_id,
                        tenant_id,
                        user_id,
                        customer_user_id,
                        channel_chat_id,
                        kf_account_name,
                        contact_method,
                        encrypted_phone,
                        contact_name,
                        demand_summary,
                        assigned_to,
                        assignee_name,
                        session_id,
                    ),
                )
                row = cursor.fetchone()
                conn.commit()
                if row:
                    row["phone"] = _decrypt_phone(row.get("phone"))
                return dict(row) if row else None
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(
                    f"后端日志：写入留资线索失败 tenant={tenant_id}: {e}"
                )
                return None

    @staticmethod
    def get_by_id(lead_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """按 lead_id 查询线索（含解密手机号）。tenant_id 传入时额外校验租户归属。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute(
                    "SELECT * FROM bs_lead_capture_leads WHERE lead_id = %s AND tenant_id = %s",
                    (lead_id, tenant_id),
                )
            else:
                cursor.execute(
                    "SELECT * FROM bs_lead_capture_leads WHERE lead_id = %s",
                    (lead_id,),
                )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            d["phone"] = _decrypt_phone(d.get("phone"))
            return d

    @staticmethod
    def list_by_tenant(
        tenant_id: str,
        page: int = 1,
        page_size: int = 20,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        channel_chat_id: Optional[str] = None,
        stage: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """线索列表（分页，created_at DESC）。

        Args:
            tenant_id: 租户 ID
            page / page_size: 分页
            start_date / end_date: 日期段（含当日，end_date 按 < 次日 语义 SQL 内 +1 天）
            channel_chat_id: 客服账号（open_kfid）筛选
            stage: 阶段筛选
            assigned_to: 归属员工筛选（普通用户仅见自己）
        """
        conditions = ["tenant_id = %s"]
        params: list = [tenant_id]
        if start_date:
            conditions.append("created_at >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("created_at < (%s::date + INTERVAL '1 day')")
            params.append(end_date)
        if channel_chat_id:
            conditions.append("channel_chat_id = %s")
            params.append(channel_chat_id)
        if stage:
            conditions.append("stage = %s")
            params.append(stage)
        if assigned_to:
            conditions.append("assigned_to = %s")
            params.append(assigned_to)

        where_clause = " AND ".join(conditions)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM bs_lead_capture_leads WHERE {where_clause}",
                params,
            )
            total = cursor.fetchone()["cnt"]

            cursor.execute(
                f"""
                SELECT * FROM bs_lead_capture_leads
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            leads = []
            for row in cursor.fetchall():
                d = dict(row)
                d["phone"] = _decrypt_phone(d.get("phone"))
                leads.append(d)

        return {"leads": leads, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def update_stage(lead_id: str, stage: str, tenant_id: str) -> bool:
        """更新线索阶段（new -> contacting -> converted / abandoned）。"""
        if stage not in LEAD_STAGES:
            return False
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_lead_capture_leads
                SET stage = %s, updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = %s AND tenant_id = %s
                """,
                (stage, lead_id, tenant_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_analysis(
        lead_id: str,
        tenant_id: str,
        intent_level: str,
        intent_reason: Optional[str],
        demand_points: Optional[List[str]],
        last_analyzed_message_id: Optional[str] = None,
    ) -> bool:
        """回写 lead_refresh 分析结果（意向度/判定依据/需求分条/分析游标）。

        demand_points 以 JSONB 存储（字符串数组）；intent_level 非法值拒绝入库
        （枚举以 src/saas/models/enums.py LeadIntentLevel 为准）。
        """
        if intent_level not in LEAD_INTENT_LEVELS:
            logger.warning(
                f"后端日志：线索分析回写拒绝非法意向度 lead_id={lead_id}, intent_level={intent_level}"
            )
            return False
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_lead_capture_leads
                SET intent_level = %s,
                    intent_reason = %s,
                    demand_points = %s::jsonb,
                    last_analyzed_message_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = %s AND tenant_id = %s
                """,
                (
                    intent_level,
                    intent_reason,
                    json.dumps(demand_points, ensure_ascii=False) if demand_points is not None else None,
                    last_analyzed_message_id,
                    lead_id,
                    tenant_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_transfer_info(
        lead_id: str,
        tenant_id: str,
        transferred_to: Optional[str],
        servicer_name: Optional[str],
    ) -> bool:
        """回写人工服务归属（transfer_to_human 工具转接成功后调用）。

        字段语义为「最近一次转人工」，多次转人工最后写赢；
        last_human_transfer_at 由数据库时间戳记录。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_lead_capture_leads
                SET transferred_to = %s,
                    servicer_name = %s,
                    last_human_transfer_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = %s AND tenant_id = %s
                """,
                (transferred_to, servicer_name, lead_id, tenant_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(lead_id: str, tenant_id: str) -> bool:
        """物理删除线索记录（隐藏命令「新会话/清空会话」清留资时调用）。

        返回是否删除了行；lead_id 不存在或不属于该租户时返回 False。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_lead_capture_leads WHERE lead_id = %s AND tenant_id = %s",
                (lead_id, tenant_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def stats(
        tenant_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        assigned_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """留资统计：总留资 / 按留资方式分组 / 按客服账号分组（含 ratio）。

        过滤基准 = bs_lead_capture_leads.created_at。
        assigned_to 传入时（普通用户）仅统计该归属员工自己的线索。
        """
        date_cond = ""
        params: list = [tenant_id]
        if start_date:
            date_cond += " AND created_at >= %s"
            params.append(start_date)
        if end_date:
            # end_date 含当日：< 次日零点 语义，SQL 内 +1 天
            date_cond += " AND created_at < (%s::date + INTERVAL '1 day')"
            params.append(end_date)
        if assigned_to:
            date_cond += " AND assigned_to = %s"
            params.append(assigned_to)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM bs_lead_capture_leads "
                f"WHERE tenant_id = %s{date_cond}",
                params,
            )
            total = cursor.fetchone()["cnt"]

            # 按留资方式分组（手机号 vs 二维码）
            cursor.execute(
                f"""
                SELECT contact_method, COUNT(*) AS count
                FROM bs_lead_capture_leads
                WHERE tenant_id = %s{date_cond}
                GROUP BY contact_method
                ORDER BY count DESC
                """,
                params,
            )
            by_contact_method = []
            for r in cursor.fetchall():
                d = dict(r)
                d["ratio"] = round(d["count"] * 100.0 / total, 1) if total else 0.0
                by_contact_method.append(d)

            # 按客服账号分组（channel_chat_id + 名称快照）
            cursor.execute(
                f"""
                SELECT channel_chat_id, kf_account_name, COUNT(*) AS count
                FROM bs_lead_capture_leads
                WHERE tenant_id = %s{date_cond}
                GROUP BY channel_chat_id, kf_account_name
                ORDER BY count DESC
                """,
                params,
            )
            by_kf_account = []
            for r in cursor.fetchall():
                d = dict(r)
                d["ratio"] = round(d["count"] * 100.0 / total, 1) if total else 0.0
                by_kf_account.append(d)

        return {
            "total_leads": total,
            "by_contact_method": by_contact_method,
            "by_kf_account": by_kf_account,
        }
