"""
平台错误日志管理 API

提供平台管理员查看和管理系统错误日志的接口。
- GET /api/admin/error-logs - 分页查询错误日志
- PUT /api/admin/error-logs/{log_id}/status - 更新处理状态
- DELETE /api/admin/error-logs/cleanup - 清理30天前的旧日志
"""

import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Query, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.db import get_db_connection
from src.saas.permissions.checker import is_platform_admin


router = APIRouter(prefix="/api/admin/error-logs", tags=["平台错误日志"])


# ============== 请求/响应模型 ==============

class ErrorLogItem(BaseModel):
    """错误日志条目"""
    id: int
    timestamp: str
    module: Optional[str]
    error_type: Optional[str]
    message: str
    traceback: Optional[str]
    status: str
    processed_by: Optional[str]
    processed_at: Optional[str]


class ErrorLogListResponse(BaseModel):
    """错误日志列表响应"""
    success: bool
    data: List[ErrorLogItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    message: Optional[str] = None


class UpdateStatusRequest(BaseModel):
    """更新状态请求"""
    status: str = Field(..., description="状态：processed / ignored")


class CleanupResponse(BaseModel):
    """清理响应"""
    success: bool
    deleted_db_records: int
    deleted_log_files: int
    message: Optional[str] = None


# ============== 工具函数 ==============

def _get_error_logs_from_db(
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    从数据库查询错误日志

    Args:
        status: 过滤状态（unprocessed / processed / ignored），None 表示全部
        page: 页码（从1开始）
        page_size: 每页数量

    Returns:
        dict 包含 data 和 total
    """
    offset = (page - 1) * page_size

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 构建查询条件
        conditions = []
        params = []
        if status:
            conditions.append("status = %s")
            params.append(status)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # 查询总数
        cursor.execute(f"""
            SELECT COUNT(*) AS cnt FROM log_error
            WHERE {where_clause}
        """, params)
        total = cursor.fetchone()['cnt']

        # 查询数据（按时间倒序）
        cursor.execute(f"""
            SELECT id, timestamp, module, error_type, message, traceback,
                   status, processed_by, processed_at
            FROM log_error
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT {page_size} OFFSET {offset}
        """, params)

        rows = cursor.fetchall()

        data = []
        for row in rows:
            row_dict = dict(row)
            # 格式化时间
            if row_dict.get('timestamp'):
                row_dict['timestamp'] = str(row_dict['timestamp'])
            if row_dict.get('processed_at'):
                row_dict['processed_at'] = str(row_dict['processed_at'])
            data.append(ErrorLogItem(**row_dict))

    return {"data": data, "total": total}


def _cleanup_old_log_files(log_dir: str = "log/agent", days: int = 30) -> int:
    """
    清理指定目录下的旧日志文件

    Args:
        log_dir: 日志目录
        days: 删除多少天前的文件

    Returns:
        删除的文件数量
    """
    deleted_count = 0
    cutoff_time = datetime.now() - timedelta(days=days)

    if not os.path.exists(log_dir):
        return deleted_count

    for filename in os.listdir(log_dir):
        filepath = os.path.join(log_dir, filename)

        # 只处理文件，跳过目录
        if not os.path.isfile(filepath):
            continue

        # 只处理日志文件（error.log.* 和 aid-work-agent.log.*）
        if not (filename.startswith("error.log.") or
                filename.startswith("aid-work-agent.log.") or
                filename.startswith("aid-work-agent.json.")):
            continue

        try:
            file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if file_mtime < cutoff_time:
                os.remove(filepath)
                deleted_count += 1
                logger.info(f"删除旧日志文件: {filepath}")
        except Exception as e:
            logger.warning(f"删除日志文件失败 {filepath}: {e}")

    return deleted_count


# ============== API端点 ==============

@router.get("", response_model=ErrorLogListResponse)
async def get_error_logs(
    request: Request,
    status: Optional[str] = Query(None, description="过滤状态：unprocessed / processed / ignored"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """
    获取错误日志列表

    仅平台管理员可访问。
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")

    # 验证 status 参数合法性
    if status and status not in ["unprocessed", "processed", "ignored"]:
        raise HTTPException(status_code=400, detail="无效的状态值")

    result = _get_error_logs_from_db(status=status, page=page, page_size=page_size)

    total_pages = (result["total"] + page_size - 1) // page_size

    return ErrorLogListResponse(
        success=True,
        data=result["data"],
        total=result["total"],
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        message=f"共 {result['total']} 条记录"
    )


@router.put("/{log_id}/status")
async def update_error_log_status(
    request: Request,
    log_id: int,
    body: UpdateStatusRequest,
):
    """
    更新错误日志的处理状态

    仅平台管理员可访问。
    状态只能是 processed 或 ignored。
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")

    # 验证状态
    if body.status not in ["processed", "ignored"]:
        raise HTTPException(status_code=400, detail="无效的状态值，仅支持 processed / ignored")

    user_id = user.get("user_id", "unknown")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 检查记录是否存在
        cursor.execute("SELECT id FROM log_error WHERE id = %s", (log_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="错误日志记录不存在")

        # 更新状态
        cursor.execute("""
            UPDATE log_error
            SET status = %s, processed_by = %s, processed_at = %s
            WHERE id = %s
        """, (body.status, user_id, datetime.now(), log_id))

        conn.commit()

    logger.info(f"平台管理员 {user_id} 更新错误日志 #{log_id} 状态为 {body.status}")

    return {"success": True, "message": "状态已更新"}


@router.delete("/cleanup", response_model=CleanupResponse)
async def cleanup_old_error_logs(request: Request):
    """
    清理旧错误日志（30天前）

    同时清理：
    1. 数据库中 30 天前的 log_error 记录
    2. log/agent/ 目录下 30 天前的轮转日志文件

    仅平台管理员可访问。
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可访问")

    days = 30
    cutoff_date = datetime.now() - timedelta(days=days)
    deleted_db = 0
    deleted_files = 0

    # 清理数据库记录
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM log_error
            WHERE timestamp < %s
        """, (cutoff_date,))
        deleted_db = cursor.rowcount
        conn.commit()

    # 清理日志文件
    deleted_files = _cleanup_old_log_files(days=days)

    logger.info(f"平台管理员 {user.get('user_id')} 清理旧日志：数据库删除 {deleted_db} 条，文件删除 {deleted_files} 个")

    return CleanupResponse(
        success=True,
        deleted_db_records=deleted_db,
        deleted_log_files=deleted_files,
        message=f"清理完成：删除 {deleted_db} 条数据库记录，删除 {deleted_files} 个日志文件"
    )
