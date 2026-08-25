"""
租户级子智能体模板文件管理 API。

提供 per-tenant per-agent 的模板文件上传 / 列表 / 删除接口。
模板文件永久存储（落盘 + Redis 永久元数据），与 subagent_extra 的 extra_md 解耦，
运行时由 agent._load_template_files() 注入 system prompt 末尾。
"""

import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from loguru import logger

from src.config import settings
from src.core.redis_client import redis_client
from src.db.subagent_template_file_db import SubagentTemplateFileDB
from src.saas.api.tenant_auth import require_admin
from src.saas.context import get_current_tenant_id


router = APIRouter(prefix="/api/saas/tenant/subagent-templates", tags=["subagent-templates"])


# 允许的模板扩展名 -> MIME 映射（与前端白名单一致）
_ALLOWED_EXTS: dict = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.pdf': 'application/pdf',
    '.md': 'text/markdown',
    '.txt': 'text/plain',
    '.csv': 'text/csv',
}


def _templates_dir(tenant_id: str) -> Path:
    """模板文件落盘目录：storage/tenants/{tenant_id}/templates/（新规范）

    统一走 ensure_tenant_storage_dir，符合租户附件存储规范；
    Redis 元数据存绝对路径，供 /api/files/{file_id}/download 与文档工具读取。
    """
    from src.core.storage import ensure_tenant_storage_dir
    return Path(ensure_tenant_storage_dir(tenant_id, "templates")).absolute()


def _persist_file_meta(file_id: str, name: str, path: Path, size: int, mime_type: str) -> None:
    """写永久 Redis 元数据（uploaded_file:{file_id}，不 expire）。

    复用 /api/upload 与 /api/files/{file_id}/download 的元数据契约，
    使文档工具按 file_id 读取、下载端点都能正常工作。
    """
    key = redis_client.make_key("uploaded_file", file_id)
    info = {
        "file_id": file_id,
        "name": name,
        "path": str(path.absolute()),
        "size": size,
        "mime_type": mime_type,
        "type": "image" if mime_type.startswith("image/") else "file",
    }
    for field, value in info.items():
        redis_client.hset(key, field, value)
    # 不调 expire = 永久（与 tenant logo 一致）


@router.get("/{subagent_name}")
async def list_templates(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """获取某子智能体的模板文件列表"""
    try:
        tenant_id = get_current_tenant_id()
        files = SubagentTemplateFileDB.get(tenant_id, subagent_name)
        return {"success": True, "data": files}
    except Exception as e:
        logger.error(f"获取模板文件列表失败: {e}")
        return {"success": False, "error": "获取模板文件列表失败"}


@router.post("/{subagent_name}/upload")
async def upload_template(
    subagent_name: str,
    name: str = Form(..., description="模板名称"),
    file: UploadFile = File(..., description="模板文件"),
    request_admin: dict = Depends(require_admin),
):
    """上传一个模板文件并追加到该子智能体的模板列表。

    扩展名白名单校验 -> 生成 file_id -> 落盘 -> 写永久 Redis 元数据 -> DB 追加。
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    display_name = (name or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="模板名称不能为空")

    # 文件必填：模板名称与文件必须配套，缺一不可。
    # （file 完全缺失时 FastAPI File(...) 已返 422；此处兜底空文件名，给出友好业务错误）
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="请选择模板文件")

    suffix = Path(file.filename or "unknown").suffix.lower()
    if suffix not in _ALLOWED_EXTS:
        allowed = ", ".join(sorted(_ALLOWED_EXTS.keys()))
        raise HTTPException(status_code=400, detail=f"不支持的文件类型，仅支持 {allowed}")

    max_size = settings.storage.max_general_file_size
    if file.size and file.size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大，最大支持 {max_size / 1024 / 1024:.0f}MB",
        )

    file_id = f"file_{uuid.uuid4().hex[:12]}"
    templates_dir = _templates_dir(tenant_id)
    file_path = templates_dir / f"{file_id}{suffix}"

    try:
        with open(file_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        logger.error(f"模板文件落盘失败: {e}")
        raise HTTPException(status_code=500, detail="文件保存失败")

    size = file_path.stat().st_size
    mime_type = _ALLOWED_EXTS[suffix]

    # 永久 Redis 元数据（供 /api/files/{file_id}/download 与文档工具读取）
    _persist_file_meta(file_id, file.filename or display_name, file_path, size, mime_type)

    # DB 追加（读取现有列表 -> 追加 -> 全量覆盖）
    files: List[dict] = SubagentTemplateFileDB.get(tenant_id, subagent_name)
    files.append({
        "name": display_name,
        "file_id": file_id,
        "original_name": file.filename or display_name,
        "mime_type": mime_type,
        "size_bytes": size,
    })
    SubagentTemplateFileDB.set(tenant_id, subagent_name, files)

    logger.info(
        f"模板上传: tenant={tenant_id}, subagent={subagent_name}, "
        f"file_id={file_id}, name={display_name}"
    )
    return {"success": True, "data": files}


@router.delete("/{subagent_name}/{file_id}")
async def delete_template(
    subagent_name: str,
    file_id: str,
    request_admin: dict = Depends(require_admin),
):
    """删除一个模板文件：DB 移除 + Redis 元数据删除 + 磁盘文件删除"""
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    files: List[dict] = SubagentTemplateFileDB.get(tenant_id, subagent_name)
    target = next((f for f in files if f.get("file_id") == file_id), None)
    if not target:
        return {"success": True, "data": files, "message": "模板不存在或已删除"}

    # 删磁盘文件（按 file_id 前缀 glob，避免依赖 ext 反推）
    try:
        for p in _templates_dir(tenant_id).glob(f"{file_id}*"):
            p.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"删除模板磁盘文件失败 file_id={file_id}: {e}")

    # 删 Redis 元数据
    try:
        redis_client.delete(redis_client.make_key("uploaded_file", file_id))
    except Exception as e:
        logger.warning(f"删除模板 Redis 元数据失败 file_id={file_id}: {e}")

    # DB 移除并全量覆盖
    files = [f for f in files if f.get("file_id") != file_id]
    SubagentTemplateFileDB.set(tenant_id, subagent_name, files)

    logger.info(
        f"模板删除: tenant={tenant_id}, subagent={subagent_name}, file_id={file_id}"
    )
    return {"success": True, "data": files}
