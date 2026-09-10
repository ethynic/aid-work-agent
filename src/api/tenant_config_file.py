"""
租户配置文件管理 API。

管理 storage/tenants/{tenant_id}/templates/ 下的子智能体配置文件
（如 after-sales-api.md）。供租户管理员或平台管理员上传/下载/删除配置文件。

按 `.claude/rules/backend_dev.md`「租户附件存储规范」统一走
`src.core.storage` 工具函数，子智能体模板/配置文件统一落 templates 场景
（与 `subagent_template_file.py` 一致），禁止直接落在租户根目录。
"""

import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from loguru import logger

from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path
from src.saas.api.tenant_auth import require_admin
from src.saas.models.enums import BehaviorAction, BehaviorResourceType
from src.services.behavior_log import audit_action

router = APIRouter(prefix="/api/saas/tenant/config-file", tags=["tenant-config-file"])

# 支持上传的文件扩展名
ALLOWED_EXTENSIONS = {".md"}

# 最大文件大小 1MB
MAX_FILE_SIZE = 1 * 1024 * 1024


def _get_config_path(tenant_id: str, subagent_name: str) -> Path:
    """获取配置文件路径：storage/tenants/{tenant_id}/templates/{subagent_name}-api.md

    统一走 `get_tenant_storage_abs_path`（normalize_tenant_id 自动剥离 `tenant_`
    前缀），与 `subagent_template_file.py::_templates_dir` 同一场景，
    禁止直接落在 `storage/tenants/{tid}/` 根目录。
    """
    ensure_tenant_storage_dir(tenant_id, "templates")
    return Path(
        get_tenant_storage_abs_path(
            tenant_id, "templates", f"{subagent_name}-api.md"
        )
    )


@router.post("/{subagent_name}")
@audit_action(BehaviorAction.CREATE, BehaviorResourceType.CONFIG, id_arg="subagent_name")
async def upload_config_file(
    request: Request,
    subagent_name: str,
    file: UploadFile = File(...),
    request_admin: dict = Depends(require_admin),
):
    """上传子智能体配置文件"""
    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法获取租户 ID")

    # 校验文件扩展名
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，仅允许 {', '.join(ALLOWED_EXTENSIONS)}")

    # 读取内容并校验大小
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 1MB 限制")

    # 保存文件（_get_config_path 内部已确保 templates 目录存在）
    config_path = _get_config_path(tenant_id, subagent_name)

    try:
        config_path.write_bytes(content)
        logger.info(f"配置文件已上传: tenant={tenant_id}, subagent={subagent_name}, size={len(content)}")
        return {"success": True, "message": "配置文件上传成功"}
    except Exception as e:
        logger.error(f"上传配置文件失败: {e}")
        raise HTTPException(status_code=500, detail="保存文件失败")


def _require_admin_or_ticket(request: Request) -> dict:
    """require_admin 的下载票据兜底：header 认证失败时，
    若中间件已验签过携带管理员角色的下载票据，则构造等价 admin 字典。

    仅用于下载直链端点（票据由 require_admin 保护的签发端点发出，角色可信）。
    """
    try:
        return require_admin(request)
    except HTTPException:
        payload = getattr(request.state, "ticket_payload", None)
        from src.core.download_ticket import payload_is_admin

        if payload_is_admin(payload) and payload.get("tenant_id"):
            return {
                "user_id": payload.get("user_id"),
                "tenant_id": payload.get("tenant_id"),
                "role": payload.get("role"),
            }
        raise


@router.post("/{subagent_name}/download_ticket")
async def create_config_file_download_ticket(
    subagent_name: str,
    request: Request,
    request_admin: dict = Depends(require_admin),
):
    """签发配置文件下载票据（5 分钟有效），供前端直链原生下载"""
    from src.core.download_ticket import TICKET_TTL_SECONDS, issue_download_ticket

    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法获取租户 ID")

    ticket = issue_download_ticket(
        f"/api/saas/tenant/config-file/{subagent_name}",
        tenant_id,
        request_admin.get("user_id"),
        request_admin.get("role"),
    )
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


@router.get("/{subagent_name}")
async def download_config_file(
    request: Request,
    subagent_name: str,
    request_admin: dict = Depends(_require_admin_or_ticket),
):
    """下载子智能体配置文件（header 认证或下载票据）"""
    from fastapi.responses import FileResponse

    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法获取租户 ID")

    config_path = _get_config_path(tenant_id, subagent_name)
    if not config_path.exists():
        return {"success": False, "error": "配置文件不存在", "configured": False}

    return FileResponse(
        path=str(config_path),
        filename=f"{subagent_name}-api.md",
        media_type="text/markdown",
    )


@router.get("/{subagent_name}/status")
async def get_config_file_status(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """查询配置文件状态"""
    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法获取租户 ID")

    config_path = _get_config_path(tenant_id, subagent_name)
    if config_path.exists():
        stat = config_path.stat()
        return {
            "success": True,
            "configured": True,
            "filename": f"{subagent_name}-api.md",
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
        }
    return {"success": True, "configured": False}


@router.delete("/{subagent_name}")
@audit_action(BehaviorAction.DELETE, BehaviorResourceType.CONFIG, id_arg="subagent_name")
async def delete_config_file(
    request: Request,
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """删除子智能体配置文件"""
    tenant_id = request_admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法获取租户 ID")

    config_path = _get_config_path(tenant_id, subagent_name)
    if not config_path.exists():
        return {"success": False, "error": "配置文件不存在"}

    try:
        config_path.unlink()
        logger.info(f"配置文件已删除: tenant={tenant_id}, subagent={subagent_name}")
        return {"success": True, "message": "配置文件已删除"}
    except Exception as e:
        logger.error(f"删除配置文件失败: {e}")
        raise HTTPException(status_code=500, detail="删除文件失败")
