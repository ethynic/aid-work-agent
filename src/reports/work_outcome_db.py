"""
工作成果表 CRUD

操作 work_outcomes 表，记录子智能体产生的重要工作成果。
- 层1 实时层：cp 工具内嵌写入（source=cp_realtime），不调用 LLM
- 层2 复盘层：每日 02:30 定时任务用小模型分析提取（source=scheduled_review）

详见 docs/system/work-outcome-record-design.md
"""

import json
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection


def generate_outcome_id() -> str:
    """生成唯一工作成果 ID（wo_ 前缀，work outcome 缩写）"""
    return f"wo_{uuid.uuid4().hex[:8]}"


class WorkOutcomeDB:
    """work_outcomes 表 CRUD"""

    @staticmethod
    def create(
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        summary: str,
        outcome_type: str = "other",
        importance: str = "normal",
        subagent_id: Optional[str] = None,
        channel: Optional[str] = None,
        file_id: Optional[str] = None,
        file_name: Optional[str] = None,
        file_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "cp_realtime",
        chat_record_id: Optional[int] = None,
        review_batch_id: Optional[str] = None,
        review_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入一条工作成果记录

        Args:
            tenant_id: 租户 ID
            user_id: 触发成果的用户 ID
            session_id: 会话 ID
            summary: 一句话摘要（含业务对象和动作）
            outcome_type: file / action / decision / other
            importance: normal / high（预留）
            subagent_id: 子智能体 ID（主智能体直接交付时为 None）
            channel: web / wecom / dingtalk / feishu / wecom_kf
            file_id: cp 工具注册的 file_id（outcome_type=file 时必填）
            file_name: 面向用户的业务文件名
            file_path: 文件存储路径
            metadata: 业务扩展信息（JSON）
            source: cp_realtime / scheduled_review / manual
            chat_record_id: 关联 chat_records.id
            review_batch_id: 复盘批次 ID（source=scheduled_review 时填写）
            review_confidence: 小模型判断置信度 0.0~1.0

        Returns:
            {"outcome_id": str, "id": int}
        """
        outcome_id = generate_outcome_id()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO work_outcomes
                        (outcome_id, tenant_id, user_id, subagent_id, session_id, channel,
                         summary, outcome_type, importance,
                         file_id, file_name, file_path,
                         metadata, source, chat_record_id,
                         review_batch_id, review_confidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, outcome_id
                    """,
                    (
                        outcome_id, tenant_id, user_id, subagent_id, session_id, channel,
                        summary, outcome_type, importance,
                        file_id, file_name, file_path,
                        metadata_json, source, chat_record_id,
                        review_batch_id, review_confidence,
                    ),
                )
                row = cursor.fetchone()
                conn.commit()
                logger.info(
                    f"work_outcomes INSERT: tenant={tenant_id}, user={user_id}, "
                    f"outcome_id={row['outcome_id']}, type={outcome_type}, source={source}"
                )
                return {"id": row["id"], "outcome_id": row["outcome_id"]}
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"work_outcomes INSERT 失败: {e}")
                raise

    @staticmethod
    def exists_by_session_and_type(
        session_id: str, outcome_type: str = "file"
    ) -> bool:
        """检查会话是否已经产生过指定类型的工作成果

        层2 复盘任务用：判断是否需要复盘该会话。
        - outcome_type=file：检查是否已有 cp_realtime 文件型成果
        - 其他类型：检查是否已有同类成果（去重用）
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM work_outcomes
                WHERE session_id = %s AND outcome_type = %s
                LIMIT 1
                """,
                (session_id, outcome_type),
            )
            return cursor.fetchone() is not None

    @staticmethod
    def list_by_tenant(
        *,
        tenant_id: str,
        user_id: Optional[str] = None,
        subagent_id: Optional[str] = None,
        outcome_type: Optional[str] = None,
        source: Optional[str] = None,
        channel: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        keyword: Optional[str] = None,
        min_confidence: Optional[float] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """列表查询（按 created_at DESC 排序）

        Args:
            tenant_id: 租户 ID（必填，租户隔离）
            user_id: 按用户筛选（普通用户强制设为自己）
            subagent_id: 按子智能体筛选
            outcome_type: file / action / decision / other
            source: cp_realtime / scheduled_review / manual
            channel: web / wecom / dingtalk / feishu / wecom_kf
            start_date: 开始日期（含）
            end_date: 结束日期（含）
            keyword: 摘要关键词搜索（ILIKE）
            min_confidence: 最小置信度（仅复盘类记录有意义）
            page: 页码（1-based）
            page_size: 每页条数

        Returns:
            {"items": List[dict], "total": int, "page": int, "page_size": int}
        """
        where_parts = ["tenant_id = %s"]
        params: list = [tenant_id]

        if user_id:
            where_parts.append("user_id = %s")
            params.append(user_id)
        if subagent_id:
            # 前端"主智能体"过滤占位值：转换为 IS NULL 查询
            # （主智能体产出的成果 subagent_id 为 NULL，不能用 = 比较）
            if subagent_id == "__NULL__":
                where_parts.append("subagent_id IS NULL")
            else:
                where_parts.append("subagent_id = %s")
                params.append(subagent_id)
        if outcome_type:
            where_parts.append("outcome_type = %s")
            params.append(outcome_type)
        if source:
            where_parts.append("source = %s")
            params.append(source)
        if channel:
            where_parts.append("channel = %s")
            params.append(channel)
        if start_date:
            where_parts.append("created_at >= %s")
            params.append(start_date)
        if end_date:
            # end_date 当天 23:59:59.999999，包含整天
            where_parts.append("created_at < %s")
            params.append(
                datetime.combine(end_date, datetime.max.time())
            )
        if keyword:
            where_parts.append("summary ILIKE %s")
            params.append(f"%{keyword}%")
        if min_confidence is not None:
            where_parts.append("(review_confidence IS NULL OR review_confidence >= %s)")
            params.append(min_confidence)

        where_clause = " AND ".join(where_parts)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 查总数
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM work_outcomes WHERE {where_clause}",
                params,
            )
            total = int(cursor.fetchone()["cnt"])

            # 查列表
            cursor.execute(
                f"""
                SELECT * FROM work_outcomes
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = cursor.fetchall()
            return {
                "items": [_row_to_outcome(r) for r in rows if r],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

    @staticmethod
    def get_by_outcome_id(outcome_id: str) -> Optional[Dict[str, Any]]:
        """按 outcome_id 查询单条记录"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM work_outcomes WHERE outcome_id = %s",
                (outcome_id,),
            )
            row = cursor.fetchone()
            return _row_to_outcome(row)

    @staticmethod
    def delete_by_outcome_id(outcome_id: str) -> bool:
        """按 outcome_id 删除单条记录

        Returns:
            True 表示已删除，False 表示记录不存在
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM work_outcomes WHERE outcome_id = %s",
                    (outcome_id,),
                )
                deleted = cursor.rowcount > 0
                conn.commit()
                if deleted:
                    logger.info(f"work_outcomes DELETE: outcome_id={outcome_id}")
                return deleted
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"work_outcomes DELETE 失败: {e}")
                raise

    @staticmethod
    def get_stats(
        *,
        tenant_id: str,
        user_id: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """统计查询

        Returns:
            {
                "total": int,
                "by_type": {file: int, action: int, ...},
                "by_subagent": {subagent_id: count, ...},
                "by_source": {cp_realtime: int, scheduled_review: int, manual: int},
                "by_channel": {web: int, wecom: int, ...},
                "review_stats": {
                    "last_batch_id": str,
                    "last_batch_outcomes_count": int,
                    "avg_confidence": float,
                },
                "time_range": {"start": str, "end": str},
            }
        """
        where_parts = ["tenant_id = %s"]
        params: list = [tenant_id]
        if user_id:
            where_parts.append("user_id = %s")
            params.append(user_id)
        if start_date:
            where_parts.append("created_at >= %s")
            params.append(start_date)
        if end_date:
            where_parts.append("created_at < %s")
            params.append(datetime.combine(end_date, datetime.max.time()))

        where_clause = " AND ".join(where_parts)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 总数
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM work_outcomes WHERE {where_clause}",
                params,
            )
            total = int(cursor.fetchone()["cnt"])

            # 按 outcome_type 分组
            cursor.execute(
                f"""
                SELECT outcome_type, COUNT(*) AS cnt
                FROM work_outcomes WHERE {where_clause}
                GROUP BY outcome_type
                """,
                params,
            )
            by_type = {r["outcome_type"]: int(r["cnt"]) for r in cursor.fetchall()}

            # 按 subagent_id 分组（NULL 归为 "main"）
            cursor.execute(
                f"""
                SELECT COALESCE(subagent_id, 'main') AS subagent, COUNT(*) AS cnt
                FROM work_outcomes WHERE {where_clause}
                GROUP BY subagent_id
                """,
                params,
            )
            by_subagent = {r["subagent"]: int(r["cnt"]) for r in cursor.fetchall()}

            # 按 source 分组
            cursor.execute(
                f"""
                SELECT source, COUNT(*) AS cnt
                FROM work_outcomes WHERE {where_clause}
                GROUP BY source
                """,
                params,
            )
            by_source = {r["source"]: int(r["cnt"]) for r in cursor.fetchall()}

            # 按 channel 分组
            cursor.execute(
                f"""
                SELECT COALESCE(channel, 'unknown') AS channel, COUNT(*) AS cnt
                FROM work_outcomes WHERE {where_clause}
                GROUP BY channel
                """,
                params,
            )
            by_channel = {r["channel"]: int(r["cnt"]) for r in cursor.fetchall()}

            # 复盘统计（最近一个批次的产出）
            review_stats: Dict[str, Any] = {
                "last_batch_id": None,
                "last_batch_outcomes_count": 0,
                "avg_confidence": None,
            }
            cursor.execute(
                f"""
                SELECT review_batch_id, COUNT(*) AS cnt, AVG(review_confidence) AS avg_conf
                FROM work_outcomes
                WHERE {where_clause} AND review_batch_id IS NOT NULL
                GROUP BY review_batch_id
                ORDER BY MAX(created_at) DESC
                LIMIT 1
                """,
                params,
            )
            review_row = cursor.fetchone()
            if review_row:
                review_stats = {
                    "last_batch_id": review_row["review_batch_id"],
                    "last_batch_outcomes_count": int(review_row["cnt"]),
                    "avg_confidence": float(review_row["avg_conf"])
                    if review_row["avg_conf"] is not None
                    else None,
                }

            # 时间范围
            cursor.execute(
                f"""
                SELECT MIN(created_at) AS start_ts, MAX(created_at) AS end_ts
                FROM work_outcomes WHERE {where_clause}
                """,
                params,
            )
            time_row = cursor.fetchone()
            time_range = {
                "start": time_row["start_ts"].isoformat() if time_row["start_ts"] else None,
                "end": time_row["end_ts"].isoformat() if time_row["end_ts"] else None,
            }

            return {
                "total": total,
                "by_type": by_type,
                "by_subagent": by_subagent,
                "by_source": by_source,
                "by_channel": by_channel,
                "review_stats": review_stats,
                "time_range": time_range,
            }


# ============== 内部工具 ==============

def _row_to_outcome(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """数据库行转 dict（解析 JSONB 字段，时间戳转 ISO 字符串）"""
    if not row:
        return None
    result = dict(row)
    # metadata 是 JSONB
    v = result.get("metadata")
    if isinstance(v, str):
        try:
            result["metadata"] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            pass
    # created_at 转 ISO 字符串
    if result.get("created_at") and hasattr(result["created_at"], "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    return result
