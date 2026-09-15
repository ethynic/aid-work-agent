"""
租户附件存储路径工具

按 `.claude/rules/backend_dev.md`「租户附件存储规范」统一管理
`storage/tenants/{tenant_id}/` 下的子目录路径。

目录结构：
    storage/
    └── tenants/
        ├── {tenant_id_a}/
        │   ├── conversation/    # 对话中产生的附件
        │   ├── knowledge/       # 知识库附件
        │   ├── export/          # 业务导出文件
        │   ├── report/          # 统计报表文件
        │   ├── avatar/          # 头像 / Logo
        │   └── temp/            # 临时文件
        └── {tenant_id_b}/
            └── ...
"""

import os
from pathlib import Path
from typing import Optional

from loguru import logger


# 仓库根目录下的统一存储根
_STORAGE_ROOT = "storage"
_TENANTS_ROOT = os.path.join(_STORAGE_ROOT, "tenants")


def get_tenants_storage_root() -> str:
    """获取租户存储根目录（`storage/tenants`，相对路径）。

    供租户数据迁移工具等需要引用租户存储根的调用方使用，
    避免在业务代码中硬编码 `storage/tenants` 路径字符串。
    """
    return _TENANTS_ROOT


def normalize_tenant_id(tenant_id: str) -> str:
    """规范化租户 ID 用于存储路径：剥离 `tenant_` 前缀。

    数据库 `tenants.tenant_id` 带 `tenant_` 前缀（如 `tenant_ea24cd1a1097`），
    而存储规范要求 `storage/tenants/{tid}/{scene}/` 中 {tid} 不带前缀。
    统一在此剥离，避免带前缀与不带前缀目录并存导致读写路径错位。

    特殊值（`_anonymous` 等匿名占位）不以 `tenant_` 开头，原样返回。
    """
    if tenant_id.startswith("tenant_") and len(tenant_id) > len("tenant_"):
        return tenant_id[len("tenant_"):]
    return tenant_id


def strip_legacy_storage_prefix(path: str) -> str:
    """剥离相对路径头部的旧存储基名段，防止产生嵌套目录。

    历史（2026-09 整改前）LLM 常回传 `storage/...`、`output/...`、
    `storage/output/...` 等带旧基名前缀的相对路径，直接拼到新基目录下会
    产生 `output/storage/...`、`storage/storage/tenants/...` 等嵌套目录
    （生产/测试服务器均已出现）。统一在拼接前剥离。

    只剥离**头部**的 `{storage, output, tenants}` 段，防误伤深层同名段
    （如用户真实想要的 `report/storage_chart.png` 不受影响）。
    """
    parts = Path(path).parts
    idx = 0
    while idx < len(parts) and parts[idx] in ("storage", "output", "tenants"):
        idx += 1
    if idx == 0:
        return path
    return str(Path(*parts[idx:])) if parts[idx:] else ""


def get_tenant_storage_dir(tenant_id: str, scene: str) -> str:
    """获取租户某场景的目录路径（相对路径，不保证存在）。

    Args:
        tenant_id: 租户 ID（自动剥离 `tenant_` 前缀，见 normalize_tenant_id）
        scene: 业务场景子目录名（conversation / knowledge / export / ...）

    Returns:
        `storage/tenants/{tenant_id}/{scene}`
    """
    return os.path.join(_TENANTS_ROOT, normalize_tenant_id(tenant_id), scene)


def ensure_tenant_storage_dir(tenant_id: str, scene: str) -> str:
    """确保租户某场景的目录存在，并返回其路径。

    目录不存在时会自动创建（`exist_ok=True`）。

    Args:
        tenant_id: 租户 ID
        scene: 业务场景子目录名

    Returns:
        `storage/tenants/{tenant_id}/{scene}`（绝对路径或相对路径，取决于 cwd）
    """
    if not tenant_id:
        raise ValueError("tenant_id 不能为空")

    dir_path = get_tenant_storage_dir(tenant_id, scene)
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


def get_tenant_storage_path(tenant_id: str, scene: str, filename: str) -> str:
    """获取租户某场景下某文件的完整路径（相对路径，不创建目录）。

    Args:
        tenant_id: 租户 ID
        scene: 业务场景子目录名
        filename: 文件名

    Returns:
        `storage/tenants/{tenant_id}/{scene}/{filename}`
    """
    return os.path.join(get_tenant_storage_dir(tenant_id, scene), filename)


def get_tenant_storage_abs_path(tenant_id: str, scene: str, filename: str) -> str:
    """获取租户某场景下某文件的绝对路径（不创建目录）。

    业务代码统一通过此函数获取写入路径，路径始终以仓库 cwd 为基准。
    """
    rel = get_tenant_storage_path(tenant_id, scene, filename)
    return os.path.abspath(rel)


# 常见附件后缀 -> MIME（磁盘扫描兜底时按扩展名推导）
_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".html": "text/html",
    ".htm": "text/html",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
}


