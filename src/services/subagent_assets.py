"""租户数字员工资产共享逻辑：模板文件落盘/元数据 + 资产克隆。

模板文件与知识栏目均为 per-(tenant, subagent) 关联，运行时按 subagent_config.dir_name
读取（副本实例 = 实例 agent_id）。复制副本实例时需把源员工的模板文件（含磁盘副本）与知识栏目
克隆到新实例 id，否则副本实例会丢失模板与栏目。

供 src/api/subagent_template_file.py（上传/删除）与
src/saas/services/tenant_agent_instance_service.py（复制副本实例）共用，避免重复实现。
"""

import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from loguru import logger


def templates_dir(tenant_id: str) -> Path:
    """模板文件落盘目录：storage/tenants/{tenant_id}/templates/（新规范）

    统一走 ensure_tenant_storage_dir，符合租户附件存储规范；
    Redis 元数据存绝对路径，供 /api/files/{file_id}/download 与文档工具读取。
    """
    from src.core.storage import ensure_tenant_storage_dir
    return Path(ensure_tenant_storage_dir(tenant_id, "templates")).absolute()


def persist_template_meta(
    file_id: str,
    name: str,
    path: Path,
    size: int,
    mime_type: str,
    tenant_id: Optional[str] = None,
) -> None:
    """写永久 Redis 元数据（uploaded_file:{file_id}，不 expire）。

    复用 /api/upload 与 /api/files/{file_id}/download 的元数据契约，
    使文档工具按 file_id 读取、下载端点都能正常工作。
    """
    from src.core.redis_client import redis_client

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
    # 持久化元数据（Redis 仅缓存；模板文件名不能随缓存失效丢失）
    try:
        from src.services.files.asset_store import upsert_file_asset
        upsert_file_asset(info, tenant_id=tenant_id, source="subagent_template")
    except Exception as e:
        logger.opt(exception=True).warning(f"模板文件元数据持久化失败（不影响主流程）: {e}")


def copy_template_files(tenant_id: str, src_agent_id: str, dst_agent_id: str) -> int:
    """克隆源员工的模板文件到目标实例（磁盘复制 + 新 file_id + 永久元数据）。

    每条模板复制为独立的磁盘文件与新 file_id，避免删除源模板时影响副本实例。
    Returns: 成功复制的模板数量（源为空或异常返回 0，best-effort 不抛错）。
    """
    if not tenant_id or not src_agent_id or not dst_agent_id or src_agent_id == dst_agent_id:
        return 0
    try:
        from src.db.subagent_template_file_db import SubagentTemplateFileDB
        src_files = SubagentTemplateFileDB.get(tenant_id, src_agent_id)
    except Exception as e:
        logger.opt(exception=True).error(f"读取源模板文件失败 src={src_agent_id}: {e}")
        return 0
    if not src_files:
        return 0

    dest_dir = templates_dir(tenant_id)
    new_files: List[dict] = []
    for f in src_files:
        old_file_id = f.get("file_id")
        if not old_file_id:
            continue
        # 按 file_id 前缀 glob 定位源磁盘文件（不依赖扩展名反推）
        matches = list(dest_dir.glob(f"{old_file_id}*"))
        if not matches:
            logger.warning(f"克隆模板跳过（源磁盘文件缺失）: src={src_agent_id}, file_id={old_file_id}")
            continue
        src_path = matches[0]
        new_file_id = f"file_{uuid.uuid4().hex[:12]}"
        new_path = dest_dir / f"{new_file_id}{src_path.suffix}"
        try:
            shutil.copy2(src_path, new_path)
        except Exception as e:
            logger.opt(exception=True).error(f"克隆模板磁盘文件失败 file_id={old_file_id}: {e}")
            continue
        size = new_path.stat().st_size
        mime_type = f.get("mime_type") or "application/octet-stream"
        original_name = f.get("original_name") or f.get("name") or new_file_id
        persist_template_meta(new_file_id, original_name, new_path, size, mime_type, tenant_id=tenant_id)
        new_files.append({
            "name": f.get("name") or original_name,
            "file_id": new_file_id,
            "original_name": original_name,
            "mime_type": mime_type,
            "size_bytes": size,
        })

    if not new_files:
        return 0
    try:
        SubagentTemplateFileDB.set(tenant_id, dst_agent_id, new_files)
    except Exception as e:
        logger.opt(exception=True).error(f"写入副本模板关联失败 dst={dst_agent_id}: {e}")
        return 0
    return len(new_files)


def copy_knowledge_sources(tenant_id: str, src_agent_id: str, dst_agent_id: str) -> int:
    """克隆源员工的知识库栏目关联到目标实例（含共享来源项原样复制）。

    Returns: 复制到的栏目项数量（源为空或异常返回 0）。
    """
    if not tenant_id or not src_agent_id or not dst_agent_id or src_agent_id == dst_agent_id:
        return 0
    try:
        from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
        sources = SubagentKnowledgeSourceDB.get(tenant_id, src_agent_id)
    except Exception as e:
        logger.opt(exception=True).error(f"读取源知识栏目失败 src={src_agent_id}: {e}")
        return 0
    if not sources:
        return 0
    try:
        ok = SubagentKnowledgeSourceDB.set(tenant_id, dst_agent_id, sources)
    except Exception as e:
        logger.opt(exception=True).error(f"写入副本知识栏目失败 dst={dst_agent_id}: {e}")
        return 0
    return len(sources) if ok else 0
