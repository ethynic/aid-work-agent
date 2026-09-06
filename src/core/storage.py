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
