"""远程连接凭据管理 API

提供 SMB/FTP 服务器连接凭据的增删改查接口
"""

import re
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from loguru import logger

from src.db.remote_credential import RemoteCredentialDB, encryption_manager
from src.db.models import UserDB
from src.api.auth import get_current_user


router = APIRouter(prefix="/api/credentials", tags=["credentials"])


# ============== 请求/响应模型 ==============

class CreateCredentialRequest(BaseModel):
    """创建凭据请求"""
    connection_type: str = Field(..., description="连接类型: smb 或 ftp")
    server_host: str = Field(..., description="服务器地址")
    server_port: int = Field(..., description="服务器端口")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    remote_path: str = Field(..., description="远程路径")
    name: Optional[str] = Field(None, description="凭据名称")
    domain: Optional[str] = Field(None, description="SMB 域")
    description: Optional[str] = Field(None, description="描述")


class UpdateCredentialRequest(BaseModel):
    """更新凭据请求"""
    connection_type: Optional[str] = Field(None, description="连接类型")
    server_host: Optional[str] = Field(None, description="服务器地址")
    server_port: Optional[int] = Field(None, description="服务器端口")
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码")
    remote_path: Optional[str] = Field(None, description="远程路径")
    name: Optional[str] = Field(None, description="凭据名称")
    domain: Optional[str] = Field(None, description="SMB 域")
    description: Optional[str] = Field(None, description="描述")
    status: Optional[int] = Field(None, description="状态")


class CredentialResponse(BaseModel):
    """凭据响应"""
    credential_id: str
    connection_type: str
    server_host: str
    server_port: int
    username: str
    remote_path: str
    domain: Optional[str]
    name: Optional[str]
    description: Optional[str]
    status: int
    created_at: str
    updated_at: str


# ============== 工具函数 ==============

def sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg

    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
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


# ============== API 路由 ==============

@router.post("/")
async def create_credential(
    request: CreateCredentialRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    创建远程连接凭据

    Args:
        request: 凭据创建请求
        current_user: 当前用户

    Returns:
        创建结果
    """
    try:
        user_id = current_user.get("user_id")

        # 验证连接类型
        if request.connection_type not in ['smb', 'ftp']:
            return {
                "success": False,
                "error": "不支持的连接类型，必须是 'smb' 或 'ftp'",
                "debug": f"连接类型: {request.connection_type}"
            }

        # 验证端口
        if request.server_port < 1 or request.server_port > 65535:
            return {
                "success": False,
                "error": "端口号必须在 1-65535 之间",
                "debug": f"端口号: {request.server_port}"
            }

        # 创建凭据
        credential_id = RemoteCredentialDB.create(
            user_id=user_id,
            connection_type=request.connection_type,
            server_host=request.server_host,
            server_port=request.server_port,
            username=request.username,
            password=request.password,
            remote_path=request.remote_path,
            name=request.name,
            domain=request.domain,
            description=request.description
        )

        logger.info(f"用户 {user_id} 创建凭据 {credential_id}")

        return {
            "success": True,
            "data": {"credential_id": credential_id},
            "message": "凭据创建成功"
        }

    except Exception as e:
        logger.error(f"创建凭据失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "创建凭据失败，请稍后重试",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/")
async def list_credentials(
    connection_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    获取当前用户的所有凭据列表

    Args:
        connection_type: 连接类型筛选（可选）
        current_user: 当前用户

    Returns:
        凭据列表
    """
    try:
        user_id = current_user.get("user_id")
        credentials = RemoteCredentialDB.list_by_user(user_id, connection_type)

        # 过滤掉密码
        safe_credentials = [
            {
                "credential_id": cred["credential_id"],
                "connection_type": cred["connection_type"],
                "server_host": cred["server_host"],
                "server_port": cred["server_port"],
                "username": cred["username"],
                "remote_path": cred["remote_path"],
                "domain": cred.get("domain"),
                "name": cred.get("name"),
                "description": cred.get("description"),
                "status": cred["status"],
                "created_at": cred["created_at"],
                "updated_at": cred["updated_at"]
            }
            for cred in credentials
        ]

        return {
            "success": True,
            "data": safe_credentials,
            "count": len(safe_credentials)
        }

    except Exception as e:
        logger.error(f"获取凭据列表失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "获取凭据列表失败",
            "debug": sanitize_error_info(str(e))
        }


@router.get("/{credential_id}")
async def get_credential(
    credential_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    获取单个凭据详情（包含解密密码）

    Args:
        credential_id: 凭据ID
        current_user: 当前用户

    Returns:
        凭据详情
    """
    try:
        user_id = current_user.get("user_id")
        credential = RemoteCredentialDB.get_by_id(credential_id, user_id)

        if not credential:
            return {
                "success": False,
                "error": "凭据不存在或无权访问",
                "debug": f"credential_id: {credential_id}, user_id: {user_id}"
            }

        return {
            "success": True,
            "data": credential
        }

    except Exception as e:
        logger.error(f"获取凭据详情失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "获取凭据详情失败",
            "debug": sanitize_error_info(str(e))
        }


@router.put("/{credential_id}")
async def update_credential(
    credential_id: str,
    request: UpdateCredentialRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    更新凭据

    Args:
        credential_id: 凭据ID
        request: 更新请求
        current_user: 当前用户

    Returns:
        更新结果
    """
    try:
        user_id = current_user.get("user_id")

        # 构建更新数据
        update_data = request.dict(exclude_unset=True)

        # 验证连接类型
        if "connection_type" in update_data and update_data["connection_type"] not in ['smb', 'ftp']:
            return {
                "success": False,
                "error": "不支持的连接类型",
                "debug": f"连接类型: {update_data['connection_type']}"
            }

        # 验证端口
        if "server_port" in update_data:
            port = update_data["server_port"]
            if port < 1 or port > 65535:
                return {
                    "success": False,
                    "error": "端口号必须在 1-65535 之间",
                    "debug": f"端口号: {port}"
                }

        # 更新凭据
        success = RemoteCredentialDB.update(credential_id, user_id, **update_data)

        if not success:
            return {
                "success": False,
                "error": "凭据不存在或无权访问",
                "debug": f"credential_id: {credential_id}, user_id: {user_id}"
            }

        logger.info(f"用户 {user_id} 更新凭据 {credential_id}")

        return {
            "success": True,
            "message": "凭据更新成功"
        }

    except Exception as e:
        logger.error(f"更新凭据失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "更新凭据失败",
            "debug": sanitize_error_info(str(e))
        }


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    删除凭据（软删除）

    Args:
        credential_id: 凭据ID
        current_user: 当前用户

    Returns:
        删除结果
    """
    try:
        user_id = current_user.get("user_id")
        success = RemoteCredentialDB.delete(credential_id, user_id)

        if not success:
            return {
                "success": False,
                "error": "凭据不存在或无权访问",
                "debug": f"credential_id: {credential_id}, user_id: {user_id}"
            }

        logger.info(f"用户 {user_id} 删除凭据 {credential_id}")

        return {
            "success": True,
            "message": "凭据删除成功"
        }

    except Exception as e:
        logger.error(f"删除凭据失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "删除凭据失败",
            "debug": sanitize_error_info(str(e))
        }