def find_uploaded_file_on_disk(file_id: str, register_to_redis: bool = True) -> Optional[dict]:
    """按文件名在 storage/tenants 下全场景扫描查找文件

    覆盖 `storage/tenants/{tenant}/{scene}/`（conversation/knowledge/templates/
    images 等）及 scene 下 1 层子目录（如 images/2026-08/）。Redis 元数据丢失
    （迁移、过期、重启）时的兜底，文件本体在磁盘上不受影响。

    匹配规则（与旧 conversation-only 兜底兼容）：
    - 输入带扩展名（如 `report.docx`）-> 精确匹配文件名
    - 输入为无扩展名的 file_id（如 `file_abc123`）-> 按文件名主干匹配任意后缀

    命中后默认回写 Redis `uploaded_file:{file_id}`（TTL 24h）自愈，后续解析
    直接走 Redis 命中，不再触发扫描。注意自愈重建的 `name` 是磁盘文件名，
    用户上传时的原始文件名无法从磁盘恢复。

    Args:
        file_id: 文件 ID（`file_xxx`）或裸文件名（`report.docx`）
        register_to_redis: 命中后是否回写 Redis 自愈

    Returns:
        元数据 dict（file_id/name/path/size/mime_type/type）或 None
    """
    if not file_id or ".." in Path(file_id).parts:
        return None
    try:
        tenants_root = Path(_TENANTS_ROOT)
        if not tenants_root.exists():
            return None

        search_dirs = []
        for d1 in tenants_root.iterdir():
            if not d1.is_dir():
                continue
            for d2 in d1.iterdir():
                if d2.is_dir():
                    search_dirs.append(d2)
                    for d3 in d2.iterdir():
                        if d3.is_dir():
                            search_dirs.append(d3)

        target = Path(file_id)
        if target.suffix:
            match = lambda f: f.name == target.name  # noqa: E731
        elif file_id.startswith("file_"):
            match = lambda f: f.stem == target.stem  # noqa: E731
        else:
            return None

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for f in search_dir.iterdir():
                if f.is_file() and match(f):
                    mime_type = _MIME_BY_SUFFIX.get(f.suffix.lower(), "application/octet-stream")
                    info = {
                        "file_id": file_id,
                        "name": f.name,
                        "path": str(f.absolute()),
                        "size": f.stat().st_size,
                        "mime_type": mime_type,
                        "type": "image" if mime_type.startswith("image/") else "file",
                    }
                    if register_to_redis and file_id.startswith("file_"):
                        try:
                            from src.core.redis_client import redis_client
                            key = redis_client.make_key("uploaded_file", file_id)
                            for field, value in info.items():
                                redis_client.hset(key, field, value)
                            redis_client.expire(key, 86400)
                        except Exception as e:
                            logger.warning(f"[storage] file_id 磁盘自愈回写 Redis 失败: {e}")
                    return info
        return None
    except Exception as e:
        logger.warning(f"[storage] file_id 磁盘扫描兜底失败: {file_id} {e}")
        return None


def resolve_uploaded_file_path(file_path: str) -> Optional[str]:
    """file_id -> 磁盘绝对路径（Redis 元数据 + 全场景磁盘扫描兜底）

    供 excel/pdf/word 等文件工具的 resolve_path 复用：
    1. Redis `uploaded_file:{file_id}` 元数据命中（最可靠，仅 file_ 前缀查询）
    2. 磁盘全场景扫描兜底（命中后回写 Redis 自愈，见 find_uploaded_file_on_disk；
       file_ 前缀按 stem 匹配，带扩展名的裸文件名按精确文件名匹配）

    真实存在的相对路径应由调用方先用 `Path.exists()` 命中，走到这里的输入
    在 cwd 下必不存在。

    Returns:
        绝对路径字符串或 None
    """
    redis_path = resolve_path_via_redis(file_path)
    if redis_path:
        return redis_path
    info = find_uploaded_file_on_disk(file_path)
    if info:
        return info["path"]
    return None


def resolve_path_via_redis(file_id: str) -> Optional[str]:
    """通过 Redis uploaded_file:{file_id} 元数据查磁盘路径

    Agent 传给工具的 file_paths 经常是 file_id（如 file_e300d0d5befc），
    而不是磁盘路径。file_id 上传时（cp/upload/subagent_template_file）
    在 Redis `uploaded_file:{file_id}` 写了永久元数据，path 字段是绝对路径，
    直接命中最可靠，不依赖目录扫描。

    Args:
        file_id: 文件 ID，形如 `file_xxxxxxxxxxx`

    Returns:
        命中且文件存在 -> 返回绝对路径字符串；否则返回 None，调用方走目录扫描兜底。

    Note:
        Redis 不可用或 key 不存在时返回 None，不抛异常（降级到目录扫描）。
        仅 `file_` 前缀的 ID 才查 Redis，其他直接返回 None。
    """
    if not file_id or not file_id.startswith("file_"):
        return None
    try:
        from src.core.redis_client import redis_client
        key = redis_client.make_key("uploaded_file", file_id)
        info = redis_client.hgetall(key)
        if not info:
            return None
        path = info.get("path")
        if path and Path(path).exists():
            return str(Path(path).absolute())
        return None
    except Exception as e:
        logger.warning(f"[storage] Redis 元数据查询失败: {e}")
        return None
