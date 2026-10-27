"""租户邮箱设置 API

租户级邮箱（SMTP/IMAP）配置的查询、保存（含测试发送）、删除接口。
仅租户管理员/平台管理员（X-Tenant-Id 代管）可操作。
供邮箱三层解析第 2 层兜底使用（用户个人邮箱 -> 租户邮箱 -> 报错提示）。
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.db.tenant_email_credential import TenantEmailCredentialDB
from src.saas.api.tenant_auth import require_admin
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.services.behavior_log import audit_action


router = APIRouter(prefix="/api/tenant/email-settings", tags=["租户邮箱设置"])


def sanitize_error_info(error_msg: str) -> str:
    import re
    for pattern in [r'password["\s:=]+\S+', r'api[_-]?key["\s:=]+\S+', r'token["\s:=]+\S+']:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***',
                           error_msg, flags=re.IGNORECASE)
    return error_msg


class TenantEmailSettingsRequest(BaseModel):
    """租户邮箱设置请求"""
    email_address: str = Field(..., description="邮箱地址")
    smtp_server: str = Field(..., description="SMTP 服务器地址")
    smtp_port: int = Field(..., description="SMTP 端口")
    smtp_user: str = Field(..., description="SMTP 用户名")
    smtp_password: str = Field(..., description="SMTP 密码")
    smtp_encryption: str = Field("ssl", description="SMTP 加密方式: ssl / tls / none")
    imap_server: str = Field(..., description="IMAP 服务器地址")
    imap_port: int = Field(..., description="IMAP 端口")
    imap_encryption: str = Field("ssl", description="IMAP 加密方式: ssl / tls / none")


def _safe_config(config: dict) -> dict:
    """剥离内部字段，密码保持掩码"""
    return {
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


@router.get("")
async def get_tenant_email_settings(request_admin: dict = Depends(require_admin)):
    """获取租户邮箱配置（密码掩码）"""
    try:
        tenant_id = request_admin.get("tenant_id")
        if not tenant_id:
            return {"success": False, "error": "缺少租户身份"}

        config = TenantEmailCredentialDB.get_masked_by_tenant(tenant_id)
        if not config:
            return {"success": True, "data": None, "bound": False}

        return {"success": True, "data": _safe_config(config), "bound": True}

    except Exception as e:
        logger.opt(exception=True).error(f"获取租户邮箱配置失败: {e}")
        return {"success": False, "error": "获取租户邮箱配置失败"}


@router.post("")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.CONFIG)
async def save_tenant_email_settings(
    request: TenantEmailSettingsRequest,
    http_request: Request,
    request_admin: dict = Depends(require_admin),
):
    """保存租户邮箱配置（先测试发送，成功才保存）

    测试邮件发给配置的邮箱自身，验证 SMTP 配置正确性。
    """
    try:
        tenant_id = request_admin.get("tenant_id")
        if not tenant_id:
            return {"success": False, "error": "缺少租户身份"}

        from src.models.user import UserEmail, EncryptionType
        from src.tools.email.email_tool import EmailProcessTool

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

        test_tool = EmailProcessTool(test_email)
        test_result = await test_tool.execute(
            action="send",
            to=request.email_address,
            subject="租户邮箱配置测试 - AID Work Agent",
            body=("这是一封测试邮件，用于验证本租户的企业邮箱配置是否正确。\n\n"
                  f"如果您看到了这封邮件，说明配置成功！\n\n"
                  f"发送时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
        )

        if not test_result.get("success"):
            error = test_result.get("error", "未知错误")
            logger.warning(f"租户邮箱测试发送失败: tenant_id={tenant_id}, error={error}")
            return {"success": False, "error": f"测试邮件发送失败: {error}"}

        TenantEmailCredentialDB.upsert(tenant_id, request.dict())
        logger.info(f"租户 {tenant_id} 邮箱配置保存成功: {request.email_address}")
        return {"success": True, "message": "租户邮箱配置成功"}

    except Exception as e:
        logger.opt(exception=True).error(f"保存租户邮箱配置失败: {e}")
        return {"success": False, "error": f"保存租户邮箱配置失败: {sanitize_error_info(str(e))}"}


@router.delete("")
@audit_action(BehaviorAction.DELETE, BehaviorResourceType.CONFIG)
async def delete_tenant_email_settings(
    http_request: Request,
    request_admin: dict = Depends(require_admin),
):
    """删除租户邮箱配置"""
    try:
        tenant_id = request_admin.get("tenant_id")
        if not tenant_id:
            return {"success": False, "error": "缺少租户身份"}

        success = TenantEmailCredentialDB.delete(tenant_id)
        if not success:
            return {"success": False, "error": "未找到租户邮箱配置"}

        return {"success": True, "message": "租户邮箱配置已删除"}

    except Exception as e:
        logger.opt(exception=True).error(f"删除租户邮箱配置失败: {e}")
        return {"success": False, "error": "删除租户邮箱配置失败"}
