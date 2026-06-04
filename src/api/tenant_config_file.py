"""
租户配置文件管理 API。

管理 storage/tenants/{tenant_id}/ 下的子智能体配置文件（如 after-sales-api.md）。
供租户管理员或平台管理员上传/下载/删除配置文件。
"""

import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from loguru import logger

from src.saas.api.tenant_auth import require_admin

router = APIRouter(prefix="/api/saas/tenant/config-file", tags=["tenant-config-file"])

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 支持上传的文件扩展名
ALLOWED_EXTENSIONS = {".md"}

# 最大文件大小 1MB
MAX_FILE_SIZE = 1 * 1024 * 1024


def _get_config_path(tenant_id: str, subagent_name: str) -> Path:
    """获取配置文件路径，文件名格式：{subagent_name}-api.md"""
    return PROJECT_ROOT / "storage" / "tenants" / tenant_id / f"{subagent_name}-api.md"


@router.post("/{subagent_name}")
async def upload_config_file(
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

    # 保存文件
    config_path = _get_config_path(tenant_id, subagent_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        config_path.write_bytes(content)
        logger.info(f"配置文件已上传: tenant={tenant_id}, subagent={subagent_name}, size={len(content)}")
        return {"success": True, "message": "配置文件上传成功"}
    except Exception as e:
        logger.error(f"上传配置文件失败: {e}")
        raise HTTPException(status_code=500, detail="保存文件失败")


@router.get("/{subagent_name}")
async def download_config_file(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """下载子智能体配置文件"""
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
async def delete_config_file(
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
