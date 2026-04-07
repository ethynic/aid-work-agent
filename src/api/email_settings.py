"""用户邮箱设置 API

提供邮箱配置的查询、保存（含测试发送）、删除接口
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.db.email_credential import EmailCredentialDB
from src.api.auth import get_current_user


router = APIRouter(prefix="/api/email-settings", tags=["邮箱设置"])


# ============== 请求/响应模型 ==============

class EmailSettingsRequest(BaseModel):
    """邮箱设置请求"""
    email_address: str = Field(..., description="邮箱地址")
    smtp_server: str = Field(..., description="SMTP 服务器地址")
    smtp_port: int = Field(..., description="SMTP 端口")
    smtp_user: str = Field(..., description="SMTP 用户名")
    smtp_password: str = Field(..., description="SMTP 密码")
    smtp_encryption: str = Field("ssl", description="SMTP 加密方式: ssl / tls / none")
    imap_server: str = Field(..., description="IMAP 服务器地址")
    imap_port: int = Field(..., description="IMAP 端口")
    imap_encryption: str = Field("ssl", description="IMAP 加密方式: ssl / tls / none")


# ============== API 路由 ==============

@router.get("/")
async def get_email_settings(current_user: dict = Depends(get_current_user)):
    """获取当前用户的邮箱配置（密码掩码）"""
    try:
        user_id = current_user.get("user_id")
        config = EmailCredentialDB.get_masked_by_user(user_id)

        if not config:
            return {"success": True, "data": None, "bound": False}

        # 移除内部字段
        safe_config = {
            "email_address": config["email_address"],
            "smtp_server": config["smtp_server"],
            "smtp_port": config["smtp_port"],
            "smtp_user": config["smtp_user"],
            "smtp_password": config["smtp_password"],
            "smtp_encryption": config.get("smtp_encryption", "ssl"),
            "imap_server": config["imap_server"],
            "imap_port": config["imap_port"],
            "imap_encryption": config.get("imap_encryption", "ssl"),
        }

        return {"success": True, "data": safe_config, "bound": True}

    except Exception as e:
        logger.error(f"获取邮箱配置失败: {e}", exc_info=True)
        return {"success": False, "error": "获取邮箱配置失败"}


@router.post("/")
async def save_email_settings(
    request: EmailSettingsRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    保存邮箱配置（先测试发送，成功才保存）

    测试邮件发送给用户自己，验证 SMTP 配置正确性
    """
    try:
        user_id = current_user.get("user_id")

        # 1. 构造 UserEmail 用于测试
        from src.models.user import UserEmail, EncryptionType

        test_email = UserEmail(
            email_address=request.email_address,
            smtp_server=request.smtp_server,
            smtp_port=request.smtp_port,
            smtp_user=request.smtp_user,
            smtp_password=request.smtp_password,
            smtp_encryption=EncryptionType(request.smtp_encryption),
            imap_server=request.imap_server,
            imap_port=request.imap_port,
            imap_encryption=EncryptionType(request.imap_encryption),
        )

        # 2. 发送测试邮件给自己
        from src.tools.email.email_tool import EmailSendTool

        test_tool = EmailSendTool(test_email)
        test_result = await test_tool.execute(
            to=request.email_address,
            subject="邮箱绑定测试 - AID Work Agent",
            body=f"这是一封测试邮件，用于验证您的邮箱配置是否正确。\n\n如果您看到了这封邮件，说明配置成功！\n\n发送时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )

        if not test_result.get("success"):
            error = test_result.get("error", "未知错误")
            logger.warning(f"邮箱测试发送失败: user_id={user_id}, error={error}")
            return {
                "success": False,
                "error": f"测试邮件发送失败: {error}",
            }

        # 3. 测试成功，保存到数据库
        config_dict = request.dict()
        EmailCredentialDB.upsert(user_id, config_dict)

        logger.info(f"用户 {user_id} 邮箱配置保存成功: {request.email_address}")
        return {
            "success": True,
            "message": "邮箱绑定成功",
        }

    except Exception as e:
        logger.error(f"保存邮箱配置失败: {e}", exc_info=True)
        return {"success": False, "error": f"保存邮箱配置失败: {str(e)}"}


@router.delete("/")
async def delete_email_settings(current_user: dict = Depends(get_current_user)):
    """删除当前用户的邮箱配置"""
    try:
        user_id = current_user.get("user_id")
        success = EmailCredentialDB.delete(user_id)

        if not success:
            return {"success": False, "error": "未找到邮箱配置"}

        logger.info(f"用户 {user_id} 删除了邮箱配置")
        return {"success": True, "message": "邮箱配置已删除"}

    except Exception as e:
        logger.error(f"删除邮箱配置失败: {e}", exc_info=True)
        return {"success": False, "error": "删除邮箱配置失败"}
