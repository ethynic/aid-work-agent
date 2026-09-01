"""招聘操作智能体简历沟通/邀约时间线服务层（第④期）

镜像 src/services/recruiting_resume_service.py 的分层约定：
- 表 bs_recruiting_operator_resume_comm_logs（沟通记录）+ bs_recruiting_operator_resume_invitations（邀约记录）
- tenant_id 显式传参，不依赖 saas context / HTTP Request
- 校验失败抛 ValueError（中文消息），由 API 层转 400
- DB 访问 get_db_connection + %s 参数化，所有查询带 tenant_id 过滤（租户隔离规范）
- 两表 resume_id 外键引用 bs_recruiting_operator_resumes(id) ON DELETE CASCADE
  （删简历级联清沟通/邀约；FK 由数据库兜底，服务层不再重复校验简历存在性，
  API 层子资源端点先查简历判 404）

范围说明（本期仅页面手动补录）：
- 沟通记录 / 邀约信息均由招聘 HR 在简历详情页手动补录
- agent 端 boss_send_to 打招呼后自动回写沟通记录为下一期（涉及本地工具链路改造）

调用方：HTTP API（src/api/recruiting_operator.py，前端简历详情页「沟通记录」「邀约信息」两个 tab）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection

# 沟通方向（out=我方发出 / in=候选人来信）
COMM_DIRECTIONS = ("out", "in")
# 沟通渠道（boss=BOSS 直聘 / wecom=企业微信 / phone=电话 / other=其他）
COMM_CHANNELS = ("boss", "wecom", "phone", "other")
# 邀约状态（pending待确认/confirmed已确认/done已到面/noshow未到面/cancelled已取消）
INVITATION_STATUSES = ("pending", "confirmed", "done", "noshow", "cancelled")


# ============== 建表（幂等） ==============

def init_recruiting_timeline_tables(conn) -> None:
    """幂等建简历时间线两表（沟通记录 / 邀约记录）。

    由 src/db/database.py 启动初始化与集成测试调用（conn 由调用方管理事务）。
    注意：两表 FK 引用 bs_recruiting_operator_resumes(id)，必须在简历表之后执行；
    简历表由 init_recruiting_operator_tables 先建（启动顺序 jobs → resumes → 本函数）。
    """
    cursor = conn.cursor()

    # 沟通记录：一简历多条，按时间倒序展示为时间线；user_id 记录补录操作人
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_operator_resume_comm_logs (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            resume_id BIGINT NOT NULL
                REFERENCES bs_recruiting_operator_resumes(id) ON DELETE CASCADE,
            direction TEXT NOT NULL,             -- out=我方发出 / in=候选人来信
            channel TEXT NOT NULL DEFAULT 'boss', -- boss/wecom/phone/other
            content TEXT NOT NULL,               -- 沟通内容全文
            user_id TEXT,                        -- 补录操作人
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # 邀约记录：一简历可多次邀约（约面失败可再约），created_at DESC 展示
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_operator_resume_invitations (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            resume_id BIGINT NOT NULL
                REFERENCES bs_recruiting_operator_resumes(id) ON DELETE CASCADE,
            interview_at TIMESTAMPTZ,            -- 面试时间（可空：还没约到具体时间）
            interviewer TEXT,                    -- 面试官
            method TEXT,                         -- 面试方式（自由文本：现场/电话/视频面试等）
            status TEXT NOT NULL DEFAULT 'pending', -- pending/confirmed/done/noshow/cancelled
            notes TEXT,                          -- 备注
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_rorcl_tenant_resume
        ON bs_recruiting_operator_resume_comm_logs(tenant_id, resume_id, created_at)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_rorinv_tenant_resume
        ON bs_recruiting_operator_resume_invitations(tenant_id, resume_id)
    """)

    logger.info(
        "recruiting_operator 简历时间线表已就绪 "
        "(bs_recruiting_operator_resume_comm_logs / bs_recruiting_operator_resume_invitations)"
    )


def ensure_tables() -> None:
    """幂等建表（自开连接版，脚本/测试兜底调用）"""
    with get_db_connection() as conn:
        init_recruiting_timeline_tables(conn)
        conn.commit()


# ============== 内部辅助 ==============

def _parse_interview_at(value: str) -> datetime:
    """把邀约请求里的 interview_at（ISO 字符串）转为 datetime，非法抛 ValueError。

    参照 resume_service._parse_fetched_at 的写法本地实现（不直接 import 复用：
    该函数错误消息为「获取日期格式非法」，对邀约时间有误导）。
    """
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"面试时间格式非法: {value}")


def _parse_occurred_at(value: str) -> datetime:
    """把沟通发生时间（ISO 字符串）转为 datetime，非法抛 ValueError（写法同 _parse_interview_at）。

    naive datetime 按 DB 会话时区解释（国内单时区部署=北京时间）。
    """
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"沟通时间格式非法: {value}")


def _row_to_timeline_item(row) -> Dict[str, Any]:
    """时间线行转 dict（列均为标量，无需 JSONB 兜底；datetime 交给 API 层 JSON 序列化）"""
    return dict(row)


def _validate_direction(direction: str) -> str:
    """沟通方向校验，非法抛 ValueError"""
    if direction not in COMM_DIRECTIONS:
        raise ValueError(f"沟通方向值非法: direction={direction} not in {COMM_DIRECTIONS}")
    return direction


def _validate_channel(channel: str) -> str:
    """沟通渠道校验，非法抛 ValueError"""
    if channel not in COMM_CHANNELS:
        raise ValueError(f"沟通渠道值非法: channel={channel} not in {COMM_CHANNELS}")
    return channel


