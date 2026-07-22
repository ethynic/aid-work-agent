"""
工作日报表 CRUD

操作 work_daily_reports / work_report_preferences 两张表。
- work_daily_reports：报告正文 + 统计指标 + 元数据
- work_report_preferences：每用户一行的推送配置（含 daily/weekly/monthly 复选）
"""

import json
import uuid
from datetime import date, time as time_type
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection


def generate_report_id() -> str:
    """生成唯一报告 ID（wdr_ 前缀，work daily report 缩写）"""
    return f"wdr_{uuid.uuid4().hex[:12]}"


# ============== work_daily_reports ==============

class WorkDailyReportDB:
    """work_daily_reports 表 CRUD"""

    @staticmethod
    def upsert(
        tenant_id: str,
        scope: str,
        report_type: str,
        report_date: date,
        target_user_id: Optional[str],
        metrics: Dict[str, Any],
        summary_text: str,
        highlights: Optional[List[Dict[str, Any]]] = None,
        suggestions: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        token_cost: int = 0,
        credit_cost: int = 0,
        is_regenerate: bool = False,
    ) -> Dict[str, Any]:
        """UPSERT 一条报告记录

        - 不存在则 INSERT，存在则 UPDATE（保留 regenerated_count 自增）
        - is_regenerate=True 时 regenerated_count + 1
        """
        report_id = generate_report_id()
        metrics_json = json.dumps(metrics, ensure_ascii=False)
        highlights_json = json.dumps(highlights, ensure_ascii=False) if highlights else None
        suggestions_json = json.dumps(suggestions, ensure_ascii=False) if suggestions else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 先尝试 UPDATE
                cursor.execute(
                    """
                    UPDATE work_daily_reports
                    SET metrics = %s, summary_text = %s, highlights = %s, suggestions = %s,
                        model = %s, token_cost = %s, credit_cost = %s,
                        generated_at = CURRENT_TIMESTAMP,
                        regenerated_count = regenerated_count + %s
                    WHERE tenant_id = %s AND scope = %s AND report_type = %s
                      AND target_user_id IS NOT DISTINCT FROM %s
                      AND report_date = %s
                    RETURNING id, report_id
                    """,
                    (
                        metrics_json, summary_text, highlights_json, suggestions_json,
                        model, token_cost, credit_cost,
                        1 if is_regenerate else 0,
                        tenant_id, scope, report_type,
                        target_user_id, report_date,
                    ),
                )
                row = cursor.fetchone()

                if row is None:
                    # 不存在，INSERT
                    cursor.execute(
                        """
                        INSERT INTO work_daily_reports
                            (report_id, tenant_id, scope, report_type, target_user_id, report_date,
                             metrics, summary_text, highlights, suggestions,
                             model, token_cost, credit_cost, regenerated_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, report_id
                        """,
                        (
                            report_id, tenant_id, scope, report_type, target_user_id, report_date,
                            metrics_json, summary_text, highlights_json, suggestions_json,
                            model, token_cost, credit_cost,
                            1 if is_regenerate else 0,
                        ),
                    )
                    row = cursor.fetchone()

                conn.commit()
                logger.info(
                    f"work_daily_reports UPSERT: tenant={tenant_id}, scope={scope}, "
                    f"type={report_type}, date={report_date}, report_id={row['report_id']}"
                )
                return {"id": row["id"], "report_id": row["report_id"]}
            except Exception as e:
                conn.rollback()
                logger.error(f"work_daily_reports UPSERT 失败: {e}", exc_info=True)
                raise

    @staticmethod
    def get(
        tenant_id: str,
        scope: str,
        report_type: str,
        report_date: date,
        target_user_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """获取单条报告"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM work_daily_reports
                WHERE tenant_id = %s AND scope = %s AND report_type = %s
                  AND target_user_id IS NOT DISTINCT FROM %s
                  AND report_date = %s
                """,
                (tenant_id, scope, report_type, target_user_id, report_date),
            )
            row = cursor.fetchone()
            return _row_to_report(row)

    @staticmethod
    def list_by_user(
        tenant_id: str,
        user_id: str,
        scope: str = "personal",
        report_type: Optional[str] = None,
        limit: int = 30,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出某用户的报告（按日期倒序）"""
        sql = """
            SELECT * FROM work_daily_reports
            WHERE tenant_id = %s AND scope = %s AND target_user_id = %s
        """
        params: list = [tenant_id, scope, user_id]
        if report_type:
            sql += " AND report_type = %s"
            params.append(report_type)
        sql += " ORDER BY report_date DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [_row_to_report(r) for r in rows if r]


# ============== work_report_preferences ==============

class WorkReportPreferenceDB:
    """work_report_preferences 表 CRUD（每用户一行）"""

    @staticmethod
    def get(tenant_id: str, user_id: str) -> Dict[str, Any]:
        """获取用户推送配置，不存在则返回默认值"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM work_report_preferences
                WHERE tenant_id = %s AND user_id = %s
                """,
                (tenant_id, user_id),
            )
            row = cursor.fetchone()
            if not row:
                return _default_preferences(tenant_id, user_id)
            return _row_to_preferences(row)

    @staticmethod
    def upsert(tenant_id: str, user_id: str, **fields) -> Dict[str, Any]:
        """UPSERT 用户推送配置

        可更新字段：personal_report_enabled, personal_report_types,
                    personal_push_channels, personal_push_time,
                    team_report_enabled, team_report_types,
                    team_push_channels, team_push_time
        """
        allowed = {
            "personal_report_enabled", "personal_report_types",
            "personal_push_channels", "personal_push_time",
            "team_report_enabled", "team_report_types",
            "team_push_channels", "team_push_time",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return WorkReportPreferenceDB.get(tenant_id, user_id)

        # 构造 UPSERT
        columns = ["tenant_id", "user_id"] + list(updates.keys())
        placeholders = ["%s"] * len(columns)
        values: list = [tenant_id, user_id]

        # 处理数组/时间字段
        for k, v in updates.items():
            if k.endswith("_report_types") and isinstance(v, list):
                values.append(v)
            elif k.endswith("_push_channels"):
                values.append(v if v is not None else None)
            elif k.endswith("_push_time") and isinstance(v, str):
                values.append(v)
            else:
                values.append(v)

        update_clause = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in updates.keys()
        )

        sql = f"""
            INSERT INTO work_report_preferences ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT (tenant_id, user_id) DO UPDATE
            SET {update_clause}, updated_at = CURRENT_TIMESTAMP
            RETURNING *
        """

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, values)
                row = cursor.fetchone()
                conn.commit()
                return _row_to_preferences(row)
            except Exception as e:
                conn.rollback()
                logger.error(f"work_report_preferences UPSERT 失败: {e}", exc_info=True)
                raise


# ============== 内部工具 ==============

def _row_to_report(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """数据库行转 dict（解析 JSONB 字段）"""
    if not row:
        return None
    result = dict(row)
    # metrics / highlights / suggestions 是 JSONB
    for k in ("metrics", "highlights", "suggestions"):
        v = result.get(k)
        if isinstance(v, str):
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
    # report_date 转 ISO 字符串，便于前端处理
    if result.get("report_date") and hasattr(result["report_date"], "isoformat"):
        result["report_date"] = result["report_date"].isoformat()
    if result.get("generated_at") and hasattr(result["generated_at"], "isoformat"):
        result["generated_at"] = result["generated_at"].isoformat()
    return result


def _row_to_preferences(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """数据库行转 preferences dict"""
    if not row:
        return None
    result = dict(row)
    if result.get("personal_push_time") and hasattr(result["personal_push_time"], "isoformat"):
        result["personal_push_time"] = result["personal_push_time"].isoformat()
    if result.get("team_push_time") and hasattr(result["team_push_time"], "isoformat"):
        result["team_push_time"] = result["team_push_time"].isoformat()
    if result.get("updated_at") and hasattr(result["updated_at"], "isoformat"):
        result["updated_at"] = result["updated_at"].isoformat()
    return result


def _default_preferences(tenant_id: str, user_id: str) -> Dict[str, Any]:
    """默认推送配置（未落库时返回）"""
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "personal_report_enabled": True,
        "personal_report_types": ["daily"],
        "personal_push_channels": ["in_app"],
        "personal_push_time": "18:00",
        "team_report_enabled": False,
        "team_report_types": ["daily"],
        "team_push_channels": [],
        "team_push_time": "19:00",
        "updated_at": None,
    }
