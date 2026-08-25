"""
长期记忆管理 API

提供用户长期记忆文件的查看和编辑接口
"""

import re
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.api.auth import get_current_user
from src.memory.long_term import LongTermMemory
from src.config.settings import settings


router = APIRouter(prefix="/api/v1/memory", tags=["memory"])

# 全局实例
_long_term_memory: Optional[LongTermMemory] = None


def _get_long_term_memory() -> LongTermMemory:
    global _long_term_memory
    if _long_term_memory is None:
        _long_term_memory = LongTermMemory(
            storage_dir=settings.memory.long_term.storage_dir
        )
    return _long_term_memory


# ============== 请求/响应模型 ==============


class UpdateMemoryRequest(BaseModel):
    """更新记忆请求"""
    content: str = Field(..., description="记忆文件完整内容（Markdown 格式）")


class MemoryResponse(BaseModel):
    """记忆响应"""
    content: str
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


# ============== 工具函数 ==============


def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg

    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
    ]

    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(
            pattern,
            lambda m: m.group(0).split('=')[0] + '=***',
            sanitized,
            flags=re.IGNORECASE
        )

    return sanitized


def _extract_meta(content: str) -> dict:
    """从记忆文件内容中提取元信息"""
    meta = {"updated_at": None, "updated_by": None}
    for line in content.split("\n"):
        if line.startswith("> 最后更新:"):
            meta["updated_at"] = line.replace("> 最后更新:", "").strip()
        elif line.startswith("> 更新来源:"):
            meta["updated_by"] = line.replace("> 更新来源:", "").strip()
    return meta


# ============== API 路由 ==============


@router.get("/long-term")
async def get_long_term_memory(
    current_user: dict = Depends(get_current_user),
):
    """获取当前用户的长期记忆文件内容"""
    try:
        if not settings.memory.long_term.enabled:
            return {
                "success": False,
                "error": "长期记忆功能未启用",
            }

        user_id = current_user.get("user_id")
        tenant_id = current_user.get("tenant_id")
        ltm = _get_long_term_memory()

        content = ltm.get_memory(tenant_id, user_id)
        meta = _extract_meta(content)

        return {
            "success": True,
            "data": {
                "content": content,
                "updated_at": meta["updated_at"],
                "updated_by": meta["updated_by"],
            },
        }

    except Exception as e:
        logger.opt(exception=True).error(f"获取长期记忆失败: {e}")
        return {
            "success": False,
            "error": "获取长期记忆失败",
            "debug": sanitize_error_info(str(e)),
        }


@router.put("/long-term")
async def update_long_term_memory(
    request: UpdateMemoryRequest,
    current_user: dict = Depends(get_current_user),
):
    """更新当前用户的长期记忆文件内容"""
    try:
        if not settings.memory.long_term.enabled:
            return {
                "success": False,
                "error": "长期记忆功能未启用",
            }

        user_id = current_user.get("user_id")
        tenant_id = current_user.get("tenant_id")
        ltm = _get_long_term_memory()

        ltm.save_memory(tenant_id, user_id, request.content, updated_by="user")
        logger.info(f"用户 {user_id} 更新了长期记忆")

        content = ltm.get_memory(tenant_id, user_id)
        meta = _extract_meta(content)

        return {
            "success": True,
            "data": {
                "content": content,
                "updated_at": meta["updated_at"],
                "updated_by": meta["updated_by"],
            },
            "message": "记忆更新成功",
        }

    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"更新长期记忆失败: {e}")
        return {
            "success": False,
            "error": "更新长期记忆失败",
            "debug": sanitize_error_info(str(e)),
        }