def _validate_invitation_status(status: str) -> str:
    """邀约状态校验，非法抛 ValueError"""
    if status not in INVITATION_STATUSES:
        raise ValueError(f"邀约状态值非法: status={status} not in {INVITATION_STATUSES}")
    return status


# ============== 沟通记录 ==============

def list_comm_logs(tenant_id: str, resume_id: int) -> List[Dict[str, Any]]:
    """某简历的沟通记录列表（created_at DESC, id DESC 次级键保序，最新在前）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_operator_resume_comm_logs "
            "WHERE tenant_id = %s AND resume_id = %s ORDER BY created_at DESC, id DESC",
            (tenant_id, resume_id),
        )
        return [_row_to_timeline_item(row) for row in cursor.fetchall()]


def create_comm_log(
    tenant_id: str,
    resume_id: int,
    direction: str,
    channel: str,
    content: str,
    user_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    """补录一条沟通记录，返回完整记录 dict；方向/渠道非法或内容为空抛 ValueError（中文消息）。

    occurred_at：沟通发生时间（ISO 字符串，可选）——回写历史聊天时传原始时间戳，
    缺省/None/空串 = 当前时间（页面即时补录场景）；格式非法抛 ValueError。
    """
    _validate_direction(direction)
    _validate_channel(channel)
    if not (content or "").strip():
        raise ValueError("沟通内容不能为空")
    occurred_dt = _parse_occurred_at(occurred_at) if occurred_at and occurred_at.strip() else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resume_comm_logs
                (tenant_id, resume_id, direction, channel, content, user_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
            RETURNING *
            """,
            (tenant_id, resume_id, direction, channel, content.strip(), user_id, occurred_dt),
        )
        row = cursor.fetchone()
        conn.commit()

    logger.info(
        f"沟通记录补录: tenant={tenant_id}, resume={resume_id}, "
        f"direction={direction}, channel={channel}"
    )
    return _row_to_timeline_item(row)


def delete_comm_log(tenant_id: str, log_id: int) -> bool:
    """删除一条沟通记录（仅本租户），返回是否删除成功"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_resume_comm_logs WHERE id = %s AND tenant_id = %s",
            (log_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# ============== 邀约记录 ==============

def list_invitations(tenant_id: str, resume_id: int) -> List[Dict[str, Any]]:
    """某简历的邀约记录列表（created_at DESC, id DESC 次级键保序；一简历可多次邀约）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_operator_resume_invitations "
            "WHERE tenant_id = %s AND resume_id = %s ORDER BY created_at DESC, id DESC",
            (tenant_id, resume_id),
        )
        return [_row_to_timeline_item(row) for row in cursor.fetchall()]


def create_invitation(
    tenant_id: str,
    resume_id: int,
    *,
    interview_at: Optional[str] = None,
    interviewer: Optional[str] = None,
    method: Optional[str] = None,
    status: str = "pending",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """发起一条邀约记录，返回完整记录 dict。

    - interview_at：ISO 字符串（面试时间），可空
    - status：默认 pending 待确认，非法抛 ValueError
    """
    _validate_invitation_status(status)
    interview_at_dt = _parse_interview_at(interview_at) if interview_at else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_operator_resume_invitations
                (tenant_id, resume_id, interview_at, interviewer, method, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (tenant_id, resume_id, interview_at_dt,
             (interviewer or None), (method or None), status, (notes or None)),
        )
        row = cursor.fetchone()
        conn.commit()

    logger.info(
        f"邀约记录创建: tenant={tenant_id}, resume={resume_id}, status={status}"
    )
    return _row_to_timeline_item(row)


def update_invitation(
    tenant_id: str,
    invitation_id: int,
    *,
    interview_at: Optional[str] = None,
    interviewer: Optional[str] = None,
    method: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """更新邀约（仅传的字段：interview_at/interviewer/method/status/notes），updated_at=NOW()。

    None 视为未传（不修改该字段）；status 非法 / 无待更新字段抛 ValueError；
    记录不存在或非本租户返回 None。
    """
    if status is not None:
        _validate_invitation_status(status)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 仅拼传了的字段（None 视为未传）
        sets: list = []
        params: list = []
        if interview_at is not None:
            sets.append("interview_at = %s")
            # 空串/纯空白 = 显式清空面试时间（与 interviewer/method/notes 传空置 NULL 对齐）
            interview_at_str = interview_at.strip()
            params.append(_parse_interview_at(interview_at_str) if interview_at_str else None)
        if interviewer is not None:
            sets.append("interviewer = %s")
            params.append(interviewer.strip() or None)
        if method is not None:
            sets.append("method = %s")
            params.append(method.strip() or None)
        if status is not None:
            sets.append("status = %s")
            params.append(status)
        if notes is not None:
            sets.append("notes = %s")
            params.append(notes.strip() or None)
        if not sets:
            raise ValueError("无待更新字段")

        sets.append("updated_at = NOW()")
        params.extend([invitation_id, tenant_id])
        cursor.execute(
            f"UPDATE bs_recruiting_operator_resume_invitations SET {', '.join(sets)} "
            "WHERE id = %s AND tenant_id = %s RETURNING *",
            params,
        )
        row = cursor.fetchone()
        if not row:
            return None
        conn.commit()

    logger.info(f"邀约记录更新: tenant={tenant_id}, invitation={invitation_id}")
    return _row_to_timeline_item(row)


def delete_invitation(tenant_id: str, invitation_id: int) -> bool:
    """删除一条邀约记录（仅本租户），返回是否删除成功。

    API 已暴露删除入口（DELETE /invitations/{invitation_id}，误录入的邀约可删除；
    正常流转仍用状态位：cancelled/noshow）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM bs_recruiting_operator_resume_invitations WHERE id = %s AND tenant_id = %s",
            (invitation_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0
